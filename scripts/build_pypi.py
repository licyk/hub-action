import os
import re
import time
from pathlib import Path
from functools import wraps
from typing import (
    Callable,
    TypeVar,
    ParamSpec,
    Any,
    cast,
)

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
def get_github_release_file(
    repo: str,
    tag: str,
) -> list[tuple[str, str]]:
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
def get_huggingface_repo_file(
    repo_id: str,
    repo_type: str,
) -> list[tuple[str, str]]:
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
def get_modelscope_repo_file(
    repo_id: str,
    repo_type: str,
) -> list[tuple[str, str]]:
    api = HubApi()

    file_list = []
    file_list_url = []

    def _get_file_path(
        repo_files: list[str],
    ) -> list[str]:
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
            raise ValueError(f"错误的 HuggingFace 仓库类型: {repo_type}")

        file_list_url.append((i, url))

    return file_list_url


def filter_whl_file(
    file_list: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    fitter_file_list = []
    for file, url in file_list:
        if file.endswith(".whl"):
            fitter_file_list.append((file, url))

    return fitter_file_list


def normalize_package_name(
    name: str,
) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# PEP 491 Wheel 文件名格式正则表达式
WHEEL_PATTERN = r"""
    ^                           # 字符串开始
    (?P<distribution>[^-]+)     # 包名 (匹配第一个非连字符段)
    -                           # 分隔符
    (?:                         # 版本号和可选构建号组合
        (?P<version>[^-]+)      # 版本号 (至少一个非连字符段)
        (?:-(?P<build>\d\w*))?  # 可选构建号 (以数字开头)
    )
    -                           # 分隔符
    (?P<python>[^-]+)           # Python 版本标签
    -                           # 分隔符
    (?P<abi>[^-]+)              # ABI 标签
    -                           # 分隔符
    (?P<platform>[^-]+)         # 平台标签
    \.whl$                      # 固定后缀
"""


def parse_wheel_filename(
    filename: str,
) -> dict[str, Any]:
    """解析 Python wheel 文件名并返回详细信息

    根据 PEP 491 规范解析 wheel 文件名
    格式: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl

    Args:
        filename (str): wheel 文件名, 例如 pydantic-1.10.15-py3-none-any.whl

    Returns:
        dict: 包含以下键的字典:
            - distribution: 包名 (str)
            - version: 版本号 (str)
            - build: 构建号 (str | None)
            - python: Python 版本标签 (str)
            - abi: ABI 标签 (str)
            - platform: 平台标签 (str)
            - filename: 原始文件名 (str)

    Raises:
        ValueError: 如果文件名不符合 PEP 491 规范

    Examples:
        >>> parse_wheel_filename("pydantic-1.10.15-py3-none-any.whl")
        {
            'distribution': 'pydantic',
            'version': '1.10.15',
            'build': None,
            'python': 'py3',
            'abi': 'none',
            'platform': 'any',
            'filename': 'pydantic-1.10.15-py3-none-any.whl'
        }

        >>> parse_wheel_filename("numpy-1.24.0-1-cp311-cp311-win_amd64.whl")
        {
            'distribution': 'numpy',
            'version': '1.24.0',
            'build': '1',
            'python': 'cp311',
            'abi': 'cp311',
            'platform': 'win_amd64',
            'filename': 'numpy-1.24.0-1-cp311-cp311-win_amd64.whl'
        }
    """
    match = re.fullmatch(WHEEL_PATTERN, filename, re.VERBOSE)
    if not match:
        print(f"警告: 未知的 Wheel 文件名: {filename}")
        raise ValueError(f"未知的 Wheel 文件名: {filename}")

    return {
        "distribution": match.group("distribution"),
        "version": match.group("version"),
        "build": match.group("build"),  # 可能为 None
        "python": match.group("python"),
        "abi": match.group("abi"),
        "platform": match.group("platform"),
        "filename": filename,
    }


def group_files_by_package(
    file_list: list[tuple[str, str]],
) -> dict[str, list[tuple[str, str, dict]]]:
    """
    将文件列表按包名分组
    返回: {normalized_package_name: [(filename, url, metadata), ...]}
    """
    packages = {}

    for file_path, url in file_list:
        # 提取文件名（去除路径）
        filename = os.path.basename(file_path)

        try:
            # 解析 wheel 文件名
            metadata = parse_wheel_filename(filename)
            package_name = metadata[
                "distribution"
            ]  # 使用 'distribution' 而不是 'package_name'
            normalized_name = normalize_package_name(package_name)

            if normalized_name not in packages:
                packages[normalized_name] = []

            packages[normalized_name].append((filename, url, metadata))
        except ValueError as e:
            # 如果解析失败，跳过该文件
            print(f"跳过无效的 wheel 文件: {filename} - {e}")
            continue

    return packages


def generate_package_index_html(
    packages: dict[str, list[tuple[str, str, dict]]],
) -> str:
    """
    生成 PyPI 简单索引的主页面 HTML
    根据 PEP 503 规范
    """
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '    <meta name="pypi:repository-version" content="1.0">',
        "    <title>Simple Index</title>",
        "</head>",
        "<body>",
        "    <h1>Simple Index</h1>",
    ]

    # 按字母顺序排序包名
    sorted_packages = sorted(packages.keys())

    for package_name in sorted_packages:
        # 根据 PEP 503，链接应该指向包名目录
        html_parts.append(f'    <a href="{package_name}/">{package_name}</a><br/>')

    html_parts.extend(
        [
            "</body>",
            "</html>",
        ]
    )

    return "\n".join(html_parts)


def generate_package_detail_html(
    package_name: str, files: list[tuple[str, str, dict]]
) -> str:
    """
    生成单个包的详情页面 HTML
    根据 PEP 503 规范
    """
    html_parts = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        '    <meta name="pypi:repository-version" content="1.0">',
        f"    <title>Links for {package_name}</title>",
        "</head>",
        "<body>",
        f"    <h1>Links for {package_name}</h1>",
    ]

    # 按文件名排序
    sorted_files = sorted(files, key=lambda x: x[0])

    for filename, url, _ in sorted_files:
        # 根据 PEP 503，每个链接应该包含文件名
        html_parts.append(f'    <a href="{url}">{filename}</a><br/>')

    html_parts.extend(
        [
            "</body>",
            "</html>",
        ]
    )

    return "\n".join(html_parts)


def build_pypi_index(
    file_list: list[tuple[str, str]],
    output_dir: Path,
) -> None:
    """
    根据 PEP 503 规范构建 PyPI 简单索引

    参数:
        file_list: 文件列表，格式为 [(文件路径, URL), ...]
        output_dir: 输出目录路径
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 按包名分组文件
    packages = group_files_by_package(file_list)

    print(f"找到 {len(packages)} 个包")

    # 生成主索引页面
    index_html = generate_package_index_html(packages)
    index_file = output_dir / "index.html"
    index_file.write_text(index_html, encoding="utf-8")
    print(f"生成主索引页面: {index_file}")

    # 为每个包生成详情页面
    for package_name, files in packages.items():
        package_dir = output_dir / package_name
        package_dir.mkdir(parents=True, exist_ok=True)

        detail_html = generate_package_detail_html(package_name, files)
        detail_file = package_dir / "index.html"
        detail_file.write_text(detail_html, encoding="utf-8")
        print(f"生成包详情页面: {detail_file} (包含 {len(files)} 个文件)")

    print(f"\nPyPI 索引生成完成, 输出目录: {output_dir}")


def main() -> None:
    root_path = Path(os.getenv("root_path", os.getcwd())).absolute()
    print(f"根目录: {root_path}")

    gh_file = get_github_release_file(repo="licyk/term-sd", tag="wheel")
    hf_file = get_huggingface_repo_file(repo_id="licyk/wheel", repo_type="model")
    ms_file = get_modelscope_repo_file(repo_id="licyks/wheels", repo_type="model")
    gh_file = filter_whl_file(gh_file)
    hf_file = filter_whl_file(hf_file)
    ms_file = filter_whl_file(ms_file)

    def _hf_mirror_list(file_list: list[tuple[str, str]]) -> list[tuple[str, str]]:
        hf_mirror_list = []
        for file, url in file_list:
            hf_mirror_list.append(
                [file, url.replace("https://huggingface.co/", "https://hf-mirror.com/")]
            )

        return hf_mirror_list

    hf_mirror_file = _hf_mirror_list(hf_file)

    print("获取文件列表完成, 生成包索引中")
    build_pypi_index(gh_file, root_path / "pypi_gh")
    build_pypi_index(hf_file, root_path / "pypi_hf")
    build_pypi_index(ms_file, root_path / "pypi")
    build_pypi_index(hf_mirror_file, root_path / "pypi_hf_mirror")


if __name__ == "__main__":
    main()
