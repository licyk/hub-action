"""Prepare and verify hf-xet wheels for the current Termux arm64 Python."""

import argparse
import ast
import gzip
import hashlib
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any, cast

import tomlkit

TERMUX_REPO = "https://packages.termux.dev/apt/termux-main/"
TERMUX_PREFIX = "data/data/com.termux/files/usr"


def download(url: str, destination: Path) -> None:
    """Download a public build dependency, failing on HTTP errors."""
    subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--retry",
            "3",
            "--output",
            str(destination),
            url,
        ],
        check=True,
    )


def python_package(index: str) -> dict[str, str]:
    """Find the arm64 Python package in a Debian Packages index."""
    for paragraph in index.split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in paragraph.splitlines()
            if ": " in line and not line.startswith(" ")
        )
        if (
            fields.get("Package") == "python"
            and fields.get("Architecture") == "aarch64"
        ):
            return fields
    raise RuntimeError("Termux index does not contain an aarch64 Python package")


def read_sysconfig(path: Path) -> dict:
    """Read target Python configuration as data without executing target code."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "build_time_vars"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise RuntimeError(f"Missing build_time_vars in {path}")


def prepare(work_dir: Path) -> None:
    """Fetch verified Termux Python libraries and configure PyO3 for Android."""
    work_dir.mkdir(parents=True, exist_ok=True)
    index_path = work_dir / "Packages.gz"
    download(TERMUX_REPO + "dists/stable/main/binary-aarch64/Packages.gz", index_path)
    package = python_package(gzip.decompress(index_path.read_bytes()).decode())
    filename = package["Filename"]
    if not filename.startswith("pool/") or ".." in Path(filename).parts:
        raise RuntimeError(f"Unexpected Termux package path: {filename}")
    deb = work_dir / "python.deb"
    download(TERMUX_REPO + filename, deb)
    digest = hashlib.sha256(deb.read_bytes()).hexdigest()
    if digest != package["SHA256"]:
        raise RuntimeError("Termux Python package SHA256 mismatch")
    root = work_dir / "sysroot"
    subprocess.run(["dpkg-deb", "--extract", str(deb), str(root)], check=True)
    lib = root / TERMUX_PREFIX / "lib"
    configs = list(lib.glob("python3.*/_sysconfigdata*.py"))
    if len(configs) != 1:
        raise RuntimeError(f"Expected one target Python configuration, got {configs}")
    config = read_sysconfig(configs[0])
    version = config["VERSION"]
    api = config["ANDROID_API_LEVEL"]
    if (
        not re.fullmatch(r"3\.\d+", version)
        or config["MULTIARCH"] != "aarch64-linux-android"
        or config.get("Py_GIL_DISABLED", 0)
        or not isinstance(api, int)
        or api < 24
    ):
        raise RuntimeError("Unsupported Termux Python configuration")
    library = f"python{version}"
    if not (lib / f"lib{library}.so").is_file():
        raise RuntimeError(f"Missing lib{library}.so")
    pyo3_config = work_dir / "pyo3-config.txt"
    pyo3_config.write_text(
        f"implementation=CPython\nversion={version}\nshared=true\nabi3=false\n"
        f"lib_name={library}\nlib_dir={lib}\npointer_width=64\n"
    )
    metadata = {
        "python_version": version,
        "termux_package_version": package["Version"],
        "termux_package_url": TERMUX_REPO + filename,
        "termux_package_sha256": digest,
        "android_api_level": api,
        "target": "aarch64-linux-android",
        "tls": "native-tls-vendored",
    }
    (work_dir / "build-info.json").write_text(json.dumps(metadata, indent=2) + "\n")
    environment = {
        "PYO3_CONFIG_FILE": str(pyo3_config),
        "PYO3_CROSS_LIB_DIR": str(lib),
        "PYO3_CROSS_PYTHON_VERSION": version,
        "PYO3_CROSS": "1",
        "ANDROID_API_LEVEL": str(api),
    }
    with Path(os.environ["GITHUB_ENV"]).open("a") as output:
        output.writelines(f"{key}={value}\n" for key, value in environment.items())
    print(json.dumps(metadata, indent=2))


def patch_source(source: Path) -> None:
    """Select OpenSSL and a version-specific Python ABI in the build checkout."""
    root_path = source / "Cargo.toml"
    binding_path = source / "hf_xet/Cargo.toml"
    root = cast(dict[str, Any], tomlkit.parse(root_path.read_text()))
    binding = cast(dict[str, Any], tomlkit.parse(binding_path.read_text()))
    if "native-tls-vendored" not in binding["features"]:
        raise RuntimeError("This hf-xet revision does not support native-tls-vendored")
    for name in ("xet-pkg", "xet-client"):
        binding["dependencies"][name]["default-features"] = False
    # Android links a specific libpython. Do not advertise this as an abi3 wheel.
    for pyo3 in (
        root["workspace"]["dependencies"]["pyo3"],
        binding["dependencies"]["pyo3"],
    ):
        pyo3["features"] = [
            feature
            for feature in pyo3.get("features", [])
            if not feature.startswith("abi3")
        ]
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    version = binding["package"]["version"].split("+", 1)[0]
    binding["package"]["version"] = f"{version}+termux.g{commit[:12]}"
    root_path.write_text(tomlkit.dumps(root))
    binding_path.write_text(tomlkit.dumps(binding))


def verify(dist: Path, work_dir: Path) -> None:
    """Reject wheels with the wrong Python ABI, Android tag, or ELF architecture."""
    info_path = work_dir / "build-info.json"
    info = json.loads(info_path.read_text())
    python_tag = "cp" + info["python_version"].replace(".", "")
    tag = f"{python_tag}-{python_tag}-android_{info['android_api_level']}_arm64_v8a"
    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected one hf-xet wheel, got {wheels}")
    wheel = wheels[0]
    if not wheel.name.startswith("hf_xet-") or not wheel.name.endswith(f"-{tag}.whl"):
        raise RuntimeError(f"Unexpected wheel name: {wheel.name}; expected {tag}")
    with zipfile.ZipFile(wheel) as archive:
        metadata = [
            name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")
        ]
        if (
            len(metadata) != 1
            or f"Tag: {tag}" not in archive.read(metadata[0]).decode().splitlines()
        ):
            raise RuntimeError("Wheel metadata does not match the target")
        extensions = [name for name in archive.namelist() if name.endswith(".so")]
        if not extensions:
            raise RuntimeError("Wheel contains no native extension")
        for name in extensions:
            data = archive.read(name)
            # ELF64, little-endian, EM_AARCH64.
            if (
                data[:6] != b"\x7fELF\x02\x01"
                or int.from_bytes(data[18:20], "little") != 183
            ):
                raise RuntimeError(f"Not an Android arm64 ELF library: {name}")
    info["wheel"] = wheel.name
    info["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    info["runtime_tested"] = False
    info_path.write_text(json.dumps(info, indent=2) + "\n")
    print(f"Verified target and wheel metadata: {wheel.name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "patch", "verify"))
    parser.add_argument("--work-dir", type=Path, default=Path("build/hf-xet-android"))
    parser.add_argument("--source", type=Path, default=Path("xet-core"))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    work_dir = args.work_dir.resolve()
    if args.command == "prepare":
        prepare(work_dir)
    elif args.command == "patch":
        patch_source(args.source.resolve())
    else:
        verify(args.dist.resolve(), work_dir)


if __name__ == "__main__":
    main()
