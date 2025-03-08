import os
import argparse
from pathlib import Path
from safetensors.torch import load_file, save_file



def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    normalized_filepath = lambda filepath: str(Path(filepath).absolute().as_posix())

    parser.add_argument("lora_path", type = normalized_filepath, default = None, help = "要移除 Norm 块的 LoRA 模型路径")

    return parser.parse_args()


def main() -> None:
    args = get_args()

    if args.lora_path:
        lora_path = args.lora_path
    else:
        print("缺少 LoRA 模型的路径")
        return

    save_path = os.path.join(
        os.path.dirname(lora_path),
        os.path.splitext(os.path.basename(lora_path))[0] + "_without_norm_block.safetensors"
    )
    norm_block_list = []

    print(f"加载模型: {lora_path}")
    model_weights = load_file(lora_path)

    print(f"LoRA 块的数量: {len(model_weights.items())}")
    for block, _ in model_weights.items():
        if "norm" in block:
            norm_block_list.append(block)

    if len(norm_block_list) > 0:
        print(f"Norm 块的数量: {len(norm_block_list)}")
        print(f"移除 Norm 块中")
        for block in norm_block_list:
            del model_weights[block]

        save_file(model_weights, save_path)
        print(f"移除完成, 保存 LoRA 模型到 {save_path}")
    else:
        print("LoRA 模型中未包含 Norm 块, 无需移除")


if __name__ == "__main__":
    main()
