"""清理过期整合包

环境变量参数:
- HF_TOKEN: HuggingFace Token
- MODELSCOPE_API_TOKEN: ModelScope Token
- HF_REPO_ID: HuggingFace 仓库 ID
- HF_REPO_TYPE: HuggingFace 仓库类型
- MS_REPO_ID: ModelScope 仓库 ID
- MS_REPO_TYPE: ModelScope 仓库类型
- DAY_THRESHOLD: 整合包过期时间 (天)
"""
import os
import re
import time
import datetime
import logging
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from functools import wraps
from typing import (
    Any,
    Literal,
    Callable,
    TypeVar,
    ParamSpec,
    cast,
)
from pathlib import Path
from collections import namedtuple

from huggingface_hub import HfApi, CommitOperationDelete
from modelscope import HubApi


logger = logging.getLogger(__name__)

T = TypeVar("T")
P = ParamSpec("P")


class RetrySignalError(Exception):
    """仅供装饰器内部使用的重试信号异常"""

    pass  # pylint: disable=unnecessary-pass


def retryable(
    times: int | None = 3,
    delay: float | None = 1.0,
    describe: str | None = None,
    catch_exceptions: type[Exception] | tuple[type[Exception], ...] = Exception,
    raise_exception: type[Exception] = RuntimeError,
    retry_on_none: bool | None = False,
) -> Callable[[Callable[P, T | None]], Callable[..., T]]:
    """通用的重试装饰器

    该装饰器会为原函数注入以下参数:
        - retry_times (int | None): 重试次数
        - retry_delay (float | None): 重试延迟

    Args:
        times (int | None):
            最大重试次数
        delay (float | None):
            失败后的延迟时间 (秒)
        describe (str | None):
            日志中显示的描述文字
        catch_exceptions (type[Exception] | tuple[type[Exception], ...]):
            需要捕获并触发重试的异常类型
        raise_exception (type[Exception]):
            超过重试次数后抛出的异常类型
        retry_on_none (bool | None):
            是否在返回 None 时触发重试

    Returns:
        (Callable[[Callable[P, T | None]], Callable[..., T]]):
            装饰器函数
    """

    def decorator(func: Callable[P, T | None]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(
            *args: P.args,
            retry_times: int | None = None,
            retry_delay: float | None = None,
            **kwargs: P.kwargs,
        ) -> T:
            actual_times = retry_times if retry_times is not None else times
            actual_delay = retry_delay if retry_delay is not None else delay
            count = 0
            err = None
            target_info = describe if describe is not None else func.__name__
            if isinstance(catch_exceptions, tuple):
                catch_exc = catch_exceptions + (RetrySignalError,)
            else:
                catch_exc = (catch_exceptions, RetrySignalError)

            while count < actual_times:
                count += 1
                try:
                    result = func(*args, **kwargs)
                    if retry_on_none and result is None:
                        # 如果返回 None 且启用了检查, 则手动抛出异常触发下面的 except
                        raise ValueError(f"'{target_info}' 返回结果为空")

                    return cast(T, result)
                except catch_exc as e:  # pylint: disable=catching-non-exception
                    err = e
                    # 判断是否是内部信号触发的
                    error_msg = (
                        str(e)
                        if isinstance(e, RetrySignalError)
                        else f"{type(e).__name__}: {e}"
                    )
                    print(
                        f"[{count}/{actual_times}] {target_info} 出现错误: {error_msg}"
                    )

                    if count < actual_times:
                        print(f"[{count}/{actual_times}] 重试 {target_info} 中")
                        if actual_delay > 0:
                            time.sleep(actual_delay)
                    else:
                        # 达到重试上限, 抛出指定的异常
                        raise raise_exception(
                            f"执行 '{target_info}' 时发生错误: {err}"
                        ) from err

                except Exception as e:  # pylint: disable=duplicate-except
                    # 如果出现了不在 catch_exceptions 列表中的异常, 立即抛出, 不重试
                    print(f"[{count}/{actual_times}] 遇到不可重试的致命错误: {e}")
                    raise

            # 正常情况下逻辑在循环内结束, 这里作为兜底抛出
            raise raise_exception(f"执行 '{target_info}' 最终失败")

        return cast(Callable[..., T], wrapper)

    return decorator




def get_modelscope_repo_url(
    token: str,
    repo_id: str,
    repo_type: Literal["model", "dataset", "space"] = "model",
) -> str:
    if repo_type == "model":
        return f"https://oauth2:{token}@www.modelscope.cn/{repo_id}.git"
    elif repo_type == "dataset":
        return f"https://oauth2:{token}@www.modelscope.cn/datasets/{repo_id}.git"
    elif repo_type == "space":
        return f"https://oauth2:{token}@www.modelscope.cn/studios/{repo_id}.git"
    else:
        raise ValueError(f"Invalid repo_type: {repo_type}")


def get_modelscope_git_token(token: str) -> str:
    api = HubApi()
    git_token, _ = api.login(access_token=token)
    if not git_token:
        raise RuntimeError("获取 ModelScope Git Token 失败")
    return git_token


def redact_sensitive_text(
    text: str | None,
    sensitive_values: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """脱敏命令输出中的 token 和带认证信息的 URL"""
    if text is None:
        return None

    redacted = text
    for value in sorted(
        [value for value in sensitive_values or [] if value],
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(value, "***")

    return re.sub(r"oauth2:[^@\s]+@", "oauth2:***@", redacted)


def preprocess_command(
    command: list[str] | str,
    shell: bool,
) -> list[str] | str:
    """针对不同平台对命令进行预处理

    Args:
        command (list[str] | str): 原始命令
        shell (bool): 是否调用 Shell
    Returns:
        (list[str] | str): 处理后的命令
    """
    if sys.platform == "win32":
        # Windows 平台
        # 字符串命令和列表命令都可行
        return command
    else:
        # Linux / macOS 平台
        if shell:
            # 使用字符串命令
            if isinstance(command, list):
                return shlex.join(command)
            return command
        # 使用列表命令
        if isinstance(command, str):
            return shlex.split(command)
        return command


def run_cmd(
    command: str | list[str],
    custom_env: dict[str, str] | None = None,
    live: bool | None = False,
    shell: bool | None = None,
    cwd: Path | None = None,
    check: bool | None = True,
    sensitive_values: list[str] | tuple[str, ...] | None = None,
) -> str | None:
    """执行 Shell 命令

    Args:
        command (str | list[str]): 要执行的命令
        custom_env (dict[str, str] | None): 自定义环境变量
        live (bool | None): 是否实时输出命令执行日志
        shell (bool | None): 是否使用内置 Shell 执行命令
        cwd (Path | None): 执行进程时的起始路径
        check (bool | None): 是否检查进程退出状态
        sensitive_values (list[str] | tuple[str, ...] | None): 需要从输出中脱敏的敏感内容
    Returns:
        str | None: 命令输出内容, 当 live=True 或执行失败时可能返回 None
    Raises:
        RuntimeError: 命令执行失败且 check=True 时
    """

    if shell is None:
        shell = sys.platform != "win32"

    if custom_env is None:
        custom_env = os.environ.copy()

    command_to_exec = preprocess_command(command=command, shell=shell)

    kwargs: dict[str, Any] = {
        "args": command_to_exec,
        "shell": shell,
        "env": custom_env,
        "cwd": cwd,
        "encoding": "utf-8",
        "errors": "ignore",
    }

    if not live or sensitive_values:
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE

    result: subprocess.CompletedProcess[str] = subprocess.run(**kwargs)  # pylint: disable=subprocess-run-check

    if check and result.returncode != 0:
        errors = [
            f"执行命令时发生错误, 错误代码: {result.returncode}",
        ]
        if result.stdout:
            stdout = redact_sensitive_text(result.stdout, sensitive_values)
            errors.append(f"标准输出: {stdout}")
        if result.stderr:
            stderr = redact_sensitive_text(result.stderr, sensitive_values)
            errors.append(f"错误输出: {stderr}")

        raise RuntimeError("\n".join(errors))

    return redact_sensitive_text(result.stdout, sensitive_values)



def remove_files(
    path: Path,
) -> None:
    """文件删除工具，支持删除只读文件和非空文件夹。

    Args:
        path (Path): 要删除的文件或目录路径
    Raises:
        ValueError: 路径不存在时
        OSError: 删除过程中的系统错误
    """

    if not _path_exists(path):
        logger.error("路径不存在: '%s'", path)
        raise ValueError(f"要删除的 {path} 路径不存在")

    def _handle_remove_readonly(
        func,
        path_str,
        _,
    ):
        """处理只读文件的错误处理函数"""
        if os.path.exists(path_str):
            os.chmod(path_str, stat.S_IWRITE)
            func(path_str)

    try:
        if path.is_symlink():
            # 处理符号链接, 不跟随链接修改目标权限
            logger.debug("删除软链接: '%s'", path)
            path.unlink()

        elif path.is_file():
            # 处理文件
            logger.debug("删除文件: '%s'", path)
            os.chmod(path, stat.S_IWRITE)
            path.unlink()

        elif path.is_dir():
            # 处理文件夹
            logger.debug("删除目录: '%s'", path)
            shutil.rmtree(path, onerror=_handle_remove_readonly)

    except OSError as e:
        logger.error("删除失败: '%s' - 原因: %s", path, e)
        raise e


def _path_exists(
    path: Path,
) -> bool:
    """判断路径是否存在, 包括失效软链接"""
    return path.exists() or path.is_symlink()


def _copy_symlink(
    src: Path,
    dst: Path,
) -> None:
    """复制软链接本身, 不复制软链接指向的内容"""
    if dst.exists() and dst.is_dir() and not dst.is_symlink():
        raise IsADirectoryError(f"目标路径已存在且为目录: {dst}")

    if _path_exists(dst):
        remove_files(dst)

    dst.symlink_to(os.readlink(src), target_is_directory=src.is_dir())


def copy_files(
    src: Path,
    dst: Path,
) -> None:
    """复制文件或目录

    Args:
        src (Path): 源文件路径
        dst (Path): 复制文件到指定的路径
    Raises:
        PermissionError: 没有权限复制文件时
        OSError: 复制文件失败时
        FileNotFoundError: 源文件未找到时
        ValueError: 路径逻辑错误（如循环复制）时
    """
    try:
        # 保留软链接路径本身, 只在循环检测时解析真实路径
        src_path = src.absolute()
        dst_path = dst.absolute()

        # 检查源是否存在
        if not _path_exists(src_path):
            logger.error("源路径不存在: '%s'", src)
            raise FileNotFoundError(f"源路径不存在: {src}")

        # 防止递归复制（例如将目录复制到其自身的子目录中）
        if src_path.is_dir() and not src_path.is_symlink() and dst_path.resolve().is_relative_to(src_path.resolve()):
            logger.error("不能将目录复制到自身或其子目录中: '%s'", src)
            raise ValueError(f"不能将目录复制到自身或其子目录中: {src}")

        # 如果目标是已存在的目录, 则在其下创建同名项
        if dst_path.exists() and dst_path.is_dir():
            dst_file = dst_path / src_path.name
        else:
            dst_file = dst_path

        # 确保目标父目录存在
        dst_file.parent.mkdir(parents=True, exist_ok=True)

        # 复制操作
        if src_path.is_symlink():
            _copy_symlink(src_path, dst_file)
        elif src_path.is_file():
            # copy2 会尽量保留文件元数据
            logger.debug("复制文件: '%s' -> '%s'", src_path, dst_file)
            shutil.copy2(src_path, dst_file)
        else:
            # symlinks=True: 保留软链接本身而非复制指向的内容
            # dirs_exist_ok=True: 实现合并逻辑，如果目标目录已存在则覆盖同名文件
            logger.debug("复制目录: '%s' -> '%s'", src_path, dst_file)
            try:
                shutil.copytree(src_path, dst_file, symlinks=True, dirs_exist_ok=True)
            except shutil.Error:
                # Linux 中遇到已存在的软链接会导致失败, 则使用 symlinks=False 重试
                logger.debug("保留软链接复制目录失败, 改为复制软链接目标内容: '%s' -> '%s'", src_path, dst_file)
                shutil.copytree(src_path, dst_file, symlinks=False, dirs_exist_ok=True)

    except PermissionError as e:
        logger.error("权限错误, 请检查文件权限或以管理员身份运行: %s", e)
        raise e
    except OSError as e:
        logger.error("复制失败: %s", e)
        raise e



def move_files(
    src: Path,
    dst: Path,
) -> None:
    """移动文件或目录

    Args:
        src (Path): 源路径
        dst (Path): 目标路径
    Raises:
        FileNotFoundError: 源路径不存在时
        PermissionError: 权限不足以移动文件时
        OSError: 移动文件失败时
        ValueError: 路径逻辑错误（如循环移动）时
    """
    try:
        src_path = src.absolute()
        dst_path = dst.absolute()

        if not _path_exists(src_path):
            logger.error("源路径不存在: '%s'", src)
            raise FileNotFoundError(f"源路径不存在: {src}")

        if src_path == dst_path:
            return

        # 确定目的路径
        if dst_path.exists() and dst_path.is_dir():
            final_dst = dst_path / src_path.name
        else:
            final_dst = dst_path

        if src_path.is_file() or src_path.is_symlink():
            if final_dst.is_file() or final_dst.is_symlink():
                remove_files(final_dst)

            final_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(final_dst))
            return

        if src_path.is_dir():
            src_real = src_path.resolve()
            final_dst_real = final_dst.resolve()
            if final_dst_real.is_relative_to(src_real):
                logger.error("不能将目录移动到自身或其子目录中: '%s'", src)
                raise ValueError(f"不能将目录移动到自身或其子目录中: {src}")

            if not final_dst.exists():
                final_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src_path), str(final_dst))
            elif not final_dst.is_dir():
                remove_files(final_dst)
                shutil.move(str(src_path), str(final_dst))
            else:
                logger.debug("目标目录已存在，执行合并操作: '%s' -> '%s'", src_path, final_dst)
                for item in src_path.iterdir():
                    move_files(item, final_dst / item.name)

                if src_path.exists():
                    src_path.rmdir()

    except PermissionError as e:
        logger.error("权限错误, 请检查文件权限或以管理员身份运行: %s", e)
        raise e
    except OSError as e:
        logger.error("移动失败: %s", e)
        raise e


# 解析整合包文件名的正则表达式
PORTABLE_NAME_PATTERN = r'''
    ^
    (?P<software>[\w_]+?)       # 软件名 (允许下划线)
    -                           
    (?P<signature>[a-z0-9]+)    # 署名 (小写字母 + 数字)
    -                           
    (?:
        # 每日构建模式：日期 + nightly
        (?P<build_date>\d{8})   # 构建日期 (YYYYMMDD)
        -
        nightly                 
    |
        # 正式版本模式：v + 版本号
        v
        (?P<version>[\d.]+)     # 版本号 (数字和点)
    )
    \.
    (?P<extension>[a-z0-9]+(?:\.[a-z0-9]+)?)  # 扩展名 (支持多级扩展)
    $
'''

# 编译正则表达式 (忽略大小写, 详细模式)
portable_name_parse_regex = re.compile(
    PORTABLE_NAME_PATTERN,
    re.VERBOSE | re.IGNORECASE
)

# 定义文件名组件的命名元组
PortableNameComponent = namedtuple(
    'PortableNameComponent', [
        'software',     # 软件名称
        'signature',    # 署名标记
        'build_type',   # 构建类型 (nightly/stable)
        'build_date',   # 构建日期 (仅 nightly 有效)
        'version',      # 版本号 (仅 stable 有效)
        'extension'     # 文件扩展名
    ]
)


def parse_portable_filename(filename: str) -> PortableNameComponent:
    """
    解析文件名并返回结构化数据

    :param filename: 要解析的文件名
    :return: PortableNameComponent 命名元组
    :raises ValueError: 当文件名不符合模式时
    """
    match = portable_name_parse_regex.match(filename)
    if not match:
        raise ValueError(f"无效文件名格式: {filename}")

    groups = match.groupdict()

    # 确定构建类型并提取相应字段
    if groups['build_date']:
        build_type = 'nightly'
        build_date = groups['build_date']
        version = None
    else:
        build_type = 'stable'
        build_date = None
        version = groups['version']

    return PortableNameComponent(
        software=groups['software'],
        signature=groups['signature'],
        build_type=build_type,
        build_date=build_date,
        version=version,
        extension=groups['extension'].lower()
    )


# HuggingFace 仓库类型
HFRepoType = Literal["model", "dataset", "space"]

# ModelScope 仓库类型
MSRepoType = Literal["model", "dataset", "space"]


class ModelScopeGitRepo:
    """通过 git 操作 ModelScope 仓库的上下文管理器"""

    def __init__(
        self,
        repo_id: str,
        repo_type: MSRepoType,
        token: str,
        repo_path: str | Path | None = None,
    ) -> None:
        self.repo_id = repo_id
        self.repo_type = repo_type
        self.token = token
        self.repo_path = Path(repo_path).expanduser() if repo_path is not None else None
        self._temp_dir: tempfile.TemporaryDirectory | None = None
        self._git_token: str | None = None
        self._repo_url: str | None = None

    def __enter__(self) -> "ModelScopeGitRepo":
        self._prepare_repo_path()
        try:
            repo_path = self._require_repo_path()
            self._git_token = get_modelscope_git_token(self.token)
            self._repo_url = get_modelscope_repo_url(
                token=self._git_token,
                repo_id=self.repo_id,
                repo_type=self.repo_type,
            )
            clone_env = os.environ.copy()
            clone_env["GIT_LFS_SKIP_SMUDGE"] = "1"

            print(f"克隆 ModelScope 仓库 {self.repo_id} (类型: {self.repo_type})")
            run_cmd(
                ["git", "clone", self._repo_url, str(repo_path)],
                custom_env=clone_env,
                live=False,
                shell=False,
                sensitive_values=self._sensitive_values(),
            )
            self._git(["lfs", "install", "--local"])
            self._ensure_git_identity()
            return self
        except Exception:
            self._cleanup()
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: Any,
    ) -> None:
        self._cleanup()

    def add(
        self,
        src: str | Path,
        dst: str | Path | None = None,
    ) -> None:
        """添加本地文件或目录到仓库"""
        src_path = Path(src).expanduser()
        if dst is None:
            if not src_path.name:
                raise ValueError(f"无法从源路径推断仓库目标路径: {src}")
            dst_path = self._resolve_repo_path(src_path.name)
        else:
            dst_path = self._resolve_repo_path(dst)

        copy_files(src_path, dst_path)

    def delete(
        self,
        path: str | Path,
    ) -> None:
        """删除仓库内文件或目录"""
        target_path = self._resolve_repo_path(path)
        if not _path_exists(target_path):
            print(f"仓库路径不存在, 跳过删除: {Path(path).as_posix()}")
            return

        remove_files(target_path)

    def copy(
        self,
        src: str | Path,
        dst: str | Path,
    ) -> None:
        """复制仓库内文件或目录"""
        copy_files(
            self._resolve_repo_path(src),
            self._resolve_repo_path(dst),
        )

    def move(
        self,
        src: str | Path,
        dst: str | Path,
    ) -> None:
        """移动仓库内文件或目录"""
        move_files(
            self._resolve_repo_path(src),
            self._resolve_repo_path(dst),
        )

    def commit(
        self,
        message: str = "Clean outdated sd portable",
    ) -> bool:
        """提交并推送仓库变更, 无变更时返回 False"""
        self._git(["add", "-A"])
        status = self._git(["status", "--porcelain"], live=False)
        if not status or not status.strip():
            print("ModelScope 仓库没有需要提交的变更")
            return False

        self._git(["commit", "-m", message])
        self._git(
            ["push", "origin", "HEAD"],
            live=False,
            sensitive_values=self._sensitive_values(),
        )
        return True

    def _prepare_repo_path(self) -> None:
        if self.repo_path is None:
            self._temp_dir = tempfile.TemporaryDirectory()
            self.repo_path = Path(self._temp_dir.name)
            return

        if self.repo_path.exists():
            if not self.repo_path.is_dir():
                raise NotADirectoryError(f"临时仓库路径不是目录: {self.repo_path}")
            if any(self.repo_path.iterdir()):
                raise ValueError(f"临时仓库路径必须为空目录: {self.repo_path}")
        else:
            self.repo_path.mkdir(parents=True, exist_ok=True)

    def _require_repo_path(self) -> Path:
        if self.repo_path is None:
            raise RuntimeError("ModelScope Git 仓库尚未初始化")
        return self.repo_path

    def _resolve_repo_path(
        self,
        path: str | Path,
    ) -> Path:
        repo_root = self._require_repo_path().resolve()
        repo_path = Path(path)
        if repo_path.is_absolute() or ".." in repo_path.parts:
            raise ValueError(f"仓库路径不能是绝对路径或包含 '..': {path}")

        target_path = (repo_root / repo_path).resolve(strict=False)
        if not target_path.is_relative_to(repo_root):
            raise ValueError(f"仓库路径不能逃出仓库目录: {path}")

        return target_path

    def _git(
        self,
        args: list[str],
        live: bool | None = True,
        check: bool | None = True,
        sensitive_values: list[str] | tuple[str, ...] | None = None,
    ) -> str | None:
        return run_cmd(
            ["git", *args],
            cwd=self._require_repo_path(),
            live=live,
            shell=False,
            check=check,
            sensitive_values=sensitive_values,
        )

    def _sensitive_values(self) -> list[str]:
        return [
            value
            for value in [self.token, self._git_token, self._repo_url]
            if value
        ]

    def _ensure_git_identity(self) -> None:
        name = self._git(["config", "user.name"], live=False, check=False)
        email = self._git(["config", "user.email"], live=False, check=False)
        if not name or not name.strip():
            self._git(["config", "user.name", "github-actions[bot]"])
        if not email or not email.strip():
            self._git(
                [
                    "config",
                    "user.email",
                    "github-actions[bot]@users.noreply.github.com",
                ]
            )

    def _cleanup(self) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None
            self.repo_path = None
            return

        if self.repo_path is not None and _path_exists(self.repo_path):
            remove_files(self.repo_path)
        self.repo_path = None


def get_repo_file(
    api: HfApi | HubApi,
    repo_id: str,
    repo_type: HFRepoType = "model",
) -> list[str]:
    """获取 HuggingFace / ModelScope 仓库文件列表

    :param api`(HfApi|HubApi)`: HuggingFace / ModelScope Api 实例
    :param repo_id`(str)`: HuggingFace / ModelScope 仓库 ID
    :param repo_type`(str)`: HuggingFace / ModelScope 仓库类型
    :return `list[str]`: 仓库文件列表
    """
    if isinstance(api, HfApi):
        print(f"获取 HuggingFace 仓库 {repo_id} (类型: {repo_type}) 的文件列表")
        return get_hf_repo_files(api, repo_id, repo_type)
    if isinstance(api, HubApi):
        print(f"获取 ModelScope 仓库 {repo_id} (类型: {repo_type}) 的文件列表")
        return get_ms_repo_files(api, repo_id, repo_type)

    print(f"未知 Api 类型: {api}")
    return []


@retryable(
    times=3,
    delay=1.0,
    describe="获取 HuggingFace 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_hf_repo_files(
    api: HfApi,
    repo_id: str,
    repo_type: HFRepoType,
) -> list[str]:
    """获取 HuggingFace 仓库文件列表

    :param api`(HfApi)`: HuggingFace Api 实例
    :param repo_id`(str)`: HuggingFace 仓库 ID
    :param repo_type`(str)`: HuggingFace 仓库类型
    :return `list[str]`: 仓库文件列表
    """
    return api.list_repo_files(
        repo_id=repo_id,
        repo_type=repo_type,
    )


@retryable(
    times=3,
    delay=1.0,
    describe="获取 ModelScope 仓库文件列表",
    catch_exceptions=Exception,
    raise_exception=RuntimeError,
)
def get_ms_repo_files(
    api: HubApi,
    repo_id: str,
    repo_type: MSRepoType = "model",
) -> list[str]:
    """ 获取 ModelScope 仓库文件列表

    :param api`(HfApi)`: ModelScope Api 实例
    :param repo_id`(str)`: ModelScope 仓库 ID
    :param repo_type`(str)`: ModelScope 仓库类型
    :return `list[str]`: 仓库文件列表
    """
    file_list = []

    def _get_file_path(repo_files: list) -> list[str]:
        """获取 ModelScope Api 返回的仓库列表中的模型路径"""
        return [
            file["Path"]
            for file in repo_files
            if file['Type'] != 'tree'
        ]

    if repo_type == "model":
        repo_files = api.get_model_files(
            model_id=repo_id,
            recursive=True
        )
        file_list = _get_file_path(repo_files)
    elif repo_type == "dataset":
        repo_files = api.get_dataset_files(
            repo_id=repo_id,
            recursive=True
        )
        file_list = _get_file_path(repo_files)
    elif repo_type == "space":
        print(f"{repo_id} 仓库类型为创空间, 不支持获取文件列表")
    else:
        raise ValueError(f"未知的 {repo_type} 仓库类型")

    return file_list


def fitter_portable_list(repo_files: list[str]) -> tuple[list[str], list[str]]:
    """从仓库文件中过滤出整合包文件列表

    :param repo_files`(list[str])`: 仓库文件列表
    :return `tuple[(list[str]),list[str]]`: Stable, Nightly 整合包文件列表
    """
    stable = []
    nightly = []
    for file in repo_files:
        if file.startswith("portable/"):
            try:
                portable = parse_portable_filename(os.path.basename(file))
                if portable.build_type == "stable":
                    stable.append(file)
                if portable.build_type == "nightly":
                    nightly.append(file)
            except ValueError as e:
                print(f"{file} 不符合整合包文件名规范: {e}")
    return stable, nightly


def is_outdated_portable(name: str, day_threshold: int) -> bool:
    """判断整合包是否过期

    :param name`(str)`: 整合包名称
    :param day_threshold`(int)`: 整合包发布天数限制
    :return `bool`: 当整合包发布时间超过限制时为过期整合包
    """
    portable = parse_portable_filename(name)
    date = datetime.datetime.strptime(portable.build_date, r"%Y%m%d")
    date_threshold = datetime.datetime.today() - datetime.timedelta(days=day_threshold)
    return date < date_threshold


def get_outdated_portable(file_list: list[str], day_threshold: int = 60) -> list[str]:
    """获取已经过期的整合包列表

    :param file_list`(list[str])`: 整合包列表
    :param `list[str]`: 过期的整合包列表
    """
    outdated_list = []
    print(f"整合包数量: {len(file_list)}")
    for file in file_list:
        name = os.path.basename(file)
        if is_outdated_portable(name, day_threshold):
            outdated_list.append(file)

    print(f"已过期的整合包数量: {len(outdated_list)}")
    return outdated_list


def remove_files_from_hf_repo(
    api: HfApi,
    repo_id: str,
    repo_type: HFRepoType,
    file_list: list[str],
) -> None:
    """从 HuggingFace 仓库中移除文件

    :param api`(HfApi)`: HuggingFace Api 实例
    :param repo_id`(str)`: HuggingFace 仓库 ID
    :param repo_type`(HFRepoType)`: HuggingFace 仓库类型
    :param file_list`(list[str])`: 要从 HuggingFace 仓库移除的文件列表
    """
    if len(file_list) == 0:
        print("要删除的文件列表为空")
        return
    op = [
        CommitOperationDelete(file)
        for file in file_list
    ]
    try:
        api.create_commit(
            repo_id=repo_id,
            repo_type=repo_type,
            operations=op,
            commit_message="Clean outdated sd portable",
        )
        print(
            f"从 HuggingFace 仓库 {repo_id} (类型: {repo_type}) 清理 {len(file_list)} 个过期整合包")
    except (ValueError, ConnectionError, TypeError) as e:
        print(
            f"从 HuggingFace 仓库 {repo_id} (类型: {repo_type}) 清理过期整合包时发送了错误: {e}")


def remove_files_from_ms_repo(
    repo_id: str,
    repo_type: MSRepoType,
    file_list: list[str],
    token: str,
) -> None:
    """从 ModelScope 仓库中移除文件

    :param repo_id`(str)`: ModelScope 仓库 ID
    :param repo_type`(MSRepoType)`: ModelScope 仓库类型
    :param file_list`(list[str])`: 要从 ModelScope 仓库移除的文件列表
    :param token`(str)`: ModelScope API Token
    """
    if len(file_list) == 0:
        print("要删除的文件列表为空")
        return
    try:
        with ModelScopeGitRepo(
            repo_id=repo_id,
            repo_type=repo_type,
            token=token,
        ) as repo:
            for file in file_list:
                repo.delete(file)
            repo.commit("Clean outdated sd portable")
        print(
            f"从 ModelScope 仓库 {repo_id} (类型: {repo_type}) 清理 {len(file_list)} 个过期整合包")
    except (ValueError, ConnectionError, TypeError, RuntimeError, OSError) as e:
        print(
            f"从 ModelScope 仓库 {repo_id} (类型: {repo_type}) 清理过期整合包时发送了错误: {e}")


def main() -> None:
    """主函数"""
    hf_token = os.getenv("HF_TOKEN")
    ms_token = os.getenv("MODELSCOPE_API_TOKEN")
    hf_repo_id = os.getenv("HF_REPO_ID")
    hf_repo_type = os.getenv("HF_REPO_TYPE", "model")
    ms_repo_id = os.getenv("MS_REPO_ID")
    ms_repo_type = os.getenv("MS_REPO_TYPE", "model")
    day_threshold = int(os.getenv("DAY_THRESHOLD", "60"))

    if hf_token and hf_repo_id:
        print(f"清理 HuggingFace 仓库 {hf_repo_id} 中的过期整合包")
        hf_api = HfApi(token=hf_token)
        hf_repo_files = get_repo_file(hf_api, hf_repo_id, hf_repo_type)
        _, hf_nightly_portable = fitter_portable_list(hf_repo_files)
        hf_outdated_portable = get_outdated_portable(
            file_list=hf_nightly_portable,
            day_threshold=day_threshold
        )
        if len(hf_outdated_portable) != 0:
            print(f"HuggingFace 仓库 {hf_repo_id} 中的过期整合包")
            for i in hf_outdated_portable:
                print(f"- {i}")
            remove_files_from_hf_repo(
                api=hf_api,
                repo_id=hf_repo_id,
                repo_type=hf_repo_type,
                file_list=hf_outdated_portable
            )

    if ms_token and ms_repo_id:
        print(f"清理 ModelScope 仓库 {ms_repo_id} 中的过期整合包")
        ms_api = HubApi()
        ms_api.login(access_token=ms_token)
        ms_repo_files = get_repo_file(ms_api, ms_repo_id, ms_repo_type)
        _, ms_nightly_portable = fitter_portable_list(ms_repo_files)
        ms_outdated_portable = get_outdated_portable(
            file_list=ms_nightly_portable,
            day_threshold=day_threshold
        )
        if len(ms_outdated_portable) != 0:
            print(f"ModelScope 仓库 {ms_repo_id} 中的过期整合包")
            for i in ms_outdated_portable:
                print(f"- {i}")
            remove_files_from_ms_repo(
                repo_id=ms_repo_id,
                repo_type=ms_repo_type,
                file_list=ms_outdated_portable,
                token=ms_token,
            )

    print("清理过期整合包完成")


if __name__ == "__main__":
    main()
