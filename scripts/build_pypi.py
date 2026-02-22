import os
import re
from typing import Any
from pathlib import Path

import requests
from modelscope.hub.api import HubApi
from huggingface_hub import HfApi


def get_github_release_file(repo: str, tag: str) -> list[tuple[str, str]]:
    url = f"https://api.github.com/repos/{repo}/releases"
    data = {
        "Accept": "application/vnd.github+json",
    }
    file_list = []

    print(f"获取 {repo} 的文件列表")
    response = requests.get(url=url, data=data, timeout=30)
    res = response.json()
    if response.status_code < 200 or response.status_code > 300:
        print(f"获取 {repo} 的文件列表失败")
        return file_list

    for i in res:
        if i.get("tag_name") == tag:
            for x in i.get("assets"):
                file_list.append((x.get("name"), x.get("browser_download_url")))

    return file_list


def get_huggingface_repo_file(repo_id: str, repo_type: str) -> list[tuple[str, str]]:
    api = HfApi()
    file_list = []
    try:
        print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
        repo_files = api.list_repo_files(
            repo_id=repo_id,
            repo_type=repo_type,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表出现错误: {e}")
        return file_list

    for i in repo_files:
        if repo_type == "model":
            url = f"https://huggingface.co/{repo_id}/resolve/main/{i}"
        elif repo_type == "dataset":
            url = f"https://huggingface.co/datasets/{repo_id}/resolve/main/{i}"
        elif repo_type == "space":
            url = f"https://huggingface.co/spaces/{repo_id}/resolve/main/{i}"
        else:
            raise ValueError(f"错误的 HuggingFace 仓库类型: {repo_type}")

        file_list.append((i, url))

    return file_list


def get_modelscope_repo_file(repo_id: str, repo_type: str) -> list[tuple[str, str]]:
    api = HubApi()

    file_list = []
    file_list_url = []

    def _get_file_path(repo_files: list[str]) -> list[str]:
        file_list = []
        for file in repo_files:
            if file["Type"] != "tree":
                file_list.append(file["Path"])
        return file_list

    if repo_type == "model":
        try:
            print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
            repo_files = api.get_model_files(model_id=repo_id, recursive=True)
            file_list = _get_file_path(repo_files)
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"获取 {repo_id} (类型: {repo_type}) 仓库的文件列表出现错误: {e}")
    elif repo_type == "dataset":
        try:
            print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
            repo_files = api.get_dataset_files(repo_id=repo_id, recursive=True)
            file_list = _get_file_path(repo_files)
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"获取 {repo_id} (类型: {repo_type}) 仓库的文件列表出现错误: {e}")
    elif repo_type == "space":
        print(f"{repo_id} 仓库类型为创空间, 不支持获取文件列表")
        return file_list_url
    else:
        raise ValueError(f"未知的 {repo_type} 仓库类型")

    for i in file_list:
        if repo_type == "model":
            url = f"https://modelscope.cn/models/{repo_id}/resolve/master/{i}"
        elif repo_type == "dataset":
            url = f"https://modelscope.cn/datasets/{repo_id}/resolve/master/{i}"
        elif repo_type == "space":
            url = f"https://modelscope.cn/studio/{repo_id}/resolve/master/{i}"
        else:
            raise ValueError(f"错误的 HuggingFace 仓库类型: {repo_type}")

        file_list_url.append((i, url))

    return file_list_url


def filter_whl_file(file_list: list[tuple[str, str]]) -> list[tuple[str, str]]:
    fitter_file_list = []
    for file, url in file_list:
        if file.endswith(".whl"):
            fitter_file_list.append((file, url))

    return fitter_file_list


def normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# PEP 491 Wheel 文件名格式正则表达式
WHEEL_PATTERN = r"""
    ^                           # 字符串开始
    (?P<distribution>[^-]+)     # 包名 (匹配第一个非连字符段)
    -                           # 分隔符
    (?:                         # 版本号和可选构建号组合
        (?P<version>[^-]+)      # 版本号 (至少一个非连字符段)
        (?:-(?P<build>\d\w*))?  # 可选构建号 (以数字开头)
    )
    -                           # 分隔符
    (?P<python>[^-]+)           # Python 版本标签
    -                           # 分隔符
    (?P<abi>[^-]+)              # ABI 标签
    -                           # 分隔符
    (?P<platform>[^-]+)         # 平台标签
    \.whl$                      # 固定后缀
"""


def parse_wheel_filename(filename: str) -> dict[str, Any]:
    """解析 Python wheel 文件名并返回详细信息

    根据 PEP 491 规范解析 wheel 文件名
    格式: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl

    Args:
        filename (str): wheel 文件名, 例如 pydantic-1.10.15-py3-none-any.whl

    Returns:
        dict: 包含以下键的字典:
            - distribution: 包名 (str)
            - version: 版本号 (str)
            - build: 构建号 (str | None)
            - python: Python 版本标签 (str)
            - abi: ABI 标签 (str)
            - platform: 平台标签 (str)
            - filename: 原始文件名 (str)

    Raises:
        ValueError: 如果文件名不符合 PEP 491 规范

    Examples:
        >>> parse_wheel_filename("pydantic-1.10.15-py3-none-any.whl")
        {
            'distribution': 'pydantic',
            'version': '1.10.15',
            'build': None,
            'python': 'py3',
            'abi': 'none',
            'platform': 'any',
            'filename': 'pydantic-1.10.15-py3-none-any.whl'
        }

        >>> parse_wheel_filename("numpy-1.24.0-1-cp311-cp311-win_amd64.whl")
        {
            'distribution': 'numpy',
            'version': '1.24.0',
            'build': '1',
            'python': 'cp311',
            'abi': 'cp311',
            'platform': 'win_amd64',
            'filename': 'numpy-1.24.0-1-cp311-cp311-win_amd64.whl'
        }
    """
    match = re.fullmatch(WHEEL_PATTERN, filename, re.VERBOSE)
    if not match:
        print(f"警告: 未知的 Wheel 文件名: {filename}")
        raise ValueError(f"未知的 Wheel 文件名: {filename}")

    return {
        "distribution": match.group("distribution"),
        "version": match.group("version"),
        "build": match.group("build"),  # 可能为 None
        "python": match.group("python"),
        "abi": match.group("abi"),
        "platform": match.group("platform"),
        "filename": filename,
    }


def group_files_by_package(
    file_list: list[tuple[str, str]],
) -> dict[str, list[tuple[str, str, dict]]]:
    """
    将文件列表按包名分组
    返回: {normalized_package_name: [(filename, url, metadata), ...]}
    """
    packages = {}

    for file_path, url in file_list:
        # 提取文件名（去除路径）
        filename = os.path.basename(file_path)

        try:
            # 解析 wheel 文件名
            metadata = parse_wheel_filename(filename)
            package_name = metadata[
                "distribution"
            ]  # 使用 'distribution' 而不是 'package_name'
            normalized_name = normalize_package_name(package_name)

            if normalized_name not in packages:
                packages[normalized_name] = []

            packages[normalized_name].append((filename, url, metadata))
        except ValueError as e:
            # 如果解析失败，跳过该文件
            print(f"跳过无效的 wheel 文件: {filename} - {e}")
            continue

    return packages


def generate_package_index_html(
    packages: dict[str, list[tuple[str, str, dict]]],
) -> str:
    """
    生成 PyPI 简单索引的主页面 HTML
    根据 PEP 503 规范
    """
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '    <meta name="pypi:repository-version" content="1.0">',
        "    <title>Simple Index</title>",
        "</head>",
        "<body>",
        "    <h1>Simple Index</h1>",
    ]

    # 按字母顺序排序包名
    sorted_packages = sorted(packages.keys())

    for package_name in sorted_packages:
        # 根据 PEP 503，链接应该指向包名目录
        html_parts.append(f'    <a href="{package_name}/">{package_name}</a><br/>')

    html_parts.extend(
        [
            "</body>",
            "</html>",
        ]
    )

    return "\n".join(html_parts)


def generate_package_detail_html(
    package_name: str, files: list[tuple[str, str, dict]]
) -> str:
    """
    生成单个包的详情页面 HTML
    根据 PEP 503 规范
    """
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '    <meta name="pypi:repository-version" content="1.0">',
        f"    <title>Links for {package_name}</title>",
        "</head>",
        "<body>",
        f"    <h1>Links for {package_name}</h1>",
    ]

    # 按文件名排序
    sorted_files = sorted(files, key=lambda x: x[0])

    for filename, url, _ in sorted_files:
        # 根据 PEP 503，每个链接应该包含文件名
        html_parts.append(f'    <a href="{url}">{filename}</a><br/>')

    html_parts.extend(
        [
            "</body>",
            "</html>",
        ]
    )

    return "\n".join(html_parts)


def build_pypi_index(file_list: list[tuple[str, str]], output_dir: Path) -> None:
    """
    根据 PEP 503 规范构建 PyPI 简单索引

    参数:
        file_list: 文件列表，格式为 [(文件路径, URL), ...]
        output_dir: 输出目录路径
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 按包名分组文件
    packages = group_files_by_package(file_list)

    print(f"找到 {len(packages)} 个包")

    # 生成主索引页面
    index_html = generate_package_index_html(packages)
    index_file = output_dir / "index.html"
    index_file.write_text(index_html, encoding="utf-8")
    print(f"生成主索引页面: {index_file}")

    # 为每个包生成详情页面
    for package_name, files in packages.items():
        package_dir = output_dir / package_name
        package_dir.mkdir(parents=True, exist_ok=True)

        detail_html = generate_package_detail_html(package_name, files)
        detail_file = package_dir / "index.html"
        detail_file.write_text(detail_html, encoding="utf-8")
        print(f"生成包详情页面: {detail_file} (包含 {len(files)} 个文件)")

    print(f"\nPyPI 索引生成完成, 输出目录: {output_dir}")


def main() -> None:
    root_path = Path(os.getenv("root_path", os.getcwd())).absolute()
    print(f"根目录: {root_path}")

    gh_file = get_github_release_file(repo="licyk/term-sd", tag="wheel")
    hf_file = get_huggingface_repo_file(repo_id="licyk/wheel", repo_type="model")
    ms_file = get_modelscope_repo_file(repo_id="licyks/wheels", repo_type="model")
    gh_file = filter_whl_file(gh_file)
    hf_file = filter_whl_file(hf_file)
    ms_file = filter_whl_file(ms_file)

    def _hf_mirror_list(file_list: list[tuple[str, str]]) -> list[tuple[str, str]]:
        hf_mirror_list = []
        for file, url in file_list:
            hf_mirror_list.append(
                [file, url.replace("https://huggingface.co/", "https://hf-mirror.com/")]
            )

        return hf_mirror_list

    hf_mirror_file = _hf_mirror_list(hf_file)

    build_pypi_index(gh_file, root_path / "pypi_gh")
    build_pypi_index(hf_file, root_path / "pypi_hf")
    build_pypi_index(ms_file, root_path / "pypi")
    build_pypi_index(hf_mirror_file, root_path / "pypi_hf_mirror")


if __name__ == "__main__":
    main()
