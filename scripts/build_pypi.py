import os
import requests
import modelscope
import huggingface_hub
from typing import Union
from pathlib import Path



def get_github_release_file(repo: str, tag: str) -> list:
    url = f"https://api.github.com/repos/{repo}/releases"
    data = {
        "Accept": "application/vnd.github+json",
    }
    file_list = []

    print(f"获取 {repo} 的文件列表")
    response = requests.get(url=url, data=data)
    res = response.json()
    if response.status_code < 200 or response.status_code > 300:
        print(f"获取 {repo} 的文件列表失败")
        return file_list

    for i in res:
        if i.get("tag_name") == tag:
            for x in i.get("assets"):
                file_list.append([x.get("name"), x.get("browser_download_url")])

    return file_list


def get_huggingface_repo_file(repo_id: str, repo_type: str) -> list:
    api = huggingface_hub.HfApi()
    file_list = []
    try:
        print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
        repo_files = api.list_repo_files(
            repo_id=repo_id,
            repo_type=repo_type,
        )
    except Exception as e:
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
            raise Exception(f"错误的 HuggingFace 仓库类型: {repo_type}")

        file_list.append([i, url])

    return file_list


def get_modelscope_repo_file(repo_id: str, repo_type: str) -> list:
    api = modelscope.HubApi()
    from modelscope.hub.snapshot_download import fetch_repo_files
    from modelscope.hub.api import DEFAULT_DATASET_REVISION

    file_list = []
    file_list_url = []
    def _get_file_path(repo_files: list) -> list:
            file_list = []
            for file in repo_files:
                    if file['Type'] != 'tree':
                        file_list.append(file["Path"])
            return file_list

    if repo_type == "model":
        try:
            print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
            repo_files = api.get_model_files(
                model_id=repo_id,
                recursive=True
            )
            file_list = _get_file_path(repo_files)
        except Exception as e:
            print(f"获取 {repo_id} (类型: {repo_type}) 仓库的文件列表出现错误: {e}")
    elif repo_type == "dataset":
        user = repo_id.split("/")[0]
        name = repo_id.split("/")[1]
        try:
            print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
            repo_files = fetch_repo_files(
                _api=api,
                group_or_owner=user,
                name=name,
                revision=DEFAULT_DATASET_REVISION
            )
            file_list = _get_file_path(repo_files)
        except Exception as e:
            print(f"获取 {repo_id} (类型: {repo_type}) 仓库的文件列表出现错误: {e}")
    elif repo_type == "space":
        # TODO: 支持创空间
        print(f"{repo_id} 仓库类型为创空间, 不支持获取文件列表")
        return file_list_url
    else:
        raise Exception(f"未知的 {repo_type} 仓库类型")

    for i in file_list:
        if repo_type == "model":
            url = f"https://modelscope.cn/models/{repo_id}/resolve/master/{i}"
        elif repo_type == "dataset":
            url = f"https://modelscope.cn/datasets/{repo_id}/resolve/master/{i}"
        elif repo_type == "space":
            url = f"https://modelscope.cn/studio/{repo_id}/resolve/master/{i}"
        else:
            raise Exception(f"错误的 HuggingFace 仓库类型: {repo_type}")

        file_list_url.append([i, url])

    return file_list_url


def build_pypi_list(file_list: list) -> list:
    html_string = []

    for file, url in file_list:
        html_string.append(f"<a href=\"{url}\">")
        html_string.append(f"    {os.path.basename(file)}")
        html_string.append(f"</a><br>")

    return html_string


def write_content_to_file(content: list, path: Union[str, Path]) -> None:
    if len(content) == 0:
        return

    print(f"写入文件到 {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding = "utf8") as f:
        for item in content:
            f.write(item + "\n")


def filter_whl_file(file_list: list) -> list:
    fitter_file_list = []
    for file, url in file_list:
        if file.endswith(".whl"):
            fitter_file_list.append([file, url])

    return fitter_file_list


def main() -> None:
    gh_file = get_github_release_file(
        repo="licyk/term-sd",
        tag="wheel"
    )
    hf_file = get_huggingface_repo_file(
        repo_id="licyk/wheel",
        repo_type="model"
    )
    ms_file = get_modelscope_repo_file(
        repo_id="licyks/wheels",
        repo_type="model"
    )
    gh_file = filter_whl_file(gh_file)
    hf_file = filter_whl_file(hf_file)
    ms_file = filter_whl_file(ms_file)

    def _hf_mirror_list(file_list: list) -> list:
        hf_mirror_list = []
        for file, url in file_list:
            hf_mirror_list.append([file, url.replace("https://huggingface.co/", "https://hf-mirror.com/")])

        return hf_mirror_list
    
    hf_mirror_file = _hf_mirror_list(hf_file)

    pypi_gh_html = build_pypi_list(gh_file)
    pypi_hf_html = build_pypi_list(hf_file)
    pypi_hf_mirror_html = build_pypi_list(hf_mirror_file)
    pypi_ms_html = build_pypi_list(ms_file)

    root_path = os.environ.get("root_path", os.getcwd())

    write_content_to_file(pypi_gh_html, os.path.join(root_path, "index_gh_mirror.html"))
    write_content_to_file(pypi_hf_html, os.path.join(root_path, "index_hf.html"))
    write_content_to_file(pypi_hf_mirror_html, os.path.join(root_path, "index_hf_mirror.html"))
    write_content_to_file(pypi_ms_html, os.path.join(root_path, "index.html"))

if __name__ == "__main__":
    main()
