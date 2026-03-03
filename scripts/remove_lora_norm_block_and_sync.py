import os
import time
import traceback
from functools import wraps
from pathlib import Path
from typing import (
    Literal,
    Callable,
    TypeVar,
    ParamSpec,
    cast,
)
from tempfile import TemporaryDirectory

from safetensors.torch import load_file, save_file
from huggingface_hub import HfApi
from tqdm import tqdm


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
    describe="获取 HuggingFace 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_hf_repo_files(
    api: HfApi,
    repo_id: str,
    repo_type: Literal["model", "dataset", "space"] = "model",
) -> list[str]:
    """获取 HuggingFace 仓库文件列表

    :param api`(HfApi)`: HuggingFace Api 实例
    :param repo_id`(str)`: HuggingFace 仓库 ID
    :param repo_type`(Literal["model","dataset","space"])`: HuggingFace 仓库类型
    :return `list[str]`: 仓库文件列表
    """
    print(f"获取 {repo_id} (类型: {repo_type}) 仓库的文件列表")
    return api.list_repo_files(
        repo_id=repo_id,
        repo_type=repo_type,
    )


def remove_lora_norm_block(
    lora_path: str | Path,
    save_path: str | Path = None,
    save_name: str = None,
) -> Path | None:
    """移除 LoRA 模型权重中的 norm 块

    :param lora_path`(str|Path)`: LoRA 模型的路径
    :param save_path`(str|Path|None)`: 保存 LoRA 模型的路径
    :param save_name`(str|None)`: 保存 LoRA 模型的名称
    :return `Path|None`: 如果 LoRA 存在 norm 块并移除后则返回路径
    """
    lora_path = (
        Path(lora_path)
        if not isinstance(lora_path, Path) and lora_path is not None
        else lora_path
    )
    save_path = (
        Path(save_path)
        if not isinstance(save_path, Path) and save_path is not None
        else save_path
    )

    if save_name is None:
        save_name = f"{lora_path.stem}_without_norm_block.safetensors"

    output_path = save_path / save_name
    norm_block_list = []

    print(f"加载模型: {lora_path}")
    model_weights = load_file(lora_path)

    print(f"{lora_path.name} 块的数量: {len(model_weights.items())}")
    for block, _ in model_weights.items():
        if "norm" in block:
            norm_block_list.append(block)

    if len(norm_block_list) > 0:
        print(f"Norm 块的数量: {len(norm_block_list)}")
        print(f"移除 {lora_path.name} 的 Norm 块中")
        for block in norm_block_list:
            del model_weights[block]

        save_file(model_weights, output_path)
        print(f"移除完成, 保存 {lora_path.name} 模型到 {output_path}")
        return output_path

    print(f"{lora_path.name} 模型中未包含 Norm 块, 无需移除")
    return None


def main() -> None:
    """主函数"""
    hf_token = os.getenv("HF_TOKEN")
    src_repo_id = os.getenv("SRC_REPO_ID")
    src_repo_type = os.getenv("SRC_REPO_TYPE")
    dst_repo_id = os.getenv("DST_REPO_ID")
    dst_repo_type = os.getenv("DST_REPO_TYPE")
    api = HfApi(token=hf_token)
    src_repo_files = get_hf_repo_files(
        api=api,
        repo_id=src_repo_id,
        repo_type=src_repo_type,
    )
    dst_repo_files = get_hf_repo_files(
        api=api,
        repo_id=dst_repo_id,
        repo_type=dst_repo_type,
    )
    dst_repo_files_set = set(dst_repo_files)
    need_process_files = [
        x
        for x in tqdm(src_repo_files, desc="计算需要处理的 LoRA 文件")
        if x not in dst_repo_files_set
        and f"{os.path.dirname(x)}/{os.path.splitext(os.path.basename(x))[0]}_without_norm_block.safetensors"
        not in dst_repo_files_set
    ]
    count = 0
    task_sum = len(need_process_files)
    with TemporaryDirectory() as tmp_dir:
        tmp_dir = Path(tmp_dir)
        for file in need_process_files:
            count += 1
            if not file.endswith(".safetensors"):
                print(f"[{count}/{task_sum}] {file} 非模型文件, 跳过处理")
                continue
            print(f"[{count}/{task_sum}] 处理 LoRA 文件: {file}")

            try:
                api.snapshot_download(
                    repo_id=src_repo_id,
                    repo_type=src_repo_type,
                    allow_patterns=file,
                    local_dir=tmp_dir,
                )
                origin_lora_path = tmp_dir / file
                lora_dir_path = origin_lora_path.parent
                lora_without_norm_block_path = remove_lora_norm_block(
                    lora_path=origin_lora_path,
                    save_path=lora_dir_path,
                )
                dir_path_in_repo = os.path.dirname(file)
                if lora_without_norm_block_path is not None:
                    api.upload_file(
                        path_or_fileobj=lora_without_norm_block_path,
                        path_in_repo=f"{dir_path_in_repo}/{os.path.splitext(os.path.basename(file))[0]}_without_norm_block.safetensors",
                        repo_id=dst_repo_id,
                        repo_type=dst_repo_type,
                        commit_message=f"Upload {lora_without_norm_block_path.name}",
                    )
                else:
                    api.upload_file(
                        path_or_fileobj=origin_lora_path,
                        path_in_repo=f"{dir_path_in_repo}/{os.path.basename(file)}",
                        repo_id=dst_repo_id,
                        repo_type=dst_repo_type,
                        commit_message=f"Upload {origin_lora_path.name}",
                    )

                origin_lora_path.unlink(missing_ok=True)
                if lora_without_norm_block_path is not None:
                    lora_without_norm_block_path.unlink(missing_ok=True)
            except Exception as e: # pylint: disable=broad-exception-caught
                traceback.print_exc()
                print(f"[{count}/{task_sum}] 处理 LoRA 文件时发生错误: {e}")

    print(f"[{count}/{task_sum}] LoRA 文件处理完成")


if __name__ == "__main__":
    main()
