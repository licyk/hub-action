#!/usr/bin/env python3
"""Copy files between Hugging Face Hub repositories.

This script uses CommitOperationCopy instead of manually constructing commit
payloads. Hugging Face Hub handles the important split internally:

- LFS files are duplicated server-side and committed as lfsFile entries.
- Regular files are downloaded from the source repo and committed as base64
  file entries.
"""

import argparse
import fnmatch
import posixpath
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


LOCAL_HF_HUB_SRC = Path(__file__).resolve().parents[1] / "huggingface_hub" / "src"
if LOCAL_HF_HUB_SRC.exists():
    sys.path.insert(0, str(LOCAL_HF_HUB_SRC))

from huggingface_hub import CommitOperationCopy, HfApi  # noqa: E402
from huggingface_hub.hf_api import RepoFile  # noqa: E402


SUPPORTED_REPO_TYPES = ("model", "dataset", "space")


@dataclass(frozen=True)
class CopyCandidate:
    source_path: str
    target_path: str
    is_lfs: bool
    size: Optional[int]
    sha256: Optional[str]
    xet_hash: Optional[str]


def _matches(path: str, patterns: Optional[list[str]]) -> bool:
    if not patterns:
        return False
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _target_path(source_path: str, target_prefix: str, strip_source_prefix: str) -> str:
    relpath = source_path.lstrip("/")
    if strip_source_prefix:
        prefix = strip_source_prefix.strip("/")
        if relpath == prefix:
            relpath = posixpath.basename(relpath)
        elif relpath.startswith(prefix + "/"):
            relpath = relpath[len(prefix) + 1 :]

    target_prefix = target_prefix.strip("/")
    candidate = posixpath.normpath(posixpath.join(target_prefix, relpath) if target_prefix else relpath)
    if candidate in ("", ".", "..") or candidate.startswith("../"):
        raise ValueError(f"Invalid target path computed from {source_path!r}: {candidate!r}")
    return candidate


def _repo_type_arg(repo_type: str) -> Optional[str]:
    return None if repo_type == "model" else repo_type


def _display_revision(revision: Optional[str]) -> str:
    return revision or "main"


def _iter_source_files(
    api: HfApi,
    *,
    repo_id: str,
    repo_type: str,
    revision: Optional[str],
    source_path: str,
    token: Optional[str],
) -> list[RepoFile]:
    source_path = source_path.strip("/")

    if source_path:
        path_info = api.get_paths_info(
            repo_id=repo_id,
            paths=[source_path],
            repo_type=_repo_type_arg(repo_type),
            revision=revision,
            token=token,
        )
        if len(path_info) == 1 and isinstance(path_info[0], RepoFile):
            return [path_info[0]]

    return [
        item
        for item in api.list_repo_tree(
            repo_id=repo_id,
            path_in_repo=source_path or None,
            recursive=True,
            repo_type=_repo_type_arg(repo_type),
            revision=revision,
            token=token,
        )
        if isinstance(item, RepoFile)
    ]


def _collect_candidates(
    files: Iterable[RepoFile],
    *,
    target_prefix: str,
    strip_source_prefix: str,
    allow_patterns: Optional[list[str]],
    ignore_patterns: Optional[list[str]],
    lfs_only: bool,
) -> list[CopyCandidate]:
    candidates = []
    skipped_regular = 0

    for item in files:
        source_path = item.path
        if allow_patterns and not _matches(source_path, allow_patterns):
            continue
        if _matches(source_path, ignore_patterns):
            continue

        is_lfs = item.lfs is not None
        if lfs_only and not is_lfs:
            skipped_regular += 1
            continue

        candidates.append(
            CopyCandidate(
                source_path=source_path,
                target_path=_target_path(source_path, target_prefix, strip_source_prefix),
                is_lfs=is_lfs,
                size=item.size,
                sha256=item.lfs.sha256 if item.lfs else None,
                xet_hash=item.xet_hash,
            )
        )

    _fail_on_duplicate_targets(candidates)
    print(f"Files selected       : {len(candidates)}")
    print(f"LFS files            : {sum(1 for item in candidates if item.is_lfs)}")
    print(f"Regular files        : {sum(1 for item in candidates if not item.is_lfs)}")
    if lfs_only:
        print(f"Skipped regular      : {skipped_regular}")
    return candidates


def _fail_on_duplicate_targets(candidates: Iterable[CopyCandidate]) -> None:
    first_source_by_target = {}
    for item in candidates:
        previous = first_source_by_target.setdefault(item.target_path, item.source_path)
        if previous != item.source_path:
            raise ValueError(
                "Multiple source files map to the same target path: "
                f"{previous!r} and {item.source_path!r} -> {item.target_path!r}"
            )


def _filter_existing_targets(
    api: HfApi,
    candidates: list[CopyCandidate],
    *,
    target_repo: str,
    target_type: str,
    target_revision: Optional[str],
    token: Optional[str],
) -> list[CopyCandidate]:
    existing = {
        item.path
        for item in api.list_repo_tree(
            repo_id=target_repo,
            recursive=True,
            repo_type=_repo_type_arg(target_type),
            revision=target_revision,
            token=token,
        )
        if isinstance(item, RepoFile)
    }
    filtered = [item for item in candidates if item.target_path not in existing]
    print(f"Skipped existing     : {len(candidates) - len(filtered)}")
    return filtered


def _fail_on_cross_repo_lfs_without_xet(
    candidates: Iterable[CopyCandidate],
    *,
    source_repo: str,
    source_type: str,
    target_repo: str,
    target_type: str,
) -> None:
    if source_repo == target_repo and source_type == target_type:
        return

    missing = [item for item in candidates if item.is_lfs and not item.xet_hash]
    if not missing:
        return

    preview = "\n".join(f"  - {item.source_path}" for item in missing[:20])
    suffix = "" if len(missing) <= 20 else f"\n  ... and {len(missing) - 20} more"
    raise RuntimeError(
        "Cannot use server-side cross-repo LFS copy for files without xet_hash.\n"
        "Hugging Face Hub requires xet_hash when duplicating LFS objects to another repo.\n"
        f"Affected files:\n{preview}{suffix}"
    )


def _build_operation(
    candidate: CopyCandidate,
    *,
    source_repo: str,
    source_type: str,
    source_revision: Optional[str],
    target_repo: str,
    target_type: str,
) -> CommitOperationCopy:
    same_repo = source_repo == target_repo and source_type == target_type
    return CommitOperationCopy(
        src_path_in_repo=candidate.source_path,
        path_in_repo=candidate.target_path,
        src_revision=source_revision,
        src_repo_id=None if same_repo else source_repo,
        src_repo_type=None if same_repo else source_type,
    )


def _commit_in_batches(
    api: HfApi,
    candidates: list[CopyCandidate],
    *,
    source_repo: str,
    source_type: str,
    source_revision: Optional[str],
    target_repo: str,
    target_type: str,
    target_revision: Optional[str],
    commit_message: str,
    batch_size: int,
    token: Optional[str],
    create_pr: bool,
) -> None:
    total_batches = (len(candidates) + batch_size - 1) // batch_size
    for batch_index in range(total_batches):
        batch = candidates[batch_index * batch_size : (batch_index + 1) * batch_size]
        operations = [
            _build_operation(
                item,
                source_repo=source_repo,
                source_type=source_type,
                source_revision=source_revision,
                target_repo=target_repo,
                target_type=target_type,
            )
            for item in batch
        ]
        message = commit_message
        if total_batches > 1:
            message = f"{commit_message} (batch {batch_index + 1}/{total_batches})"

        commit_info = api.create_commit(
            repo_id=target_repo,
            repo_type=_repo_type_arg(target_type),
            revision=target_revision,
            operations=operations,
            commit_message=message,
            token=token,
            create_pr=create_pr,
        )
        print(
            f"Committed batch {batch_index + 1}/{total_batches}: "
            f"{len(batch)} file(s), oid={commit_info.oid}"
        )
        if commit_info.pr_url:
            print(f"Pull request         : {commit_info.pr_url}")


def _print_preview(candidates: list[CopyCandidate], max_preview: int) -> None:
    print("Dry run: no commits will be created.")
    for item in candidates[:max_preview]:
        kind = "lfs" if item.is_lfs else "regular"
        print(f"  [{kind}] {item.source_path} -> {item.target_path}")
    if len(candidates) > max_preview:
        print(f"  ... and {len(candidates) - max_preview} more")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy files between Hugging Face Hub repositories using CommitOperationCopy."
    )
    parser.add_argument("source_repo", help="Source repo id, e.g. owner/name.")
    parser.add_argument("target_repo", help="Target repo id, e.g. owner/name.")
    parser.add_argument(
        "--source-type",
        choices=SUPPORTED_REPO_TYPES,
        default="model",
        help="Source repo type. Defaults to model.",
    )
    parser.add_argument(
        "--target-type",
        choices=SUPPORTED_REPO_TYPES,
        default=None,
        help="Target repo type. Defaults to --source-type.",
    )
    parser.add_argument("--source-revision", default=None, help="Source revision. Defaults to main.")
    parser.add_argument("--target-revision", default=None, help="Target branch/revision. Defaults to main.")
    parser.add_argument(
        "--source-path",
        default="",
        help="Optional source file or folder to copy. Defaults to the repo root.",
    )
    parser.add_argument(
        "--path-in-repo",
        default="",
        help="Prefix to prepend in the target repo.",
    )
    parser.add_argument(
        "--strip-source-prefix",
        default="",
        help="Optional source path prefix to remove before applying --path-in-repo.",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=None,
        help="Only copy source paths matching at least one glob.",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=None,
        help="Skip source paths matching any glob.",
    )
    parser.add_argument(
        "--lfs-only",
        action="store_true",
        help="Copy only LFS files. By default both LFS and regular files are copied.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip target paths that already exist in the target revision.",
    )
    parser.add_argument(
        "--create-target",
        action="store_true",
        help="Create target repo if missing. Uses --visibility and --region.",
    )
    parser.add_argument(
        "--visibility",
        choices=("public", "private", "protected"),
        default="private",
        help="Visibility used only with --create-target. Defaults to private.",
    )
    parser.add_argument(
        "--region",
        choices=("us", "eu"),
        default=None,
        help="Storage region used only with --create-target.",
    )
    parser.add_argument(
        "--space-sdk",
        default="gradio",
        help="Space SDK used only when --create-target and --target-type space.",
    )
    parser.add_argument(
        "--commit-message",
        default=None,
        help="Commit message for target repo. Defaults to 'Copy files from <source>'.",
    )
    parser.add_argument("--batch-size", type=int, default=256, help="Number of files per commit.")
    parser.add_argument(
        "--create-pr",
        action="store_true",
        help="Create a pull request instead of committing directly to the target branch.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Token used to read the source repo and write the target repo.",
    )
    parser.add_argument("--endpoint", default=None, help="Hub endpoint. Defaults to huggingface.co.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be copied without committing.")
    parser.add_argument("--max-preview", type=int, default=30, help="Number of dry-run entries to print.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_type = args.target_type or args.source_type

    if args.batch_size <= 0:
        raise ValueError("--batch-size must be greater than 0")
    if args.max_preview < 0:
        raise ValueError("--max-preview must be greater than or equal to 0")

    api = HfApi(endpoint=args.endpoint, token=args.token)

    if args.create_target:
        create_kwargs = {}
        if args.region:
            create_kwargs["region"] = args.region
        if target_type == "space":
            create_kwargs["space_sdk"] = args.space_sdk
        api.create_repo(
            repo_id=args.target_repo,
            repo_type=_repo_type_arg(target_type),
            token=args.token,
            visibility=args.visibility,
            exist_ok=True,
            **create_kwargs,
        )
    elif not api.repo_exists(repo_id=args.target_repo, repo_type=_repo_type_arg(target_type), token=args.token):
        raise RuntimeError(
            f"Target repo {args.target_repo!r} does not exist. Create it first or pass --create-target."
        )

    print(f"Endpoint             : {api.endpoint}")
    print(f"Source               : {args.source_type}:{args.source_repo}@{_display_revision(args.source_revision)}")
    print(f"Target               : {target_type}:{args.target_repo}@{_display_revision(args.target_revision)}")

    source_files = _iter_source_files(
        api,
        repo_id=args.source_repo,
        repo_type=args.source_type,
        revision=args.source_revision,
        source_path=args.source_path,
        token=args.token,
    )
    print(f"Source files         : {len(source_files)}")

    candidates = _collect_candidates(
        source_files,
        target_prefix=args.path_in_repo,
        strip_source_prefix=args.strip_source_prefix,
        allow_patterns=args.include,
        ignore_patterns=args.exclude,
        lfs_only=args.lfs_only,
    )

    if args.skip_existing and candidates:
        candidates = _filter_existing_targets(
            api,
            candidates,
            target_repo=args.target_repo,
            target_type=target_type,
            target_revision=args.target_revision,
            token=args.token,
        )

    if not candidates:
        print("Nothing to copy.")
        return 0

    _fail_on_cross_repo_lfs_without_xet(
        candidates,
        source_repo=args.source_repo,
        source_type=args.source_type,
        target_repo=args.target_repo,
        target_type=target_type,
    )

    if args.dry_run:
        _print_preview(candidates, args.max_preview)
        return 0

    commit_message = args.commit_message or f"Copy files from {args.source_type}s/{args.source_repo}"
    _commit_in_batches(
        api,
        candidates,
        source_repo=args.source_repo,
        source_type=args.source_type,
        source_revision=args.source_revision,
        target_repo=args.target_repo,
        target_type=target_type,
        target_revision=args.target_revision,
        commit_message=commit_message,
        batch_size=args.batch_size,
        token=args.token,
        create_pr=args.create_pr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
