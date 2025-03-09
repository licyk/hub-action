import os
import re
import importlib.metadata
from pathlib import Path
import subprocess
import sys
from typing import Optional



# 获取记录 onnxruntime 版本的文件路径
def get_onnxruntime_version_file() -> str:
    package = 'onnxruntime-gpu'
    try:
        util = [p for p in importlib.metadata.files(package) if 'onnxruntime/capi/version_info.py' in str(p)][0]
        info_path = Path(util.locate()).as_posix()
    except:
        info_path = None

    return info_path


# 获取 onnxruntime 支持的 CUDA 版本
def get_onnxruntime_support_cuda_version() -> tuple:
    ver_path = get_onnxruntime_version_file()
    cuda_ver = None
    cudnn_ver = None
    try:
        with open(ver_path, 'r', encoding = 'utf8') as f:
            for line in f:
                if 'cuda_version' in line:
                    cuda_ver = line.strip()
                if 'cudnn_version' in line:
                    cudnn_ver = line.strip()
    except:
        pass

    return cuda_ver, cudnn_ver


# 截取版本号
def get_version(ver: str) -> str:
    return ''.join(re.findall(r'[\d.]+', ver.split('=').pop().strip()))


# 判断版本
def compare_versions(version1: str, version2: str) -> int:
    nums1 = re.sub(r'[a-zA-Z]+', '', version1).split('.')  # 将版本号 1 拆分成数字列表
    nums2 = re.sub(r'[a-zA-Z]+', '', version2).split('.')  # 将版本号 2 拆分成数字列表

    for i in range(max(len(nums1), len(nums2))):
        num1 = int(nums1[i]) if i < len(nums1) else 0  # 如果版本号 1 的位数不够, 则补 0
        num2 = int(nums2[i]) if i < len(nums2) else 0  # 如果版本号 2 的位数不够, 则补 0

        if num1 == num2:
            continue
        elif num1 > num2:
            return 1  # 版本号 1 更大
        else:
            return -1  # 版本号 2 更大

    return 0  # 版本号相同


# 获取 Torch 的 CUDA, CUDNN 版本
def get_torch_cuda_ver() -> tuple:
    try:
        import torch
        return torch.__version__, int(str(torch.backends.cudnn.version())[0]) if torch.backends.cudnn.version() is not None else None
    except:
        return None, None


# 判断需要安装的 onnxruntime 版本
def need_install_ort_ver():
    # 检测是否安装了 Torch
    torch_ver, cuddn_ver = get_torch_cuda_ver()
    # 缺少 Torch 版本或者 CUDNN 版本时取消判断
    if torch_ver is None or cuddn_ver is None:
        print("PyTorch not installed")
        return None

    print(f"PyTorch version: {torch_ver}")
    print(f"PyTorch CUDDN version: {cuddn_ver}")

    # 检测是否安装了 onnxruntime-gpu
    ort_support_cuda_ver, ort_support_cudnn_ver = get_onnxruntime_support_cuda_version()
    # 通常 onnxruntime 的 CUDA 版本和 CUDNN 版本会同时存在, 所以只需要判断 CUDA 版本是否存在即可
    if ort_support_cuda_ver:
        # 当 onnxruntime 已安装
        ort_support_cuda_ver = get_version(ort_support_cuda_ver)
        ort_support_cudnn_ver = int(get_version(ort_support_cudnn_ver))

        print(f"Onnxruntime support CUDA version: {ort_support_cuda_ver}")
        print(f"Onnxruntime support CUDDN version: {ort_support_cudnn_ver}")

        # 判断 Torch 中的 CUDA 版本是否为 CUDA 12.1
        if 'cu12' in torch_ver: # CUDA 12.1
            # 比较 onnxtuntime 支持的 CUDA 版本是否和 Torch 中所带的 CUDA 版本匹配
            if compare_versions(ort_support_cuda_ver, '12.0') == 1:
                # CUDA 版本为 12.x, torch 和 ort 的 CUDA 版本匹配

                # 判断 torch 和 ort 的 CUDNN 是否匹配
                if ort_support_cudnn_ver > cuddn_ver: # ort CUDNN 版本 > torch CUDNN 版本
                    return 'cu121cudnn8'
                elif ort_support_cudnn_ver < cuddn_ver: # ort CUDNN 版本 < torch CUDNN 版本
                    return 'cu121cudnn9'
                else:
                    return None
            else:
                # CUDA 版本非 12.x
                if cuddn_ver > 8:
                    return 'cu121cudnn9'
                else:
                    return 'cu121cudnn8'
        else: # CUDA <= 11.8
            if compare_versions(ort_support_cuda_ver, '12.0') == -1:
                return None
            else:
                return 'cu118'
    else:
        print("Onnxruntime GPU not installed")
        # 当 onnxruntime 未安装
        # 判断 Torch 中的 CUDA 版本是否为 CUDA 12.1
        if 'cu12' in torch_ver: # CUDA 12.1
            if cuddn_ver > 8:
                return 'cu121cudnn9'
            else:
                return 'cu121cudnn8'
        else: # CUDA <= 11.8
            return 'cu118'


def run(command,
        desc: Optional[str] = None,
        errdesc: Optional[str] = None,
        custom_env: Optional[list] = None,
        live: Optional[bool] = True,
        shell: Optional[bool] = None):

    if shell is None:
        shell = False if sys.platform == "win32" else True

    if desc is not None:
        print(desc)

    if live:
        result = subprocess.run(command, shell=shell, env=os.environ if custom_env is None else custom_env)
        if result.returncode != 0:
            raise RuntimeError(f"""{errdesc or 'Error running command'}.
Command: {command}
Error code: {result.returncode}""")

        return ""

    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            shell=shell, env=os.environ if custom_env is None else custom_env)

    if result.returncode != 0:
        message = f"""{errdesc or 'Error running command'}.
Command: {command}
Error code: {result.returncode}
stdout: {result.stdout.decode(encoding="utf8", errors="ignore") if len(result.stdout) > 0 else '<empty>'}
stderr: {result.stderr.decode(encoding="utf8", errors="ignore") if len(result.stderr) > 0 else '<empty>'}
"""
        raise RuntimeError(message)

    return result.stdout.decode(encoding="utf8", errors="ignore")


def run_pip(command, desc=None, live=False):
    return run(f'"{sys.executable}" -m pip {command}', desc=f"Installing {desc}", errdesc=f"Couldn't install {desc}", live=live)


if __name__ == "__main__":
    print("Check Onnxruntime GPU")
    ver = need_install_ort_ver()

    if ver == "cu118":
        os.environ["PIP_INDEX_URL"] = "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-11/pypi/simple/"
        os.environ["PIP_EXTRA_INDEX_URL"] = "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-11/pypi/simple/"
        run_pip("uninstall onnxruntime-gpu -y", "onnxruntime-gpu", live=True)
        run_pip("install onnxruntime-gpu>=1.18.1 --no-cache-dir", "onnxruntime-gpu>=1.18.1", live=True)
    elif ver == "cu121cudnn9":
        os.environ["PIP_INDEX_URL"] = "https://mirrors.cloud.tencent.com/pypi/simple"
        os.environ["PIP_EXTRA_INDEX_URL"] = "https://mirrors.cernet.edu.cn/pypi/web/simple"
        run_pip("uninstall onnxruntime-gpu -y", "onnxruntime-gpu", live=True)
        run_pip("install onnxruntime-gpu>=1.19.0 --no-cache-dir", "onnxruntime-gpu>=1.19.0", live=True)
    elif ver == "cu121cudnn8":
        os.environ["PIP_INDEX_URL"] = "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/"
        os.environ["PIP_EXTRA_INDEX_URL"] = "https://aiinfra.pkgs.visualstudio.com/PublicPackages/_packaging/onnxruntime-cuda-12/pypi/simple/"
        run_pip("uninstall onnxruntime-gpu -y", "onnxruntime-gpu", live=True)
        run_pip("install onnxruntime-gpu==1.17.1 --no-cache-dir", "onnxruntime-gpu==1.17.1", live=True)
    else:
        print("No Onnxruntime GPU version issue")
