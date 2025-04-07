import os
import modelscope
from typing import Union
from pathlib import Path
from datetime import datetime, timezone, timedelta



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


def build_download_page_list(package_list: list) -> list:
    html_string = []
    html_string.append("<ul>")
    for p_type, pkg_list in package_list:
        html_string.append(f"<li>{replace_package_name(p_type)}</li>")
        html_string.append("<ul>")
        for p_path, url in pkg_list:
            filename = os.path.basename(p_path)
            tmp = f"""
<li><a href="{url}">
    {filename}
</a></li>
            """
            html_string.append(tmp)
        html_string.append("</ul>")
    html_string.append("</ul>")

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


def classify_package(package_list: list) -> list:
    portable_type = set()
    file_list = []

    for a, b in package_list:
        portable_type.add(os.path.basename(a).split("_licyk_")[0])

    portable_type = sorted(list(portable_type))

    for p_type in portable_type:
        tmp = []
        for a, b in package_list:
            filename = os.path.basename(a)
            if filename.startswith(f"{p_type}_licyk_"):
                tmp.append([a, b])

        file_list.append([p_type, tmp])

    return file_list


def replace_package_name(name: str) -> str:
    if name == "sd_webui":
        return "Stable Diffusion WebUI"

    if name == "sd_webui_forge":
        return "Stable Diffusion WebUI Forge"

    if name == "sd_webui_reforge":
        return "Stable Diffusion WebUI reForge"

    if name == "comfyui":
        return "ComfyUI"

    if name == "fooocus":
        return "Fooocus"

    if name == "invokeai":
        return "InvokeAI"

    if name == "sd_trainer":
        return "SD Trainer"

    if name == "kohya_gui":
        return "Kohya GUI"

    if name == "sd_scripts":
        return "SD Scripts"

    if name == "musubi_tuner":
        return "Musubi Tuner"

    return name


def main() -> None:
    ms_file = get_modelscope_repo_file(repo_id="licyks/sdnote", repo_type="model")
    ms_file = filter_portable_file(ms_file)
    stable, nightly = split_release_list(ms_file)
    stable = classify_package(stable)
    nightly = classify_package(nightly)
    html_string_stable = build_download_page_list(stable)
    html_string_nightly = build_download_page_list(nightly)

    current_time = (
        datetime.now(timezone.utc)+ timedelta(hours=8)
    ).strftime("%Y-%m-%d %H:%M:%S")

    content_s = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="shortcut icon" href="../favicon.ico" type="image/x-icon">
    <style>
        body {
            line-height: 1.5;
        }
    </style>
    <title>AI 绘画 / 训练整合包列表</title>
</head>
<body>
    <h1>AI 绘画 / 训练整合包列表</h1>
    基于 <a href="https://github.com/licyk/sd-webui-all-in-one?tab=readme-ov-file#installer">sd-webui-all-in-one/Installer</a> 全自动构建整合包
    <br>
    项目地址：<a href="https://github.com/licyk/sd-webui-all-in-one">https://github.com/licyk/sd-webui-all-in-one</a>
    <br>
    原仓库：<a href="https://huggingface.co/licyk/sdnote/tree/main/portable">HuggingFace</a> / <a href="https://modelscope.cn/models/licyks/sdnote/files">ModelScope</a>
    <br>
    <br>
    Stable 列表为稳定版本, Nightly 为测试版本, 根据需求自行下载
    <br>
    若整合包无法解压，请下载并安装 <a href="https://7-zip.org/">7-Zip</a> 后再尝试解压
    <br>
    整合包说明可阅读：<a href="https://github.com/licyk/sd-webui-all-in-one/discussions/1">AI 绘画 / 训练整合包 · licyk/sd-webui-all-in-one · Discussion #1</a>
    <br>
    <br>
    """ + f"""
    列表更新时间：{current_time}
    <br>
    ===================================================
    <h2>下载列表</h2>
    """

    content_e = """
</body>
</html>
    """

    package_list_html = (
        content_s.strip().split("\n")
        + ["<h3>Stable</h3>"]
        + html_string_stable
        + ["<h3>Nightly</h3>"]
        + html_string_nightly
        + content_e.strip().split("\n")
    )
    root_path = os.environ.get("root_path", os.getcwd())

    write_content_to_file(package_list_html, os.path.join(root_path, "index.html"))



if __name__ == "__main__":
    main()
