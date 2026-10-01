"""Upload built hf-xet wheels to the same ModelScope repository as Flash Attention."""

import argparse
import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    token = os.environ.get("MODELSCOPE_API_TOKEN")
    if not token:
        raise RuntimeError("MODELSCOPE_API_TOKEN is required")
    wheels = sorted(args.directory.rglob("hf_xet-*.whl"))
    if not wheels:
        raise RuntimeError(f"No hf-xet wheels found in {args.directory}")
    if len({wheel.name for wheel in wheels}) != len(wheels):
        raise RuntimeError("Duplicate wheel names in downloaded artifacts")

    from sd_webui_all_in_one.repo_manager import RepoManager

    manager = RepoManager(ms_token=token)
    # Stage wheels only; build logs and metadata stay in the Actions artifact.
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for wheel in wheels:
            shutil.copy2(wheel, directory / wheel.name)
        manager.upload_files_to_repo(
            api_type="modelscope",
            repo_id="licyks/wheels",
            repo_type="model",
            upload_path=directory,
            path_in_repo="hf_xet",
        )
    print(f"Uploaded {len(wheels)} wheel(s) to ModelScope:licyks/wheels/hf_xet")


if __name__ == "__main__":
    main()
