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
    api = HfApi()
    file_list = []

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
def get_modelscope_repo_file(repo_id: str, repo_type: str) -> list[tuple[str, str]]:
    api = HubApi()

    file_list = []
    file_list_url = []

    def _get_file_path(repo_files: list[str]) -> list[str]:
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
        repo_files = api.get_dataset_files(repo_id=repo_id, recursive=True)
        file_list = _get_file_path(repo_files)
    elif repo_type == "space":
        raise RuntimeError(f"{repo_id} 仓库类型为创空间, 不支持获取文件列表")
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
            raise ValueError(f"错误的 HuggingFace 仓库类型: {repo_type}")

        file_list_url.append((i, url))

    return file_list_url


def build_pypi_list(file_list: list[str]) -> list[str]:
    html_string = []

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
        hf_mirror_list = []
        for file, url in file_list:
            hf_mirror_list.append(
                [file, url.replace("https://huggingface.co/", "https://hf-mirror.com/")]
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
