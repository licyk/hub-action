"""Regression checks for scheduled hf-xet builds and matrix artifact uploads."""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import check_hf_xet_update as check
from scripts import upload_hf_xet_whl as upload


def wheel(python: str, version: str = "1.6.0+termux.g123") -> str:
    abi = "cp" + python.replace(".", "")
    return f"hf_xet/hf_xet-{version}-{abi}-{abi}-android_24_arm64_v8a.whl"


class UpdateTests(unittest.TestCase):
    def test_empty_repository_builds_all(self):
        result = check.plan_builds([], "1.6.0")
        self.assertEqual(result["python_versions"], check.PYTHON_VERSIONS)
        self.assertIsNone(result["repository_latest"])

    def test_complete_release_ignores_local_suffix(self):
        files = [wheel(python) for python in check.PYTHON_VERSIONS]
        self.assertFalse(check.plan_builds(files, "1.6.0")["should_build"])

    def test_partial_release_only_builds_missing_or_outdated_abis(self):
        files = [wheel("3.10"), wheel("3.11", "1.5.2"), wheel("3.14")]
        result = check.plan_builds(files, "1.6.0")
        self.assertEqual(result["python_versions"], ["3.11", "3.12", "3.13"])

    def test_semantic_comparison_and_no_downgrade(self):
        files = [wheel(python, "1.10.0") for python in check.PYTHON_VERSIONS]
        self.assertFalse(check.plan_builds(files, "1.9.0")["should_build"])
        self.assertTrue(check.plan_builds(files, "1.11.0")["should_build"])

    def test_force_rebuilds_all(self):
        files = [wheel(python) for python in check.PYTHON_VERSIONS]
        self.assertEqual(
            check.plan_builds(files, "1.6.0", True)["python_versions"],
            check.PYTHON_VERSIONS,
        )

    def test_incompatible_or_unrelated_wheels_do_not_suppress_build(self):
        files = [
            wheel("3.14").replace("android_24_arm64_v8a", "manylinux_2_28_aarch64"),
            wheel("3.14").replace("android_24_arm64_v8a", "android_24_x86_64"),
            wheel("3.14").replace("android_24_arm64_v8a", "android_30_arm64_v8a"),
            wheel("3.14").replace("cp314-cp314", "cp314-cp314t"),
            wheel("3.14").replace("hf_xet/", "other/"),
            wheel("3.14", "2.0.0rc1"),
            "hf_xet/invalid.whl",
        ]
        self.assertEqual(
            check.plan_builds(files, "1.6.0")["python_versions"], check.PYTHON_VERSIONS
        )

    def test_latest_release_excludes_prerelease_yanked_and_empty(self):
        data = {
            "releases": {
                "1.9.0": [{"yanked": False}],
                "1.10.0": [{"yanked": False}],
                "1.11.0": [{"yanked": True}],
                "2.0.0rc1": [{"yanked": False}],
                "3.0.0": [],
            }
        }
        with patch.object(check, "fetch", return_value=json.dumps(data).encode()):
            self.assertEqual(check.latest_release(), "1.10.0")

    def test_repository_listing_failure_is_not_an_empty_repository(self):
        manager = Mock()
        manager.get_repo_file.side_effect = RuntimeError("listing failed")
        with (
            patch.object(check, "latest_release", return_value="1.6.0"),
            patch.object(check, "RepoManager", return_value=manager),
        ):
            with self.assertRaisesRegex(RuntimeError, "listing failed"):
                check.main()

    def test_upload_collects_matrix_subdirectories_and_propagates_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = set()
            for python in check.PYTHON_VERSIONS:
                directory = root / f"hf-xet-termux-arm64-py{python}"
                directory.mkdir()
                filename = Path(wheel(python)).name
                (directory / filename).write_bytes(b"test wheel")
                (directory / "build-info.json").write_text("{}")
                expected.add(filename)
            manager = Mock()
            module = types.ModuleType("sd_webui_all_in_one.repo_manager")
            module.__dict__["RepoManager"] = Mock(return_value=manager)

            def validate(**kwargs):
                self.assertEqual(kwargs["repo_id"], "licyks/wheels")
                self.assertEqual(kwargs["path_in_repo"], "hf_xet")
                self.assertEqual(
                    {file.name for file in kwargs["upload_path"].iterdir()}, expected
                )

            manager.upload_files_to_repo.side_effect = validate
            with (
                patch.dict(sys.modules, {"sd_webui_all_in_one.repo_manager": module}),
                patch.dict(os.environ, {"MODELSCOPE_API_TOKEN": "test"}),
                patch.object(sys, "argv", ["upload", str(root)]),
            ):
                upload.main()
                manager.upload_files_to_repo.side_effect = RuntimeError("upload failed")
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    upload.main()


if __name__ == "__main__":
    unittest.main()
