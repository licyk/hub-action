import os
import time
import requests
from enum import Enum
from functools import wraps
from typing import (
    Union,
    Literal,
    Callable,
    TypeVar,
    ParamSpec,
    cast,
)
from pathlib import Path
from huggingface_hub import HfApi
from modelscope import HubApi


T = TypeVar("T")
P = ParamSpec("P")


class RetrySignalError(Exception):
    """仅供装饰器内部使用的重试信号异常"""

    pass  # pylint: disable=unnecessary-pass


def retryable(
    times: int | None = 3,
    delay: float | None = 1.0,
    describe: str | None = None,
    catch_exceptions: type[Exception] | tuple[type[Exception], ...] = Exception,
    raise_exception: type[Exception] = RuntimeError,
    retry_on_none: bool | None = False,
) -> Callable[[Callable[P, T | None]], Callable[..., T]]:
    """通用的重试装饰器

    该装饰器会为原函数注入以下参数:
        - retry_times (int | None): 重试次数
        - retry_delay (float | None): 重试延迟

    Args:
        times (int | None):
            最大重试次数
        delay (float | None):
            失败后的延迟时间 (秒)
        describe (str | None):
            日志中显示的描述文字
        catch_exceptions (type[Exception] | tuple[type[Exception], ...]):
            需要捕获并触发重试的异常类型
        raise_exception (type[Exception]):
            超过重试次数后抛出的异常类型
        retry_on_none (bool | None):
            是否在返回 None 时触发重试

    Returns:
        (Callable[[Callable[P, T | None]], Callable[..., T]]):
            装饰器函数
    """

    def decorator(func: Callable[P, T | None]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(
            *args: P.args,
            retry_times: int | None = None,
            retry_delay: float | None = None,
            **kwargs: P.kwargs,
        ) -> T:
            actual_times = retry_times if retry_times is not None else times
            actual_delay = retry_delay if retry_delay is not None else delay
            count = 0
            err = None
            target_info = describe if describe is not None else func.__name__
            if isinstance(catch_exceptions, tuple):
                catch_exc = catch_exceptions + (RetrySignalError,)
            else:
                catch_exc = (catch_exceptions, RetrySignalError)

            while count < actual_times:
                count += 1
                try:
                    result = func(*args, **kwargs)
                    if retry_on_none and result is None:
                        # 如果返回 None 且启用了检查, 则手动抛出异常触发下面的 except
                        raise ValueError(f"'{target_info}' 返回结果为空")

                    return cast(T, result)
                except catch_exc as e:  # pylint: disable=catching-non-exception
                    err = e
                    # 判断是否是内部信号触发的
                    error_msg = (
                        str(e)
                        if isinstance(e, RetrySignalError)
                        else f"{type(e).__name__}: {e}"
                    )
                    print(
                        f"[{count}/{actual_times}] {target_info} 出现错误: {error_msg}"
                    )

                    if count < actual_times:
                        print(f"[{count}/{actual_times}] 重试 {target_info} 中")
                        if actual_delay > 0:
                            time.sleep(actual_delay)
                    else:
                        # 达到重试上限, 抛出指定的异常
                        raise raise_exception(
                            f"执行 '{target_info}' 时发生错误: {err}"
                        ) from err

                except Exception as e:  # pylint: disable=duplicate-except
                    # 如果出现了不在 catch_exceptions 列表中的异常, 立即抛出, 不重试
                    print(f"[{count}/{actual_times}] 遇到不可重试的致命错误: {e}")
                    raise

            # 正常情况下逻辑在循环内结束, 这里作为兜底抛出
            raise raise_exception(f"执行 '{target_info}' 最终失败")

        return cast(Callable[..., T], wrapper)

    return decorator


class ListType(int, Enum):
    single = 1
    multiple = 2


# HuggingFace 仓库类型
HFRepoType = Literal["model", "dataset", "space"]

# ModelScope 仓库类型
MSRepoType = Literal["model", "dataset", "space"]


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
            file_list.append(
                [x.get("name"), x.get("browser_download_url")])

    return file_list


def get_repo_file(
    api: HfApi | HubApi,
    repo_id: str,
    repo_type: HFRepoType = "model",
) -> list[str]:
    """获取 HuggingFace / ModelScope 仓库文件列表

    :param api`(HfApi|HubApi)`: HuggingFace / ModelScope Api 实例
    :param repo_id`(str)`: HuggingFace / ModelScope 仓库 ID
    :param repo_type`(str)`: HuggingFace / ModelScope 仓库类型
    :return `list[str]`: 仓库文件列表
    """
    if isinstance(api, HfApi):
        print(f"获取 HuggingFace 仓库 {repo_id} (类型: {repo_type}) 的文件列表")
        return get_hf_repo_files(api, repo_id, repo_type)
    if isinstance(api, HubApi):
        print(f"获取 ModelScope 仓库 {repo_id} (类型: {repo_type}) 的文件列表")
        return get_ms_repo_files(api, repo_id, repo_type)

    print(f"未知 Api 类型: {api}")
    return []


@retryable(
    times=3,
    delay=1.0,
    describe="获取 HuggingFace 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_hf_repo_files(
    api: HfApi,
    repo_id: str,
    repo_type: HFRepoType,
) -> list[str]:
    """获取 HuggingFace 仓库文件列表

    :param api`(HfApi)`: HuggingFace Api 实例
    :param repo_id`(str)`: HuggingFace 仓库 ID
    :param repo_type`(str)`: HuggingFace 仓库类型
    :return `list[str]`: 仓库文件列表
    """
    return api.list_repo_files(
        repo_id=repo_id,
        repo_type=repo_type,
    )


@retryable(
    times=3,
    delay=1.0,
    describe="获取 ModelScope 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_ms_repo_files(
    api: HubApi,
    repo_id: str,
    repo_type: MSRepoType = "model",
) -> list[str]:
    """ 获取 ModelScope 仓库文件列表

    :param api`(HfApi)`: ModelScope Api 实例
    :param repo_id`(str)`: ModelScope 仓库 ID
    :param repo_type`(str)`: ModelScope 仓库类型
    :return `list[str]`: 仓库文件列表
    """
    file_list = []

    def _get_file_path(repo_files: list) -> list[str]:
        """获取 ModelScope Api 返回的仓库列表中的模型路径"""
        return [
            file["Path"]
            for file in repo_files
            if file['Type'] != 'tree'
        ]

    if repo_type == "model":
        repo_files = api.get_model_files(
            model_id=repo_id,
            recursive=True
        )
        file_list = _get_file_path(repo_files)
    elif repo_type == "dataset":
        repo_files = api.get_dataset_files(
            repo_id=repo_id,
            recursive=True
        )
        file_list = _get_file_path(repo_files)
    elif repo_type == "space":
        print(f"{repo_id} 仓库类型为创空间, 不支持获取文件列表")
    else:
        raise ValueError(f"未知的 {repo_type} 仓库类型")

    return file_list


def filter_whl_file(file_list: list[str], list_type: ListType) -> list[str]:
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
            # in_hf = False
            pass # 不再同步文件到 HuggingFace
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
    from tqdm import tqdm

    if not file_name:
        parts = urlparse(url)
        file_name = os.path.basename(parts.path)

    cached_file = os.path.abspath(os.path.join(model_dir, file_name))

    if re_download or not os.path.exists(cached_file):
        os.makedirs(model_dir, exist_ok=True)
        temp_file = os.path.join(model_dir, f"{file_name}.tmp")
        print(f'Downloading: "{url}" to {cached_file}')
        response = requests.get(url, stream=True, timeout=30)
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
    hf_api: HfApi,
    hf_repo_id: str,
    hf_repo_type: str,
    ms_api: HubApi,
    ms_repo_id: str,
    ms_repo_type: str
) -> None:
    if len(download_tasks) == 0:
        print("无上传任务")
        return

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
                hf_api.upload_file(
                    repo_id=hf_repo_id,
                    repo_type=hf_repo_type,
                    path_in_repo=file_in_repo_path,
                    path_or_fileobj=file_in_local_path,
                    commit_message=f"Upload {file}",
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
        except Exception as e: # pylint: disable=broad-exception-caught
            print(f"上传 / 下载 {file} 时发生了错误: {e}")
        finally:
            if os.path.exists(file_in_local_path):
                os.remove(file_in_local_path)

    print(f"[{task_count}/{task_sum}] 同步文件完成")


def main() -> None:
    hf_api = HfApi(token=os.environ.get("HF_TOKEN", None))
    ms_api = HubApi()
    ms_api.login(access_token=os.environ.get("MODELSCOPE_API_TOKEN", None))
    gh_file = (
        get_github_release_file("kingbri1/flash-attention")
        +
        get_github_release_file("Dao-AILab/flash-attention")
    )
    hf_file = get_repo_file(
        api=hf_api,
        repo_id="licyk/wheel",
        repo_type="model"
    )
    ms_file = get_repo_file(
        api=ms_api,
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
        root_path=os.environ.get("ROOT_PATH", os.getcwd()),
        hf_api=hf_api,
        hf_repo_id="licyk/wheel",
        hf_repo_type="model",
        ms_api=ms_api,
        ms_repo_id="licyks/wheels",
        ms_repo_type="model"
    )


if __name__ == "__main__":
    main()
