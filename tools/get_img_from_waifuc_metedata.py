import os
import sys
import json
import copy
import time
import hashlib
import logging
import argparse
import requests
import threading
import datetime
from queue import Queue
from tqdm import tqdm
from pathlib import Path
from urllib.parse import urlparse
from typing import Union



class ColoredFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[0;36m",  # CYAN
        "INFO": "\033[0;32m",  # GREEN
        "WARNING": "\033[0;33m",  # YELLOW
        "ERROR": "\033[0;31m",  # RED
        "CRITICAL": "\033[0;37;41m",  # WHITE ON RED
        "RESET": "\033[0m",  # RESET COLOR
    }

    def format(self, record):
        colored_record = copy.copy(record)
        levelname = colored_record.levelname
        seq = self.COLORS.get(levelname, self.COLORS["RESET"])
        colored_record.levelname = f"{seq}{levelname}{self.COLORS['RESET']}"
        return super().format(colored_record)


def load_file_from_url(
    url: str,
    *,
    model_dir: str,
    progress: bool = True,
    file_name: str | None = None,
    hash_prefix: str | None = None,
    re_download: bool = False,
) -> str:
    """Download a file from `url` into `model_dir`, using the file present if possible.
    Returns the path to the downloaded file.
    file_name: if specified, it will be used as the filename, otherwise the filename will be extracted from the url.
        file is downloaded to {file_name}.tmp then moved to the final location after download is complete.
    hash_prefix: sha256 hex string, if provided, the hash of the downloaded file will be checked against this prefix.
        if the hash does not match, the temporary file is deleted and a ValueError is raised.
    re_download: forcibly re-download the file even if it already exists.
    """

    if not file_name:
        parts = urlparse(url)
        file_name = os.path.basename(parts.path)

    cached_file = os.path.abspath(os.path.join(model_dir, file_name))

    if re_download or not os.path.exists(cached_file):
        os.makedirs(model_dir, exist_ok=True)
        temp_file = os.path.join(model_dir, f"{file_name}.tmp")
        # logger.info(f'下载文件中: "{url}" 到 {cached_file}')
        response = requests.get(url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        with tqdm(total=total_size, unit='B', unit_scale=True, desc=file_name, disable=not progress) as progress_bar:
            with open(temp_file, 'wb') as file:
                for chunk in response.iter_content(chunk_size=1024):
                    if chunk:
                        file.write(chunk)
                        progress_bar.update(len(chunk))

        if hash_prefix and not compare_sha256(temp_file, hash_prefix):
            logger.error(f"{temp_file} 的哈希值不匹配, 正在删除临时文件")
            os.remove(temp_file)
            raise ValueError(f"文件哈希值与预期的哈希前缀不匹配: {hash_prefix}")

        os.rename(temp_file, cached_file)
    return cached_file


def compare_sha256(file_path: str, hash_prefix: str) -> bool:
    """Check if the SHA256 hash of the file matches the given prefix."""

    hash_sha256 = hashlib.sha256()
    blksize = 1024 * 1024

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(blksize), b""):
            hash_sha256.update(chunk)
    return hash_sha256.hexdigest().startswith(hash_prefix.strip().lower())


def download_image(url, path, tags = None):
    try:
        load_file_from_url(url, model_dir=path)
        name = os.path.splitext(os.path.basename(urlparse(url).path))[0]
        tag_path = os.path.join(path, f"{name}.txt")
        if tags:
            tag_string = ', '.join(tags.split()).replace("_", " ")
            try:
                with open(tag_path, "w") as f:
                    f.write(tag_string)
            except Exception as e:
                logger.error(f"写入 {name}.txt 时出现错误", e)
    except Exception as e:
        logger.error(f"下载 {url} 出现错误", e)


class ModelDownload:
    def __init__(self, urls) -> None:
        self.urls = urls
        self.queue = Queue()
        self.total_urls = len(urls)  # 记录总的URL数
        self.downloaded_count = 0  # 记录已下载的数量
        self.lock = threading.Lock()  # 创建锁以保护对下载计数器的访问


    def worker(self):
        while True:
            url = self.queue.get()
            if url is None:
                break
            download_image(url[0], url[1], url[2])
            self.queue.task_done()
            with self.lock:  # 访问共享资源时加锁
                self.downloaded_count += 1
                self.print_progress()  # 打印进度


    def print_progress(self) -> None:
        """进度条显示"""
        progress = (self.downloaded_count / self.total_urls) * 100
        current_time = datetime.datetime.now()
        time_interval = current_time - self.start_time
        hours = time_interval.seconds // 3600
        minutes = (time_interval.seconds // 60) % 60
        seconds = time_interval.seconds % 60
        formatted_time = f"{hours:02}:{minutes:02}:{seconds:02}"

        if self.downloaded_count > 0:
            speed = self.downloaded_count / time_interval.total_seconds()
        else:
            speed = 0

        remaining_urls = self.total_urls - self.downloaded_count

        if speed > 0:
            estimated_remaining_time_seconds = remaining_urls / speed
            estimated_remaining_time = datetime.timedelta(seconds=estimated_remaining_time_seconds)
            estimated_hours = estimated_remaining_time.seconds // 3600
            estimated_minutes = (estimated_remaining_time.seconds // 60) % 60
            estimated_seconds = estimated_remaining_time.seconds % 60
            formatted_estimated_time = f"{estimated_hours:02}:{estimated_minutes:02}:{estimated_seconds:02}"
        else:
            formatted_estimated_time = "N/A"

        logger.info(f"下载进度: {progress:.2f}% | {self.downloaded_count}/{self.total_urls} [{formatted_time}<{formatted_estimated_time}, {speed:.2f}it/s]")


    def start_threads(self, num_threads=16):
        threads = []
        self.start_time = datetime.datetime.now()
        time.sleep(0.1)
        for _ in range(num_threads):
            thread = threading.Thread(target=self.worker)
            thread.start()
            threads.append(thread)

        for url in self.urls:
            self.queue.put(url)

        self.queue.join()

        for _ in range(num_threads):
            self.queue.put(None)

        for thread in threads:
            thread.join()


def get_logger() -> logging.Logger:
    logger = logging.getLogger("Waifuc-JSON-Manager")
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            ColoredFormatter(
                "[%(name)s]-|%(asctime)s|-%(levelname)s: %(message)s", "%H:%M:%S"
            )
        )
        logger.addHandler(handler)

    logger.setLevel(logging.INFO)
    logger.debug("Logger initialized.")

    return logger



logger = get_logger()


def get_args():
    parser = argparse.ArgumentParser(description="一个从 Waifuc 爬取的数据中获取原图链接并下载的工具")
    normalized_filepath = lambda filepath: str(Path(filepath).absolute().as_posix())

    parser.add_argument("--path", type = normalized_filepath, default = None, help = "包含 JSON 文件的路径 (Waifuc 爬取数据保存的路径)")
    parser.add_argument("--dl-path", type = normalized_filepath, default = None, help = "下载图片路径")
    parser.add_argument("--no-caption", action='store_true', help="不保存原 JSON 数据中的标签")
    parser.add_argument("--thread", type = int, default = None, help = "下载线程")

    return parser.parse_args()


def get_all_file(directory: str) -> list:
    file_list = []
    for dirname, _, filenames in os.walk(directory):
        for filename in filenames:
            file_list.append(Path(os.path.join(dirname, filename)).as_posix())
    return file_list


def fitter_json_file(file_list: list) -> list:
    fitter_list = []
    for file in file_list:
        if file.endswith(".json"):
            fitter_list.append(file)

    return fitter_list


def get_json_file_key(file_list: list) -> list:
    metadata = []
    tag_string = ""
    for file in file_list:
        try:
            with open(file, "r", encoding="utf8") as f:
                data = json.load(f)
                first_key = list(data.keys())[0]
                url = data.get(first_key).get("file_url")
                tag_string = data.get(first_key).get("tag_string")
                metadata.append([url, tag_string])
        except Exception:
            logger.error(f"{file} 文件损坏, 无法读取")
    
    return metadata


def get_user_input() -> Union[str, str, int, bool]:
    logger.warning("检测到当前命令行未输入 Waifuc 爬取数据保存的路径或者下载图片路径, 进入手动输入参数模式")
    path = Path(input("Waifuc 爬取数据路径 (如: D:/Downloads/waifuc): ")).as_posix()
    dl_path = Path(input("下载图片路径 (如: D:/Downloads/img): ")).as_posix()
    thread = int(input("下载线程 (如: 16): "))
    caption = input("是否导出 Waifuc 数据中的标签 (通常建议导出, 输入 yes 确认): ")
    if caption == "yes" \
        or caption == "y" \
        or caption == "YES" \
        or caption == "Y":
        no_caption = False
    else:
        no_caption = True
    return path, dl_path, thread, no_caption


def main() -> None:
    arg = get_args()

    if not arg.path or not arg.dl_path:
        path, dl_path, num_thread, no_caption = get_user_input()
    else:
        path = arg.path
        dl_path = arg.dl_path
        num_thread = arg.thread if arg.thread else 16
        no_caption = arg.no_caption

    # 读取 metadata 的 file_url 值
    file_list = get_all_file(path)
    file_list = fitter_json_file(file_list)
    metadata_list = get_json_file_key(file_list)

    # 创建下载任务
    task = []
    for metadata in metadata_list:
        url = metadata[0]
        tag_string = None if no_caption else metadata[1]
        task.append([url, dl_path, tag_string])

    # 下载文件
    logger.info(f"即将下载的文件数量: {len(task)}")
    logger.info(f"将下载数据集到 {dl_path} 中, 下载线程数: {num_thread}")
    logger.info(f"下载线程数: {num_thread}")
    logger.info(f"导出标签: {False if no_caption else True}")
    model_downloader = ModelDownload(task)
    model_downloader.start_threads(num_threads=num_thread)
    logger.info(f"下载数据集完成, 路径: {dl_path}")


if __name__ == "__main__":
    main()
