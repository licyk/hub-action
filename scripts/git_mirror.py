"""并发镜像同步工具: 把 GitHub 仓库同步到 Gitee / GitLab / Bitbucket 等平台

    python scripts/git_mirror.py [--mode fanout|pairwise] [--destination gitee] ...

替代原本基于 Docker 的 wearerequired/git-mirror-action, 以及三个 GitHub Actions
工作流里各自复制一份的 matrix 仓库列表。仓库列表统一放在 scripts/mirror_repos.json。

两种同步模式:

* ``fanout`` (一对多, 默认)
    每个仓库只从源站 ``git clone --mirror`` 一次, 然后并发推送到所有目的平台。
    克隆流量减少到原来的 1/N, 代价是一个任务里需要同时持有各平台的 SSH 密钥。

* ``pairwise`` (一对一)
    每个 (仓库, 目的平台) 组合各自独立克隆并推送, 与旧的工作流行为一致。
    适合只想重跑单个平台, 或者不希望多个平台的密钥出现在同一个任务里的场景。

真正耗时的是往境外平台推送而不是从 GitHub 克隆, 所以 fanout 模式下同一个仓库的
多个推送也是并发的: 外层线程池 (--jobs) 控制同时克隆的仓库数, 也就控制了磁盘占用;
内层线程池 (--push-jobs) 控制单个仓库同时推送的目的平台数。

只依赖标准库, 不需要额外安装任何包。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path(__file__).resolve().parent / "mirror_repos.json"

# 同一个仓库的所有输出共用一种颜色, 颜色不够用时靠前缀里的编号区分。
COLOURS = ["\033[35m", "\033[36m", "\033[33m", "\033[32m", "\033[34m", "\033[95m", "\033[96m", "\033[92m"]
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"

# GitHub 会额外公开 refs/pull/*、refs/github-services/* 之类的隐藏引用,
# Gitee / GitLab / Bitbucket / Codeup 会拒绝这些引用, 导致整次 push 失败,
# 所以推送前先在本地把 refs/heads 和 refs/tags 以外的引用全部删掉。
#
# 排除模式不带通配符, 这样匹配的是整个命名空间: "refs/heads" 匹配它下面的所有
# 引用, 而 "refs/heads/*" 只能匹配一层, 会漏掉 refs/heads/feature/foo 这种。
KEEP_NAMESPACES = ["refs/heads", "refs/tags"]


class MirrorError(RuntimeError):
    """一次 git 操作失败"""


# -- 输出 --------------------------------------------------------------------


class Printer:
    """给每一行输出打上任务标签的并发安全输出器

    多个线程同时往终端写日志时, 只有整行写入是原子的才不会串行。所有写入都在同一把
    锁里完成, 每行前面带上 ``[编号 仓库 →平台]`` 形式的标签和该仓库专属的颜色。
    """

    def __init__(self, *, colour: bool, tag_width: int, stream: object = None) -> None:
        self._lock = threading.Lock()
        self._colour = colour
        self._tag_width = tag_width
        self._stream = stream if stream is not None else sys.stdout

    def paint(self, text: str, code: str) -> str:
        return f"{code}{text}{RESET}" if self._colour else text

    def line(self, tag: str, colour: str, text: str) -> None:
        """输出一行带标签的任务日志"""
        label = self.paint(f"[{tag}]".ljust(self._tag_width + 2), colour)
        with self._lock:
            self._stream.write(f"{label} │ {text}\n")
            self._stream.flush()

    def notice(self, text: str, colour: str = BOLD) -> None:
        """输出一行不属于任何任务的全局信息"""
        with self._lock:
            self._stream.write(f"{self.paint(text, colour)}\n")
            self._stream.flush()


def _pump(stream: object, emit: Callable[[str], None]) -> None:
    """把子进程的输出按行喂给 emit

    git 的进度输出用回车而不是换行分隔, 所以这里三种换行都当作行结束符处理,
    否则一整段进度会挤成超长的一行。
    """
    buffer = b""
    while True:
        chunk = stream.read1(4096)  # type: ignore[attr-defined]
        if not chunk:
            break
        buffer += chunk
        parts = re.split(rb"\r\n|\n|\r", buffer)
        buffer = parts.pop()
        for part in parts:
            text = part.decode("utf-8", errors="replace").rstrip()
            if text:
                emit(text)
    text = buffer.decode("utf-8", errors="replace").rstrip()
    if text:
        emit(text)


# -- 配置 --------------------------------------------------------------------


@dataclass(frozen=True)
class Destination:
    """一个目的平台"""

    name: str
    url_template: str
    key_env: str
    enabled: bool = True

    def url(self, repo_name: str) -> str:
        return self.url_template.format(name=repo_name)


@dataclass(frozen=True)
class Repo:
    """一个待同步的仓库, 以及它在各平台上的名字"""

    src: str
    dst: str
    rename: dict[str, str] = field(default_factory=dict)
    only: tuple[str, ...] | None = None
    enabled: bool = True

    def name_on(self, destination: str) -> str:
        return self.rename.get(destination, self.dst)

    def goes_to(self, destination: str) -> bool:
        return self.only is None or destination in self.only


@dataclass(frozen=True)
class Config:
    source_url: str
    source_key_env: str
    destinations: dict[str, Destination]
    repos: tuple[Repo, ...]

    def source(self, repo: Repo) -> str:
        return self.source_url.format(name=repo.src)


def _parse_repo(entry: object, destinations: Iterable[str]) -> Repo:
    """解析一条仓库配置

    支持三种写法:
        "foo"                  两边同名
        "foo:bar"              源仓库 foo, 目的仓库 bar
        {"src": ..., "dst": ..., "rename": {...}, "destinations": [...], "enabled": ...}

    enabled 为 false 的仓库平时不同步, 但配置还留着 —— 比原来直接把整行删掉好, 既能看出
    这个仓库是"特意不同步"而不是"忘了加", 需要时用 --repo 指名或者 --include-disabled 就能捞回来。
    """
    if isinstance(entry, str):
        src, _, dst = entry.partition(":")
        return Repo(src=src, dst=dst or src)
    if not isinstance(entry, dict):
        raise ValueError(f"仓库配置只能是字符串或对象, 但得到了 {type(entry).__name__}: {entry!r}")

    src = entry.get("src") or entry.get("name")
    if not src:
        raise ValueError(f"仓库配置缺少 src 字段: {entry!r}")
    rename = entry.get("rename") or {}
    known = set(destinations)
    for name in rename:
        if name not in known:
            raise ValueError(f"仓库 {src} 的 rename 指向了未定义的平台 {name}")
    only = entry.get("destinations")
    if only is not None:
        for name in only:
            if name not in known:
                raise ValueError(f"仓库 {src} 的 destinations 指向了未定义的平台 {name}")
        only = tuple(only)
    return Repo(src=src, dst=entry.get("dst") or src, rename=dict(rename), only=only, enabled=bool(entry.get("enabled", True)))


def load_config(path: Path) -> Config:
    """读取并校验配置文件"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MirrorError(f"找不到配置文件: {path}") from None
    except json.JSONDecodeError as exc:
        raise MirrorError(f"配置文件 {path} 不是合法的 JSON: {exc}") from None

    source = raw.get("source") or {}
    source_url = source.get("url")
    if not source_url:
        raise MirrorError("配置文件缺少 source.url")

    destinations: dict[str, Destination] = {}
    for name, item in (raw.get("destinations") or {}).items():
        if not item.get("url"):
            raise MirrorError(f"平台 {name} 缺少 url")
        if not item.get("key_env"):
            raise MirrorError(f"平台 {name} 缺少 key_env")
        destinations[name] = Destination(
            name=name,
            url_template=item["url"],
            key_env=item["key_env"],
            enabled=bool(item.get("enabled", True)),
        )
    if not destinations:
        raise MirrorError("配置文件里没有定义任何目的平台")

    repos = [_parse_repo(entry, destinations) for entry in (raw.get("repos") or [])]
    if not repos:
        raise MirrorError("配置文件里没有定义任何仓库")

    seen: set[str] = set()
    for repo in repos:
        if repo.src in seen:
            raise MirrorError(f"仓库 {repo.src} 在配置里重复出现")
        seen.add(repo.src)

    return Config(
        source_url=source_url,
        source_key_env=source.get("key_env") or "SOURCE_SSH_PRIVATE_KEY",
        destinations=destinations,
        repos=tuple(repos),
    )


# -- SSH ---------------------------------------------------------------------


def write_private_key(directory: Path, name: str, key: str) -> Path:
    """把私钥写进本次运行专属的临时目录

    刻意不去碰 ~/.ssh 和 /etc/ssh/ssh_config: 原来的 Docker action 是全局改配置的,
    那样不同平台没法用不同的密钥, 也会污染运行环境。
    """
    path = directory / f"{name}.key"
    path.touch(mode=0o600)
    path.write_text(key if key.endswith("\n") else key + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def ssh_env(key_path: Path, known_hosts: Path | None) -> dict[str, str]:
    """构造带 GIT_SSH_COMMAND 的环境变量"""
    command = [
        "ssh",
        "-i",
        shlex.quote(str(key_path)),
        # 只用指定的这把密钥, 并且不允许任何交互式提问, 免得在 CI 里卡住。
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
    ]
    if known_hosts is not None:
        command += ["-o", f"UserKnownHostsFile={shlex.quote(str(known_hosts))}", "-o", "StrictHostKeyChecking=yes"]
    else:
        command += ["-o", "UserKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=no"]
    return {
        **os.environ,
        "GIT_SSH_COMMAND": " ".join(command),
        "GIT_TERMINAL_PROMPT": "0",
    }


def write_known_hosts(directory: Path) -> Path | None:
    """写入 SSH_KNOWN_HOSTS, 没有设置就返回 None (此时跳过主机密钥校验)"""
    value = os.environ.get("SSH_KNOWN_HOSTS")
    if not value:
        return None
    path = directory / "known_hosts"
    path.touch(mode=0o600)
    path.write_text(value if value.endswith("\n") else value + "\n", encoding="utf-8")
    return path


# -- 执行 git ----------------------------------------------------------------


@dataclass
class Task:
    """一个任务的输出身份: 编号、标签和颜色"""

    index: int
    label: str
    colour: str
    printer: Printer
    timeout: float

    def tagged(self, suffix: str = "") -> Task:
        label = f"{self.label} →{suffix}" if suffix else self.label
        return Task(self.index, label, self.colour, self.printer, self.timeout)

    @property
    def tag(self) -> str:
        return f"{self.index:02d} {self.label}"

    def log(self, text: str) -> None:
        self.printer.line(self.tag, self.colour, text)


def run_git(args: Sequence[str], *, cwd: Path | None, env: dict[str, str], task: Task) -> None:
    """执行一条 git 命令, 实时把输出打上标签转发出去, 失败则抛出 MirrorError"""
    task.log(f"$ git {' '.join(args)}")
    proc = subprocess.Popen(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        proc.kill()

    # 用定时器而不是 proc.communicate(timeout=...): 这里要一边读一边输出, 不能等它结束。
    timer = threading.Timer(task.timeout, kill)
    timer.start()
    try:
        _pump(proc.stdout, task.log)
        status = proc.wait()
    finally:
        timer.cancel()
        if proc.stdout is not None:
            proc.stdout.close()

    if timed_out.is_set():
        raise MirrorError(f"git {args[0]} 超时 ({task.timeout:g} 秒)")
    if status != 0:
        raise MirrorError(f"git {args[0]} 退出码 {status}")


def with_retry(action: Callable[[], None], *, attempts: int, delay: float, describe: str, task: Task) -> None:
    """失败后重试若干次, 全部失败才抛出最后一次的错误"""
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            action()
            return
        except MirrorError as exc:
            last = str(exc)
            if attempt >= attempts:
                break
            task.log(f"{describe}失败 ({last}), {delay:g} 秒后重试 (第 {attempt + 1}/{attempts} 次)")
            time.sleep(delay)
    raise MirrorError(f"{describe}失败: {last}")


# -- 同步流程 ----------------------------------------------------------------


@dataclass
class Options:
    mode: str
    jobs: int
    push_jobs: int
    dry_run: bool
    timeout: float
    retries: int
    retry_delay: float


@dataclass
class Result:
    repo: str
    destination: str
    status: str  # ok / failed / skipped
    detail: str = ""
    seconds: float = 0.0


def clone_mirror(url: str, target: Path, *, env: dict[str, str], task: Task, options: Options) -> None:
    """裸克隆源仓库"""

    def attempt() -> None:
        # 克隆失败后目录里可能残留了半成品, 不清掉的话重试会直接报 "already exists"。
        shutil.rmtree(target, ignore_errors=True)
        run_git(["clone", "--mirror", url, str(target)], cwd=None, env=env, task=task)

    with_retry(
        attempt,
        attempts=options.retries,
        delay=options.retry_delay,
        describe="克隆源仓库",
        task=task,
    )


def prune_refs(repo_dir: Path, *, env: dict[str, str], task: Task) -> None:
    """删除 refs/heads 和 refs/tags 以外的引用, 只镜像分支和标签"""
    exclude: list[str] = []
    for namespace in KEEP_NAMESPACES:
        exclude += ["--exclude", namespace]
    listing = subprocess.run(
        ["git", "for-each-ref", "--format", "delete %(refname)", *exclude, "refs"],
        cwd=str(repo_dir),
        env=env,
        capture_output=True,
    )
    if listing.returncode != 0:
        raise MirrorError(f"git for-each-ref 退出码 {listing.returncode}: {listing.stderr.decode('utf-8', 'replace').strip()}")
    if not listing.stdout.strip():
        task.log("没有需要清理的引用")
        return

    count = len(listing.stdout.strip().splitlines())
    task.log(f"清理 {count} 个 refs/heads 与 refs/tags 之外的引用")
    update = subprocess.run(
        ["git", "update-ref", "--stdin"],
        cwd=str(repo_dir),
        env=env,
        input=listing.stdout,
        capture_output=True,
    )
    if update.returncode != 0:
        raise MirrorError(f"git update-ref 退出码 {update.returncode}: {update.stderr.decode('utf-8', 'replace').strip()}")


def push_mirror(repo_dir: Path, url: str, *, env: dict[str, str], task: Task, options: Options) -> None:
    """把裸仓库镜像推送到目的平台"""
    args = ["push", "--mirror"]
    if options.dry_run:
        args.append("--dry-run")
    args.append(url)
    with_retry(
        lambda: run_git(args, cwd=repo_dir, env=env, task=task),
        attempts=options.retries,
        delay=options.retry_delay,
        describe="推送仓库",
        task=task,
    )


@dataclass
class Environment:
    """本次运行解析好的密钥环境"""

    source_env: dict[str, str]
    destination_env: dict[str, dict[str, str]]


def mirror_repo(
    repo: Repo,
    destinations: Sequence[Destination],
    *,
    config: Config,
    environment: Environment,
    options: Options,
    task: Task,
) -> list[Result]:
    """fanout 模式: 克隆一次, 并发推送到所有目的平台"""
    started = time.monotonic()
    workspace = Path(tempfile.mkdtemp(prefix=f"mirror-{repo.src}-"))
    repo_dir = workspace / f"{repo.src}.git"
    try:
        try:
            clone_mirror(config.source(repo), repo_dir, env=environment.source_env, task=task, options=options)
            prune_refs(repo_dir, env=environment.source_env, task=task)
        except MirrorError as exc:
            elapsed = time.monotonic() - started
            task.log(f"源仓库准备失败: {exc}")
            return [Result(repo.src, dest.name, "failed", str(exc), elapsed) for dest in destinations]

        def push_one(dest: Destination) -> Result:
            sub = task.tagged(dest.name)
            begin = time.monotonic()
            try:
                push_mirror(repo_dir, dest.url(repo.name_on(dest.name)), env=environment.destination_env[dest.name], task=sub, options=options)
            except MirrorError as exc:
                sub.log(f"同步失败: {exc}")
                return Result(repo.src, dest.name, "failed", str(exc), time.monotonic() - begin)
            sub.log("同步完成")
            return Result(repo.src, dest.name, "ok", "", time.monotonic() - begin)

        if len(destinations) == 1:
            return [push_one(destinations[0])]
        # 推送到境外平台才是耗时的部分, 所以同一个仓库的多个推送也并发执行,
        # 不然一次克隆省下来的时间又会被串行的推送吃掉。
        workers = max(1, min(options.push_jobs, len(destinations)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"push-{repo.src}") as pool:
            return list(pool.map(push_one, destinations))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def mirror_pair(
    repo: Repo,
    dest: Destination,
    *,
    config: Config,
    environment: Environment,
    options: Options,
    task: Task,
) -> Result:
    """pairwise 模式: 每个 (仓库, 平台) 组合各自克隆并推送"""
    started = time.monotonic()
    workspace = Path(tempfile.mkdtemp(prefix=f"mirror-{repo.src}-{dest.name}-"))
    repo_dir = workspace / f"{repo.src}.git"
    try:
        clone_mirror(config.source(repo), repo_dir, env=environment.source_env, task=task, options=options)
        prune_refs(repo_dir, env=environment.source_env, task=task)
        push_mirror(repo_dir, dest.url(repo.name_on(dest.name)), env=environment.destination_env[dest.name], task=task, options=options)
    except MirrorError as exc:
        task.log(f"同步失败: {exc}")
        return Result(repo.src, dest.name, "failed", str(exc), time.monotonic() - started)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    task.log("同步完成")
    return Result(repo.src, dest.name, "ok", "", time.monotonic() - started)


# -- 汇总 --------------------------------------------------------------------


def summarise(results: Sequence[Result], printer: Printer, elapsed: float) -> int:
    """打印结果汇总, 顺便写 GitHub Actions 的注解和步骤摘要, 返回退出码"""
    ok = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status == "failed"]
    skipped = [r for r in results if r.status == "skipped"]

    printer.notice("")
    printer.notice(f"同步结束, 用时 {elapsed:.1f} 秒: 成功 {len(ok)} 个, 失败 {len(failed)} 个, 跳过 {len(skipped)} 个")
    for result in failed:
        printer.notice(f"  失败  {result.repo} → {result.destination}: {result.detail}", RED)
    for result in skipped:
        printer.notice(f"  跳过  {result.repo} → {result.destination}: {result.detail}", YELLOW)
    if not failed and not skipped:
        printer.notice("  全部同步成功", GREEN)

    if os.environ.get("GITHUB_ACTIONS") == "true":
        for result in failed:
            print(f"::error title=同步失败::{result.repo} → {result.destination}: {result.detail}", flush=True)

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        lines = [
            f"## 仓库同步结果: 成功 {len(ok)} / 失败 {len(failed)} / 跳过 {len(skipped)}",
            "",
            "|仓库|目的平台|结果|用时|说明|",
            "|---|---|---|---|---|",
        ]
        label = {"ok": "✅ 成功", "failed": "❌ 失败", "skipped": "⏭️ 跳过"}
        for result in sorted(results, key=lambda r: (r.status != "failed", r.repo, r.destination)):
            lines.append(f"|{result.repo}|{result.destination}|{label.get(result.status, result.status)}|{result.seconds:.1f}s|{result.detail or '-'}|")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    return 1 if failed else 0


# -- 命令行 ------------------------------------------------------------------


MODE_ALIASES = {"one-to-many": "fanout", "one-to-one": "pairwise"}


def split_list(values: Sequence[str] | None) -> list[str]:
    """把 --repo a,b --repo c 这样的参数摊平成一个列表"""
    items: list[str] = []
    for value in values or []:
        items += [part.strip() for part in value.split(",") if part.strip()]
    return items


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python scripts/git_mirror.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="仓库配置文件 (默认: scripts/mirror_repos.json)")
    parser.add_argument(
        "--mode",
        default="fanout",
        choices=["fanout", "pairwise", "one-to-many", "one-to-one"],
        help="fanout / one-to-many: 克隆一次推送到所有平台 (默认); pairwise / one-to-one: 每个平台各自克隆推送",
    )
    parser.add_argument("--destination", action="append", help="只同步指定平台, 可重复或用逗号分隔 (默认: 配置里 enabled 的平台)")
    parser.add_argument("--repo", action="append", help="只同步指定仓库 (按源仓库名, 支持通配符), 可重复或用逗号分隔")
    parser.add_argument("--exclude", action="append", help="排除指定仓库 (按源仓库名, 支持通配符), 可重复或用逗号分隔")
    parser.add_argument("--include-disabled", action="store_true", help="连同配置里 enabled 为 false 的仓库一起同步")
    parser.add_argument("--jobs", type=int, default=6, help="同时克隆的仓库数, 也决定磁盘占用 (默认: 6)")
    parser.add_argument("--push-jobs", type=int, default=0, help="fanout 模式下单个仓库同时推送的平台数 (默认: 平台数量)")
    parser.add_argument("--dry-run", action="store_true", help="推送时加上 --dry-run, 不实际写入目的平台")
    parser.add_argument("--timeout", type=float, default=1800.0, help="单条 git 命令的超时秒数 (默认: 1800)")
    parser.add_argument("--retries", type=int, default=3, help="克隆和推送的尝试次数 (默认: 3)")
    parser.add_argument("--retry-delay", type=float, default=5.0, help="重试间隔秒数 (默认: 5)")
    parser.add_argument("--no-color", action="store_true", help="关闭彩色输出")
    parser.add_argument("--list", action="store_true", dest="list_only", help="只打印本次会同步哪些仓库, 不实际执行")
    return parser.parse_args(argv)


def select_destinations(config: Config, wanted: Sequence[str]) -> list[Destination]:
    """确定本次要同步的平台"""
    if not wanted:
        return [dest for dest in config.destinations.values() if dest.enabled]
    if len(wanted) == 1 and wanted[0] == "all":
        return list(config.destinations.values())
    chosen: list[Destination] = []
    for name in wanted:
        if name not in config.destinations:
            known = ", ".join(config.destinations)
            raise MirrorError(f"未知的目的平台 {name}, 可选: {known}")
        chosen.append(config.destinations[name])
    return chosen


def select_repos(
    config: Config,
    wanted: Sequence[str],
    excluded: Sequence[str],
    *,
    include_disabled: bool = False,
) -> tuple[list[Repo], list[str]]:
    """确定本次要同步的仓库, 返回 (要同步的仓库, 因为在配置里禁用而跳过的仓库名)

    --repo 和 --exclude 都支持通配符, 38 个仓库里挑一批出来时比一个个敲名字方便:
    ``--repo 'ComfyUI-*'``、``--exclude 'sd-webui-*'``。

    通配符只会命中配置里已启用的仓库; 但把仓库名原样写出来时, 即使它在配置里是
    enabled: false 也照样同步 —— 指名道姓就是明确想要它, 应该盖过配置里的默认值。
    """
    by_name = {repo.src: repo for repo in config.repos}

    def matching(pattern: str) -> list[str]:
        if pattern in by_name:
            return [pattern]
        return [name for name in by_name if fnmatch.fnmatchcase(name, pattern)]

    forced: set[str] = set()
    if wanted:
        picked: set[str] = set()
        for pattern in wanted:
            hit = matching(pattern)
            if not hit:
                raise MirrorError(f"--repo {pattern} 没有匹配到任何仓库")
            if pattern in by_name and not by_name[pattern].enabled:
                forced.add(pattern)
            picked.update(hit)
        candidates = [repo for repo in config.repos if repo.src in picked]
    else:
        candidates = list(config.repos)

    # 对着全部仓库校验, 拼错的模式直接报错而不是静悄悄少同步或者少排除一个
    removed: set[str] = set()
    for pattern in excluded:
        hit = matching(pattern)
        if not hit:
            raise MirrorError(f"--exclude {pattern} 没有匹配到任何仓库")
        removed.update(hit)

    chosen: list[Repo] = []
    disabled: list[str] = []
    for repo in candidates:
        if repo.src in removed:
            continue
        if not repo.enabled and not include_disabled and repo.src not in forced:
            disabled.append(repo.src)
            continue
        chosen.append(repo)

    if not chosen:
        raise MirrorError("筛选之后没有剩下任何仓库")
    return chosen, disabled


def prepare_environment(
    config: Config,
    destinations: Sequence[Destination],
    directory: Path,
    printer: Printer,
) -> tuple[Environment, list[Destination], list[str]]:
    """写入各平台的私钥, 丢掉没有配置密钥的平台"""
    known_hosts = write_known_hosts(directory)
    if known_hosts is None:
        printer.notice("警告: 未设置 SSH_KNOWN_HOSTS, 已关闭主机密钥校验", YELLOW)

    destination_env: dict[str, dict[str, str]] = {}
    usable: list[Destination] = []
    missing: list[str] = []
    for dest in destinations:
        key = os.environ.get(dest.key_env)
        if not key:
            missing.append(dest.name)
            continue
        destination_env[dest.name] = ssh_env(write_private_key(directory, dest.name, key), known_hosts)
        usable.append(dest)

    # 源站的密钥: 没有单独配置时, 沿用第一个可用平台的密钥 —— 旧的工作流就是拿同一把
    # 密钥既从 GitHub 克隆又往 Gitee 推送的, 这里保持一致。
    source_key = os.environ.get(config.source_key_env)
    if source_key:
        source_env = ssh_env(write_private_key(directory, "source", source_key), known_hosts)
        printer.notice(f"源仓库使用 {config.source_key_env} 中的密钥")
    elif usable:
        source_env = destination_env[usable[0].name]
        printer.notice(f"未设置 {config.source_key_env}, 源仓库改用 {usable[0].key_env} 中的密钥", YELLOW)
    else:
        source_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    return Environment(source_env=source_env, destination_env=destination_env), usable, missing


def tag_width(repos: Sequence[Repo], destinations: Sequence[Destination]) -> int:
    """所有标签对齐到同样的宽度, 输出才能像几路日志并排那样读"""
    longest_dest = max((len(dest.name) for dest in destinations), default=0)
    return max(len(f"{index:02d} {repo.src} →") + longest_dest for index, repo in enumerate(repos, start=1))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    mode = MODE_ALIASES.get(args.mode, args.mode)
    use_colour = not args.no_color and not os.environ.get("NO_COLOR") and (sys.stdout.isatty() or os.environ.get("GITHUB_ACTIONS") == "true")

    try:
        config = load_config(args.config)
        destinations = select_destinations(config, split_list(args.destination))
        repos, disabled = select_repos(
            config,
            split_list(args.repo),
            split_list(args.exclude),
            include_disabled=args.include_disabled,
        )
    except MirrorError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return 2
    if not destinations:
        print("没有可同步的目的平台, 检查配置里的 enabled 或 --destination 参数", file=sys.stderr)
        return 2

    printer = Printer(colour=use_colour, tag_width=tag_width(repos, destinations))

    if disabled:
        printer.notice(f"配置里禁用而跳过的 {len(disabled)} 个仓库: {', '.join(disabled)}", YELLOW)

    if args.list_only:
        printer.notice(f"模式 {mode}, 共 {len(repos)} 个仓库 × {len(destinations)} 个平台")
        for index, repo in enumerate(repos, start=1):
            colour = COLOURS[(index - 1) % len(COLOURS)]
            for dest in destinations:
                if not repo.goes_to(dest.name):
                    continue
                task = Task(index, f"{repo.src} →{dest.name}", colour, printer, args.timeout)
                task.log(f"{config.source(repo)}  →  {dest.url(repo.name_on(dest.name))}")
        return 0

    started = time.monotonic()
    # 私钥只在本次运行的临时目录里存在, 结束时连目录一起删掉。
    directory = Path(tempfile.mkdtemp(prefix="git-mirror-keys-"))
    try:
        directory.chmod(0o700)
        environment, usable, missing = prepare_environment(config, destinations, directory, printer)
        results: list[Result] = []
        for name in missing:
            key_env = config.destinations[name].key_env
            printer.notice(f"跳过平台 {name}: 环境变量 {key_env} 为空", YELLOW)
            results += [Result(repo.src, name, "skipped", f"{key_env} 未设置") for repo in repos if repo.goes_to(name)]
        if not usable:
            printer.notice("所有平台都缺少 SSH 私钥, 无法同步", RED)
            return summarise(results, printer, time.monotonic() - started)

        push_jobs = args.push_jobs if args.push_jobs > 0 else len(usable)
        options = Options(
            mode=mode,
            jobs=max(1, args.jobs),
            push_jobs=max(1, push_jobs),
            dry_run=args.dry_run,
            timeout=args.timeout,
            retries=max(1, args.retries),
            retry_delay=max(0.0, args.retry_delay),
        )
        plan = ", ".join(dest.name for dest in usable)
        printer.notice(f"模式 {mode}, {len(repos)} 个仓库 → {plan}, 并发 {options.jobs}" + (" (dry run)" if options.dry_run else ""))
        printer.notice("")

        def task_for(index: int, repo: Repo, suffix: str = "") -> Task:
            label = f"{repo.src} →{suffix}" if suffix else repo.src
            return Task(index, label, COLOURS[(index - 1) % len(COLOURS)], printer, options.timeout)

        with ThreadPoolExecutor(max_workers=options.jobs, thread_name_prefix="mirror") as pool:
            futures = []
            if mode == "fanout":
                for index, repo in enumerate(repos, start=1):
                    targets = [dest for dest in usable if repo.goes_to(dest.name)]
                    if not targets:
                        continue
                    futures.append(
                        pool.submit(
                            mirror_repo,
                            repo,
                            targets,
                            config=config,
                            environment=environment,
                            options=options,
                            task=task_for(index, repo),
                        )
                    )
            else:
                for index, repo in enumerate(repos, start=1):
                    for dest in usable:
                        if not repo.goes_to(dest.name):
                            continue
                        futures.append(
                            pool.submit(
                                mirror_pair,
                                repo,
                                dest,
                                config=config,
                                environment=environment,
                                options=options,
                                task=task_for(index, repo, dest.name),
                            )
                        )
            for future in futures:
                produced = future.result()
                results += produced if isinstance(produced, list) else [produced]
    finally:
        shutil.rmtree(directory, ignore_errors=True)

    return summarise(results, printer, time.monotonic() - started)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:
        # 输出被 head 之类的命令截断时安静退出, 不要打一堆回溯出来。
        # 解释器退出时还会再冲一次 stdout, 所以这里把它接到 devnull 上。
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(141)
