"""Prepare and verify hf-xet wheels for Termux arm64 Python 3.10 through 3.14."""

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
TUR_REPO = "https://tur.kcubeterm.com/"
# 旧版开发包来源：https://github.com/termux-user-repository/tur
TERMUX_PREFIX = "data/data/com.termux/files/usr"
PYTHON_VERSIONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
# Python 3.12 is no longer in the rolling Termux/TUR indexes. Keep this
# community build immutable, including the digest published by GitHub.
# 固定的 Python 3.12.14 社区包：
# https://github.com/adybag14-cyber/termux-python/releases/tag/termux-aarch64-20260914.47.1
PYTHON_312_URL = (
    "https://github.com/adybag14-cyber/termux-python/releases/download/"
    "termux-aarch64-20260914.47.1/python3.12_3.12.14_aarch64.deb"
)
PYTHON_312_SHA256 = "f4def29d5b581c18c0585158b5fafd398db9459ef241bb6ea9e1ee12adbb2654"


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


def python_package(index: str, version: str) -> dict[str, str] | None:
    """Find a matching Python minor in an arm64 Debian Packages index."""
    for paragraph in index.split("\n\n"):
        fields = dict(
            line.split(": ", 1)
            for line in paragraph.splitlines()
            if ": " in line and not line.startswith(" ")
        )
        if (
            fields.get("Package") in ("python", f"python{version}")
            and fields.get("Architecture") == "aarch64"
            and fields.get("Version", "").startswith(version + ".")
        ):
            return fields
    return None


def select_package(work_dir: Path, version: str) -> tuple[str, dict[str, str]]:
    """Prefer official Termux, then TUR; use the pinned community 3.12 package."""
    if version == "3.12":
        return PYTHON_312_URL, {"Version": "3.12.14", "SHA256": PYTHON_312_SHA256}
    repositories = (
        (TERMUX_REPO, "dists/stable/main/binary-aarch64/Packages.gz"),
        (TUR_REPO, "dists/tur-packages/tur/binary-aarch64/Packages.gz"),
    )
    for number, (repo, index) in enumerate(repositories):
        index_path = work_dir / f"Packages-{number}.gz"
        download(repo + index, index_path)
        package = python_package(
            gzip.decompress(index_path.read_bytes()).decode(), version
        )
        if package is None:
            continue
        filename = package["Filename"]
        if not filename.startswith("pool/") or ".." in Path(filename).parts:
            raise RuntimeError(f"Unexpected Termux package path: {filename}")
        return repo + filename, package
    raise RuntimeError(f"No Termux/TUR arm64 Python {version} package found")


def read_sysconfig(path: Path) -> dict:
    """Read target Python configuration as data without executing target code."""
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "build_time_vars"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise RuntimeError(f"Missing build_time_vars in {path}")


def prepare(work_dir: Path, python_version: str) -> None:
    """Fetch verified Termux Python libraries and configure PyO3 for Android."""
    # 开发包优先选 Termux 官方仓库，旧版使用 TUR，3.12 使用固定社区包。
    # 校验 SHA256、实际 Python 版本、Android API 和 arm64 ELF 后交给 Android NDK 编译。
    work_dir.mkdir(parents=True, exist_ok=True)
    url, package = select_package(work_dir, python_version)
    deb = work_dir / "python.deb"
    download(url, deb)
    digest = hashlib.sha256(deb.read_bytes()).hexdigest()
    if digest != package["SHA256"]:
        raise RuntimeError("Termux Python package SHA256 mismatch")
    root = work_dir / f"sysroot-{python_version}"
    subprocess.run(["dpkg-deb", "--extract", str(deb), str(root)], check=True)
    lib = root / TERMUX_PREFIX / "lib"
    configs = list(lib.glob(f"python{python_version}/_sysconfigdata*.py"))
    if len(configs) != 1:
        raise RuntimeError(f"Expected one target Python configuration, got {configs}")
    config = read_sysconfig(configs[0])
    version = config["VERSION"]
    api = config["ANDROID_API_LEVEL"]
    if (
        not re.fullmatch(r"3\.\d+", version)
        or version != python_version
        # Older Termux Pythons leave MULTIARCH empty (e.g. 3.10).
        or config["MULTIARCH"] not in ("", "aarch64-linux-android")
        or config.get("Py_GIL_DISABLED", 0)
        or not isinstance(api, int)
        or api < 24
    ):
        raise RuntimeError("Unsupported Termux Python configuration")
    library = f"python{version}"
    if not (lib / f"lib{library}.so").is_file():
        raise RuntimeError(f"Missing lib{library}.so")
    header = (lib / f"lib{library}.so").read_bytes()[:20]
    if (
        header[:6] != b"\x7fELF\x02\x01"
        or int.from_bytes(header[18:20], "little") != 183
    ):
        raise RuntimeError("Target libpython is not an arm64 ELF library")
    pyo3_config = work_dir / "pyo3-config.txt"
    pyo3_config.write_text(
        f"implementation=CPython\nversion={version}\nshared=true\nabi3=false\n"
        f"lib_name={library}\nlib_dir={lib}\npointer_width=64\n"
    )
    metadata = {
        "python_version": version,
        "termux_package_version": package["Version"],
        "termux_package_url": url,
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


def patch_source(source: Path, expected_version: str | None = None) -> None:
    """Select OpenSSL and a version-specific Python ABI in the build checkout."""
    # 关闭默认 Rustls 后端，由工作流启用静态 OpenSSL，并指定 Termux 证书目录，
    # 避免依赖 Android Java TLS 初始化。保留上游版本号，不添加本地版本后缀；
    # 源码 commit 由工作流记录在 toolchain.txt 中。
    root_path = source / "Cargo.toml"
    binding_path = source / "hf_xet/Cargo.toml"
    root = cast(dict[str, Any], tomlkit.parse(root_path.read_text()))
    binding = cast(dict[str, Any], tomlkit.parse(binding_path.read_text()))
    if (
        expected_version
        and binding["package"]["version"].split("+", 1)[0]
        != expected_version.split("+", 1)[0]
    ):
        raise RuntimeError(
            "Checked-out source version differs from the version check job"
        )
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
    root_path.write_text(tomlkit.dumps(root))
    binding_path.write_text(tomlkit.dumps(binding))


def verify(dist: Path, work_dir: Path) -> None:
    """Reject wheels with the wrong Python ABI, Android tag, or ELF architecture."""
    # 目标仅为 Termux / Android arm64，产物绑定所选 Python 小版本，
    # 不保证兼容其他小版本或 APK 内嵌 Python；manylinux wheel 不能替代 Android wheel，
    # 也不要通过改文件名绕过 pip 的兼容性检查。
    # 在 Termux 中安装与当前解释器匹配的一个 wheel：
    #   pkg update
    #   pkg install python python-pip ca-certificates
    #   python --version
    #   python -m pip install ./hf_xet-*.whl
    #   python -c "import hf_xet; print(hf_xet.__file__)"
    # 使用其他小版本时改用对应解释器（如 python3.10 -m pip），不要安装全部五种 wheel。
    # 此处只校验 wheel 标签和 ELF 架构，不执行手机运行测试；首次使用还需验证实际
    # Xet 下载。若未正确识别证书路径，可设置 SSL_CERT_FILE="$PREFIX/etc/tls/cert.pem"。
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
    parser.add_argument("--python-version", choices=PYTHON_VERSIONS, default="3.14")
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    work_dir = args.work_dir.resolve()
    if args.command == "prepare":
        prepare(work_dir, args.python_version)
    elif args.command == "patch":
        patch_source(args.source.resolve(), args.expected_version)
    else:
        verify(args.dist.resolve(), work_dir)


if __name__ == "__main__":
    main()
