import os
import argparse
from pathlib import Path
from typing import Union



def get_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将文件保存为 UTF8 BOM 编码")
    normalized_filepath = lambda filepath: str(Path(filepath).absolute().as_posix())

    parser.add_argument('path', type=normalized_filepath, help="要保存为 UTF8 BOM 编码的文件路径")

    return parser.parse_args()


def has_utf8_bom(file_path: Union[str, Path]) -> bool:
    with open(file_path, "rb") as file:
        bom = file.read(3)
        return bom == b"\xef\xbb\xbf"


def save_as_utf8_with_bom(input_file: Union[str, Path]) -> None:
    if has_utf8_bom(input_file):
        read_encode = "utf-8-sig"
    else:
        read_encode = "utf-8"

    with open(input_file, "r", encoding=read_encode) as file:
        content = file.read()

    content = content.replace("\r\n", "\n")

    with open(input_file, "w", encoding="utf-8-sig", newline="\n") as file:
        file.write(content)


if __name__ == "__main__":
    args = get_args()

    file = args.path

    if os.path.isfile(file):
        save_as_utf8_with_bom(file)
    else:
        print(f"{file} 不是文件或者文件路径错误")
