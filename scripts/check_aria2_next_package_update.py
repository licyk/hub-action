#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from packaging.version import Version


UPSTREAM_REPO = "AnInsomniacy/aria2-next"
PYPI_PROJECT = "aria2-next"
USER_AGENT = "hub-action"

TARGET_SUFFIXES = [
    "linux-x86_64",
    "linux-aarch64",
    "macos-x86_64",
    "macos-arm64",
    "windows-x86_64.exe",
    "windows-arm64.exe",
    "android-arm64",
]

WHEEL_NAMES = [
    "aria2_next-{version}-py3-none-manylinux_2_28_x86_64.whl",
    "aria2_next-{version}-py3-none-manylinux_2_28_aarch64.whl",
    "aria2_next-{version}-py3-none-macosx_10_13_x86_64.whl",
    "aria2_next-{version}-py3-none-macosx_11_0_arm64.whl",
    "aria2_next-{version}-py3-none-win_amd64.whl",
    "aria2_next-{version}-py3-none-win_arm64.whl",
    "aria2_next-{version}-py3-none-android_21_arm64_v8a.whl"
]


@dataclass(frozen=True)
class CheckResult:
    release: str
    version: str
    pypi_latest: str | None
    assets_ready: bool
    should_trigger: bool
    targets_without_digest: list[str]
    missing_assets: list[str]
    missing_wheels: list[str]
    reason: str


def get_json(url: str, accept: str = "application/json") -> dict:
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"

    with urlopen(Request(url, headers=headers), timeout=60) as response:
        return json.load(response)


def normalize_package_version(tag: str) -> str:
    text = tag[1:] if tag.startswith(("v", "V")) else tag
    return str(Version(text))


def release_asset_version(tag: str) -> str:
    return tag[1:] if tag.startswith(("v", "V")) else tag


def has_sha256_digest(asset: dict) -> bool:
    digest = asset.get("digest")
    return isinstance(digest, str) and digest.startswith("sha256:")


def fetch_release(selector: str) -> dict:
    base = f"https://api.github.com/repos/{UPSTREAM_REPO}"
    if not selector or selector == "latest":
        return get_json(f"{base}/releases/latest", "application/vnd.github+json")

    try:
        return get_json(f"{base}/releases/tags/{selector}", "application/vnd.github+json")
    except HTTPError as exc:
        if exc.code != 404 or selector.startswith(("v", "V")):
            raise

    return get_json(f"{base}/releases/tags/v{selector}", "application/vnd.github+json")


def expected_target_asset_names(asset_version: str) -> list[str]:
    return [f"aria2-next-{asset_version}-{suffix}" for suffix in TARGET_SUFFIXES]


def missing_release_assets(release: dict, asset_version: str) -> tuple[list[str], list[str]]:
    assets_by_name = {
        asset.get("name"): asset
        for asset in release.get("assets", [])
        if asset.get("name")
    }
    target_asset_names = expected_target_asset_names(asset_version)
    missing_assets = [name for name in target_asset_names if name not in assets_by_name]

    targets_without_digest = [
        name
        for name in target_asset_names
        if name in assets_by_name and not has_sha256_digest(assets_by_name[name])
    ]
    checksum_asset = f"aria2-next-{asset_version}-checksums.sha256"
    if targets_without_digest and checksum_asset not in assets_by_name:
        missing_assets.append(checksum_asset)

    return missing_assets, targets_without_digest


def fetch_pypi_releases() -> tuple[dict, str | None]:
    try:
        pypi = get_json(f"https://pypi.org/pypi/{PYPI_PROJECT}/json")
    except HTTPError as exc:
        if exc.code != 404:
            raise
        return {}, None

    return pypi.get("releases", {}), pypi.get("info", {}).get("version")


def missing_pypi_wheels(pypi_releases: dict, version: str) -> list[str]:
    expected_wheels = {template.format(version=version) for template in WHEEL_NAMES}
    pypi_files = {
        file.get("filename")
        for file in pypi_releases.get(version, [])
        if file.get("filename")
    }
    return sorted(expected_wheels - pypi_files)


def check_update(release_selector: str, force: bool) -> CheckResult:
    release = fetch_release(release_selector)
    release_tag = release["tag_name"]
    package_version = normalize_package_version(release_tag)
    asset_version = release_asset_version(release_tag)

    missing_assets, targets_without_digest = missing_release_assets(release, asset_version)
    assets_ready = not missing_assets

    pypi_releases, pypi_latest = fetch_pypi_releases()
    missing_wheels = missing_pypi_wheels(pypi_releases, package_version)
    pypi_has_version = package_version in pypi_releases and not missing_wheels

    should_trigger = assets_ready and (force or not pypi_has_version)
    if not assets_ready:
        reason = "upstream release assets are incomplete"
    elif pypi_has_version and not force:
        reason = "PyPI already has all expected wheels"
    elif force:
        reason = "forced by workflow input"
    else:
        reason = "PyPI is missing this version or some wheels"

    return CheckResult(
        release=release_tag,
        version=package_version,
        pypi_latest=pypi_latest,
        assets_ready=assets_ready,
        should_trigger=should_trigger,
        targets_without_digest=targets_without_digest,
        missing_assets=missing_assets,
        missing_wheels=missing_wheels,
        reason=reason,
    )


def write_github_outputs(result: CheckResult) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    with open(output_path, "a", encoding="utf-8") as output:
        print(f"release={result.release}", file=output)
        print(f"version={result.version}", file=output)
        print(f"assets_ready={str(result.assets_ready).lower()}", file=output)
        print(f"should_trigger={str(result.should_trigger).lower()}", file=output)
        print(f"missing_assets={','.join(result.missing_assets)}", file=output)
        print(f"missing_wheels={','.join(result.missing_wheels)}", file=output)
        print(f"reason={result.reason}", file=output)


def print_summary(result: CheckResult) -> None:
    print(f"release={result.release}")
    print(f"version={result.version}")
    print(f"pypi_latest={result.pypi_latest}")
    print(f"assets_ready={result.assets_ready}")
    print(f"targets_without_digest={result.targets_without_digest}")
    print(f"missing_assets={result.missing_assets}")
    print(f"missing_wheels={result.missing_wheels}")
    print(f"should_trigger={result.should_trigger}")
    print(f"reason={result.reason}")


def main() -> int:
    release_selector = os.environ.get("RELEASE", "latest")
    force = os.environ.get("FORCE", "false").lower() == "true"
    result = check_update(release_selector, force)
    write_github_outputs(result)
    print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
