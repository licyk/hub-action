import os
from pathlib import Path

import requests
from sd_webui_all_in_one.package_analyzer import (
    normalize_package_name,
    parse_wheel_filename,
)
from sd_webui_all_in_one.retry_decorator import retryable
from sd_webui_all_in_one.repo_manager import RepoManager



@retryable(
    times=3,
    delay=1.0,
    describe="获取 GitHub Release 文件列表",
    catch_exceptions=(requests.RequestException, ValueError),
    raise_exception=RuntimeError,
)
def get_github_release_file(
    repo: str,
    tag: str,
) -> list[tuple[str, str]]:
    url = f"https://api.github.com/repos/{repo}/releases"
    data = {
        "Accept": "application/vnd.github+json",
    }
    file_list = []

    print(f"获取 {repo} 的文件列表")
    response = requests.get(url=url, data=data, timeout=30)
    res = response.json()
    if response.status_code < 200 or response.status_code > 300:
        error_msg = f"获取 {repo} 的文件列表失败，状态码: {response.status_code}"
        print(error_msg)
        raise RuntimeError(error_msg)

    for i in res:
        if i.get("tag_name") == tag:
            for x in i.get("assets"):
                file_list.append((x.get("name"), x.get("browser_download_url")))

    return file_list


@retryable(
    times=3,
    delay=1.0,
    describe="获取 HuggingFace 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_huggingface_repo_file(
    repo_id: str,
    repo_type: str,
) -> list[tuple[str, str]]:
    repo_manager = RepoManager()
    repo_files = repo_manager.get_repo_file(
        api_type="huggingface",
        repo_id=repo_id,
        repo_type=repo_type,
    )
    return [
        (
            file_path,
            repo_manager.get_repo_file_download_url(
                api_type="huggingface",
                repo_id=repo_id,
                file_path=file_path,
                repo_type=repo_type,
            ),
        )
        for file_path in repo_files
    ]


@retryable(
    times=3,
    delay=1.0,
    describe="获取 ModelScope 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_modelscope_repo_file(
    repo_id: str,
    repo_type: str,
) -> list[tuple[str, str]]:
    repo_manager = RepoManager()
    repo_files = repo_manager.get_repo_file(
        api_type="modelscope",
        repo_id=repo_id,
        repo_type=repo_type,
    )
    return [
        (
            file_path,
            repo_manager.get_repo_file_download_url(
                api_type="modelscope",
                repo_id=repo_id,
                file_path=file_path,
                repo_type=repo_type,
            ),
        )
        for file_path in repo_files
    ]


def filter_whl_file(
    file_list: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    fitter_file_list = []
    for file, url in file_list:
        if file.endswith(".whl"):
            fitter_file_list.append((file, url))

    return fitter_file_list


def group_files_by_package(
    file_list: list[tuple[str, str]],
) -> dict[str, list[tuple[str, str]]]:
    """
    将文件列表按包名分组
    返回: {normalized_package_name: [(filename, url), ...]}
    """
    packages: dict[str, list[tuple[str, str]]] = {}

    for file_path, url in file_list:
        filename = os.path.basename(file_path)

        try:
            package_name = parse_wheel_filename(filename)
            normalized_name = normalize_package_name(package_name)
            packages.setdefault(normalized_name, []).append((filename, url))
        except ValueError as e:
            print(f"跳过无效的 wheel 文件: {filename} - {e}")

    return packages


def generate_package_index_html(
    packages: dict[str, list[tuple[str, str]]],
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
    package_name: str, files: list[tuple[str, str]]
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

    for filename, url in sorted_files:
        # 根据 PEP 503，每个链接应该包含文件名
        html_parts.append(f'    <a href="{url}">{filename}</a><br/>')

    html_parts.extend(
        [
            "</body>",
            "</html>",
        ]
    )

    return "\n".join(html_parts)


def build_pypi_index(
    file_list: list[tuple[str, str]],
    output_dir: Path,
) -> None:
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
        hf_mirror_list: list[tuple[str, str]] = []
        for file, url in file_list:
            hf_mirror_list.append(
                (file, url.replace("https://huggingface.co/", "https://hf-mirror.com/"))
            )

        return hf_mirror_list

    hf_mirror_file = _hf_mirror_list(hf_file)

    print("获取文件列表完成, 生成包索引中")
    build_pypi_index(gh_file, root_path / "pypi_gh")
    build_pypi_index(hf_file, root_path / "pypi_hf")
    build_pypi_index(ms_file, root_path / "pypi")
    build_pypi_index(hf_mirror_file, root_path / "pypi_hf_mirror")


if __name__ == "__main__":
    main()
