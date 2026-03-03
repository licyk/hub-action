import subprocess
import sys
import os
import argparse
from tempfile import TemporaryDirectory
from pathlib import Path


def get_args_parse() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-uv", action="store_true", help="是否禁用 uv 进行 Python 软件包安装"
    )
    parser.add_argument("--debug", action="store_true", help="显示调试信息")
    parser.add_argument(
        "--skip-if-missing",
        action="store_true",
        help="当 onnxruntime 未安装时是否跳过检查",
    )
    return parser


def install_core() -> None:
    try:
        print("尝试安装 SD WebUI All In One 内核用于 Onnxruntime GPU 检查")
        subprocess.run(
            f'"{sys.executable}" -m pip install sd-webui-all-in-one --upgrade',
            bufsize=1,
            text=True,
            shell=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        print("SD WebUI All In One 内核安装")
    except Exception as e:
        raise RuntimeError(
            "安装 SD WebUI All In One 内核发生错误, 无法修复 Onnxruntime GPU 问题"
        ) from e


def main() -> None:
    args = get_args_parse().parse_args()
    os.environ["PIP_NO_WARN_SCRIPT_LOCATION"] = "0"
    os.environ["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    os.environ["PIP_INDEX_URL"] = "https://mirrors.cloud.tencent.com/pypi/simple"
    os.environ["SD_WEBUI_ALL_IN_ONE_PROXY"] = "1"
    install_core()
    if args.debug:
        os.environ["SD_WEBUI_ALL_IN_ONE_LOGGER_LEVEL"] = "10"
    from sd_webui_all_in_one.env_check.onnxruntime_gpu_check import (  # pylint: disable=import-outside-toplevel
        check_onnxruntime_gpu,
    )

    custom_env = os.environ.copy()
    with TemporaryDirectory() as tmp_dir:
        tmp_dir = Path(tmp_dir)
        pip_ini = tmp_dir / "pip.ini"
        uv_toml = tmp_dir / "uv.toml"
        pip_ini.write_text("", encoding="utf-8")
        uv_toml.write_text("", encoding="utf-8")
        custom_env["PIP_CONFIG_FILE"] = pip_ini.as_posix()
        custom_env["UV_CONFIG_FILE"] = uv_toml.as_posix()
        check_onnxruntime_gpu(
            use_uv=not args.no_uv,
            skip_if_missing=args.skip_if_missing,
            custom_env=custom_env,
        )


if __name__ == "__main__":
    main()
