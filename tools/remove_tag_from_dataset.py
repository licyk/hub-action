import os
import argparse
from pathlib import Path
from tqdm import tqdm


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    normalized_filepath = lambda filepath: str(Path(filepath).absolute().as_posix())

    parser.add_argument("--path", type = normalized_filepath, default=None, help="数据集路径")
    parser.add_argument("--tag", nargs='+', type=str, default=[], help="要移除的 Tag, 若有多个 Tag, 则使用空格进行分割, 并且每个 Tag 用双引号括起来")

    return parser.parse_args()


def get_all_file(directory: str) -> list:
    file_list = []
    for dirname, _, filenames in os.walk(directory):
        for filename in filenames:
            file_list.append(Path(os.path.join(dirname, filename)).as_posix())
    return file_list


def fitter_caption(file_list: list) -> list:
    caption_list = []
    for file in file_list:
        if file.endswith(".txt"):
            caption_list.append(file)
    return caption_list


def read_cation_file(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as file:
            return [x.strip() for x in file.read().split(",")]
    except Exception as e:
        print(f"读取文件时出现错误, {e}")


def remove_tag(content: list, tag: str) -> list:
    tag_list = []
    for t in content:
        if t != tag:
            tag_list.append(t)
    return tag_list


def write_tag_to_file(path: str, tag_list: list) -> None:
    tag_content = ", ".join(tag_list)
    try:
        with open(path, "w", encoding="utf-8") as file:
            file.write(tag_content)
    except Exception as e:
        print(f"写入数据到 {path} 时出现错误, {e}")


def remove_tag_from_dataset(path: str, tag: str) -> None:
    file_list = get_all_file(path)
    caption_list = fitter_caption(file_list)
    for caption in tqdm(caption_list, desc=f"移除 {tag}"):
        tag_list = read_cation_file(caption)
        tag_list = remove_tag(tag_list, tag)
        write_tag_to_file(caption, tag_list)
    print(f"从 {path} 训练集删除 {tag} 提示词完成")


def main():
    args = get_args()
    dataset_path = args.path
    remove_tag_list = args.tag
    if not dataset_path:
        print("未使用 --path 指定数据集路径")
        return

    if not remove_tag_list:
        print("未使用 --tag 指定要移除的 Tag")
        return

    print(f"处理 {dataset_path} 中")
    for t in remove_tag_list:
        remove_tag_from_dataset(dataset_path, t)

    print(f"处理 {dataset_path} 完成")



if __name__ == "__main__":
    main()
