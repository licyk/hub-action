import os
import argparse
from pathlib import Path


def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    normalized_filepath = lambda filepath: str(Path(filepath).absolute().as_posix())

    parser.add_argument("--path", type=normalized_filepath, default=None, help="数据集的路径")
    parser.add_argument("--exts", type=list, default=[".png", ".jpg", ".jpeg", ".webp", ".bmp"], help='图片的扩展名列表: [".png", ".jpg", ".jpeg", ".webp", ".bmp"]')
    parser.add_argument("--full-path", action="store_true", help="显示完整路径")

    return parser.parse_args()


def get_all_file(directory: str) -> list:
    file_list = []
    if not os.path.exists(directory):
        return file_list
    for dirname, _, filenames in os.walk(directory):
        for filename in filenames:
            file_list.append(Path(os.path.join(dirname, filename)).as_posix())
    return file_list


def fitter_image(file_list: list, img_exts: list) -> list:
    img_list = []
    img_exts = tuple(img_exts)
    for img in file_list:
        if img.endswith(img_exts):
            img_list.append(img)
    return img_list


def fitter_caption(file_list: list) -> list:
    caption_list = []
    for file in file_list:
        if file.endswith(".txt"):
            caption_list.append(file)
    return caption_list


def is_image_not_caption(file_path: str) -> bool:
    dir_path = os.path.dirname(file_path)
    file_name = os.path.basename(file_path)
    file_name_without_ext = os.path.splitext(file_name)[0]
    caption_path = os.path.join(dir_path, f"{file_name_without_ext}.txt")
    return not os.path.exists(caption_path)


def is_isolation_caption(file_path: str, img_exts: list) -> bool:
    dir_path = os.path.dirname(file_path)
    file_name = os.path.basename(file_path)
    file_name_without_ext = os.path.splitext(file_name)[0]
    base_path = os.path.join(dir_path, file_name_without_ext)
    for ext in img_exts:
        if os.path.exists(f"{base_path}{ext}"):
            return False
    return True


def main() -> None:
    args = get_args()
    dataset_path = args.path
    img_exts = args.exts
    full_path = args.full_path

    print("帮助信息可通过 --help 进行查看")

    if not dataset_path:
        print("未通过 --path 参数指定数据集路径, 将使用当前路径作为训练集路径")
        dataset_path = os.getcwd()

    print("检查训练集中")
    file_list = get_all_file(dataset_path)
    img = fitter_image(file_list, img_exts)
    caption = fitter_caption(file_list)
    no_caption_img = []
    isolation_caption = []

    print(f"数据集路径: {dataset_path}")
    print(f"指定的图片格式: {img_exts}")
    print(f"数据集目录的文件数量: {len(file_list)}")
    print(f"图片数量: {len(img)}")
    print(f"打标文件数量: {len(caption)}")

    for file in img:
        if is_image_not_caption(file):
            no_caption_img.append(file)

    for file in caption:
        if is_isolation_caption(file, img_exts):
            isolation_caption.append(file)

    if len(no_caption_img) > 0:
        print("\n缺少打标的图片:")
        for file in no_caption_img:
            print(f"- {file if full_path else os.path.basename(file)}")
        print()

    if len(isolation_caption) > 0:
        print("\n孤立的打标文件:")
        for file in isolation_caption:
            print(f"- {file if full_path else os.path.basename(file)}")
        print()

    print("训练集检查完成")



if __name__ == "__main__":
    main()
