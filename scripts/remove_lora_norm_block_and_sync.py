import os
import shutil
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, TypeAlias, cast

from safetensors.torch import load_file, save_file
from sd_webui_all_in_one.repo_manager import RepoManager
from tqdm import tqdm


RepoType: TypeAlias = Literal["model", "dataset", "space"]


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"缺少环境变量: {name}")
    return value


def get_repo_type_env(name: str) -> RepoType:
    value = get_required_env(name)
    if value not in {"model", "dataset", "space"}:
        raise RuntimeError(f"环境变量 {name} 的仓库类型无效: {value}")
    return cast(RepoType, value)


def remove_lora_norm_block(
    lora_path: str | Path,
    save_path: str | Path | None = None,
    save_name: str | None = None,
) -> Path | None:
    """移除 LoRA 模型权重中的 norm 块

    :param lora_path`(str|Path)`: LoRA 模型的路径
    :param save_path`(str|Path|None)`: 保存 LoRA 模型的路径
    :param save_name`(str|None)`: 保存 LoRA 模型的名称
    :return `Path|None`: 如果 LoRA 存在 norm 块并移除后则返回路径
    """
    lora_path = Path(lora_path)
    save_path = Path(save_path) if save_path is not None else lora_path.parent

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
    src_repo_id = get_required_env("SRC_REPO_ID")
    src_repo_type = get_repo_type_env("SRC_REPO_TYPE")
    dst_repo_id = get_required_env("DST_REPO_ID")
    dst_repo_type = get_repo_type_env("DST_REPO_TYPE")
    repo_manager = RepoManager(hf_token=hf_token)
    src_repo_files = repo_manager.get_repo_file(
        api_type="huggingface",
        repo_id=src_repo_id,
        repo_type=src_repo_type,
    )
    dst_repo_files = repo_manager.get_repo_file(
        api_type="huggingface",
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
                repo_manager.download_files_from_repo(
                    api_type="huggingface",
                    repo_id=src_repo_id,
                    repo_type=src_repo_type,
                    local_dir=tmp_dir,
                    folder=file,
                    num_threads=1,
                )
                origin_lora_path = tmp_dir / file
                lora_dir_path = origin_lora_path.parent
                lora_without_norm_block_path = remove_lora_norm_block(
                    lora_path=origin_lora_path,
                    save_path=lora_dir_path,
                )
                dir_path_in_repo = os.path.dirname(file)
                upload_file = (
                    lora_without_norm_block_path
                    if lora_without_norm_block_path is not None
                    else origin_lora_path
                )
                with TemporaryDirectory() as upload_dir:
                    shutil.copy2(upload_file, Path(upload_dir) / upload_file.name)
                    repo_manager.upload_files_to_repo(
                        api_type="huggingface",
                        repo_id=dst_repo_id,
                        repo_type=dst_repo_type,
                        upload_path=Path(upload_dir),
                        path_in_repo=dir_path_in_repo or None,
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
