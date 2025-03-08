import argparse
from pathlib import Path
from waifuc.action import (
    HeadCountAction,
    NoMonochromeAction, 
    FilterSimilarAction,
    TaggingAction, 
    PersonSplitAction,
    FaceCountAction,
    FirstNSelectAction,
    CCIPAction,
    ModeConvertAction,
    ClassFilterAction,
    RandomFilenameAction,
    AlignMinSizeAction
)
from waifuc.export import SaveExporter
from waifuc.source import YandeSource



def get_args():
    parser = argparse.ArgumentParser()
    normalized_filepath = lambda filepath: str(Path(filepath).absolute().as_posix())

    parser.add_argument("--path", type = normalized_filepath, default = None, help = "爬取文件的保存路径")
    # parser.add_argument("--dl-path", type = normalized_filepath, default = None, help = "下载图片路径")
    parser.add_argument("--tag", type=str, nargs='+', help="爬取的图片标签")
    parser.add_argument("--count", type = int, default = 1000, help = "爬取的图片数量")

    return parser.parse_args()


def main():
    arg = get_args()

    if arg.path is None:
        print("未输入保存路径")
        return
    else:
        path = arg.path

    if arg.tag is None:
        print("未输入爬取的图片标签")
        return
    else:
        tag = arg.tag

    count = int(arg.count)

    print(f"爬取的 Tag: {tag}")
    print(f"爬取的图片数量: {count}")
    print(f"保存路径: {path}")

    source = YandeSource(tag)
    source.attach(
        HeadCountAction(1), # only 1 head,
        ModeConvertAction('RGB', 'white'), # 以RGB色彩模式加载图像并将透明背景替换为白色背景
        ClassFilterAction(['illustration', 'bangumi']),  # 丢弃漫画或3D图像
        FilterSimilarAction('all'),  # 丢弃相似或重复的图像
    )[:count].export(
        SaveExporter(path)
    )


if __name__ == '__main__':
    main()
