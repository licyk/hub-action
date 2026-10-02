"""Plan missing Android hf-xet wheels using the shared repository/version tools."""

import json
import os
import re
import tomllib
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from sd_webui_all_in_one.package_analyzer import (
    InvalidWheelFilename,
    Version,
    WheelFilename,
)
from sd_webui_all_in_one.repo_manager import RepoManager

PYTHON_VERSIONS = ["3.10", "3.11", "3.12", "3.13", "3.14"]
GITHUB_REPO = "huggingface/xet-core"


def public_version(version: str) -> Version:
    """Parse a version and drop its local segment (e.g. the legacy +termux suffix)."""
    return Version.parse(version).without_local()


def fetch(url: str) -> bytes:
    headers = {"User-Agent": "hub-action-hf-xet"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=60) as response:
        return response.read()


def latest_release() -> str:
    """Select the newest non-yanked stable hf-xet release from PyPI."""
    releases = json.loads(fetch("https://pypi.org/pypi/hf-xet/json"))["releases"]
    latest = None
    for version, files in releases.items():
        parsed = Version.parse(version)
        if parsed.is_prerelease or parsed.is_devrelease or not files:
            continue
        if all(file.get("yanked", False) for file in files):
            continue
        if latest is None or parsed > Version.parse(latest):
            latest = version
    if latest is None:
        raise RuntimeError("PyPI contains no non-yanked stable hf-xet release")
    return latest


def resolve_source(selector: str, latest: str) -> tuple[str, str]:
    """Resolve a release/ref once so every matrix job builds the same commit."""
    refs = [f"v{latest}", latest] if selector == "latest" else [selector]
    for ref in refs:
        try:
            commit = json.loads(
                fetch(
                    f"https://api.github.com/repos/{GITHUB_REPO}/commits/{quote(ref, safe='')}"
                )
            )["sha"]
            break
        except HTTPError as error:
            if error.code != 404 or ref == refs[-1]:
                raise
    else:
        raise RuntimeError("Cannot resolve hf-xet source revision")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("GitHub returned an invalid commit SHA")
    manifest = tomllib.loads(
        fetch(
            f"https://raw.githubusercontent.com/{GITHUB_REPO}/{commit}/hf_xet/Cargo.toml"
        ).decode()
    )
    version = manifest["package"]["version"]
    if selector == "latest" and public_version(version) != public_version(latest):
        raise RuntimeError(
            f"Source version {version} does not match PyPI release {latest}"
        )
    return commit, version


def plan_builds(files: list[str], version: str, force: bool = False) -> dict:
    """Compare each Python ABI independently, including partially uploaded releases."""
    # 复用 sd-webui-all-in-one 的版本比较，去除本地版本标识以兼容历史 +termux 后缀。
    # 按 ABI 分别检查：同版或更新版跳过，旧版或缺失加入矩阵；部分上传后可再次补齐。
    current: dict[str, str | None] = dict.fromkeys(PYTHON_VERSIONS)
    latest = None
    for path in files:
        if PurePosixPath(path).parts[:1] != ("hf_xet",):
            continue
        try:
            wheel = WheelFilename.parse(PurePosixPath(path).name)
        except InvalidWheelFilename:
            continue
        wheel_version = wheel.version
        if (
            wheel.name != "hf-xet"
            or wheel_version.is_prerelease
            or wheel_version.is_devrelease
        ):
            continue
        text = str(wheel_version)
        for python in PYTHON_VERSIONS:
            abi = "cp" + python.replace(".", "")
            if not any(
                tag.interpreter == abi
                and tag.abi == abi
                and re.fullmatch(r"android_\d+_arm64_v8a", tag.platform)
                and int(tag.platform.split("_")[1]) <= 24
                for tag in wheel.tags
            ):
                continue
            previous = current[python]
            if previous is None or public_version(text) > public_version(previous):
                current[python] = text
            if latest is None or public_version(text) > public_version(latest):
                latest = text
    missing = [
        python
        for python, existing in current.items()
        if force
        or existing is None
        or public_version(version) > public_version(existing)
    ]
    return {
        "repository_latest": latest,
        "repository_versions": current,
        "python_versions": missing,
        "should_build": bool(missing),
    }


def main() -> None:
    selector = os.environ.get("HF_XET_REF", "latest").strip() or "latest"
    force = os.environ.get("FORCE_BUILD", "false").lower() == "true"
    upstream = latest_release()
    manager = RepoManager(ms_token=os.environ.get("MODELSCOPE_API_TOKEN"))
    # 使用 sd-webui-all-in-one 的 RepoManager 查询目标仓库。
    # 查询失败必须报错，不能当作空仓库而触发全部重建。
    files = manager.get_repo_file(
        api_type="modelscope", repo_id="licyks/wheels", repo_type="model"
    )
    commit, version = resolve_source(selector, upstream)
    result = plan_builds(files, version, force)
    result.update(
        upstream_latest=upstream, source_version=version, source_commit=commit
    )
    print(json.dumps(result, indent=2))
    outputs = {
        "should_build": str(result["should_build"]).lower(),
        "matrix": json.dumps({"python": result["python_versions"]}),
        "source_commit": commit,
        "source_version": version,
    }
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            stream.writelines(f"{key}={value}\n" for key, value in outputs.items())
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a") as stream:
            stream.write(
                "### hf-xet version check\n\n```json\n"
                + json.dumps(result, indent=2)
                + "\n```\n"
            )


if __name__ == "__main__":
    main()
