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
            if file["Type"] != "tree":
                file_list.append(file["Path"])
        return file_list

    if repo_type == "model":
        try:
            print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
            repo_files = api.get_model_files(model_id=repo_id, recursive=True)
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
                revision=DEFAULT_DATASET_REVISION,
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


def build_download_page(filename: str, url: str) -> str:
    html_string = f"""
<!DOCTYPE html>
<html>

<head>
	<link rel="shortcut icon" href="../../favicon.ico" type="image/x-icon">
	<script language="javascript"> location.replace("{url}") </script>
	<meta name="viewport"
		content="width=device-width,initial-scale=1.0,maximum-scale=1.0,minimum-scale=1.0,user-scalable=no">
	<meta charset="utf-8">
</head>

<title>正在跳转到 {filename} 下载链接中...</title>

<body>
	<p style="font-family:arial;color:black;font-size:30px;"></p>若未自动跳转到下载链接请点击
	<a href="{url}">{filename} 手动下载</a></p>
</body>

</html>
""".strip()

    return html_string


def write_content_to_file(content: list, path: Union[str, Path]) -> None:
    if len(content) == 0:
        return

    dir_path = os.path.dirname(path)
    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)

    print(f"写入文件到 {path}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as f:
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


def find_latest_package(package_list: list) -> list:
    portable_type = set()
    file_list = []

    for a, b in package_list:
        portable_type.add(os.path.basename(a).split("_licyk_")[0])

    for p_type in list(portable_type):
        tmp = []
        for a, b in package_list:
            filename = os.path.basename(a)
            if filename.startswith(f"{p_type}_licyk_"):
                tmp.append([a, b])

        max_version = -1
        for a, b in tmp:
            filename = os.path.basename(a)
            ver = int(filename.split(f"{p_type}_licyk_").pop().split("_nightly")[0])
            if max_version < ver:
                max_version = ver

        package_name = f"{p_type}_licyk_{max_version}_nightly.7z"

        for a, b in tmp:
            if package_name in a:
                file_list.append([p_type, a, b])

    return file_list


def main() -> None:
    ms_file = get_modelscope_repo_file(repo_id="licyks/sdnote", repo_type="model")
    ms_file = filter_portable_file(ms_file)
    _, nightly = split_release_list(ms_file)
    latest = find_latest_package(nightly)
    root_path = os.environ.get("root_path", os.getcwd())

    for portable_type, file_path, url in latest:
        filename = os.path.basename(file_path)
        html_string = [build_download_page(filename, url)]
        write_content_to_file(html_string, os.path.join(root_path, portable_type, "index.html"))


if __name__ == "__main__":
    main()
