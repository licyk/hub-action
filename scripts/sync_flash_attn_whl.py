import os
import shutil
import requests
from enum import Enum
from tempfile import TemporaryDirectory
from typing import Literal, TypeAlias
from pathlib import Path

from sd_webui_all_in_one.retry_decorator import retryable
from sd_webui_all_in_one.repo_manager import RepoManager
from sd_webui_all_in_one.downloader import download_file


RepoType: TypeAlias = Literal["model", "dataset", "space"]


class ListType(int, Enum):
    single = 1
    multiple = 2


@retryable(
    times=3,
    delay=1.0,
    describe="获取 GitHub Release 文件列表",
    catch_exceptions=(requests.RequestException, ValueError),
    raise_exception=RuntimeError,
)
def get_github_release_file(repo: str) -> list:
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
        for x in i.get("assets"):
            file_list.append([x.get("name"), x.get("browser_download_url")])

    return file_list


def filter_whl_file(file_list: list, list_type: ListType) -> list:
    fitter_file_list = []

    if len(file_list) == 0:
        return fitter_file_list

    if list_type == ListType.multiple:
        for file, url in file_list:
            if file.endswith(".whl"):
                fitter_file_list.append([file, url])
    elif list_type == ListType.single:
        for file in file_list:
            if file.endswith(".whl"):
                fitter_file_list.append(file)
    else:
        print(f"未知的列表类型: {list_type}")

    return fitter_file_list


def fitter_flash_attn_whl(file_list: list, prefix: str, list_type: ListType) -> list:
    fitter_file_list = []
    if len(file_list) == 0:
        return fitter_file_list

    if list_type == ListType.multiple:
        for file, url in file_list:
            if file.startswith(prefix):
                fitter_file_list.append([file, url])
    elif list_type == ListType.single:
        for file in file_list:
            if file.startswith(prefix):
                fitter_file_list.append(file)
    else:
        print(f"未知的列表类型: {list_type}")

    return fitter_file_list


def create_download_task(
    github_file_list: list, hf_file_list: list, ms_file_list: list, prefix: str
) -> list:
    tasks = []
    for file, url in github_file_list:
        file_in_repo = f"{prefix}/{file}"
        in_hf = True
        in_ms = True
        if file_in_repo not in hf_file_list:
            # in_hf = False
            pass  # 不再同步文件到 HuggingFace
        if file_in_repo not in ms_file_list:
            in_ms = False
        if not in_hf or not in_ms:
            tasks.append([file, url, in_hf, in_ms])

    return tasks


def sync_file_to_repo(
    download_tasks: list,
    prefix: str,
    root_path: Path,
    repo_manager: RepoManager,
    hf_repo_id: str,
    hf_repo_type: RepoType,
    ms_repo_id: str,
    ms_repo_type: RepoType,
) -> None:
    if len(download_tasks) == 0:
        print("无上传任务")
        return

    download_path = root_path / prefix
    task_sum = len(download_tasks)
    task_count = 0

    for file, url, in_hf, in_ms in download_tasks:
        task_count += 1
        try:
            print(f"[{task_count}/{task_sum}] 下载 {file} 中")
            file_in_local_path = download_file(url=url, path=download_path, save_name=file)
            if not in_hf:
                print(
                    f"[{task_count}/{task_sum}] 上传 {file} 到 HuggingFace:{hf_repo_id} (类型: {hf_repo_type}) 中"
                )
                with TemporaryDirectory() as upload_dir:
                    shutil.copy2(file_in_local_path, Path(upload_dir) / file)
                    repo_manager.upload_files_to_repo(
                        api_type="huggingface",
                        repo_id=hf_repo_id,
                        repo_type=hf_repo_type,
                        upload_path=Path(upload_dir),
                        path_in_repo=prefix,
                    )

            if not in_ms:
                print(
                    f"[{task_count}/{task_sum}] 上传 {file} 到 ModelScope:{ms_repo_id} (类型: {ms_repo_type}) 中"
                )
                with TemporaryDirectory() as upload_dir:
                    shutil.copy2(file_in_local_path, Path(upload_dir) / file)
                    repo_manager.upload_files_to_repo(
                        api_type="modelscope",
                        repo_id=ms_repo_id,
                        repo_type=ms_repo_type,
                        upload_path=Path(upload_dir),
                        path_in_repo=prefix,
                    )
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"上传 / 下载 {file} 时发生了错误: {e}")
        finally:
            if file_in_local_path is not None and file_in_local_path.exists():
                file_in_local_path.unlink()

    print(f"[{task_count}/{task_sum}] 同步文件完成")


def main() -> None:
    repo_manager = RepoManager(
        hf_token=os.environ.get("HF_TOKEN"),
        ms_token=os.environ.get("MODELSCOPE_API_TOKEN"),
    )
    gh_file = get_github_release_file(
        "kingbri1/flash-attention"
    ) + get_github_release_file("Dao-AILab/flash-attention")
    hf_file = repo_manager.get_repo_file(
        api_type="huggingface",
        repo_id="licyk/wheel",
        repo_type="model",
    )
    ms_file = repo_manager.get_repo_file(
        api_type="modelscope",
        repo_id="licyks/wheels",
        repo_type="model",
    )
    gh_file = filter_whl_file(file_list=gh_file, list_type=ListType.multiple)
    hf_file = filter_whl_file(file_list=hf_file, list_type=ListType.single)
    ms_file = filter_whl_file(file_list=ms_file, list_type=ListType.single)
    gh_file_flash_attn = fitter_flash_attn_whl(
        file_list=gh_file, prefix="flash_attn", list_type=ListType.multiple
    )
    hf_file_flash_attn = fitter_flash_attn_whl(
        file_list=hf_file, prefix="flash_attn/", list_type=ListType.single
    )
    ms_file_flash_attn = fitter_flash_attn_whl(
        file_list=ms_file, prefix="flash_attn/", list_type=ListType.single
    )
    download_tasks = create_download_task(
        github_file_list=gh_file_flash_attn,
        hf_file_list=hf_file_flash_attn,
        ms_file_list=ms_file_flash_attn,
        prefix="flash_attn",
    )
    print(f"flash_attn wheel 源仓库文件数量: {len(gh_file_flash_attn)}")
    print(
        f"flash_attn wheel 镜像仓库 (HuggingFace) 文件数量: {len(hf_file_flash_attn)}"
    )
    print(f"flash_attn wheel 镜像仓库 (ModelScope) 文件数量: {len(ms_file_flash_attn)}")
    sync_file_to_repo(
        download_tasks=download_tasks,
        prefix="flash_attn",
        root_path=Path(os.getenv("ROOT_PATH", os.getcwd())),
        repo_manager=repo_manager,
        hf_repo_id="licyk/wheel",
        hf_repo_type="model",
        ms_repo_id="licyks/wheels",
        ms_repo_type="model",
    )


if __name__ == "__main__":
    main()
