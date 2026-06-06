#!/usr/bin/env python3
"""Copy files between ModelScope repositories.

LFS files are copied without downloading by reusing their SHA256 blobs. Regular
files can be copied by downloading them to a temporary directory and uploading
them to the target repo.
"""

import argparse
import fnmatch
import json
import posixpath
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional


LOCAL_MODELSCOPE_REPO = Path(__file__).resolve().parents[1] / 'modelscope'
if LOCAL_MODELSCOPE_REPO.exists():
    sys.path.insert(0, str(LOCAL_MODELSCOPE_REPO))

from modelscope.hub.api import HubApi  # noqa: E402
from modelscope.hub.constants import UPLOAD_SIZE_THRESHOLD_TO_ENFORCE_LFS  # noqa: E402
from modelscope.hub.errors import raise_on_error  # noqa: E402
from modelscope.hub.file_download import (  # noqa: E402
    dataset_file_download,
    model_file_download,
)
from modelscope.hub.utils.utils import resolve_endpoint  # noqa: E402
from modelscope.utils.constant import (  # noqa: E402
    DEFAULT_DATASET_REVISION,
    DEFAULT_MODEL_REVISION,
    DEFAULT_REPOSITORY_REVISION,
    REPO_TYPE_DATASET,
    REPO_TYPE_MODEL,
)
from modelscope.utils.repo_utils import DATASET_LFS_SUFFIX, MODEL_LFS_SUFFIX  # noqa: E402


SUPPORTED_REPO_TYPES = (REPO_TYPE_MODEL, REPO_TYPE_DATASET)


def _field(item: Dict, *names, default=None):
    for name in names:
        if name in item:
            return item[name]
    return default


def _as_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ('true', '1', 'yes', 'y'):
            return True
        if lowered in ('false', '0', 'no', 'n'):
            return False
    return None


def _matches(path: str, patterns: Optional[List[str]]) -> bool:
    if not patterns:
        return False
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _is_lfs_by_metadata(item: Dict) -> bool:
    value = _field(item, 'IsLFS', 'IsLfs', 'is_lfs', 'isLfs')
    return _as_bool(value) is True


def _infer_lfs(path: str, size: Optional[int], repo_type: str) -> bool:
    suffixes = MODEL_LFS_SUFFIX if repo_type == REPO_TYPE_MODEL else DATASET_LFS_SUFFIX
    return (
        Path(path).suffix in suffixes
        or (size is not None and size > UPLOAD_SIZE_THRESHOLD_TO_ENFORCE_LFS)
    )


def _target_path(source_path: str, target_prefix: str,
                 strip_source_prefix: str) -> str:
    relpath = source_path.lstrip('/')
    if strip_source_prefix:
        prefix = strip_source_prefix.strip('/')
        if relpath == prefix:
            relpath = posixpath.basename(relpath)
        elif relpath.startswith(prefix + '/'):
            relpath = relpath[len(prefix) + 1:]

    target_prefix = target_prefix.strip('/')
    candidate = posixpath.normpath(
        posixpath.join(target_prefix, relpath) if target_prefix else relpath)
    if candidate in ('', '.', '..') or candidate.startswith('../'):
        raise ValueError(
            f'Invalid target path computed from source path {source_path!r}: '
            f'{candidate!r}')
    return candidate


def list_repo_files(api: HubApi, repo_id: str, repo_type: str, revision: str,
                    endpoint: str, token: Optional[str]) -> List[Dict]:
    if repo_type == REPO_TYPE_MODEL:
        cookies = api.get_cookies(access_token=token, cookies_required=False)
        return api.get_model_files(
            model_id=repo_id,
            revision=revision,
            recursive=True,
            use_cookies=False if cookies is None else cookies,
            endpoint=endpoint,
        )

    if repo_type == REPO_TYPE_DATASET:
        files = []
        page_number = 1
        page_size = 200
        while True:
            page = api.get_dataset_files(
                repo_id=repo_id,
                revision=revision,
                root_path='/',
                recursive=True,
                page_number=page_number,
                page_size=page_size,
                endpoint=endpoint,
                token=token,
            )
            files.extend(page)
            if len(page) < page_size:
                break
            page_number += 1
        return files

    raise ValueError(f'Unsupported repo type: {repo_type}')


def collect_copy_candidates(
    files: Iterable[Dict],
    *,
    source_repo_type: str,
    target_prefix: str,
    strip_source_prefix: str,
    allow_patterns: Optional[List[str]],
    ignore_patterns: Optional[List[str]],
    infer_lfs: bool,
    lfs_only: bool,
) -> Dict[str, List[Dict]]:
    lfs_candidates = []
    regular_candidates = []
    skipped_non_lfs = 0
    skipped_missing_meta = 0

    for item in files:
        if _field(item, 'Type', 'type') == 'tree':
            continue

        source_path = _field(item, 'Path', 'path')
        if not source_path:
            skipped_missing_meta += 1
            continue

        if allow_patterns and not _matches(source_path, allow_patterns):
            continue
        if _matches(source_path, ignore_patterns):
            continue

        sha256 = _field(item, 'Sha256', 'sha256')
        size = _field(item, 'Size', 'size')
        size = int(size) if size is not None else None

        is_lfs = _is_lfs_by_metadata(item)
        if not is_lfs and infer_lfs:
            is_lfs = _infer_lfs(source_path, size, source_repo_type)
        target_path = _target_path(
            source_path, target_prefix, strip_source_prefix)

        if is_lfs:
            if sha256 is None or size is None:
                skipped_missing_meta += 1
                continue
            lfs_candidates.append({
                'source_path': source_path,
                'target_path': target_path,
                'sha256': sha256,
                'size': size,
            })
        elif lfs_only:
            skipped_non_lfs += 1
        else:
            regular_candidates.append({
                'source_path': source_path,
                'target_path': target_path,
                'size': size,
            })

    print(f'LFS fast-copy files  : {len(lfs_candidates)}')
    print(f'Regular copy files   : {len(regular_candidates)}')
    if lfs_only:
        print(f'Skipped non-LFS      : {skipped_non_lfs}')
    print(f'Skipped missing meta : {skipped_missing_meta}')
    return {
        'lfs': lfs_candidates,
        'regular': regular_candidates,
    }


def filter_existing_target_paths(api: HubApi, candidates: List[Dict],
                                 target_repo: str, target_type: str,
                                 target_revision: str, endpoint: str,
                                 target_token: Optional[str]) -> List[Dict]:
    target_files = list_repo_files(
        api, target_repo, target_type, target_revision, endpoint, target_token)
    existing = {
        _field(item, 'Path', 'path')
        for item in target_files
        if _field(item, 'Type', 'type') != 'tree'
    }
    filtered = [
        item for item in candidates if item['target_path'] not in existing
    ]
    print(f'Skipped existing     : {len(candidates) - len(filtered)}')
    return filtered


def validate_reusable_blobs(api: HubApi, candidates: List[Dict],
                            target_repo: str, target_type: str,
                            endpoint: str,
                            target_token: Optional[str]) -> List[Dict]:
    objects = [
        {
            'oid': item['sha256'],
            'size': item['size']
        }
        for item in candidates
    ]
    validated = api._validate_blob(
        repo_id=target_repo,
        repo_type=target_type,
        objects=objects,
        endpoint=endpoint,
        token=target_token,
    )

    reusable = []
    not_reusable = 0
    for item in candidates:
        # None means "already exists globally"; a string means upload is needed.
        if validated.get(item['sha256']) is None:
            reusable.append(item)
        else:
            not_reusable += 1

    print(f'Reusable blobs       : {len(reusable)}')
    print(f'Need real upload     : {not_reusable}')
    return reusable


def create_lfs_commit(api: HubApi, target_repo: str, target_type: str,
                      target_revision: str, actions: List[Dict],
                      commit_message: str, endpoint: str,
                      target_token: Optional[str]) -> str:
    url = f'{endpoint}/api/v1/repos/{target_type}s/{target_repo}/commit/{target_revision}'
    cookies = api.get_cookies(access_token=target_token, cookies_required=True)
    payload = {
        'commit_message': commit_message,
        'actions': actions,
    }
    response = api.session.post(
        url,
        headers=api.builder_headers(api.headers),
        data=json.dumps(payload),
        cookies=cookies,
    )
    if response.status_code != 200:
        try:
            detail = response.json()
        except json.JSONDecodeError:
            detail = response.text
        raise RuntimeError(f'Commit failed with HTTP {response.status_code}: {detail}')

    data = response.json()
    raise_on_error(data)
    return data.get('Data', {}).get('oid', '')


def commit_in_batches(api: HubApi, candidates: List[Dict], target_repo: str,
                      target_type: str, target_revision: str,
                      commit_message: str, batch_size: int, endpoint: str,
                      target_token: Optional[str]) -> None:
    total_batches = (len(candidates) + batch_size - 1) // batch_size
    for batch_index in range(total_batches):
        batch = candidates[batch_index * batch_size:(batch_index + 1)
                           * batch_size]
        actions = [
            {
                'action': 'create',
                'path': item['target_path'],
                'type': 'lfs',
                'size': item['size'],
                'sha256': item['sha256'],
                'content': '',
                'encoding': '',
            }
            for item in batch
        ]
        message = commit_message
        if total_batches > 1:
            message = f'{commit_message} (batch {batch_index + 1}/{total_batches})'
        oid = create_lfs_commit(
            api=api,
            target_repo=target_repo,
            target_type=target_type,
            target_revision=target_revision,
            actions=actions,
            commit_message=message,
            endpoint=endpoint,
            target_token=target_token,
        )
        print(
            f'Committed batch {batch_index + 1}/{total_batches}: '
            f'{len(batch)} file(s), oid={oid or "<unknown>"}')


def download_regular_file(source_repo: str, source_type: str, source_path: str,
                          source_revision: str, local_dir: str, endpoint: str,
                          source_token: Optional[str]) -> str:
    if source_type == REPO_TYPE_MODEL:
        return model_file_download(
            model_id=source_repo,
            file_path=source_path,
            revision=source_revision,
            local_dir=local_dir,
            token=source_token,
            endpoint=endpoint,
        )
    if source_type == REPO_TYPE_DATASET:
        return dataset_file_download(
            dataset_id=source_repo,
            file_path=source_path,
            revision=source_revision,
            local_dir=local_dir,
            token=source_token,
            endpoint=endpoint,
        )
    raise ValueError(f'Unsupported source repo type: {source_type}')


def copy_regular_files(api: HubApi, regular_files: List[Dict],
                       source_repo: str, source_type: str,
                       source_revision: str, target_repo: str,
                       target_type: str, target_revision: str,
                       commit_message: str, endpoint: str,
                       source_token: Optional[str],
                       target_token: Optional[str]) -> None:
    if not regular_files:
        return

    with tempfile.TemporaryDirectory(prefix='modelscope-copy-regular-') as tmp:
        for index, item in enumerate(regular_files, start=1):
            print(
                f"Copying regular file {index}/{len(regular_files)}: "
                f"{item['source_path']} -> {item['target_path']}")
            local_path = download_regular_file(
                source_repo=source_repo,
                source_type=source_type,
                source_path=item['source_path'],
                source_revision=source_revision,
                local_dir=tmp,
                endpoint=endpoint,
                source_token=source_token,
            )
            api.upload_file(
                path_or_fileobj=local_path,
                path_in_repo=item['target_path'],
                repo_id=target_repo,
                token=target_token,
                repo_type=target_type,
                commit_message=f"{commit_message} ({item['target_path']})",
                revision=target_revision,
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Copy ModelScope files. LFS blobs are reused when possible.')
    parser.add_argument('source_repo', help='Source repo id, e.g. owner/name.')
    parser.add_argument('target_repo', help='Target repo id, e.g. owner/name.')
    parser.add_argument(
        '--source-type',
        choices=SUPPORTED_REPO_TYPES,
        default=REPO_TYPE_MODEL,
        help='Source repo type. Defaults to model.')
    parser.add_argument(
        '--target-type',
        choices=SUPPORTED_REPO_TYPES,
        default=None,
        help='Target repo type. Defaults to --source-type.')
    parser.add_argument(
        '--source-revision',
        default=None,
        help='Source revision. Defaults by source type.')
    parser.add_argument(
        '--target-revision',
        default=DEFAULT_REPOSITORY_REVISION,
        help='Target branch/revision. Defaults to master.')
    parser.add_argument(
        '--path-in-repo',
        default='',
        help='Prefix to prepend in the target repo.')
    parser.add_argument(
        '--strip-source-prefix',
        default='',
        help='Optional source path prefix to remove before applying target prefix.')
    parser.add_argument(
        '--include',
        nargs='*',
        default=None,
        help='Only copy source paths matching at least one glob.')
    parser.add_argument(
        '--exclude',
        nargs='*',
        default=None,
        help='Skip source paths matching any glob.')
    parser.add_argument(
        '--infer-lfs',
        action='store_true',
        help='Infer LFS from suffix/size when the file list has no IsLFS flag.')
    parser.add_argument(
        '--lfs-only',
        action='store_true',
        help='Keep the old behavior: skip non-LFS files instead of downloading and uploading them.')
    parser.add_argument(
        '--skip-existing',
        action='store_true',
        help='Skip paths that already exist in the target revision.')
    parser.add_argument(
        '--create-target',
        action='store_true',
        help='Create target repo if missing. Uses --visibility.')
    parser.add_argument(
        '--visibility',
        choices=('public', 'private', 'internal'),
        default='private',
        help='Visibility used only with --create-target. Defaults to private.')
    parser.add_argument(
        '--commit-message',
        default='Copy LFS files by reusing existing blobs',
        help='Commit message for target repo.')
    parser.add_argument(
        '--batch-size',
        type=int,
        default=256,
        help='Number of files per commit.')
    parser.add_argument(
        '--token',
        default=None,
        help='Token used for both source and target unless overridden.')
    parser.add_argument('--source-token', default=None, help='Source token.')
    parser.add_argument('--target-token', default=None, help='Target token.')
    parser.add_argument(
        '--endpoint',
        default=None,
        help='ModelScope endpoint, e.g. https://www.modelscope.cn.')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Print what would be copied without committing.')
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_type = args.target_type or args.source_type
    source_token = args.source_token or args.token
    target_token = args.target_token or args.token
    endpoint = resolve_endpoint(args.endpoint) if args.endpoint else HubApi().endpoint
    source_revision = args.source_revision
    if source_revision is None:
        source_revision = (
            DEFAULT_DATASET_REVISION
            if args.source_type == REPO_TYPE_DATASET
            else DEFAULT_MODEL_REVISION)

    if args.batch_size <= 0:
        raise ValueError('--batch-size must be greater than 0')

    api = HubApi(endpoint=endpoint, token=target_token or source_token)

    if args.create_target:
        api.create_repo(
            repo_id=args.target_repo,
            token=target_token,
            visibility=args.visibility,
            repo_type=target_type,
            endpoint=endpoint,
            exist_ok=True,
            create_default_config=False,
        )
    elif not api.repo_exists(
            repo_id=args.target_repo,
            repo_type=target_type,
            endpoint=endpoint,
            token=target_token):
        raise RuntimeError(
            f'Target repo {args.target_repo!r} does not exist. '
            'Create it first or pass --create-target.')

    print(f'Endpoint             : {endpoint}')
    print(f'Source               : {args.source_type}:{args.source_repo}@{source_revision}')
    print(f'Target               : {target_type}:{args.target_repo}@{args.target_revision}')

    source_files = list_repo_files(
        api=api,
        repo_id=args.source_repo,
        repo_type=args.source_type,
        revision=source_revision,
        endpoint=endpoint,
        token=source_token,
    )
    print(f'Source file entries  : {len(source_files)}')

    candidates = collect_copy_candidates(
        source_files,
        source_repo_type=args.source_type,
        target_prefix=args.path_in_repo,
        strip_source_prefix=args.strip_source_prefix,
        allow_patterns=args.include,
        ignore_patterns=args.exclude,
        infer_lfs=args.infer_lfs,
        lfs_only=args.lfs_only,
    )
    lfs_candidates = candidates['lfs']
    regular_candidates = candidates['regular']

    if args.skip_existing and lfs_candidates:
        lfs_candidates = filter_existing_target_paths(
            api=api,
            candidates=lfs_candidates,
            target_repo=args.target_repo,
            target_type=target_type,
            target_revision=args.target_revision,
            endpoint=endpoint,
            target_token=target_token,
        )
    if args.skip_existing and regular_candidates:
        regular_candidates = filter_existing_target_paths(
            api=api,
            candidates=regular_candidates,
            target_repo=args.target_repo,
            target_type=target_type,
            target_revision=args.target_revision,
            endpoint=endpoint,
            target_token=target_token,
        )
    if not lfs_candidates and not regular_candidates:
        print('Nothing to copy.')
        return 0

    reusable = []
    if lfs_candidates:
        reusable = validate_reusable_blobs(
            api=api,
            candidates=lfs_candidates,
            target_repo=args.target_repo,
            target_type=target_type,
            endpoint=endpoint,
            target_token=target_token,
        )

    if args.dry_run:
        print('Dry run; LFS files that would be committed without download:')
        for item in reusable:
            print(
                f"  {item['source_path']} -> {item['target_path']} "
                f"size={item['size']} sha256={item['sha256']}")
        print('Dry run; regular files that would be downloaded and uploaded:')
        for item in regular_candidates:
            size = f" size={item['size']}" if item.get('size') is not None else ''
            print(f"  {item['source_path']} -> {item['target_path']}{size}")
        return 0

    if reusable:
        commit_in_batches(
            api=api,
            candidates=reusable,
            target_repo=args.target_repo,
            target_type=target_type,
            target_revision=args.target_revision,
            commit_message=args.commit_message,
            batch_size=args.batch_size,
            endpoint=endpoint,
            target_token=target_token,
        )
    elif lfs_candidates:
        print('No reusable LFS blobs found for fast-copy.')

    copy_regular_files(
        api=api,
        regular_files=regular_candidates,
        source_repo=args.source_repo,
        source_type=args.source_type,
        source_revision=source_revision,
        target_repo=args.target_repo,
        target_type=target_type,
        target_revision=args.target_revision,
        commit_message=args.commit_message,
        endpoint=endpoint,
        source_token=source_token,
        target_token=target_token,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
