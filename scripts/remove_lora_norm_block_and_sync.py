import os
import traceback
from pathlib import Path
from typing import Literal
from tempfile import TemporaryDirectory
from safetensors.torch import load_file, save_file
from huggingface_hub import HfApi
from tqdm import tqdm


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
    try:
        return api.list_repo_files(
            repo_id=repo_id,
            repo_type=repo_type,
        )
    except (ValueError, ConnectionError, TypeError) as e:
        print(f"获取 {repo_id} (类型: {repo_type}) 仓库的文件列表出现错误: {e}")
        return []


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
                    lora_without_norm_block_path.unlink()
            except Exception as e:
                traceback.print_exc()
                print(f"[{count}/{task_sum}] 处理 LoRA 文件时发生错误: {e}")

    print(f"[{count}/{task_sum}] LoRA 文件处理完成")


if __name__ == "__main__":
    main()
