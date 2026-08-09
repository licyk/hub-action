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
    TypeAlias,
    cast,
)
from pathlib import Path
from collections import namedtuple

from sd_webui_all_in_one.retry_decorator import retryable
from sd_webui_all_in_one.repo_manager import RepoManager
from sd_webui_all_in_one.file_manager import remove_files, copy_files, move_files
from sd_webui_all_in_one.cmd import preprocess_command
from sd_webui_all_in_one.logger import get_logger

from huggingface_hub import HfApi, CommitOperationDelete
from modelscope import HubApi


logger = get_logger(__name__)



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


def get_modelscope_git_token(repo_manager: RepoManager) -> str:
    git_token = repo_manager.get_ms_git_token()
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


# 仓库类型
RepoType: TypeAlias = Literal["model", "dataset", "space"]
REPO_TYPES: tuple[RepoType, ...] = ("model", "dataset", "space")

# HuggingFace 仓库类型
HFRepoType: TypeAlias = RepoType

# ModelScope 仓库类型
MSRepoType: TypeAlias = RepoType


def get_env_repo_type(env_name: str) -> RepoType:
    repo_type = os.getenv(env_name, "model")
    if repo_type not in REPO_TYPES:
        raise ValueError(f"{env_name} 必须是以下值之一: {', '.join(REPO_TYPES)}")

    return cast(RepoType, repo_type)


def _path_exists(
    path: Path,
) -> bool:
    """判断路径是否存在, 包括失效软链接"""
    return path.exists() or path.is_symlink()


class ModelScopeGitRepo:
    """通过 git 操作 ModelScope 仓库的上下文管理器"""

    def __init__(
        self,
        repo_id: str,
        repo_type: MSRepoType,
        repo_manager: RepoManager,
        repo_path: str | Path | None = None,
    ) -> None:
        self.repo_id = repo_id
        self.repo_type = repo_type
        self.repo_manager = repo_manager
        self.repo_path = Path(repo_path).expanduser() if repo_path is not None else None
        self._temp_dir: tempfile.TemporaryDirectory | None = None
        self._git_token: str | None = None
        self._repo_url: str | None = None

    def __enter__(self) -> "ModelScopeGitRepo":
        self._prepare_repo_path()
        try:
            repo_path = self._require_repo_path()
            self._git_token = get_modelscope_git_token(self.repo_manager)
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
            self._configure_lfs_locksverify()
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
        live: bool | None = False,
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
            for value in [self._git_token, self._repo_url]
            if value
        ]

    def _configure_lfs_locksverify(self) -> None:
        if self._repo_url is None:
            raise RuntimeError("ModelScope Git 仓库 URL 尚未初始化")

        self._git(
            [
                "config",
                "--local",
                f"lfs.{self._repo_url}/info/lfs.locksverify",
                "true",
            ],
            live=False,
            sensitive_values=self._sensitive_values(),
        )

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
    repo_manager: RepoManager,
) -> None:
    """从 ModelScope 仓库中移除文件

    :param repo_id`(str)`: ModelScope 仓库 ID
    :param repo_type`(MSRepoType)`: ModelScope 仓库类型
    :param file_list`(list[str])`: 要从 ModelScope 仓库移除的文件列表
    :param repo_manager`(RepoManager)`: RepoManager 实例
    """
    if len(file_list) == 0:
        print("要删除的文件列表为空")
        return
    try:
        with ModelScopeGitRepo(
            repo_id=repo_id,
            repo_type=repo_type,
            repo_manager=repo_manager,
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
    hf_repo_type = get_env_repo_type("HF_REPO_TYPE")
    ms_repo_id = os.getenv("MS_REPO_ID")
    ms_repo_type = get_env_repo_type("MS_REPO_TYPE")
    day_threshold = int(os.getenv("DAY_THRESHOLD", "60"))

    repo_manager = RepoManager(
        hf_token=hf_token,
        ms_token=ms_token,
    )

    if hf_token and hf_repo_id:
        print(f"清理 HuggingFace 仓库 {hf_repo_id} 中的过期整合包")
        hf_api = HfApi(token=hf_token)
        hf_repo_files = repo_manager.get_repo_file(
            api_type="huggingface",
            repo_id=hf_repo_id,
            repo_type=hf_repo_type,
        )
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
        ms_repo_files = repo_manager.get_repo_file(
            api_type="modelscope",
            repo_id=ms_repo_id,
            repo_type=ms_repo_type,
        )
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
                repo_manager=repo_manager,
            )

    print("清理过期整合包完成")


if __name__ == "__main__":
    main()
