import os
import time
from functools import wraps
from typing import (
    Callable,
    TypeVar,
    ParamSpec,
    cast,
)
from pathlib import Path

import requests
from sd_webui_all_in_one.retry_decorator import retryable
from sd_webui_all_in_one.repo_manager import RepoManager


@retryable(
    times=3,
    delay=1.0,
    describe="获取 GitHub Release 文件列表",
    catch_exceptions=(requests.RequestException, ValueError),
    raise_exception=RuntimeError,
)
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
def get_huggingface_repo_file(repo_id: str, repo_type: str) -> list[tuple[str, str]]:
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
def get_modelscope_repo_file(repo_id: str, repo_type: str) -> list[tuple[str, str]]:
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


def build_pypi_list(file_list: list[tuple[str, str]]) -> list[str]:
    html_string: list[str] = []

    for file, url in file_list:
        html_string.append(f'<a href="{url}">')
        html_string.append(f"    {os.path.basename(file)}")
        html_string.append("</a><br>")

    return html_string


def write_content_to_file(
    content: list[str],
    path: Path,
) -> None:
    if len(content) == 0:
        return

    print(f"写入文件到 {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
        for item in content:
            f.write(item + "\n")


def filter_whl_file(
    file_list: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    fitter_file_list = []
    for file, url in file_list:
        if file.endswith(".whl"):
            fitter_file_list.append((file, url))

    return fitter_file_list


def main() -> None:
    gh_file = get_github_release_file(repo="licyk/term-sd", tag="wheel")
    hf_file = get_huggingface_repo_file(repo_id="licyk/wheel", repo_type="model")
    ms_file = get_modelscope_repo_file(repo_id="licyks/wheels", repo_type="model")
    gh_file = filter_whl_file(gh_file)
    hf_file = filter_whl_file(hf_file)
    ms_file = filter_whl_file(ms_file)

    def _hf_mirror_list(
        file_list: list[tuple[str, str]],
    ) -> list[tuple[str, str]]:
        hf_mirror_list: list[tuple[str, str]] = []
        for file, url in file_list:
            hf_mirror_list.append(
                (file, url.replace("https://huggingface.co/", "https://hf-mirror.com/"))
            )

        return hf_mirror_list

    hf_mirror_file = _hf_mirror_list(hf_file)

    pypi_gh_html = build_pypi_list(gh_file)
    pypi_hf_html = build_pypi_list(hf_file)
    pypi_hf_mirror_html = build_pypi_list(hf_mirror_file)
    pypi_ms_html = build_pypi_list(ms_file)

    root_path = Path(os.getenv("root_path", os.getcwd()))
    root_path.mkdir(parents=True, exist_ok=True)

    write_content_to_file(pypi_gh_html, root_path / "index_gh_mirror.html")
    write_content_to_file(pypi_hf_html, root_path / "index_hf.html")
    write_content_to_file(pypi_hf_mirror_html, root_path / "index_hf_mirror.html")
    write_content_to_file(pypi_ms_html, root_path / "index.html")


if __name__ == "__main__":
    main()
