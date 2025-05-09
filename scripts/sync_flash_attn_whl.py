import os
import requests
import modelscope
import huggingface_hub
from enum import Enum
from typing import Union
from pathlib import Path


class ListType(int, Enum):
    single = 1
    multiple = 2


def get_github_release_file(repo: str) -> list:
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
        for x in i.get("assets"):
            file_list.append(
                [x.get("name"), x.get("browser_download_url")])

    return file_list


def get_huggingface_repo_file(repo_id: str, repo_type: str) -> list:
    api = huggingface_hub.HfApi()
    repo_files = []

    try:
        print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
        repo_files = api.list_repo_files(
            repo_id=repo_id,
            repo_type=repo_type,
        )
    except Exception as e:
        print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表出现错误: {e}")

    return repo_files


def get_modelscope_repo_file(repo_id: str, repo_type: str) -> list:
    api = modelscope.HubApi()
    from modelscope.hub.snapshot_download import fetch_repo_files
    from modelscope.hub.api import DEFAULT_DATASET_REVISION

    file_list = []

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
        user, name = repo_id.split("/")
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
    else:
        raise Exception(f"未知的 {repo_type} 仓库类型")

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
    github_file_list: list,
    hf_file_list: list,
    ms_file_list: list,
    prefix: str
) -> list:
    tasks = []
    for file, url in github_file_list:
        file_in_repo = f"{prefix}/{file}"
        in_hf = True
        in_ms = True
        if file_in_repo not in hf_file_list:
            in_hf = False
        if file_in_repo not in ms_file_list:
            in_ms = False
        if not in_hf or not in_ms:
            tasks.append([file, url, in_hf, in_ms])

    return tasks


def load_file_from_url(
    url: str,
    *,
    model_dir: str,
    progress: bool = True,
    file_name: str | None = None,
    hash_prefix: str | None = None,
    re_download: bool = False,
) -> str:
    """Download a file from `url` into `model_dir`, using the file present if possible.
    Returns the path to the downloaded file.

    file_name: if specified, it will be used as the filename, otherwise the filename will be extracted from the url.
        file is downloaded to {file_name}.tmp then moved to the final location after download is complete.
    hash_prefix: sha256 hex string, if provided, the hash of the downloaded file will be checked against this prefix.
        if the hash does not match, the temporary file is deleted and a ValueError is raised.
    re_download: forcibly re-download the file even if it already exists.
    """
    from urllib.parse import urlparse
    import requests
    try:
        from tqdm import tqdm
    except ImportError:
        class tqdm:
            def __init__(self, *args, **kwargs):
                pass

            def update(self, n=1, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

    if not file_name:
        parts = urlparse(url)
        file_name = os.path.basename(parts.path)

    cached_file = os.path.abspath(os.path.join(model_dir, file_name))

    if re_download or not os.path.exists(cached_file):
        os.makedirs(model_dir, exist_ok=True)
        temp_file = os.path.join(model_dir, f"{file_name}.tmp")
        print(f'Downloading: "{url}" to {cached_file}')
        response = requests.get(url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        with tqdm(total=total_size, unit='B', unit_scale=True, desc=file_name, disable=not progress) as progress_bar:
            with open(temp_file, 'wb') as file:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        file.write(chunk)
                        progress_bar.update(len(chunk))

        if hash_prefix and not compare_sha256(temp_file, hash_prefix):
            print(
                "Hash mismatch for %s. Deleting the temporary file.",
                temp_file
            )
            os.remove(temp_file)
            raise ValueError(
                f"File hash does not match the expected hash prefix {hash_prefix}!")

        os.rename(temp_file, cached_file)
    return cached_file


def compare_sha256(file_path: str, hash_prefix: str) -> bool:
    """Check if the SHA256 hash of the file matches the given prefix."""
    import hashlib
    hash_sha256 = hashlib.sha256()
    blksize = 1024 * 1024

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(blksize), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest().startswith(hash_prefix.strip().lower())


def sync_file_to_repo(
    download_tasks: list,
    prefix: str,
    root_path: Union[str, Path],
    hf_repo_id: str,
    hf_repo_type: str,
    ms_repo_id: str,
    ms_repo_type: str
) -> None:
    from huggingface_hub import HfApi, CommitOperationAdd
    from modelscope.hub.api import HubApi

    if len(download_tasks) == 0:
        print("无上传任务")
        return

    hf_api = HfApi()
    ms_api = HubApi()
    download_path = os.path.join(root_path, prefix)
    task_sum = len(download_tasks)
    task_count = 0

    for file, url, in_hf, in_ms in download_tasks:
        task_count += 1
        try:
            print(f"[{task_count}/{task_sum}] 下载 {file} 中")
            file_in_local_path = load_file_from_url(
                url=url,
                model_dir=download_path,
                file_name=file
            )
            file_in_repo_path = Path(os.path.relpath(
                file_in_local_path,
                root_path
            )).as_posix()

            if not in_hf:
                print(
                    f"[{task_count}/{task_sum}] 上传 {file} 到 HuggingFace:{hf_repo_id} (类型: {hf_repo_type}) 中")
                hf_api.create_commit(
                    repo_id=hf_repo_id,
                    repo_type=hf_repo_type,
                    operations=[
                        CommitOperationAdd(
                            path_in_repo=file_in_repo_path,
                            path_or_fileobj=file_in_local_path
                        )
                    ],
                    commit_message=f"Upload {file}",
                    token=os.environ.get("HF_TOKEN", None)
                )

            if not in_ms:
                print(
                    f"[{task_count}/{task_sum}] 上传 {file} 到 ModelScope:{ms_repo_id} (类型: {ms_repo_type}) 中")
                ms_api.upload_file(
                    repo_id=ms_repo_id,
                    repo_type=ms_repo_type,
                    path_in_repo=file_in_repo_path,
                    path_or_fileobj=file_in_local_path,
                    commit_message=f"Upload {file}",
                    token=os.environ.get("MODELSCOPE_API_TOKEN", None)
                )
        except Exception as e:
            print(f"上传 / 下载 {file} 时发生了错误: {e}")
        finally:
            if os.path.exists(file_in_local_path):
                os.remove(file_in_local_path)

    print(f"[{task_count}/{task_sum}] 同步文件完成")


def main() -> None:
    gh_file = (
        get_github_release_file("kingbri1/flash-attention")
        +
        get_github_release_file("Dao-AILab/flash-attention")
    )
    hf_file = get_huggingface_repo_file(
        repo_id="licyk/wheel",
        repo_type="model"
    )
    ms_file = get_modelscope_repo_file(
        repo_id="licyks/wheels",
        repo_type="model"
    )
    gh_file = filter_whl_file(
        file_list=gh_file,
        list_type=ListType.multiple
    )
    hf_file = filter_whl_file(
        file_list=hf_file,
        list_type=ListType.single
    )
    ms_file = filter_whl_file(
        file_list=ms_file,
        list_type=ListType.single
    )
    gh_file_flash_attn = fitter_flash_attn_whl(
        file_list=gh_file,
        prefix="flash_attn",
        list_type=ListType.multiple
    )
    hf_file_flash_attn = fitter_flash_attn_whl(
        file_list=hf_file,
        prefix="flash_attn/",
        list_type=ListType.single
    )
    ms_file_flash_attn = fitter_flash_attn_whl(
        file_list=ms_file,
        prefix="flash_attn/",
        list_type=ListType.single
    )
    download_tasks = create_download_task(
        github_file_list=gh_file_flash_attn,
        hf_file_list=hf_file_flash_attn,
        ms_file_list=ms_file_flash_attn,
        prefix="flash_attn"
    )
    print(f"flash_attn wheel 源仓库文件数量: {len(gh_file_flash_attn)}")
    print(f"flash_attn wheel 镜像仓库 (HuggingFace) 文件数量: {len(hf_file_flash_attn)}")
    print(f"flash_attn wheel 镜像仓库 (ModelScope) 文件数量: {len(ms_file_flash_attn)}")
    sync_file_to_repo(
        download_tasks=download_tasks,
        prefix="flash_attn",
        root_path=os.environ.get("root_path", os.getcwd()),
        hf_repo_id="licyk/wheel",
        hf_repo_type="model",
        ms_repo_id="licyks/wheels",
        ms_repo_type="model"
    )


if __name__ == "__main__":
    main()
