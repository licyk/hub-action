import os
import json
import time
from functools import wraps
from typing import (
    Any,
    Union,
    Literal,
    Callable,
    TypeVar,
    ParamSpec,
    TypedDict,
    TypeAlias,
    cast,
)
from pathlib import Path

from modelscope.hub.api import HubApi
from huggingface_hub import HfApi


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
        - **retry_times** *(int | None)*:
            重试次数
        - **retry_delay** *(float | None)*:
            重试延迟

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
            *args: Any,
            retry_times: int | None = None,
            retry_delay: float | None = None,
            **kwargs: Any,
        ) -> T:
            actual_times = retry_times if retry_times is not None else (times if times is not None else 0)
            actual_delay = retry_delay if retry_delay is not None else (delay if delay is not None else 0)
            count = 0
            err = None
            target_info = describe if describe is not None else getattr(func, "__name__", repr(func))
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
                    error_msg = str(e) if isinstance(e, RetrySignalError) else f"{type(e).__name__}: {e}"
                    print(
                        f"[{count}/{actual_times}] {target_info} 出现错误: {error_msg}"
                    )

                    if count < actual_times:
                        print(f"[{count}/{actual_times}] 重试 {target_info} 中")
                        if actual_delay > 0:
                            time.sleep(actual_delay)
                    else:
                        # 达到重试上限, 抛出指定的异常
                        raise raise_exception(f"执行 '{target_info}' 时发生错误: {err}") from err

                except Exception as e:  # pylint: disable=duplicate-except
                    # 如果出现了不在 catch_exceptions 列表中的异常, 立即抛出, 不重试
                    print(f"[{count}/{actual_times}] 遇到不可重试的致命错误: {e}")
                    raise

            # 正常情况下逻辑在循环内结束, 这里作为兜底抛出
            raise raise_exception(f"执行 '{target_info}' 最终失败")

        return cast(Callable[..., T], wrapper)

    return decorator


@retryable(
    times=3,
    delay=1.0,
    describe="获取 HuggingFace 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_huggingface_repo_file(
    repo_id: str,
    repo_type: Literal["model", "dataset", "space"],
) -> list[tuple[str, str]]:
    '''从 HuggingFace 仓库获取文件列表

    :param repo_id`(str)`: 仓库 ID
    :param repo_type`(str)`: 仓库种类 (model/dataset/space)
    :return `list[tuple[str, str]]`: 仓库文件列表 `[<路径>, <链接>]`
    '''
    api = HfApi()
    file_list: list[tuple[str, str]] = []

    print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
    repo_files = api.list_repo_files(
        repo_id=repo_id,
        repo_type=repo_type,
    )

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


@retryable(
    times=3,
    delay=1.0,
    describe="获取 ModelScope 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_modelscope_repo_file(
    repo_id: str,
    repo_type: Literal["model", "dataset", "space"],
) -> list[tuple[str, str]]:
    '''从 ModelScope 仓库获取文件列表

    :param repo_id`(str)`: 仓库 ID
    :param repo_type`(str)`: 仓库种类 (model/dataset/space)
    :return `tuple[str, str]`: 仓库文件列表 `[<路径>, <链接>]`
    '''
    api = HubApi()
    file_list_url: list[tuple[str, str]] = []

    def _get_file_path(repo_files: list[dict[str, Any]]) -> list:
        file_list = []
        for file in repo_files:
            if file["Type"] != "tree":
                file_list.append(file["Path"])
        return file_list

    if repo_type == "model":
        print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
        repo_files = api.get_model_files(model_id=repo_id, recursive=True)
        file_list = _get_file_path(repo_files)
    elif repo_type == "dataset":
        print(f"获取 {repo_id} (类型: {repo_type}) 中的文件列表")
        all_files = []
        page_number = 1
        page_size = 100
        owner, dataset_name = repo_id.split("/")
        dataset_hub_id, _ = api.get_dataset_id_and_type(
            dataset_name=dataset_name,
            namespace=owner,
        )
        while True:
            repo_files = api.get_dataset_files(
                repo_id=repo_id,
                recursive=True,
                page_number=page_number,
                page_size=page_size,
                dataset_hub_id=dataset_hub_id,
            )
            if not repo_files:
                break

            all_files.extend(repo_files)
            if len(repo_files) < page_size:
                break

            page_number += 1
        file_list = _get_file_path(all_files)
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
            raise ValueError(f"错误的 ModelScope 仓库类型: {repo_type}")

        file_list_url.append((i, url))

    return file_list_url


def write_content_to_file(content: list, path: Union[str, Path]) -> None:
    '''将列表写入文件中

    :param content`(list)`: 列表内容
    :param path`(Union[str,Path])`: 保存路径
    '''
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


def save_list_to_json(save_path: Path | str, data: Any) -> bool:
    """保存列表到 Json 文件中

    :param save_path`(Path,str)`: 保存 Json 文件的路径
    :param data`(Any)`: 要保存的列表
    :return `bool`: 当文件保存成功时返回`True`
    """
    dir_path = os.path.dirname(save_path)

    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)

    try:
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=4,
                separators=(',', ': ')
            )
        print(f"保存 Json 文件到 {save_path}")
        return True
    except Exception as e: # pylint: disable=broad-exception-caught
        print(f"保存列表到 {save_path} 时发生错误: {e}")
        return False


RepoFileType: TypeAlias = tuple[str, str]

class RepoMetadata(TypedDict):
    repo_id: str
    repo_type: Literal["model", "dataset", "space"]
    files: list[RepoFileType]



def build_hf_repo_data(
    repo_id: str,
    repo_type: Literal["model", "dataset", "space"],
) -> RepoMetadata:
    hf_files = get_huggingface_repo_file(
        repo_id=repo_id,
        repo_type=repo_type,
    )
    return {
        "repo_id": repo_id,
        "repo_type": repo_type,
        "files": hf_files,
    }


def build_ms_repo_data(
    repo_id: str,
    repo_type: Literal["model", "dataset", "space"],
) -> RepoMetadata:
    ms_files = get_modelscope_repo_file(
        repo_id=repo_id,
        repo_type=repo_type,
    )
    return {
        "repo_id": repo_id,
        "repo_type": repo_type,
        "files": ms_files,
    }


class RepoInfo(TypedDict):
    repo_id: str
    repo_type: Literal["model", "dataset", "space"]

RepoInfoList = list[RepoInfo]


def build_hf_repos_data(
    repo_info_list: RepoInfoList,
) -> list[RepoMetadata]:
    return [
        build_hf_repo_data(repo_id=repo_info["repo_id"], repo_type=repo_info["repo_type"])
        for repo_info in repo_info_list
    ]

def build_ms_repos_data(
    repo_info_list: RepoInfoList,
) -> list[RepoMetadata]:
    return [
        build_ms_repo_data(repo_id=repo_info["repo_id"], repo_type=repo_info["repo_type"])
        for repo_info in repo_info_list
    ]

def build_repo_info_data(
    hf_repo_info_list: RepoInfoList,
    ms_repo_info_list: RepoInfoList,
) -> dict[str, list[RepoMetadata]]:
    return {
        "huggingface": build_hf_repos_data(repo_info_list=hf_repo_info_list),
        "modelscope": build_ms_repos_data(repo_info_list=ms_repo_info_list),
    }



def parse_repo_env(env_value: str | None) -> RepoInfoList:
    """从环境变量中解析仓库列表
    
    :param env_value`(str | None)`: 环境变量值，格式为 "repo_id:repo_type"，每行一个
    :return RepoInfoList: 解析后的仓库信息列表
    """
    if not env_value:
        return []

    repo_list: RepoInfoList = []
    for line in env_value.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        
        parts = line.split(':')
        if len(parts) != 2:
            print(f"警告: 跳过无效的仓库配置: {line}")
            continue
        
        repo_id, repo_type = parts[0].strip(), parts[1].strip()
        
        if repo_type not in ("model", "dataset", "space"):
            print(f"警告: 跳过无效的仓库类型 {repo_type} (仓库: {repo_id})")
            continue
        
        repo_list.append({
            "repo_id": repo_id,
            "repo_type": cast(Literal["model", "dataset", "space"], repo_type),
        })
    
    return repo_list


def main() -> None:
    '''主函数'''
    root_path = os.environ.get("ROOT_PATH")
    hf_repo_env = os.environ.get("HF_REPO_LIST")
    ms_repo_env = os.environ.get("MS_REPO_LIST")
    
    hf_repo_info_list = parse_repo_env(hf_repo_env)
    ms_repo_info_list = parse_repo_env(ms_repo_env)
    
    if not hf_repo_info_list and not ms_repo_info_list:
        print("警告: 未找到任何仓库配置")
        return
    
    print(f"解析到 HuggingFace 仓库数量: {len(hf_repo_info_list)}")
    print(f"解析到 ModelScope 仓库数量: {len(ms_repo_info_list)}")
    
    repo_data = build_repo_info_data(
        hf_repo_info_list=hf_repo_info_list,
        ms_repo_info_list=ms_repo_info_list,
    )
    
    if root_path:
        output_path = os.path.join(root_path, "repo_list.json")
        save_list_to_json(output_path, repo_data)
        print(f"仓库列表已保存到: {output_path}")
    else:
        print(json.dumps(repo_data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()