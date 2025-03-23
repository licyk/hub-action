import os
import modelscope
from typing import Union
from pathlib import Path



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


def build_download_page_list(file_list: list) -> list:
    html_string = []

    if not file_list:
        return ["<ul><li>无</li></ul>"]

    html_string.append("<ul>")
    for file, url in file_list:
        html_string.append(f"<li><a href=\"{url}\">")
        html_string.append(f"    {os.path.basename(file)}")
        html_string.append(f"</a></li>")

    html_string.append("</ul>")

    return html_string


def write_content_to_file(content: list, path: Union[str, Path]) -> None:
    if len(content) == 0:
        return

    print(f"写入文件到 {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding = "utf8") as f:
        for item in content:
            f.write(item + "\n")


def filter_portable_file(file_list: list) -> list:
    fitter_file_list = []
    for file, url in file_list:
        if file.startswith("portable/") and file.endswith(".7z"):
            fitter_file_list.append([file, url])

    return fitter_file_list


def split_release_list(file_list: list) -> Union[list, list]:
    stable_list = []
    nightly_list = []
    for file, url in file_list:
        if file.startswith("portable/stable"):
            stable_list.append([file, url])

    for file, url in file_list:
        if file.startswith("portable/nightly"):
            nightly_list.append([file, url])

    return stable_list, nightly_list


def main() -> None:
    ms_file = get_modelscope_repo_file(
        repo_id="licyks/sdnote",
        repo_type="model"
    )
    ms_file = filter_portable_file(ms_file)

    stable, nightly = split_release_list(ms_file)


    content_s = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>下载列表</title>
</head>
<body>
    <h1>AI 绘画 / 训练整合包列表</h1>
    基于 sd-webui-all-in-one/Installer 全自动构建整合包
    <br>
    项目地址：https://github.com/licyk/sd-webui-all-in-one
    <br>
    原仓库：<a href="https://huggingface.co/licyk/sdnote/tree/main/portable">HuggingFace</a> / <a href="https://modelscope.cn/models/licyks/sdnote/files">ModelScope</a>
    <br>
    <br>
    Stable 列表为稳定版本, Nightly 为测试版本, 根据需求自行下载
    <br>
    <br>
    <br>
    """

    content_e = """
</body>
</html>
    """

    content_s = content_s.strip().split("\n")
    content_e = content_e.strip().split("\n")

    pypi_hf_html_s = build_download_page_list(stable)
    pypi_hf_html_e = build_download_page_list(nightly)
    html_str = content_s + ["<h2>Stable</h2>"] + pypi_hf_html_s + ["<h2>Nightly</h2>"] + pypi_hf_html_e + content_e

    root_path = os.environ.get("root_path", os.getcwd())
    write_content_to_file(html_str, os.path.join(root_path, "index.html"))



if __name__ == "__main__":
    main()
