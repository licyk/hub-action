#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_danbooru_tags.py —— Danbooru tag 同步 / 翻译工具

读取 Hanatsumi 抓取的全量 Danbooru tag（``Hanatsumi/data/tags.csv``），合并进
``tag++`` 格式文件（无表头，4 列）::

    tag名,分类,帖子数,中文翻译

行为说明：

* **依赖与进度**：启动时通过 importlib 检测 requests / tqdm，缺失时自动调用
  当前解释器的 ``-m pip install``；使用 translators 后端时同时检查该库。
  批处理使用 tqdm 显示进度，``-q`` 隐藏进度条。流式 CSV 显示已处理行数与速度。
* **合并**（``sync``）：已存在的 tag 用 Danbooru 最新值覆盖 分类 / 帖子数（中文翻译
  原样保留），Danbooru 里新增的 tag 追加到文件末尾（按帖子数从多到少排序），翻译列先留空。
* **废弃 tag**：``is_deprecated=true`` 的 tag 不会被新增；已写进文件的废弃 tag
  默认保留，加 ``--prune`` 后与“Danbooru 里已经不存在”的 tag 一起被移除。
* **翻译**（``translate``）：把翻译列为空的 tag 交给翻译 API 补齐，结果写进
  ``<输出文件>.translations.json`` 缓存，中断后重跑可续传。

翻译 API 层参考 sd-webui-prompt-all-in-one 实现（结构硬编码，不依赖其仓库）：

* API 目录 ``APIS`` —— 每个后端带 ``concurrent`` 并发数、``support`` 语言映射
  （``en_US`` / ``zh_CN`` locale 码 → 各家代码）、所需 ``config`` 密钥，
  与它的 ``translate_apis.json`` 对应。
* ``BaseTranslator`` —— ``translate()`` 单条 + ``translate_batch()`` 分组并发、
  组间 sleep，与其 ``base_tanslator.py`` 同款。
* 统一入口 ``make_translator(api, config)`` —— 与它的 ``translate()`` 一样先查
  API 目录再建 translator、做语言映射。
* 支持后端：``myMemory_free``（内置 requests，免密钥）、``*_free`` 系列
  （translators 库，免密钥）、``google`` / ``deepl`` / ``microsoft``（ApiKey）、
  ``openai``（LLM 批量 JSON）。
* 批量策略：openai 走批量 JSON（每批 N 条一次请求）；其余后端按原版
  分组并发单条翻译、组间 sleep（``concurrent=1`` 的后端强制串行）。

命令行结构参考 sd-webui-all-in-one 的 ``cli_manager``：薄入口 ``main()`` 只负责
解析 + 分发（``args.func(args)``），每个功能各自提供 ``register_xxx(subparsers)``
注册自己的子命令，注册里只做参数声明与 ``set_defaults(func=...)`` 绑定，不做业务逻辑；
实际工作由 ``sync_cli()`` / ``translate_cli()`` / ``update_cli()`` / ``list_apis_cli()``
这几个薄封装完成，它们只负责把 ``args`` 摊开传给 core 函数。

子命令与用法示例::

    # sync —— 只同步（路径参数全部必填，不写死默认值）
    python3 sync_danbooru_tags.py sync --hanatsumi ../Hanatsumi/data/tags.csv --input tag++.csv
    python3 sync_danbooru_tags.py sync --hanatsumi ... --input tag++.csv --prune
    python3 sync_danbooru_tags.py sync --hanatsumi ... --input tag++.csv --output new.csv --dry-run

    # translate —— 只补翻译（不碰同步）
    python3 sync_danbooru_tags.py translate --input tag++.csv --translate-api myMemory_free
    python3 sync_danbooru_tags.py translate --input tag++.csv --translate-api google --api-key XXX
    python3 sync_danbooru_tags.py translate --input tag++.csv --translate-api openai --api-config model=gpt-4o-mini

    # update —— sync + translate 一次跑完
    python3 sync_danbooru_tags.py update --hanatsumi ... --input tag++.csv --translate-api myMemory_free

    # list-apis —— 列出可用翻译后端
    python3 sync_danbooru_tags.py list-apis
"""

from __future__ import annotations

import argparse
import csv
import importlib
import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from math import ceil
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # main 启动时检测并自动安装，导入模块时不触发 pip
    requests = None

SCRIPT_DIR = Path(__file__).resolve().parent
_tqdm: Any = None
_PROGRESS_ENABLED = True


def ensure_dependencies(*modules: str) -> None:
    """用 importlib 检测依赖，缺失时交给当前解释器的 pip 安装。"""
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    if not missing:
        return
    log.info("缺少第三方依赖 %s，正在自动安装", ", ".join(missing))
    command = [sys.executable, "-m", "pip", "install", *missing]
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        log.error("自动安装依赖失败：%s；请使用当前 Python 手动执行 -m pip install %s",
                  exc, " ".join(missing))
        raise SystemExit(1) from exc
    importlib.invalidate_caches()
    unavailable = [name for name in missing if importlib.util.find_spec(name) is None]
    if unavailable:
        log.error("安装后仍无法找到依赖：%s", ", ".join(unavailable))
        raise SystemExit(1)


def initialize_dependencies(args: argparse.Namespace) -> None:
    """启动时安装基础依赖，以及本次主/备用翻译后端需要的可选依赖。"""
    global requests, _tqdm
    modules = ["requests", "tqdm"]
    if args.main_command in {"translate", "update"} and not args.dry_run:
        for api in (args.translate_api, args.fallback_api):
            if APIS.get(api, {}).get("type") == "translators":
                modules.append("translators")
                break
    ensure_dependencies(*modules)
    requests = importlib.import_module("requests")
    _tqdm = importlib.import_module("tqdm").tqdm


def progress(iterable=None, *, desc: str, total: int | None = None,
             unit: str = "行", leave: bool = True):
    """统一进度条；流式 CSV 不预扫描，显示已处理行数和速度。"""
    global _tqdm
    if _tqdm is None:
        ensure_dependencies("tqdm")
        _tqdm = importlib.import_module("tqdm").tqdm
    return _tqdm(iterable, desc=desc, total=total, unit=unit, leave=leave,
                 dynamic_ncols=True, file=sys.stderr,
                 disable=not _PROGRESS_ENABLED)


def sort_rows(rows: list[list[str]], *, key, desc: str) -> None:
    """排序时显示排序键处理进度，结束后关闭进度条。"""
    with progress(desc=desc, total=len(rows)) as bar:
        def tracked_key(row):
            value = key(row)
            bar.update(1)
            return value
        rows.sort(key=tracked_key)


# tag++ 固定 4 列
COL_NAME, COL_CATEGORY, COL_COUNT, COL_ZH = 0, 1, 2, 3


# ===========================================================================
# 日志 —— 脚本内所有 print 统一走 logging
#
#   * ``DEBUG`` / ``INFO``  → stdout（可以正常重定向 / 管道）
#   * ``WARNING`` 及以上    → stderr
#   * ``HH:MM:SS LEVEL 消息``，级别按严重度着色；非 TTY 或 ``--no-color``
#     时自动退化成纯文本（同时认 ``NO_COLOR`` / ``FORCE_COLOR`` 环境变量）
#   * ``extra={"raw": True}`` 的记录只输出正文，给 ``list-apis`` 这类表格用，
#     避免时间戳 / 级别前缀破坏对齐
# ===========================================================================

LOGGER_NAME = "sync_tags"
log = logging.getLogger(LOGGER_NAME)

_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[2m"
# setup_logging 里决定的最终颜色开关，供 list_apis 这类“原样输出”复用
_COLOR_ENABLED = False
_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",  # 青
    logging.INFO: "\033[32m",  # 绿
    logging.WARNING: "\033[33m",  # 黄
    logging.ERROR: "\033[31m",  # 红
    logging.CRITICAL: "\033[1;31m",  # 亮红
}


class ColorFormatter(logging.Formatter):
    """``HH:MM:SS LEVEL 消息`` 格式，级别按严重度着色。"""

    def __init__(self, *, color: bool, datefmt: str = "%H:%M:%S") -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(message)s", datefmt=datefmt)
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        if getattr(record, "raw", False):  # 表格 / 原样输出
            return record.getMessage()
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        stamp = self.formatTime(record, self.datefmt)
        level = record.levelname.ljust(7)
        if not self.color:
            return f"{stamp} {level} {message}"
        color = _LEVEL_COLORS.get(record.levelno, "")
        return f"{_ANSI_DIM}{stamp}{_ANSI_RESET} {color}{level}{_ANSI_RESET} {message}"


class ProgressStreamHandler(logging.StreamHandler):
    """日志写出时暂时清除进度条，写完恢复，避免日志与进度条交错。"""

    def emit(self, record: logging.LogRecord) -> None:
        if _tqdm is None:
            super().emit(record)
        else:
            with _tqdm.external_write_mode(file=self.stream):
                super().emit(record)


class _MaxLevelFilter(logging.Filter):
    """只放行 <= 指定级别的记录：把 INFO/DEBUG 分到 stdout、警告以上分到 stderr。"""

    def __init__(self, max_level: int) -> None:
        super().__init__()
        self.max_level = max_level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.max_level


def _want_color(stream: Any, *, color: bool | None = None) -> bool:
    """判断某个流要不要上色：``--color/--no-color`` 优先，其次 ``NO_COLOR`` / ``FORCE_COLOR`` / isatty。"""
    if color is not None:
        return color
    if os.getenv("NO_COLOR"):
        return False
    if os.getenv("FORCE_COLOR"):
        return True
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError, ValueError):
        return False


def setup_logging(*, level: int = logging.INFO, color: bool | None = None) -> None:
    """统一日志出口（可重复调用，会先清掉上次的 handler）。

    Args:
        level: 最低可见级别，``-v`` 给 DEBUG、``-q`` 给 WARNING
        color: None = 按是否为终端自动判断；True / False = 强制开 / 关
    """
    global _COLOR_ENABLED, _PROGRESS_ENABLED
    _PROGRESS_ENABLED = level < logging.WARNING

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG)  # 级别过滤交给 handler，好让 stdout/stderr 各自分流

    out_color = _want_color(sys.stdout, color=color)
    _COLOR_ENABLED = out_color

    out = ProgressStreamHandler(sys.stdout)
    out.setLevel(level)
    out.addFilter(_MaxLevelFilter(logging.INFO))
    out.setFormatter(ColorFormatter(color=out_color))

    err = ProgressStreamHandler(sys.stderr)
    err.setLevel(max(level, logging.WARNING))
    err.setFormatter(ColorFormatter(color=_want_color(sys.stderr, color=color)))

    root.addHandler(out)
    root.addHandler(err)
    # 这两个库的连接日志即使 -v 也过于冗长，固定压到 WARNING
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def _mask_config(config: dict[str, Any]) -> dict[str, Any]:
    """调试输出时把密钥打码。"""
    return {k: ("***" if ("key" in k.lower() or "token" in k.lower() or "secret" in k.lower()) else v)
            for k, v in config.items()}


# ===========================================================================
# 翻译 API 目录（参考 sd-webui-prompt-all-in-one 的 translate_apis.json 硬编码）
#
#   key           --translate-api 用的名字
#   name          展示名（含官方限额提示）
#   type          translators=需 translators 库 / builtin=内置实现 /
#                 apikey=ApiKey 型 / llm=OpenAI 兼容批量
#   concurrent    原版并发数；1 表示该后端必须串行
#   support       locale 码 → 该后端的代码（只收录本脚本需要的几个语言）
#   config        需要的配置项，'*' 结尾表示可选
#   env           对应的默认环境变量名
# ===========================================================================

APIS: dict[str, dict[str, Any]] = {
    # ---- 免密钥：内置实现 ----
    "myMemory_free": {
        "name": "[Free] [builtin] MyMemory [5,000 chars/day]",
        "type": "builtin",
        "concurrent": 999,
        "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-Hans", "zh_TW": "zh-Hant"},
        "config": ["api_key*"],
        "env": {"api_key": "MYMEMORY_API_KEY"},
    },
    # ---- 免密钥：translators 库（pip install translators）----
    "google_free": {"name": "[Free] [translators] Google", "type": "translators", "translator": "google", "concurrent": 999,
                    "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-CN", "zh_TW": "zh-TW"}, "config": ["region*"], "env": {}},
    "bing_free": {"name": "[Free] [translators] Microsoft Bing", "type": "translators", "translator": "bing", "concurrent": 999,
                  "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-Hans", "zh_TW": "zh-Hant"}, "config": ["region*"], "env": {}},
    "alibaba_free": {"name": "[Free] [translators] Alibaba / 阿里翻译", "type": "translators", "translator": "alibaba", "concurrent": 999,
                     "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh", "zh_TW": "cht"}, "config": [], "env": {}},
    "baidu_free": {"name": "[Free] [translators] Baidu / 百度翻译", "type": "translators", "translator": "baidu", "concurrent": 1,
                   "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh", "zh_TW": "cht"}, "config": [], "env": {}},
    "sogou_free": {"name": "[Free] [translators] Sogou / 搜狗翻译", "type": "translators", "translator": "sogou", "concurrent": 999,
                   "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-CHS", "zh_TW": "zh-CHT"}, "config": [], "env": {}},
    "youdao_free": {"name": "[Free] [translators] Youdao / 有道翻译", "type": "translators", "translator": "youdao", "concurrent": 1,
                    "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-CHS", "zh_TW": "zh-CHT"}, "config": [], "env": {}},
    "qqTranSmart_free": {"name": "[Free] [translators] QQTranSmart / 腾讯交互翻译", "type": "translators", "translator": "qqTranSmart", "concurrent": 999,
                         "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh", "zh_TW": "cht"}, "config": [], "env": {}},
    "iciba_free": {"name": "[Free] [translators] Iciba / 爱词霸", "type": "translators", "translator": "iciba", "concurrent": 999,
                   "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh", "zh_TW": "cht"}, "config": [], "env": {}},
    "caiyun_free": {"name": "[Free] [translators] Caiyun / 彩云翻译", "type": "translators", "translator": "caiyun", "concurrent": 999,
                    "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh", "zh_TW": "zt"}, "config": [], "env": {}},
    "reverso_free": {"name": "[Free] [translators] Reverso", "type": "translators", "translator": "reverso", "concurrent": 999,
                     "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh", "zh_TW": "zt"}, "config": [], "env": {}},
    "papago_free": {"name": "[Free] [translators] Papago / Naver", "type": "translators", "translator": "papago", "concurrent": 999,
                    "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-CN", "zh_TW": "zh-TW"}, "config": [], "env": {}},
    "modernMt_free": {"name": "[Free] [translators] ModernMt / Translated", "type": "translators", "translator": "modernMt", "concurrent": 999,
                      "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh", "zh_TW": "zt"}, "config": [], "env": {}},
    "itranslate_free": {"name": "[Free] [translators] iTranslate", "type": "translators", "translator": "itranslate", "concurrent": 999,
                        "support": {"en_US": "en-US", "en_GB": "en-GB", "zh_CN": "zh-CN", "zh_TW": "zh-TW"}, "config": [], "env": {}},
    "lingvanex_free": {"name": "[Free] [translators] Lingvanex", "type": "translators", "translator": "lingvanex", "concurrent": 999,
                       "support": {"en_US": "en_GB", "en_GB": "en_GB", "zh_CN": "zh-Hans_CN", "zh_TW": "zh-Hant_TW"}, "config": [], "env": {}},
    "sysTran_free": {"name": "[Free] [translators] SysTran", "type": "translators", "translator": "sysTran", "concurrent": 999,
                     "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-Hans", "zh_TW": "zh-Hant"}, "config": [], "env": {}},
    "translateCom_free": {"name": "[Free] [translators] TranslateCom", "type": "translators", "translator": "translateCom", "concurrent": 999,
                          "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh", "zh_TW": "zt"}, "config": [], "env": {}},
    "translateMe_free": {"name": "[Free] [translators] TranslateMe / Neosus", "type": "translators", "translator": "translateMe", "concurrent": 999,
                         "support": {"en_US": "English", "en_GB": "English", "zh_CN": "Chinese", "zh_TW": "Chinese"}, "config": [], "env": {}},
    "cloudYi_free": {"name": "[Free] [translators] CloudTranslation / 深圳云译", "type": "translators", "translator": "cloudTranslation", "concurrent": 999,
                     "support": {"en_US": "en-us", "en_GB": "en-us", "zh_CN": "zh-cn", "zh_TW": "zh-tw"}, "config": [], "env": {}},
    # ---- ApiKey 型 ----
    "google": {
        "name": "[ApiKey] Google [500,000 chars/month]",
        "type": "apikey",
        "concurrent": 10,
        "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-CN", "zh_TW": "zh-TW"},
        "config": ["api_key"],
        "env": {"api_key": "GOOGLE_API_KEY"},
    },
    "deepl": {
        "name": "[ApiKey] DeepL [500,000 chars/month]",
        "type": "apikey",
        "concurrent": 999,
        "support": {"en_US": "EN", "en_GB": "EN", "zh_CN": "ZH", "zh_TW": "ZH-HANT"},
        "config": ["api_key"],
        "env": {"api_key": "DEEPL_API_KEY"},
    },
    "microsoft": {
        "name": "[ApiKey] Microsoft [2,000,000 chars/month]",
        "type": "apikey",
        "concurrent": 999,
        "support": {"en_US": "en", "en_GB": "en", "zh_CN": "zh-Hans", "zh_TW": "zh-Hant"},
        "config": ["api_key", "region"],
        "env": {"api_key": "MICROSOFT_API_KEY", "region": "MICROSOFT_REGION"},
        "defaults": {"region": "eastasia"},
    },
    # ---- LLM 批量 ----
    "openai": {
        "name": "OpenAI / ChatGPT（OpenAI 兼容接口，批量 JSON）",
        "type": "llm",
        "concurrent": 1,
        "support": {"en_US": "English", "en_GB": "English", "zh_CN": "Simplified Chinese", "zh_TW": "Traditional Chinese"},
        "config": ["api_key", "api_base", "model"],
        "env": {"api_key": "OPENAI_API_KEY", "api_base": "OPENAI_BASE_URL", "model": "AI_MODEL"},
        "defaults": {"api_base": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    },
}


class TranslateError(Exception):
    """翻译失败（可重试）。"""


class FatalTranslateError(Exception):
    """不值得重试的失败（缺密钥、语言不支持、依赖缺失等）。"""


# ===========================================================================
# BaseTranslator —— 参考 sd-webui-prompt-all-in-one scripts/physton_prompt/
# translator/base_tanslator.py
# ===========================================================================

class BaseTranslator(ABC):
    """翻译器基类：语言映射 + 分组批量（组间 sleep），与原版结构一致。"""

    from_lang: str | None = None
    to_lang: str | None = None
    api: str = ""
    api_item: dict[str, Any] = {}
    api_config: dict[str, Any] = {}

    def __init__(self, api: str, api_config: dict[str, Any] | None = None):
        if api not in APIS:
            raise FatalTranslateError(f"翻译API不支持: {api}（用 list-apis 子命令查看可用项）")
        self.api = api
        self.api_item = APIS[api]
        self.api_config = api_config or {}

    def set_from_lang(self, from_lang: str) -> "BaseTranslator":
        mapped = self.api_item["support"].get(from_lang)
        if not mapped:
            raise FatalTranslateError(f"{self.api} 不支持源语言 {from_lang}")
        self.from_lang = mapped
        return self

    def set_to_lang(self, to_lang: str) -> "BaseTranslator":
        mapped = self.api_item["support"].get(to_lang)
        if not mapped:
            raise FatalTranslateError(f"{self.api} 不支持目标语言 {to_lang}")
        self.to_lang = mapped
        return self

    @property
    def concurrent(self) -> int:
        return int(self.api_item.get("concurrent", 1))

    @abstractmethod
    def translate(self, text: str | list[str]) -> str | list[str]:
        """翻一条（或 llm 后端一次翻一批，传 list 返回 list）。"""

    def translate_batch(self, texts: list[str], *, retries: int = 3, interval: float = 1.0) -> list[str]:
        """单条模式的分组执行（非 llm 后端用）。

        组内并发 ``min(concurrent, 组大小)`` 条单条翻译，每组完成后 sleep ——
        与原版 translate_batch 的节奏一致；``concurrent=1`` 的后端
        （baidu_free / youdao_free）强制串行。
        """
        results: dict[int, str] = {}
        if self.concurrent == 1:
            # 串行后端（baidu_free / youdao_free）：逐条翻译，条间 sleep —— 与原版
            # concurrent=1 时 group_size=1 的节奏一致
            group_size = 1
        else:
            group_size = max(1, min(self.concurrent, len(texts)))
        workers = 1 if self.concurrent == 1 else group_size
        group_num = ceil(len(texts) / group_size)

        with progress(desc=f"{self.api} 单条翻译", total=len(texts), unit="tag", leave=False) as bar, ThreadPoolExecutor(max_workers=workers) as pool:
            for g in range(group_num):
                group = list(enumerate(texts[g * group_size : (g + 1) * group_size], start=g * group_size))
                futures = {pool.submit(self.translate_one, text, retries): idx for idx, text in group}
                for fut in as_completed(futures):
                    idx = futures[fut]
                    results[idx] = fut.result()
                    bar.update(1)
                if interval > 0 and g < group_num - 1:
                    time.sleep(interval)
        return [results[i] for i in range(len(texts))]

    def translate_one(self, text: str, retries: int = 3) -> str:
        """单条翻译，带指数退避重试（FatalTranslateError 不重试）。"""
        last_err: Exception | None = None
        for attempt in range(max(1, retries)):
            try:
                out = self.translate(text)
                if isinstance(out, list):  # 有的后端支持批量，单条传入仍返回 list
                    out = out[0] if out else ""
                return str(out).strip()
            except FatalTranslateError:
                raise
            except Exception as exc:  # noqa: BLE001 - 网络/限速/解析都可重试
                last_err = exc
                time.sleep(min(2**attempt, 15))
        raise TranslateError(str(last_err))


# ===========================================================================
# 各后端实现 —— 参考 prompt-all-in-one 的 translator/*.py（requests 直调）
# ===========================================================================

class MyMemoryFreeTranslator(BaseTranslator):
    """内置免密钥实现，参考 mymemory_translator.py（5,000 字符/天）。"""

    def translate(self, text: str | list[str]) -> str | list[str]:
        if isinstance(text, list):
            return [self.translate(t) for t in text]
        if not text:
            return ""
        params = {"q": text, "langpair": f"{self.from_lang}|{self.to_lang}"}
        api_key = self.api_config.get("api_key", "")
        if api_key:
            params["key"] = api_key
        try:
            resp = requests.get("https://api.mymemory.translated.net/get", params=params, timeout=30)
        except requests.RequestException as exc:
            raise TranslateError(f"MyMemory 请求失败: {exc}") from exc
        if resp.status_code != 200:
            raise TranslateError(f"MyMemory HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError as exc:
            raise TranslateError("MyMemory 返回内容错误") from exc
        if data.get("responseStatus") != 200:
            raise TranslateError(f"MyMemory: {data.get('responseDetails', 'no response')}")
        translated = (data.get("responseData") or {}).get("translatedText")
        if translated is None:
            raise TranslateError("MyMemory: 没有收到返回内容")
        return str(translated)


class TranslatorsTranslator(BaseTranslator):
    """translators 库（UlionTse）的免密钥后端，参考 translators_translator.py。"""

    def __init__(self, api: str, api_config: dict[str, Any] | None = None):
        super().__init__(api, api_config)
        self._ts: Any = None  # 延迟导入：--dry-run 只统计不翻译时无需该库
        region = self.api_config.get("region", "")
        if region:
            os.environ["translators_default_region"] = region

    @property
    def ts(self) -> Any:
        if self._ts is None:
            try:
                ensure_dependencies("translators")
                ts = importlib.import_module("translators")
            except ImportError as exc:
                raise FatalTranslateError(f"{self.api} 无法导入 translators 库：{exc}") from exc
            self._ts = ts
        return self._ts

    def translate(self, text: str | list[str]) -> str | list[str]:
        if isinstance(text, list):
            return [self.translate(t) for t in text]
        if not text:
            return ""
        try:
            return self.ts.translate_text(
                text,
                translator=self.api_item["translator"],
                from_language=self.from_lang,
                to_language=self.to_lang,
                timeout=30,
            )
        except FatalTranslateError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"{self.api}: {exc}") from exc


class GoogleTranslator(BaseTranslator):
    """Google Cloud Translation v2，参考 google_tanslator.py。"""

    def translate(self, text: str | list[str]) -> str | list[str]:
        if isinstance(text, list):
            return [self.translate(t) for t in text]
        if not text:
            return ""
        api_key = self.api_config.get("api_key", "")
        if not api_key:
            raise FatalTranslateError("google: API Key 必须输入！")
        params = {
            "key": api_key,
            "q": text,
            "source": self.from_lang,
            "target": self.to_lang,
            "format": "text",
        }
        try:
            resp = requests.get("https://translation.googleapis.com/language/translate/v2/", params=params, timeout=30)
        except requests.RequestException as exc:
            raise TranslateError(f"Google 请求错误: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise TranslateError("Google 返回内容错误") from exc
        if "error" in data:
            raise TranslateError(data["error"].get("message", "Google error"))
        try:
            return data["data"]["translations"][0]["translatedText"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslateError("Google: 没有收到返回内容") from exc


class DeeplTranslator(BaseTranslator):
    """DeepL，参考 deepl_translator.py（free 端点）。"""

    def translate(self, text: str | list[str]) -> str | list[str]:
        is_list = isinstance(text, list)
        if not text:
            return [] if is_list else ""
        api_key = self.api_config.get("api_key", "")
        if not api_key:
            raise FatalTranslateError("deepl: API Key 必须输入！")
        url = self.api_config.get("api_base") or (
            "https://api.deepl.com/v2/translate" if not str(api_key).endswith(":fx") else "https://api-free.deepl.com/v2/translate"
        )
        headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
        data: list[tuple[str, str]] = [
            *(([("text", text)] if not is_list else [("text", t) for t in text])),
            ("source_lang", self.from_lang),
            ("target_lang", self.to_lang),
        ]
        try:
            resp = requests.post(url, headers=headers, data=data, timeout=30)
        except requests.RequestException as exc:
            raise TranslateError(f"DeepL 请求错误: {exc}") from exc
        if resp.status_code != 200:
            raise TranslateError(f"DeepL HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            result = resp.json()
        except ValueError as exc:
            raise TranslateError("DeepL 返回内容错误") from exc
        if "message" in result:
            raise TranslateError(result["message"])
        translations = result.get("translations")
        if not isinstance(translations, list) or not translations:
            raise TranslateError("DeepL: 没有收到返回内容")
        out = [item.get("text", "") for item in translations]
        return out if is_list else out[0]


class MicrosoftTranslator(BaseTranslator):
    """Microsoft Translator，参考 microsoft_translator.py（原生批量）。"""

    def translate(self, text: str | list[str]) -> str | list[str]:
        is_list = isinstance(text, list)
        if not text:
            return [] if is_list else ""
        api_key = self.api_config.get("api_key", "")
        region = self.api_config.get("region", "")
        if not api_key:
            raise FatalTranslateError("microsoft: API Key 必须输入！")
        if not region:
            raise FatalTranslateError("microsoft: Region 必须输入！")
        import uuid  # noqa: PLC0415
        params = {"api-version": "3.0", "from": self.from_lang, "to": self.to_lang}
        headers = {
            "Ocp-Apim-Subscription-Key": api_key,
            "Ocp-Apim-Subscription-Region": region,
            "Content-type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }
        body = [{"text": t} for t in (text if is_list else [text])]
        try:
            resp = requests.post(
                "https://api.cognitive.microsofttranslator.com/translate",
                params=params, headers=headers, json=body, timeout=30,
            )
        except requests.RequestException as exc:
            raise TranslateError(f"Microsoft 请求错误: {exc}") from exc
        try:
            result = resp.json()
        except ValueError as exc:
            raise TranslateError("Microsoft 返回内容错误") from exc
        if isinstance(result, dict) and "error" in result:
            raise TranslateError(result["error"].get("message", "Microsoft error"))
        if not isinstance(result, list) or not result:
            raise TranslateError("Microsoft: 没有收到返回内容")
        out = [item["translations"][0]["text"] for item in result]
        return out if is_list else out[0]


class OpenaiTranslator(BaseTranslator):
    """OpenAI 兼容 chat/completions 批量翻译，参考 openai_translator.py。

    prompt 与返回解析沿用原版：body 是 ``[{"text": ...}]``，从返回里取第一个
    ``[`` 到最后一个 ``]`` 的 JSON 数组。原版用 openai SDK，这里改用 requests
    直调，保持本脚本只有 requests 一个三方依赖。
    """

    def translate(self, text: str | list[str]) -> str | list[str]:
        is_list = isinstance(text, list)
        items = text if is_list else [text]
        if not items:
            return [] if is_list else ""
        api_key = self.api_config.get("api_key", "")
        if not api_key:
            raise FatalTranslateError("openai: API Key 必须输入！")
        base_url = (self.api_config.get("api_base") or "https://api.openai.com/v1").rstrip("/")
        model = self.api_config.get("model") or "gpt-4o-mini"
        body_str = json.dumps([{"text": t} for t in items], ensure_ascii=False)
        messages = [
            {"role": "system", "content": "You are a translator assistant."},
            {
                "role": "user",
                "content": (
                    f"You are a translator assistant. Please translate the following JSON data {self.to_lang}. "
                    "Preserve the original format. Only return the translation result, without any additional "
                    "content or annotations. If the prompt word is in the target language, please send it to "
                    f"me unchanged:\n{body_str}"
                ),
            },
        ]
        payload = {
            "model": model,
            "messages": messages,
            "temperature": float(self.api_config.get("temperature", 0.2)),
            "max_tokens": 4096,
        }
        try:
            resp = requests.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=float(self.api_config.get("timeout", 120)),
            )
        except requests.RequestException as exc:
            raise TranslateError(f"OpenAI 请求错误: {exc}") from exc
        if resp.status_code != 200:
            detail = resp.text[:300]
            if resp.status_code in (400, 401, 403, 404, 422):
                raise FatalTranslateError(f"OpenAI HTTP {resp.status_code}: {detail}")
            raise TranslateError(f"OpenAI HTTP {resp.status_code}: {detail}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise TranslateError(f"OpenAI 返回内容错误: {resp.text[:200]}") from exc

        start, end = content.find("["), content.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise TranslateError(f"OpenAI 返回内容错误: {content[:200]!r}")
        try:
            result = json.loads(content[start : end + 1])
        except ValueError as exc:
            raise TranslateError(f"OpenAI JSON 解析失败: {content[:200]!r}") from exc
        out: list[str] = []
        for i in range(len(items)):
            if i < len(result):
                item = result[i]
                out.append(_clean_zh(item.get("text", "") if isinstance(item, dict) else item))
            else:
                out.append("")  # 缺的留给下一轮重试
        return out if is_list else out[0]


def make_translator(api: str, api_config: dict[str, Any] | None = None) -> BaseTranslator:
    """按 API 目录建 translator —— 与原版 translate() 的分发逻辑对应。"""
    if api not in APIS:
        raise FatalTranslateError(f"翻译API不支持: {api}（用 list-apis 子命令查看可用项）")
    api_type = APIS[api].get("type")
    cls: type[BaseTranslator]
    if api == "myMemory_free":
        cls = MyMemoryFreeTranslator
    elif api_type == "translators":
        cls = TranslatorsTranslator
    elif api == "google":
        cls = GoogleTranslator
    elif api == "deepl":
        cls = DeeplTranslator
    elif api == "microsoft":
        cls = MicrosoftTranslator
    elif api_type == "llm":
        cls = OpenaiTranslator
    else:
        raise FatalTranslateError(f"翻译API不支持: {api}")
    return cls(api, api_config)


# ---------------------------------------------------------------------------
# 配置解析：--api-config k=v > 显式参数 > 环境变量 > 后端默认值
# ---------------------------------------------------------------------------

def build_api_config(
    translate_api: str,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_config: list[str] | None = None,
    temperature: float = 0.2,
    timeout: float = 120.0,
    require: bool = True,
    config_flag: str = "--api-config",
) -> dict[str, Any]:
    """组装某个翻译后端的运行配置。

    优先级：``--api-config k=v`` > 显式参数 > 环境变量 > 后端默认值。
    不依赖 argparse，方便被子命令薄封装直接调用。

    Args:
        require: 是否校验必填配置（``--dry-run`` 不调 API，传 False 跳过）
        config_flag: 缺配置时错误信息里提示的旗标名；备用后端传
            ``--fallback-api-config``，否则会误导用户往主后端的旗标里填密钥
    """
    if translate_api not in APIS:
        raise FatalTranslateError(f"翻译API不支持: {translate_api}（用 list-apis 查看可用项）")
    item = APIS[translate_api]
    config: dict[str, Any] = {}

    # 1) 环境变量 + 后端默认值
    for key, env_name in (item.get("env") or {}).items():
        value = os.getenv(env_name, "")
        if not value and key == "api_base":  # 兼容 prompt-all-in-one 的 OPENAI_API_BASE
            value = os.getenv("OPENAI_API_BASE", "")
        if value:
            config[key] = value
    for key, value in (item.get("defaults") or {}).items():
        config.setdefault(key, value)

    # 2) 显式参数（沿用既有 CLI 名字）
    if api_key:
        config["api_key"] = api_key
    if base_url:
        config["api_base"] = base_url
    if model:
        config["model"] = model

    # 3) --api-config k=v（最高优先级）
    for pair in api_config or []:
        if "=" not in pair:
            raise FatalTranslateError(f"--api-config 格式应为 key=value，收到: {pair}")
        k, _, v = pair.partition("=")
        config[k.strip()] = v.strip()

    # 4) 校验必填项（--dry-run 不调 API，跳过校验）
    if require:
        for need in item.get("config", []):
            if need.endswith("*"):
                continue
            if not config.get(need):
                hint = (item.get("env") or {}).get(need, need)
                raise FatalTranslateError(
                    f"{translate_api} 缺少配置 {need}"
                    f"（可用 {config_flag} {need}=... 或环境变量 {hint}）"
                )
    config["temperature"] = temperature
    config["timeout"] = timeout
    return config


def list_apis() -> None:
    """列出全部翻译后端（表格走 raw 记录，避免时间戳 / 级别前缀破坏对齐）。"""
    header = f"{'key':20} {'并发':>4}  {'需要配置':38} 名称"
    lines = [f"\033[1m{header}\033[0m" if _COLOR_ENABLED else header, "-" * 110]
    for key, item in APIS.items():
        need = ",".join(item.get("config", [])) or "-"
        lines.append(f"{key:20} {item.get('concurrent', 1):>4}  {need:38} {item['name']}")
    lines.append("")
    lines.append("语言支持（--from-lang / --to-lang）: " + ", ".join(sorted(APIS["google"]["support"])))
    lines.append("*_free 免密钥；*_free(translators) 需要 pip install translators；myMemory_free / openai / google / deepl / microsoft 开箱可用（后四者需密钥）")
    log.info("\n".join(lines), extra={"raw": True})


# ===========================================================================
# 环境变量 / 文件读写
# ===========================================================================

def load_dotenv(path: Path) -> None:
    """极简 .env 加载（只认 KEY=VALUE，已存在的环境变量优先），避免额外依赖。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _clean_zh(value: Any) -> str:
    if value is None:
        return ""
    s = str(value).replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r" {2,}", " ", s).strip()


def to_prompt_text(name: str, *, underscore_to_space: bool = True) -> str:
    """把 tag 变成送给翻译引擎的提示词。

    ``long_hair`` 这种整串下划线形式对翻译引擎是黑盒：多数引擎把它当成一个
    词，翻成 ``long_hair`` 原样吐回来，或者把 ``_`` 当成分隔符逐段硬翻。
    换成 ``long hair`` 后引擎能正常分词，``long hair → 长发`` 这类结果质量高很多。

    Args:
        name: 原始 tag 名（也是缓存键 / 行定位键，全程不改）
        underscore_to_space: False 表示原样送翻（``--keep-underscores``）
    """
    return name.replace("_", " ") if underscore_to_space else name


def restore_if_unchanged(name: str, zh: str, *, underscore_to_space: bool = True) -> str:
    """引擎按“原样返回”处理时，把结果还原成带下划线的原始 tag。

    提示词里写了 “If the prompt word is in the target language, please send it
    to me unchanged”，引擎照做时回吐的是 ``long hair``；但 zh 列要的是与 tag
    同形的值，所以这种情况下换回 ``long_hair``。真正翻译出来的结果不受影响。
    """
    prompt = to_prompt_text(name, underscore_to_space=underscore_to_space)
    if prompt != name and _clean_zh(zh) == prompt:
        return name
    return zh


def read_tagpp(path: Path) -> list[list[str]]:
    """读取 tag++ 格式文件（无表头 4 列），坏行做保守修复。"""
    if not path.is_file():
        return []
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for raw in progress(csv.reader(fh), desc="读取 tag++"):
            if not raw:
                continue
            if len(raw) < 4:
                raw = raw + [""] * (4 - len(raw))
            elif len(raw) > 4:
                raw = raw[:3] + [",".join(raw[3:])]
            rows.append(raw[:4])
    return rows


def write_tagpp(path: Path, rows: list[list[str]], *, backup: bool) -> None:
    """原子写出 tag++ 文件（先写临时文件再替换），可选先留 .bak 备份。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)  # 默认 CRLF + 最小引用，与现有文件一致
        writer.writerows(progress(rows, desc="写入 tag++"))
    if backup and path.is_file():
        bak = path.with_name(path.name + ".bak")
        with (
            path.open("rb") as src,
            bak.open("wb") as dst,
            progress(desc="备份 tag++", total=path.stat().st_size, unit="B") as bar,
        ):
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
                bar.update(len(chunk))
    os.replace(tmp, path)


def iter_danbooru(path: Path):
    """逐行产出 Hanatsumi 抓取的 tag：(name, category, post_count, is_deprecated)。"""
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if not header:
            log.error("%s 是空文件", path)
            raise SystemExit(1)
        header = [h.lstrip("﻿") for h in header]
        try:
            i_name = header.index("name")
            i_cat = header.index("category")
            i_cnt = header.index("post_count")
            i_dep = header.index("is_deprecated")
        except ValueError as exc:
            log.error("%s 表头不认识，需要 name/category/post_count/is_deprecated（实际：%s）", path, header)
            raise SystemExit(1) from exc
        for row in reader:
            if len(row) <= max(i_name, i_cat, i_cnt, i_dep):
                continue
            yield row[i_name], row[i_cat], row[i_cnt], row[i_dep] == "true"


# ===========================================================================
# 同步
# ===========================================================================

def sync_rows(
    local: list[list[str]],
    db_iter,
    *,
    prune: bool,
    min_post_count: int,
    sort: str,
) -> tuple[list[list[str]], dict]:
    """把 Danbooru 数据合并进本地行，返回 (新行, 统计信息)。"""
    index: dict[str, list[list[str]]] = {}
    for row in progress(local, desc="建立本地索引"):
        index.setdefault(row[COL_NAME], []).append(row)

    stats = {
        "local": len(local),
        "db_total": 0,
        "db_deprecated": 0,
        "updated": 0,
        "added": 0,
        "skipped_deprecated": 0,
        "skipped_low_count": 0,
        "removed_missing": 0,
        "removed_deprecated": 0,
        "kept_unmatched": 0,
    }

    seen: set[str] = set()
    new_rows: list[list[str]] = []

    for name, category, count, deprecated in progress(db_iter, desc="读取并合并 Danbooru", unit="tag"):
        stats["db_total"] += 1
        if deprecated:
            stats["db_deprecated"] += 1

        holders = index.get(name)
        if holders:
            seen.add(name)
            if deprecated and prune:
                for row in holders:
                    row[COL_NAME] = "\x00"  # 标记待删，避免影响后续统计
                stats["removed_deprecated"] += len(holders)
                continue
            # 已存在：用最新 category / post_count 覆盖，中文翻译保留
            for row in holders:
                row[COL_CATEGORY] = category
                row[COL_COUNT] = count
            stats["updated"] += len(holders)
            continue

        # Danbooru 新增的 tag
        if deprecated:
            stats["skipped_deprecated"] += 1  # 废弃 tag 不新增
            continue
        try:
            if int(count) < min_post_count:
                stats["skipped_low_count"] += 1
                continue
        except ValueError:
            count = "0"
        new_rows.append([name, category, count, ""])

    # Danbooru 里已经找不到的本地 tag
    for name, holders in progress(index.items(), desc="检查缺失 tag", unit="tag"):
        if name in seen:
            continue
        if prune:
            for row in holders:
                row[COL_NAME] = "\x00"
            stats["removed_missing"] += len(holders)
        else:
            stats["kept_unmatched"] += len(holders)

    sort_rows(new_rows, key=lambda r: (-int(r[COL_COUNT] or 0), r[COL_NAME]), desc="排序新增 tag")
    stats["added"] = len(new_rows)

    merged = [row for row in progress(local, desc="整理合并结果") if row[COL_NAME] != "\x00"] + new_rows

    if sort == "count":
        sort_rows(merged, key=lambda r: (-_to_int(r[COL_COUNT]), r[COL_NAME]), desc="排序全部 tag")

    return merged, stats


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ===========================================================================
# 翻译流程
# ===========================================================================

def load_cache(cache_path: Path | None, use_cache: bool) -> dict[str, str]:
    if not (use_cache and cache_path and cache_path.is_file()):
        return {}
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        return {str(k): _clean_zh(v) for k, v in progress(data.items(), desc="读取翻译缓存", unit="tag")}
    except ValueError as exc:
        log.warning("翻译缓存损坏，忽略之（%s）", exc)
        return {}


def save_cache(cache_path: Path | None, use_cache: bool, cache: dict[str, str], new: dict[str, str]) -> None:
    if not (use_cache and cache_path):
        return
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_name(cache_path.name + ".tmp")
    encoder = json.JSONEncoder(ensure_ascii=False, indent=0)
    with tmp.open("w", encoding="utf-8") as fh:
        for chunk in progress(encoder.iterencode({**cache, **new}), desc="写入翻译缓存", unit="片段"):
            fh.write(chunk)
    os.replace(tmp, cache_path)


def _llm_call(trans: BaseTranslator, prompt: list[str], *, retries: int) -> list[str]:
    """LLM 后端翻一批，带指数退避重试（``FatalTranslateError`` 不重试直接抛）。

    主后端与备用后端共用：降级时备用也可能是 llm 型。
    """
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            out = trans.translate(prompt)
            return out if isinstance(out, list) else [out]
        except FatalTranslateError:
            raise
        except Exception as exc:  # noqa: BLE001 - 网络/限速/解析都可重试
            last = exc
            time.sleep(min(2**attempt, 15))
    raise TranslateError(str(last))


def run_translate(
    rows: list[list[str]],
    translator: BaseTranslator,
    cache_path: Path | None,
    *,
    fallback: BaseTranslator | None = None,
    use_cache: bool,
    limit: int,
    save_every: int,
    dry_run: bool,
    batch_size: int = 50,
    workers: int = 4,
    interval: float = 1.0,
    retries: int = 3,
    rows_path: Path | None = None,
    underscore_to_space: bool = True,
) -> dict:
    """补齐 rows 里翻译为空的行，返回统计信息。

    不依赖 argparse，方便 update / translate 子命令复用同一份流程。

    送翻用 ``to_prompt_text()`` 规整过的提示词（下划线 → 空格），返回结果仍按
    原始 tag 名做 zip / 写缓存 / 回填 —— 输入侧改写，输出侧不动。

    Args:
        fallback: 备用后端。主后端整批失败（重试耗尽）时用它把**同一批**再翻
            一遍，成功则照常回填、不算失败；备用也失败才计入 ``failed``。
            只在批级降级，不做条级混翻 —— 保证一个批次的结果可预期。
    """
    global requests

    # 1) 先把缓存里已有的结果灌回文件（上次中断的进度）
    cache = load_cache(cache_path, use_cache)
    restored = 0
    for row in progress(rows, desc="恢复缓存翻译"):
        name = row[COL_NAME]
        if name and not row[COL_ZH].strip() and name in cache:
            row[COL_ZH] = cache[name]
            restored += 1

    # 2) 收集还欠翻译的 tag
    pending_index: dict[str, list[int]] = {}
    for i, row in enumerate(progress(rows, desc="筛选待翻译 tag")):
        name = row[COL_NAME]
        if name.strip() and not row[COL_ZH].strip() and name not in cache:
            pending_index.setdefault(name, []).append(i)
    if limit > 0:
        names = list(pending_index)[:limit]
        pending_index = {n: pending_index[n] for n in names}

    stats = {"restored": restored, "pending": len(pending_index), "translated": 0, "failed": 0, "unreturned": 0,
             "fell_back": 0, "fell_back_groups": 0}
    log.info("%s（%s）%s -> %s", translator.api, APIS[translator.api]["name"], translator.from_lang, translator.to_lang)
    if fallback is not None:
        log.info("备用后端：%s（%s），主后端整批失败时降级", fallback.api, APIS[fallback.api]["name"])
    log.info("待翻译 %d 个 tag（缓存恢复 %d 个）", stats["pending"], restored)
    if dry_run or not pending_index:
        return stats
    if requests is None:
        ensure_dependencies("requests")
        requests = importlib.import_module("requests")

    names = list(pending_index)
    new_cache: dict[str, str] = {}
    is_llm = APIS[translator.api].get("type") == "llm"
    # 两种后端都按 --batch-size 切批：llm 一批=一次请求；非 llm 一批=一次
    # translate_batch 调用（内部再按 concurrent 分组 + 组间 sleep）
    group_size = max(1, batch_size)
    groups = [names[i : i + group_size] for i in progress(range(0, len(names), group_size), desc="划分翻译批次", unit="批")]
    total = len(groups)

    def persist() -> None:
        save_cache(cache_path, use_cache, cache, new_cache)
        if rows_path:
            write_tagpp(rows_path, rows, backup=False)

    def apply(group: list[str], translated: list[str]) -> None:
        for name, zh in zip(group, translated):
            if not zh:
                stats["unreturned"] += 1
                continue
            zh = restore_if_unchanged(name, zh, underscore_to_space=underscore_to_space)
            new_cache[name] = zh
            for idx in pending_index.get(name, []):
                rows[idx][COL_ZH] = zh
            stats["translated"] += 1

    def finish_group(group: list[str], out: Any) -> None:
        if isinstance(out, str):  # 后端把整批当一条返回
            out = [out]
        out = list(out)
        if len(out) < len(group):  # LLM 少返回的补空串，算未返回
            out = out + [""] * (len(group) - len(out))
        apply(group, out[: len(group)])

    def prompts_of(group: list[str]) -> list[str]:
        """送翻文本：按需把下划线换成空格，顺序与 group 保持一致。"""
        return [to_prompt_text(n, underscore_to_space=underscore_to_space) for n in group]

    def call_backend(trans: BaseTranslator, group: list[str]) -> list[str]:
        """按后端类型选批量入口：llm = 一次请求翻一批；非 llm = translate_batch。"""
        prompt = prompts_of(group)
        if APIS[trans.api].get("type") == "llm":
            return _llm_call(trans, prompt, retries=retries)
        return trans.translate_batch(prompt, retries=retries, interval=interval)

    def call_with_fallback(group: list[str]) -> tuple[list[str], str]:
        """翻一批；主后端整批失败且配了备用后端时，降级用备用翻同一批。

        只做批级降级，不做条级混翻 —— 一个批次的结果来源单一，可预期。

        Returns:
            (结果, 实际生效的备用后端名)。第二个元素为空串表示主后端直接成功。
            计数与日志留给调用方，线程池里只做翻译，避免跨线程改 stats。

        Raises:
            TranslateError: 主后端失败且无备用，或主/备两个后端都失败
                （消息里同时带上两边的错误，方便定位）。
        """
        try:
            return call_backend(translator, group), ""
        except FatalTranslateError:
            raise
        except Exception as primary_exc:
            if fallback is None:
                raise
            try:
                out = call_backend(fallback, group)
            except FatalTranslateError:
                raise
            except Exception as fallback_exc:
                raise TranslateError(
                    f"主 {translator.api}: {primary_exc}；备用 {fallback.api}: {fallback_exc}"
                ) from fallback_exc
            return out, fallback.api

    done = 0
    if is_llm:
        # LLM 后端：一次请求翻一批，外层并发多个批（原版 openai 是单条，这里按
        # 混合策略改成批量 JSON），每批带重试
        pool_workers = max(1, workers)
        log.info("每批 %d 条 · 并发 %d 批", group_size, pool_workers)
        if underscore_to_space and any("_" in n for n in names):
            log.debug("提示词预处理：%d 个 tag 的下划线会替换成空格再送翻", sum(1 for n in names if "_" in n))

        with ThreadPoolExecutor(max_workers=pool_workers) as pool:
            futures = {pool.submit(call_with_fallback, g): g for g in groups}
            for fut in progress(as_completed(futures), desc="翻译批次", total=total, unit="批"):
                group = futures[fut]
                done += 1
                try:
                    out, fell_back_api = fut.result()
                except FatalTranslateError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    stats["failed"] += len(group)
                    log.error("批次 %d/%d 失败（%d 条，重跑可续传）: %s", done, total, len(group), exc)
                else:
                    if fell_back_api:
                        stats["fell_back"] += len(group)
                        stats["fell_back_groups"] += 1
                        log.warning("批次 %d/%d 主后端 %s 整批失败，已降级备用 %s 翻完（%d 条）",
                                    done, total, translator.api, fell_back_api, len(group))
                    finish_group(group, out)
                if save_every > 0 and done % save_every == 0:
                    persist()
    else:
        # 非 LLM 后端：照搬原版 —— 分批串行推进，每批内部按 concurrent 并发单条、
        # 批内组间 sleep，批与批之间再等一次 interval（限速的关键）
        log.info("每批 %d 条 · 组内并发 %d · 组间等待 %ss", group_size, translator.concurrent, interval)
        for idx, chunk in enumerate(progress(groups, desc="翻译批次", unit="批"), start=1):
            done = idx
            try:
                out, fell_back_api = call_with_fallback(chunk)
            except FatalTranslateError:
                raise
            except Exception as exc:  # noqa: BLE001
                stats["failed"] += len(chunk)
                log.error("批次 %d/%d 失败（%d 条，重跑可续传）: %s", done, total, len(chunk), exc)
            else:
                if fell_back_api:
                    stats["fell_back"] += len(chunk)
                    stats["fell_back_groups"] += 1
                    log.warning("批次 %d/%d 主后端 %s 整批失败，已降级备用 %s 翻完（%d 条）",
                                done, total, translator.api, fell_back_api, len(chunk))
                finish_group(chunk, out)
            if save_every > 0 and done % save_every == 0:
                persist()
            # concurrent=1 的后端在 translate_batch 内部已逐条 sleep，这里不重复等待
            if interval > 0 and translator.concurrent > 1 and idx < total:
                time.sleep(interval)

    persist()
    summary = "完成：成功 %d，失败 %d，未返回 %d"
    args_summary = (stats["translated"], stats["failed"], stats["unreturned"])
    if stats["fell_back_groups"]:
        summary += "，降级 %d 批（%d 条走备用 %s）"
        args_summary += (stats["fell_back_groups"], stats["fell_back"], fallback.api)
    if stats["failed"]:
        log.warning(summary + "（失败的重跑同一命令可续传）", *args_summary)
    else:
        log.info(summary, *args_summary)
    return stats


# ===========================================================================
# CLI —— 参考 sd-webui-all-in-one 的 cli_manager 结构：
#
#   * build_parser()            薄入口：只建根解析器 + 依次调用 register_xxx()
#   * register_xxx(subparsers)  每个功能自己注册自己的子命令，注册里只做
#                               “add_parser + add_argument + set_defaults(薄封装)”
#   * xxx_cli(args)             薄封装 handler：只摊开 args 转调下面的 helper
#   * _sync_step / _translate_step / _write_output   真正干活的地方
#
#   路径一律通过命令行参数显式给出（required），不在代码里写死默认位置。
# ===========================================================================


def add_subparsers_with_help(parser: argparse.ArgumentParser, *, dest: str) -> "argparse._SubParsersAction":
    """添加“未选择子命令时打印当前层帮助”的子解析器。

    Args:
        parser: 当前层级的参数解析器
        dest: 用于存储所选子命令名称的属性名

    Returns:
        新建的子解析器 action
    """
    parser.set_defaults(func=lambda _args: parser.print_help())
    return parser.add_subparsers(dest=dest, required=False)


def normalized_filepath(value: str) -> Path:
    """把命令行里的路径统一转成绝对路径（会展开 ``~``）。"""
    if value is None:
        return value
    return Path(value).expanduser().absolute()


# ---------------------------------------------------------------------------
# 共享参数：子命令按需拼装，避免重复声明
# ---------------------------------------------------------------------------

def _add_io_arguments(p: argparse.ArgumentParser, *, need_hanatsumi: bool) -> None:
    """输入 / 输出参数。路径全部必填，不写死默认值。"""
    if need_hanatsumi:
        p.add_argument("--hanatsumi", type=normalized_filepath, required=True, metavar="PATH",
                       help="Hanatsumi 抓取的 tags.csv（表头需含 name/category/post_count/is_deprecated）")
    p.add_argument("--input", type=normalized_filepath, required=True, metavar="PATH",
                   help="待合并的 tag++ 格式文件（无表头 4 列），不存在则新建")
    p.add_argument("--output", type=normalized_filepath, default=None, metavar="PATH",
                   help="输出文件，默认与 --input 相同（原地更新）")
    p.add_argument("--backup", action=argparse.BooleanOptionalAction, default=True,
                   help="原地更新前先写 .bak 备份（默认开启）")
    p.add_argument("--dry-run", action="store_true", help="只打印统计，不写文件、不调用 API")


def _add_sync_arguments(p: argparse.ArgumentParser) -> None:
    """同步参数（sync / update 共用）。"""
    p.add_argument("--prune", action="store_true", help="移除 Danbooru 里已不存在或 is_deprecated=true 的 tag")
    p.add_argument("--min-post-count", type=int, default=0, metavar="N",
                   help="追加新 tag 时忽略帖子数低于 N 的（默认 0 = 全部）")
    p.add_argument("--sort", choices=["none", "count"], default="none",
                   help="输出排序：none=已有行保持原位、新 tag 追加在末尾；count=按帖子数从多到少整表排序（默认 none）")


def _add_translate_arguments(p: argparse.ArgumentParser) -> None:
    """翻译参数（translate / update 共用；API 层参考 sd-webui-prompt-all-in-one）。"""
    api_choices = list(APIS)
    language_choices = sorted({locale for item in APIS.values() for locale in item["support"]})
    p.add_argument("--translate-api", choices=api_choices, default="myMemory_free",
                   help="翻译后端，默认 myMemory_free（免密钥），用 list-apis 查看各后端详情")
    p.add_argument("--from-lang", choices=language_choices, default="en_US",
                   help="源语言 locale 码（默认 en_US；需所选后端支持）")
    p.add_argument("--to-lang", choices=language_choices, default="zh_CN",
                   help="目标语言 locale 码（默认 zh_CN；需所选后端支持）")
    p.add_argument("--api-key", default=None, help="API key（映射到该后端的 api_key 配置）")
    p.add_argument("--api-config", action="append", default=[], metavar="KEY=VALUE",
                   help="额外配置，可重复，如 --api-config region=eastasia（优先级最高）")
    p.add_argument("--fallback-api", choices=api_choices, default=None,
                   help="备用翻译后端：主后端整批失败（重试耗尽）时自动降级，用它把这一批再翻一遍；"
                        "不填则不降级。批级降级，不做条级混翻")
    p.add_argument("--fallback-api-config", action="append", default=[], metavar="KEY=VALUE",
                   help="备用后端的配置，可重复，如 --fallback-api-config api_key=...。"
                        "备用后端只认环境变量和这里的值，不继承 --api-key/--base-url/--model/"
                        "--api-config（避免把主后端的密钥发给别的后端）")
    p.add_argument("--base-url", default=None, help="OpenAI 兼容接口 base url（openai 后端；默认 OPENAI_BASE_URL 环境变量）")
    p.add_argument("--model", default=None, help="模型名（openai 后端；默认 AI_MODEL 环境变量）")
    p.add_argument("--batch-size", type=int, default=50,
                   help="每批条数：openai=一次请求翻多少条；其他后端=单条分组的组大小（默认 50）")
    p.add_argument("--workers", type=int, default=4, help="并发批次数（concurrent=1 的后端强制串行；默认 4）")
    p.add_argument("--interval", type=float, default=1.0,
                   help="分组之间的等待秒数，照搬原版 sleep 1（默认 1.0，0=不等待）")
    p.add_argument("--limit", type=int, default=0, metavar="N", help="本次最多翻译 N 个 tag，0 = 不限（默认 0）")
    p.add_argument("--temperature", type=float, default=0.2, help="采样温度（仅 openai 后端；默认 0.2）")
    p.add_argument("--timeout", type=float, default=120.0, help="单次请求超时秒数（默认 120）")
    p.add_argument("--retries", type=int, default=3, help="单条失败重试次数（默认 3）")
    p.add_argument("--cache", type=normalized_filepath, default=None, metavar="PATH",
                   help="翻译缓存路径，默认 <输出文件>.translations.json")
    p.add_argument("--no-cache", action="store_true", help="不读写翻译缓存（每次都会重翻空翻译）")
    p.add_argument("--save-every", type=int, default=10,
                   help="每完成多少批落盘一次缓存和输出文件，0 = 只在结束时落盘（默认 10）")
    p.add_argument("--keep-underscores", action="store_true",
                   help="送翻前保留 tag 里的下划线（默认把 _ 替换成空格再翻译，"
                        "long_hair → long hair，引擎分词更准；引擎原样返回时会还原成原始 tag）")


def _add_logging_arguments(p: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    """挂上日志相关开关。

    Args:
        p: 目标解析器
        suppress_defaults: 子解析器传 True —— 用 SUPPRESS 默认值，避免把根解析器
            已经解析到的 ``-v`` 覆盖回默认值（argparse 会用子解析器的默认值刷一遍 namespace）
    """
    default = argparse.SUPPRESS if suppress_defaults else False
    p.add_argument("-v", "--verbose", action="store_true", default=default,
                   help="输出 DEBUG 级别日志（含解析出的路径 / 运行参数，密钥打码）")
    p.add_argument("-q", "--quiet", action="store_true", default=default,
                   help="只输出 WARNING 及以上级别日志")
    p.add_argument("--color", action=argparse.BooleanOptionalAction,
                   default=argparse.SUPPRESS if suppress_defaults else None,
                   help="彩色输出（默认按是否为终端自动判断，同时认 NO_COLOR / FORCE_COLOR）")


# ---------------------------------------------------------------------------
# 干活的 helper（薄封装 handler 只负责调用它们）
# ---------------------------------------------------------------------------

def _read_input(args: argparse.Namespace) -> list[list[str]]:
    """读入 tag++ 文件（不存在则按空文件处理）。"""
    rows = read_tagpp(args.input)
    if not rows:
        log.warning("%s 不存在或为空，将生成新文件", args.input)
    else:
        log.debug("读入 %s：%d 行", args.input, len(rows))
    return rows


def _print_sync_stats(stats: dict, *, prune: bool, min_post_count: int) -> None:
    """整块统计一次输出：首行带时间戳，后续行缩进对齐（多行消息只染首行）。"""
    lines = ["== 同步 =="]
    lines.append(f"  输入行数            {stats['local']}")
    lines.append(f"  Danbooru tag 总数   {stats['db_total']}（其中已废弃 {stats['db_deprecated']}）")
    lines.append(f"  刷新已有行          {stats['updated']}")
    lines.append(f"  新增 tag            {stats['added']}")
    lines.append(f"  跳过：已废弃未新增  {stats['skipped_deprecated']}")
    if min_post_count:
        lines.append(f"  跳过：帖子数 < {min_post_count:<5}{stats['skipped_low_count']}")
    if prune:
        lines.append(f"  移除：Danbooru 已不存在 {stats['removed_missing']}")
        lines.append(f"  移除：已废弃           {stats['removed_deprecated']}")
    elif stats["kept_unmatched"]:
        lines.append(f"  保留：Danbooru 已不存在（未加 --prune）{stats['kept_unmatched']}")
    lines.append(f"  输出行数            {stats['output_rows']}")
    log.info("\n".join(lines))


def _sync_step(args: argparse.Namespace, rows: list[list[str]]) -> tuple[list[list[str]], dict]:
    """sync / update 共用：跑一遍 Danbooru 合并并打印统计。"""
    if not args.hanatsumi.is_file():
        log.error("找不到 Hanatsumi 数据文件 %s（可用 --hanatsumi 指定）", args.hanatsumi)
        raise SystemExit(1)
    rows, stats = sync_rows(
        rows,
        iter_danbooru(args.hanatsumi),
        prune=args.prune,
        min_post_count=args.min_post_count,
        sort=args.sort,
    )
    stats["output_rows"] = len(rows)
    _print_sync_stats(stats, prune=args.prune, min_post_count=args.min_post_count)
    return rows, stats


def _build_fallback(args: argparse.Namespace) -> BaseTranslator | None:
    """按 ``--fallback-api`` 建备用 translator；没配则返回 None。

    备用后端的配置**只**来自环境变量、后端默认值和 ``--fallback-api-config`` ——
    刻意不继承 ``--api-key`` / ``--base-url`` / ``--model`` / ``--api-config``，
    否则会把主后端的密钥发给另一个后端（比如拿 OpenAI key 去请求 myMemory）。
    """
    if not args.fallback_api:
        if args.fallback_api_config:
            raise FatalTranslateError("--fallback-api-config 需要同时指定 --fallback-api")
        return None
    # 不校验 fallback == primary：同一个后端换个端点 / 密钥 / 模型（限速时切 key）
    # 也是合理用法，配置不同就仍有降级意义
    fb_config = build_api_config(
        args.fallback_api,
        api_config=args.fallback_api_config,
        temperature=args.temperature,
        timeout=args.timeout,
        require=not args.dry_run,
        config_flag="--fallback-api-config",
    )
    log.debug("备用后端配置：%s", _mask_config(fb_config))
    fallback = make_translator(args.fallback_api, fb_config)
    fallback.set_from_lang(args.from_lang).set_to_lang(args.to_lang)
    return fallback


def _translate_step(args: argparse.Namespace, rows: list[list[str]]) -> dict:
    """translate / update 共用：建 translator 并补齐空翻译，返回统计。"""
    output: Path = args.output or args.input
    cache_path: Path = args.cache or output.with_name(output.name + ".translations.json")
    log.info("== 翻译 ==")
    try:
        api_config = build_api_config(
            args.translate_api,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            api_config=args.api_config,
            temperature=args.temperature,
            timeout=args.timeout,
            require=not args.dry_run,
        )
        log.debug("翻译配置 %s -> %s，缓存 %s", args.from_lang, args.to_lang, cache_path)
        log.debug("运行参数：%s", _mask_config(api_config))
        translator = make_translator(args.translate_api, api_config)
        translator.set_from_lang(args.from_lang).set_to_lang(args.to_lang)
        fallback = _build_fallback(args)
        return run_translate(
            rows,
            translator,
            cache_path,
            fallback=fallback,
            use_cache=not args.no_cache,
            limit=args.limit,
            save_every=args.save_every,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
            workers=args.workers,
            interval=args.interval,
            retries=args.retries,
            rows_path=None if args.dry_run else output,
            underscore_to_space=not args.keep_underscores,
        )
    except FatalTranslateError as exc:
        log.error("%s", exc)
        raise SystemExit(1) from exc


def _write_output(args: argparse.Namespace, rows: list[list[str]], *, changed: bool) -> int:
    if args.dry_run:
        log.info("[dry-run] 未写入任何文件")
        return 0
    if not changed:
        log.info("[输出] 无变化，跳过写入")
        return 0
    output: Path = args.output or args.input
    write_tagpp(output, rows, backup=args.backup and output == args.input)
    log.info("[输出] 已写入 %s（%d 行）", output, len(rows))
    return 0


# ---------------------------------------------------------------------------
# 薄封装 handler：只摊开 args 转调上面的 helper
# ---------------------------------------------------------------------------

def sync_cli(args: argparse.Namespace) -> int:
    """sync 子命令：只同步，不翻译。"""
    rows, _stats = _sync_step(args, _read_input(args))
    return _write_output(args, rows, changed=True)


def translate_cli(args: argparse.Namespace) -> int:
    """translate 子命令：只补翻译，不动同步。"""
    rows = _read_input(args)
    tr_stats = _translate_step(args, rows)
    changed = bool(tr_stats["translated"] or tr_stats["restored"])
    return _write_output(args, rows, changed=changed)


def update_cli(args: argparse.Namespace) -> int:
    """update 子命令：先同步再翻译，一次跑完。"""
    rows, _stats = _sync_step(args, _read_input(args))
    _translate_step(args, rows)
    return _write_output(args, rows, changed=True)


def list_apis_cli(_args: argparse.Namespace) -> int:
    """list-apis 子命令：列出可用翻译后端。"""
    list_apis()
    return 0


# ---------------------------------------------------------------------------
# 子命令注册：每个功能只负责把自己的子命令挂上去（薄封装，不写业务逻辑）
# ---------------------------------------------------------------------------

def register_sync(subparsers: "argparse._SubParsersAction") -> None:
    """注册 sync 子命令：把 Danbooru tag 合并进 tag++ 文件。"""
    p = subparsers.add_parser("sync", help="同步：把 Danbooru tag 合并进 tag++ 文件（新增 tag 的翻译列留空）")
    _add_logging_arguments(p, suppress_defaults=True)
    _add_io_arguments(p, need_hanatsumi=True)
    _add_sync_arguments(p)
    p.set_defaults(func=sync_cli)


def register_translate(subparsers: "argparse._SubParsersAction") -> None:
    """注册 translate 子命令：只补齐翻译列为空的 tag。"""
    p = subparsers.add_parser("translate", help="翻译：补齐 tag++ 文件里翻译列为空的 tag（不同步）")
    _add_logging_arguments(p, suppress_defaults=True)
    _add_io_arguments(p, need_hanatsumi=False)
    _add_translate_arguments(p)
    p.set_defaults(func=translate_cli)


def register_update(subparsers: "argparse._SubParsersAction") -> None:
    """注册 update 子命令：sync + translate 一次跑完。"""
    p = subparsers.add_parser("update", help="更新：先同步再翻译，一次跑完（= sync + translate）")
    _add_logging_arguments(p, suppress_defaults=True)
    _add_io_arguments(p, need_hanatsumi=True)
    _add_sync_arguments(p)
    _add_translate_arguments(p)
    p.set_defaults(func=update_cli)


def register_list_apis(subparsers: "argparse._SubParsersAction") -> None:
    """注册 list-apis 子命令：列出可用翻译后端。"""
    p = subparsers.add_parser("list-apis", help="列出可用翻译后端及其并发数 / 所需配置 / 语言支持")
    _add_logging_arguments(p, suppress_defaults=True)
    p.set_defaults(func=list_apis_cli)


# ---------------------------------------------------------------------------
# 薄入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """建根解析器，并把各功能的子命令依次挂上去。"""
    p = argparse.ArgumentParser(
        prog="sync_danbooru_tags.py",
        description="把 Hanatsumi 抓取的 Danbooru tag 同步成 tag++ 格式，并可用翻译 API 补齐中文翻译。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "子命令：\n"
            "  sync        同步：合并 Danbooru tag（刷新已有行、追加新 tag，翻译列留空）\n"
            "  translate   翻译：补齐翻译列为空的 tag\n"
            "  update      更新：先同步再翻译，一次跑完\n"
            "  list-apis   列出可用翻译后端\n"
            "\n"
            "示例：\n"
            "  python3 sync_danbooru_tags.py sync --hanatsumi ../Hanatsumi/data/tags.csv --input tag++.csv\n"
            "  python3 sync_danbooru_tags.py sync --hanatsumi ../Hanatsumi/data/tags.csv --input tag++.csv --prune\n"
            "  python3 sync_danbooru_tags.py translate --input tag++.csv --translate-api myMemory_free\n"
            "  python3 sync_danbooru_tags.py translate --input tag++.csv --translate-api google --api-key XXX\n"
            "  python3 sync_danbooru_tags.py update --hanatsumi ../Hanatsumi/data/tags.csv --input tag++.csv \\\n"
            "      --translate-api openai --api-config model=gpt-4o-mini\n"
            "  python3 sync_danbooru_tags.py list-apis\n"
            "  python3 sync_danbooru_tags.py sync --hanatsumi ... --input tag++.csv --output new.csv --dry-run\n"
        ),
    )
    subparsers = add_subparsers_with_help(p, dest="main_command")
    _add_logging_arguments(p)
    register_sync(subparsers)
    register_translate(subparsers)
    register_update(subparsers)
    register_list_apis(subparsers)
    return p


def _log_level_from(args: argparse.Namespace) -> int:
    if getattr(args, "verbose", False):
        return logging.DEBUG
    if getattr(args, "quiet", False):
        return logging.WARNING
    return logging.INFO


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(level=_log_level_from(args), color=getattr(args, "color", None))

    initialize_dependencies(args)

    load_dotenv(SCRIPT_DIR / ".env")
    load_dotenv(Path.cwd() / ".env")

    handler = getattr(args, "func", None)
    if handler is None:  # 理论上 add_subparsers_with_help 已兜底
        parser.print_help()
        return 0
    return handler(args) or 0


if __name__ == "__main__":
    setup_logging()  # 让 main() 抛出前的任何日志也有格式
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        log.warning("已中断（翻译进度已写入缓存，重跑同一命令可续传）")
        raise SystemExit(130)
