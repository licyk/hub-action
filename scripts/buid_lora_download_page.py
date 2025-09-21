import re
import os
import json
import html
from urllib.parse import urljoin
from pathlib import Path
from dataclasses import dataclass
import requests
from bs4 import BeautifulSoup


@dataclass
class VersionInfo:
    """模型版本信息"""

    model_name: str  # 模型名称
    model_link: str  # 模型链接
    version: str  # 版本号
    download_link: str  # 下载链接


@dataclass
class ModelCard:
    """LoRA 模型卡片信息"""

    model_title: str  # 模型标题
    preview_img_url: str  # 预览图 URL
    trigger_words: str  # 触发词
    versions_info: list[VersionInfo]  # 模型版本信息列表


LoRAModelCards = dict[str, ModelCard]


def fetch_webpage_content(
    url: str, timeout: int | None = 10, headers: dict[str, str] = None
) -> str:
    """
    使用 requests 获取网页内容

    :param url`(str)`: 网页 URL
    :param timeout`(int|None)`: 超时时间 (秒)
    :param headers`(dict[str,str])`: 请求头部
    :return `str`: 网页内容字符串
    :raises `requests.RequestException`: 当请求失败时抛出异常
    """
    if headers is None:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()  # 如果响应状态码不是 200 会抛出异常
        response.encoding = "utf-8"
        return response.text
    except requests.RequestException as e:
        raise Exception(f"获取网页内容时发生错误: {e}")


def get_lora_model_info(html_content: str, base_url: str) -> LoRAModelCards:
    """解析 LoRA 模型页面内容

    :param html_content`(str)`: HTML 字符串
    :param base_url`(str)`: 模型预览图的根链接
    :return `LoRAModelCards`: LoRA 模型卡片列表
    """
    soup = BeautifulSoup(html_content, "html.parser")
    lora_model_cards: LoRAModelCards = {}

    # 遍历每个 table
    for table in soup.find_all("table"):
        model_title = None
        preview_img_url = None
        trigger_words = None
        versions_info = []

        # 查找 LoRA 行, 获取模型名称
        for row in table.find_all("tr"):
            cells = row.find_all("th")
            if len(cells) >= 2:
                header = cells[0].get_text(strip=True)
                if header == "LoRA":
                    # 获取 LoRA 右边的名称
                    lora_cell = cells[1]
                    # 如果单元格内有链接, 提取链接文本
                    link = lora_cell.find("a")
                    if link:
                        model_title = link.get_text(strip=True)
                    else:
                        # 否则直接获取文本
                        model_title = lora_cell.get_text(strip=True)
                    break

        if model_title is None:
            continue

        # 找到"预览图"行
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                header = cells[0].get_text(strip=True)
                if header == "预览图":
                    img_tag = cells[1].find("img")
                    if img_tag and "src" in img_tag.attrs:
                        preview_img_url = urljoin(base_url, img_tag["src"])
                        break

        # 找到"触发词"行
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                header = cells[0].get_text(strip=True)
                if header == "触发词":
                    # 获取触发词内容
                    trigger_cell = cells[1]
                    # 提取文本内容, 保留 <br> 标签表示的换行
                    trigger_words = ""
                    for content in trigger_cell.contents:
                        if isinstance(content, str):
                            # 文本节点
                            trigger_words += content
                        elif content.name == "br":
                            # 换行标签
                            trigger_words += "\n"
                        else:
                            # 其他标签，提取文本
                            trigger_words += content.get_text()
                    # 清理首尾空白字符
                    trigger_words = trigger_words.strip()
                    # 将 HTML 实体转换为正常字符
                    trigger_words = html.unescape(trigger_words)
                    break

        # 查找"版本"行
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                header = cells[0].get_text(strip=True)
                if header == "版本":
                    # 提取版本信息
                    version_cell = cells[1]

                    # 获取单元格的文本内容
                    content = version_cell.decode_contents()

                    # 按逗号分割不同的模型版本
                    model_parts = re.split(r"\s*,\s*", content)

                    for part in model_parts:
                        # 对于每个部分，提取模型名称和版本链接
                        # 格式: <a>模型名</a> (<a>版本号</a>)
                        # 提取模型链接和名称
                        model_match = re.search(
                            r'<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', part
                        )
                        # 提取版本链接和版本号 (在括号内)
                        version_match = re.search(
                            r'\(\s*<a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>\s*\)', part
                        )

                        if model_match and version_match:
                            model_href, model_name = model_match.groups()
                            version_href, version_name = version_match.groups()

                            versions_info.append(
                                {
                                    "model_name": model_name.strip(),
                                    "model_link": model_href,  # 模型名称对应的链接
                                    "version": version_name.strip(),
                                    "download_link": version_href,  # 版本号对应的链接
                                }
                            )

        lora_model_cards[model_title] = {
            "model_title": model_title,
            "preview_img_url": preview_img_url,
            "trigger_words": trigger_words,
            "versions_info": versions_info,
        }

    return lora_model_cards


def save_list_to_json(save_path: Path | str, origin_list: list) -> bool:
    """保存列表到 Json 文件中

    :param save_path`(Path,str)`: 保存 Json 文件的路径
    :param origin_list`(list)`: 要保存的列表
    :return `bool`: 当文件保存成功时返回`True`
    """
    dir_path = os.path.dirname(save_path)

    if not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)

    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(
                origin_list, f, ensure_ascii=False, indent=4, separators=(",", ": ")
            )
        print(f"保存 Json 文件到 {save_path}")
        return True
    except Exception as e:
        print(f"保存列表到 {save_path} 时发生错误: {e}")
        return False


def main() -> None:
    """主函数"""
    base_url = os.getenv("BASE_URL", "https://licyk.netlify.app")
    lora_model_url = os.getenv(
        "LORA_MODEL_URL", "https://licyk.netlify.app/2024/10/05/my-sd-model-list"
    )
    root_path = os.getenv("ROOT_PATH", os.getcwd())
    root_path = Path(root_path)

    lora_page = fetch_webpage_content(lora_model_url)
    lora_info = get_lora_model_info(
        html_content=lora_page,
        base_url=base_url,
    )
    save_list_to_json(
        save_path=root_path / "lora_list.json",
        origin_list=lora_info,
    )


if __name__ == "__main__":
    main()
