"""清理 Hanafubuki 和 Hanafubuki Launcher 的旧版本目录。

环境变量参数:
- HF_TOKEN: Hugging Face Token
- MODELSCOPE_API_TOKEN: ModelScope Token
- HF_REPO_ID: Hugging Face 仓库 ID
- HF_REPO_TYPE: Hugging Face 仓库类型
- MS_REPO_ID: ModelScope 仓库 ID
- MS_REPO_TYPE: ModelScope 仓库类型
- KEEP_VERSION_COUNT: 每个产品保留的最新版本目录数量，默认为 30
- DRY_RUN: 为 true 时只输出清理计划，不修改远端仓库
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias
from urllib.parse import quote

from huggingface_hub import CommitOperationDelete
from sd_webui_all_in_one.package_analyzer import CommonVersionComparison
from sd_webui_all_in_one.repo_manager import RepoManager


RepoType: TypeAlias = Literal["model", "dataset", "space"]

REPO_TYPES: tuple[RepoType, ...] = ("model", "dataset", "space")
RELEASE_ROOTS = ("hanafubuki", "hanafubuki-launcher")
VERSION_DIRECTORY_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class ReleaseRetentionPlan:
    """单个产品版本目录的保留和清理计划。"""

    release_root: str
    versions: tuple[str, ...]
    retained_versions: tuple[str, ...]
    outdated_versions: tuple[str, ...]
    invalid_directories: tuple[str, ...]

    @property
    def outdated_directories(self) -> tuple[str, ...]:
        """返回需要删除的仓库相对目录。

        Returns:
            tuple[str, ...]: 需要删除的目录。
        """
        return tuple(
            f"{self.release_root}/releases/{version}"
            for version in self.outdated_versions
        )


def get_required_env(name: str) -> str:
    """读取必需环境变量。

    Args:
        name (str): 环境变量名。

    Returns:
        str: 环境变量值。

    Raises:
        ValueError: 环境变量未配置时。
    """
    value = os.getenv(name)
    if value is None or not value.strip():
        raise ValueError(f"缺少必需环境变量: {name}")
    return value


def get_env_repo_type(name: str) -> RepoType:
    """读取并校验仓库类型环境变量。

    Args:
        name (str): 环境变量名。

    Returns:
        RepoType: 校验后的仓库类型。

    Raises:
        ValueError: 仓库类型不受支持时。
    """
    value = os.getenv(name, "model")
    if value not in REPO_TYPES:
        raise ValueError(f"{name} 必须是以下值之一: {', '.join(REPO_TYPES)}")
    return value


def get_env_positive_int(name: str, default: int) -> int:
    """读取正整数环境变量。

    Args:
        name (str): 环境变量名。
        default (int): 环境变量未配置时的默认值。

    Returns:
        int: 校验后的正整数。

    Raises:
        ValueError: 环境变量值不是正整数时。
    """
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as e:
        raise ValueError(f"{name} 必须是正整数，当前值: {raw_value}") from e
    if value <= 0:
        raise ValueError(f"{name} 必须是正整数，当前值: {raw_value}")
    return value


def get_env_bool(name: str, default: bool = False) -> bool:
    """读取布尔环境变量。

    Args:
        name (str): 环境变量名。
        default (bool): 环境变量未配置时的默认值。

    Returns:
        bool: 解析后的布尔值。

    Raises:
        ValueError: 环境变量值不是支持的布尔字符串时。
    """
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"{name} 必须是布尔值，当前值: {raw_value}")


def build_retention_plan(
    repo_files: list[str],
    release_root: str,
    keep_version_count: int,
) -> ReleaseRetentionPlan:
    """根据仓库文件列表构建单个产品的版本目录清理计划。

    Args:
        repo_files (list[str]): 仓库文件列表。
        release_root (str): 产品在仓库中的根目录。
        keep_version_count (int): 需要保留的最新版本数量。

    Returns:
        ReleaseRetentionPlan: 版本目录保留和清理计划。

    Raises:
        ValueError: 保留数量不大于零时。
    """
    if keep_version_count <= 0:
        raise ValueError("keep_version_count 必须大于 0")

    release_prefix = f"{release_root}/releases/"
    versions: set[str] = set()
    invalid_directories: set[str] = set()

    for repo_file in repo_files:
        if not repo_file.startswith(release_prefix):
            continue

        relative_path = repo_file[len(release_prefix) :]
        version, separator, _ = relative_path.partition("/")
        if not separator or not version:
            continue

        if VERSION_DIRECTORY_PATTERN.fullmatch(version):
            versions.add(version)
        else:
            invalid_directories.add(version)

    sorted_versions = tuple(
        sorted(
            versions,
            key=CommonVersionComparison,
            reverse=True,
        )
    )
    return ReleaseRetentionPlan(
        release_root=release_root,
        versions=sorted_versions,
        retained_versions=sorted_versions[:keep_version_count],
        outdated_versions=sorted_versions[keep_version_count:],
        invalid_directories=tuple(sorted(invalid_directories)),
    )


def build_repository_plans(
    repo_files: list[str],
    keep_version_count: int,
) -> tuple[ReleaseRetentionPlan, ...]:
    """构建 Hanafubuki 两个产品的清理计划。

    Args:
        repo_files (list[str]): 仓库文件列表。
        keep_version_count (int): 每个产品需要保留的最新版本数量。

    Returns:
        tuple[ReleaseRetentionPlan, ...]: 两个产品的清理计划。
    """
    return tuple(
        build_retention_plan(
            repo_files=repo_files,
            release_root=release_root,
            keep_version_count=keep_version_count,
        )
        for release_root in RELEASE_ROOTS
    )


def print_repository_plans(
    provider_name: str,
    repo_id: str,
    plans: tuple[ReleaseRetentionPlan, ...],
) -> None:
    """输出仓库清理计划。

    Args:
        provider_name (str): 仓库平台名称。
        repo_id (str): 仓库 ID。
        plans (tuple[ReleaseRetentionPlan, ...]): 清理计划。
    """
    print(f"{provider_name} 仓库 {repo_id} 的版本目录清理计划")
    for plan in plans:
        print(
            f"- {plan.release_root}: 当前 {len(plan.versions)} 个版本，"
            f"保留 {len(plan.retained_versions)} 个，删除 {len(plan.outdated_versions)} 个"
        )
        for version in plan.outdated_versions:
            print(f"  - 删除 {plan.release_root}/releases/{version}/")
        for directory in plan.invalid_directories:
            print(f"  - 保留无法识别的目录 {plan.release_root}/releases/{directory}/")


def get_outdated_directories(
    plans: tuple[ReleaseRetentionPlan, ...],
) -> tuple[str, ...]:
    """汇总仓库中需要删除的版本目录。

    Args:
        plans (tuple[ReleaseRetentionPlan, ...]): 清理计划。

    Returns:
        tuple[str, ...]: 需要删除的仓库相对目录。
    """
    return tuple(directory for plan in plans for directory in plan.outdated_directories)


def clean_huggingface_repository(
    repo_manager: RepoManager,
    repo_id: str,
    repo_type: RepoType,
    keep_version_count: int,
    dry_run: bool,
) -> None:
    """清理 Hugging Face 仓库中的旧版本目录。

    Args:
        repo_manager (RepoManager): 仓库管理器。
        repo_id (str): 仓库 ID。
        repo_type (RepoType): 仓库类型。
        keep_version_count (int): 每个产品需要保留的最新版本数量。
        dry_run (bool): 是否只输出清理计划。

    Raises:
        RuntimeError: 无法确定仓库当前提交时。
    """
    repo_info = repo_manager.hf_api.repo_info(
        repo_id=repo_id,
        repo_type=repo_type,
    )
    revision = repo_info.sha
    if not revision:
        raise RuntimeError("无法获取 Hugging Face 仓库当前提交")

    repo_files = repo_manager.get_repo_file(
        api_type="huggingface",
        repo_id=repo_id,
        repo_type=repo_type,
        revision=revision,
    )
    plans = build_repository_plans(repo_files, keep_version_count)
    print_repository_plans("Hugging Face", repo_id, plans)
    outdated_directories = get_outdated_directories(plans)

    if not outdated_directories:
        print("Hugging Face 仓库没有需要删除的版本目录")
        return
    if dry_run:
        print("DRY_RUN 已启用，跳过 Hugging Face 删除提交")
        return

    repo_manager.hf_api.create_commit(
        repo_id=repo_id,
        repo_type=repo_type,
        parent_commit=revision,
        operations=[
            CommitOperationDelete(
                path_in_repo=f"{directory}/",
                is_folder=True,
            )
            for directory in outdated_directories
        ],
        commit_message="Clean outdated Hanafubuki releases",
    )
    print(
        f"已从 Hugging Face 仓库 {repo_id} 删除 "
        f"{len(outdated_directories)} 个旧版本目录"
    )


def get_modelscope_repo_url(token: str, repo_id: str, repo_type: RepoType) -> str:
    """构建带认证信息的 ModelScope Git URL。

    Args:
        token (str): ModelScope Git Token。
        repo_id (str): 仓库 ID。
        repo_type (RepoType): 仓库类型。

    Returns:
        str: ModelScope Git URL。
    """
    encoded_token = quote(token, safe="")
    if repo_type == "model":
        return f"https://oauth2:{encoded_token}@www.modelscope.cn/{repo_id}.git"
    if repo_type == "dataset":
        return (
            f"https://oauth2:{encoded_token}@www.modelscope.cn/datasets/{repo_id}.git"
        )
    return f"https://oauth2:{encoded_token}@www.modelscope.cn/studios/{repo_id}.git"


def redact_sensitive_text(text: str, sensitive_values: tuple[str, ...]) -> str:
    """从命令输出中移除 token 和认证 URL。

    Args:
        text (str): 需要脱敏的文本。
        sensitive_values (tuple[str, ...]): 需要替换的敏感值。

    Returns:
        str: 脱敏后的文本。
    """
    redacted = text
    for value in sorted(
        (value for value in sensitive_values if value),
        key=len,
        reverse=True,
    ):
        redacted = redacted.replace(value, "***")
    return re.sub(r"oauth2:[^@\s]+@", "oauth2:***@", redacted)


def run_git(
    args: list[str],
    cwd: Path | None = None,
    custom_env: dict[str, str] | None = None,
    sensitive_values: tuple[str, ...] = (),
) -> str:
    """执行 Git 命令并对错误输出脱敏。

    Args:
        args (list[str]): Git 子命令和参数。
        cwd (Path | None): Git 命令工作目录。
        custom_env (dict[str, str] | None): 自定义进程环境变量。
        sensitive_values (tuple[str, ...]): 需要从错误输出中移除的敏感值。

    Returns:
        str: Git 命令标准输出。

    Raises:
        RuntimeError: Git 命令返回非零状态时。
    """
    result = subprocess.run(  # noqa: S603
        ["git", *args],
        cwd=cwd,
        env=custom_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        details = []
        if result.stdout.strip():
            details.append(
                f"标准输出: {redact_sensitive_text(result.stdout, sensitive_values)}"
            )
        if result.stderr.strip():
            details.append(
                f"错误输出: {redact_sensitive_text(result.stderr, sensitive_values)}"
            )
        suffix = "\n" + "\n".join(details) if details else ""
        raise RuntimeError(f"Git 命令执行失败，退出码: {result.returncode}{suffix}")
    return result.stdout


def delete_modelscope_directories(
    repo_manager: RepoManager,
    repo_id: str,
    repo_type: RepoType,
    directories: tuple[str, ...],
) -> None:
    """通过临时 Git 仓库删除 ModelScope 中的版本目录。

    Args:
        repo_manager (RepoManager): 仓库管理器。
        repo_id (str): 仓库 ID。
        repo_type (RepoType): 仓库类型。
        directories (tuple[str, ...]): 需要删除的仓库相对目录。

    Raises:
        RuntimeError: 无法获取 ModelScope Git Token 时。
    """
    git_token = repo_manager.get_ms_git_token()
    if not git_token:
        raise RuntimeError("获取 ModelScope Git Token 失败")

    repo_url = get_modelscope_repo_url(git_token, repo_id, repo_type)
    sensitive_values = (git_token, quote(git_token, safe=""), repo_url)
    clone_env = os.environ.copy()
    clone_env["GIT_LFS_SKIP_SMUDGE"] = "1"

    with tempfile.TemporaryDirectory() as temp_dir:
        repo_path = Path(temp_dir) / "repo"
        print(f"克隆 ModelScope 仓库 {repo_id}，跳过 LFS 文件下载")
        run_git(
            ["clone", "--depth", "1", repo_url, str(repo_path)],
            custom_env=clone_env,
            sensitive_values=sensitive_values,
        )
        run_git(["lfs", "install", "--local"], cwd=repo_path)
        run_git(
            ["config", "user.name", "github-actions[bot]"],
            cwd=repo_path,
        )
        run_git(
            [
                "config",
                "user.email",
                "github-actions[bot]@users.noreply.github.com",
            ],
            cwd=repo_path,
        )
        run_git(
            ["rm", "-r", "--ignore-unmatch", "--", *directories],
            cwd=repo_path,
        )

        status = run_git(["status", "--porcelain"], cwd=repo_path)
        if not status.strip():
            print("ModelScope 仓库没有需要提交的变更")
            return

        run_git(
            ["commit", "-m", "Clean outdated Hanafubuki releases"],
            cwd=repo_path,
        )
        run_git(
            ["push", "origin", "HEAD"],
            cwd=repo_path,
            sensitive_values=sensitive_values,
        )


def clean_modelscope_repository(
    repo_manager: RepoManager,
    repo_id: str,
    repo_type: RepoType,
    keep_version_count: int,
    dry_run: bool,
) -> None:
    """清理 ModelScope 仓库中的旧版本目录。

    Args:
        repo_manager (RepoManager): 仓库管理器。
        repo_id (str): 仓库 ID。
        repo_type (RepoType): 仓库类型。
        keep_version_count (int): 每个产品需要保留的最新版本数量。
        dry_run (bool): 是否只输出清理计划。
    """
    repo_files = repo_manager.get_repo_file(
        api_type="modelscope",
        repo_id=repo_id,
        repo_type=repo_type,
    )
    plans = build_repository_plans(repo_files, keep_version_count)
    print_repository_plans("ModelScope", repo_id, plans)
    outdated_directories = get_outdated_directories(plans)

    if not outdated_directories:
        print("ModelScope 仓库没有需要删除的版本目录")
        return
    if dry_run:
        print("DRY_RUN 已启用，跳过 ModelScope 删除提交")
        return

    delete_modelscope_directories(
        repo_manager=repo_manager,
        repo_id=repo_id,
        repo_type=repo_type,
        directories=outdated_directories,
    )
    print(
        f"已从 ModelScope 仓库 {repo_id} 删除 {len(outdated_directories)} 个旧版本目录"
    )


def main() -> None:
    """清理两个远端仓库中的旧 Hanafubuki 版本目录。

    Raises:
        RuntimeError: 任一远端仓库清理失败时。
    """
    hf_token = get_required_env("HF_TOKEN")
    ms_token = get_required_env("MODELSCOPE_API_TOKEN")
    hf_repo_id = get_required_env("HF_REPO_ID")
    ms_repo_id = get_required_env("MS_REPO_ID")
    hf_repo_type = get_env_repo_type("HF_REPO_TYPE")
    ms_repo_type = get_env_repo_type("MS_REPO_TYPE")
    keep_version_count = get_env_positive_int("KEEP_VERSION_COUNT", 30)
    dry_run = get_env_bool("DRY_RUN")

    print(f"每个产品保留最新 {keep_version_count} 个版本目录")
    print(f"运行模式: {'仅预览' if dry_run else '执行删除'}")
    repo_manager = RepoManager(hf_token=hf_token, ms_token=ms_token)

    errors = []
    for provider_name, clean_repository in (
        (
            "Hugging Face",
            lambda: clean_huggingface_repository(
                repo_manager=repo_manager,
                repo_id=hf_repo_id,
                repo_type=hf_repo_type,
                keep_version_count=keep_version_count,
                dry_run=dry_run,
            ),
        ),
        (
            "ModelScope",
            lambda: clean_modelscope_repository(
                repo_manager=repo_manager,
                repo_id=ms_repo_id,
                repo_type=ms_repo_type,
                keep_version_count=keep_version_count,
                dry_run=dry_run,
            ),
        ),
    ):
        try:
            clean_repository()
        except Exception as e:  # pylint: disable=broad-exception-caught
            message = redact_sensitive_text(str(e), (hf_token, ms_token))
            print(f"{provider_name} 清理失败: {type(e).__name__}: {message}")
            errors.append(f"{provider_name}: {type(e).__name__}: {message}")

    if errors:
        raise RuntimeError("部分仓库清理失败:\n" + "\n".join(errors))
    print("Hanafubuki 旧版本目录清理完成")


if __name__ == "__main__":
    main()
