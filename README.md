# hub-action
Github Action / 工具合集，工具可查看 [tools](https://github.com/licyk/hub-action/tree/main/tools) 目录


## 当前状态
|Github Action|Status|
|---|---|
|Github -> Gitee / Gitlab|[![Sync To Mirror](https://github.com/licyk/hub-action/actions/workflows/sync-to-mirror.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/sync-to-mirror.yml)|
|Github Mirror Test|[![Test Avaliable Github Mirror](https://github.com/licyk/hub-action/actions/workflows/test-avaliable-github-mirror.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/test-avaliable-github-mirror.yml)|
|HuggingFace Mirror Test|[![Test Avaliable HuggingFace Mirror](https://github.com/licyk/hub-action/actions/workflows/test-avaliable-huggingface-mirror.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/test-avaliable-huggingface-mirror.yml)|
|List HuggingFace Repo|[![List HuggingFace Repo](https://github.com/licyk/hub-action/actions/workflows/list-hugginface-repo.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/list-hugginface-repo.yml)|
|Build PyPI|[![Build PyPI](https://github.com/licyk/hub-action/actions/workflows/build-pypi.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-pypi.yml)|
|Build SD Protable Download Page|[![Build SD Protable Download Page](https://github.com/licyk/hub-action/actions/workflows/build-sd-portable-download-pages.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-sd-portable-download-pages.yml)|
|Build SD Protable Download Link|[![Build SD Protable Download Link](https://github.com/licyk/hub-action/actions/workflows/build-sd-portable-link.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-sd-portable-link.yml)|
|Sync Flash Attn Wheel|[![Sync Flash Attn Wheel](https://github.com/licyk/hub-action/actions/workflows/sync-flash-attn-whl.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/sync-flash-attn-whl.yml)|
|Build HF Xet Android Wheel|[![Build HF Xet Android Wheel](https://github.com/licyk/hub-action/actions/workflows/build-hf-xet-android.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-hf-xet-android.yml)|
|Sync HuggingFace / ModelScope Repo|[![Sync HuggingFace / ModelScope Repo](https://github.com/licyk/hub-action/actions/workflows/sync-hf-to-ms.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/sync-hf-to-ms.yml)|
|Build SageAttention|[![Build SageAttention](https://github.com/licyk/hub-action/actions/workflows/build-sageattn.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-sageattn.yml)|
|Build Triton|[![Build Triton](https://github.com/licyk/hub-action/actions/workflows/build-triton.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-triton.yml)|
|Clean Outdated SD Portable and Hanafubuki Releases|[![Clean Outdated SD Portable and Hanafubuki Releases](https://github.com/licyk/hub-action/actions/workflows/clean-outdated-sd-portable.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/clean-outdated-sd-portable.yml)|
|Clean HuggingFace Repo Space|[![Clean HuggingFace Repo Space](https://github.com/licyk/hub-action/actions/workflows/clean-hf-repo.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/clean-hf-repo.yml)|
|Build LoRA Download Page|[![Build LoRA Download Page](https://github.com/licyk/hub-action/actions/workflows/build-lora-download-page.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-lora-download-page.yml)|
|Build SpargeAttention|[![Build SpargeAttention](https://github.com/licyk/hub-action/actions/workflows/build-spargeattn.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-spargeattn.yml)|
|Build and Test Triton|[![Build and Test Triton](https://github.com/licyk/hub-action/actions/workflows/build-and-test-triton.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-and-test-triton.yml)|
|Remove LoRA norm block and sync (for InvokeAI)|[![Remove LoRA norm block and sync (for InvokeAI)](https://github.com/licyk/hub-action/actions/workflows/remove-lora-norm-block-and-sync.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/remove-lora-norm-block-and-sync.yml)|
|Build SageAttention3|[![Build SageAttention3](https://github.com/licyk/hub-action/actions/workflows/build-sageattn3.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-sageattn3.yml)|
|Trigger aria2-next Package Update|[![Trigger aria2-next package update](https://github.com/licyk/hub-action/actions/workflows/trigger-aria2-next-package-update.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/trigger-aria2-next-package-update.yml)|
|Build HF and MS Repo List|[![Build HF and MS Repo List](https://github.com/licyk/hub-action/actions/workflows/build-huggingface-and-modelscope-repo-list.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/build-huggingface-and-modelscope-repo-list.yml)|
|Query VCRedist x64 DLLs|[![Query VCRedist x64 DLLs](https://github.com/licyk/hub-action/actions/workflows/query-vcredist-x64-dlls.yml/badge.svg)](https://github.com/licyk/hub-action/actions/workflows/query-vcredist-x64-dlls.yml)|

VCRedist x64 DLL 查询默认下载链接来源：[Microsoft Visual C++ Redistributable latest supported downloads](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170#latest-supported-redistributable-version)

## 编译 Termux hf-xet wheel

工作流 [Build HF Xet Android Wheel](https://github.com/licyk/hub-action/actions/workflows/build-hf-xet-android.yml) 每天 UTC 16:20（北京时间次日 00:20）自动检查，也支持手动运行：

- `git_ref`：默认 `latest`，跟随 PyPI 上最新的未撤回稳定版，解析对应的 `huggingface/xet-core` 标签并校验源码版本。也可指定分支、标签或 commit；源码需要支持 `native-tls-vendored`。
- `force`：默认关闭；开启后强制重建 Python 3.10～3.14 的全部 wheel。
- `upload`：默认开启，构建成功后将 wheel 上传到 [ModelScope licyks/wheels](https://modelscope.cn/models/licyks/wheels/files) 的 `hf_xet/` 目录，沿用 Flash Attention 同步任务的 `MODELSCOPE_API_TOKEN` Secret。关闭后只保留 Actions artifact。

版本检查使用 `sd-webui-all-in-one` 的 `RepoManager.get_repo_file()` 查询目标仓库，通过 `PyWhlVersionComparison.compare_versions(..., ignore_local=True)` 比较已有 wheel 与上游版本，忽略 `+termux` 后缀。按 Python ABI 分别检查：已有同版或更新版则跳过，旧版或缺失则加入 Actions 动态矩阵并行编译；全齐时跳过构建和上传。查询失败会报错，不会被当成空仓库。所有矩阵任务固定使用检查阶段解析出的同一个源码 commit。

目标为 **Termux / Android arm64，Python 3.10、3.11、3.12、3.13、3.14**。开发包优先来自 Termux 官方仓库，旧版使用 [TUR](https://github.com/termux-user-repository/tur)。Python 3.12 使用固定的[社区发布包](https://github.com/adybag14-cyber/termux-python/releases/tag/termux-aarch64-20260914.47.1)（3.12.14，SHA256 固定在构建脚本中）。每个任务校验开发包 SHA256、实际 Python 版本、Android API 和 arm64 ELF 架构，再用 Android NDK 编译。产物绑定对应 Python 小版本，不保证兼容其他版本或 APK 内嵌 Python。版本号附带 `+termux.g<commit>`，用于区分适配构建。

构建时关闭默认 Rustls 后端，启用静态编译的 OpenSSL，并使用 Termux 的证书目录，避免 Android Java TLS 初始化依赖。每个 Python 版本有独立 artifact，包含 wheel、构建信息、依赖树、Cargo.lock 和适配补丁；矩阵构建成功后统一上传 wheel 到 ModelScope，避免并行提交同一个仓库。上传失败会使工作流失败，不会删除仓库中已有文件；下次检查会重新补齐仍缺失的 wheel。

在 Termux 中安装与当前 Python 小版本匹配的 wheel：

```bash
pkg update
pkg install python python-pip ca-certificates
python --version
# 只下载与当前解释器匹配的一个 wheel，再使用该解释器安装：
python -m pip install ./hf_xet-*.whl
python -c "import hf_xet; print(hf_xet.__file__)"
```

使用其他 Python 小版本时，改用对应的解释器（如 `python3.10 -m pip`），不要用当前 `python` 安装全部五种 wheel。工作流校验 wheel 标签和 ELF 架构，不执行手机上的运行测试。首次使用还需验证实际 Xet 下载；若证书路径未被正确识别，可设置 `SSL_CERT_FILE="$PREFIX/etc/tls/cert.pem"`。Linux manylinux wheel 不能替代这个 Android wheel，也不要通过改文件名绕过 pip 的兼容性检查。


## 同步仓库教程
使用 [scripts/git_mirror.py](scripts/git_mirror.py) 进行同步，仓库列表统一写在 [scripts/mirror_repos.json](scripts/mirror_repos.json) 里，由 [Sync To Mirror](.github/workflows/sync-to-mirror.yml) 工作流调用。

该脚本只依赖 Python 标准库，支持多线程并发同步，用法见[同步脚本用法](#同步脚本用法)。

### 1、生成 SSH 公钥

执行命令：`ssh-keygen -t rsa -C "youremail@example.com"`，连续三次回车，id_rsa  为`私钥`，id_rsa.pub 为`公钥`  
不使用默认 SSH 参考：[生成 / 添加 SSH 公钥](https://help.gitee.com/enterprise/code-manage/%E6%9D%83%E9%99%90%E4%B8%8E%E8%AE%BE%E7%BD%AE/%E9%83%A8%E7%BD%B2%E5%85%AC%E9%92%A5%E7%AE%A1%E7%90%86/%E7%94%9F%E6%88%90%E6%88%96%E6%B7%BB%E5%8A%A0SSH%E5%85%AC%E9%92%A5)


### 2、GitHub 项目配置 SSH 密钥

在 Github 项目  
`Settings`->`Secrets`->`Actions`，名称为：`GITEE_RSA_PRIVATE_KEY`，值为：上面生成 SSH 的`私钥`

![1.png](assets/1.png)
![2.png](assets/2.png)


### 3、GitHub 配置 SSH 公钥

![3.png](assets/3.png)

在 Github 中  
`Settings`->`SSH and GPG keys`->`New SSH key`，名称为：`GITEE_RSA_PUBLIC_KEY`，值为：上面生成SSH的`公钥`


### 4、Gitee 配置 SSH 公钥

在 Gitee 中  
`设置`->`安全设置`->`SSH公钥`，标题为：`GITEE_RSA_PUBLIC_KEY`，值为：上面生成 SSH 的`公钥`

![4.png](assets/4.png)


### 5、GitHub 创建 Github workflow

在 Github 项目  
`Actions`创建一个新的 workflow

![5.png](assets/5.png)
![6.png](assets/6.png)
![7.png](assets/7.png)

需要同步的仓库写在 `scripts/mirror_repos.json` 里，一条仓库配置支持三种写法：

```jsonc
{
  "source": {
    // {name} 会被替换成源仓库名
    "url": "git@github.com:licyk/{name}.git",
    // 克隆源仓库用的私钥所在的环境变量，没有设置时回落到第一个可用平台的私钥
    "key_env": "SOURCE_SSH_PRIVATE_KEY"
  },
  "destinations": {
    "gitee": {
      "url": "git@gitee.com:licyk/{name}.git",
      "key_env": "GITEE_RSA_PRIVATE_KEY",
      // 为 false 时默认不同步，需要用 --destination gitee 显式指定
      "enabled": true
    }
  },
  "repos": [
    // 1. 两边同名
    "hub-action",
    // 2. 源仓库 foo 同步到目的仓库 bar
    "foo:bar",
    // 3. 只在某个平台上改名，或者只同步到部分平台
    { "src": "t", "rename": { "gitee": "tt" }, "destinations": ["gitee", "gitlab"] },
    // 4. 除了某个平台，其他都同步
    { "src": "SDNote", "exclude_destinations": ["gitee"] },
    // 5. 平时不同步，但配置留着
    { "src": "some-repo", "enabled": false }
  ]
}
```

挑选要同步的仓库有四种办法，按需要挑一种：

|办法|适用场景|
|---|---|
|配置里 `"enabled": false`|这个仓库长期不需要同步。比直接把整行删掉好，能看出是特意不同步而不是漏加了|
|配置里 `"destinations": [...]`|白名单，这个仓库只同步到列出的平台|
|配置里 `"exclude_destinations": [...]`|黑名单，这个仓库不同步到列出的平台|
|命令行 `--repo` / `--exclude`|临时只跑其中几个仓库，两者都支持通配符|

`--repo` 写通配符时只会命中已启用的仓库；把仓库名原样写出来时，
即使它在配置里是 `"enabled": false` 也照样同步，因为指名道姓就是明确想要它。
想一次性带上所有被禁用的仓库则用 `--include-disabled`。

模式没有匹配到任何仓库时脚本会直接报错退出，免得名字拼错以后悄悄少同步一个。

想表达"除了某个平台哪里都同步"时用黑名单而不是白名单：以后配置里新增一个平台，
白名单写法会把新平台漏掉，黑名单写法不会。两者同时列出同一个平台会直接报配置错误。

workflow 里调用脚本，各平台的私钥通过环境变量传进去：

```yml
name: Sync To Mirror

on:
  schedule:
    - cron: '0 16 * * *' # 北京时间每日 00:00 执行
  workflow_dispatch:

jobs:
  git-mirror:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v6
        with:
          python-version: '3.x'

      - name: Sync repos
        shell: bash
        env:
          # 注意在 Settings -> Secrets 配置这些私钥
          GITEE_RSA_PRIVATE_KEY: ${{ secrets.GITEE_RSA_PRIVATE_KEY }}
          GITLAB_RSA_PRIVATE_KEY: ${{ secrets.GITLAB_RSA_PRIVATE_KEY || secrets.GITEE_RSA_PRIVATE_KEY }}
        run: python scripts/git_mirror.py --jobs 6
```


### 同步脚本用法

```shell
# 同步配置里已启用的所有平台（fanout 模式：每个仓库只克隆一次，并发推送到所有平台）
python scripts/git_mirror.py

# 一对一模式：每个（仓库，平台）组合各自克隆并推送，与旧的 matrix 行为一致
python scripts/git_mirror.py --mode pairwise

# 只同步指定平台和指定仓库
python scripts/git_mirror.py --destination gitee --repo hub-action,term-sd

# 用通配符挑一批仓库，或者反过来排除一批
python scripts/git_mirror.py --repo 'ComfyUI-*'
python scripts/git_mirror.py --exclude 'sd-webui-*,aria2-*'

# 连同配置里 enabled 为 false 的仓库一起同步
python scripts/git_mirror.py --include-disabled

# 演练，不实际推送
python scripts/git_mirror.py --dry-run

# 只看这次会同步哪些仓库
python scripts/git_mirror.py --list
```

|参数|说明|默认值|
|---|---|---|
|`--config`|仓库配置文件|`scripts/mirror_repos.json`|
|`--mode`|`fanout` / `one-to-many` 克隆一次推送到所有平台；`pairwise` / `one-to-one` 每个平台各自克隆|`fanout`|
|`--destination`|只同步指定平台，可重复或用逗号分隔，`all` 表示包括未启用的平台|配置里 `enabled` 的平台|
|`--repo`|只同步指定仓库（按源仓库名，支持通配符），可重复或用逗号分隔|全部|
|`--exclude`|排除指定仓库（按源仓库名，支持通配符），可重复或用逗号分隔|无|
|`--include-disabled`|连同配置里 `enabled` 为 false 的仓库一起同步|关闭|
|`--jobs`|同时克隆的仓库数，也决定磁盘占用|`6`|
|`--push-jobs`|fanout 模式下单个仓库同时推送的平台数|平台数量|
|`--dry-run`|推送时加上 `--dry-run`，不实际写入目的平台|关闭|
|`--timeout`|单条 git 命令的超时秒数|`1800`|
|`--retries`|克隆和推送的尝试次数|`3`|
|`--retry-delay`|重试间隔秒数|`5`|
|`--no-color`|关闭彩色输出|自动判断|
|`--list`|只打印本次会同步哪些仓库，不实际执行|关闭|

脚本会把 `refs/heads` 和 `refs/tags` 之外的引用（比如 GitHub 额外公开的 `refs/pull/*`）在推送前删掉，
否则 Gitee / GitLab / Bitbucket 会拒绝这些引用，导致整次推送失败。

另外可以设置 `SSH_KNOWN_HOSTS` 环境变量来启用主机密钥校验，不设置时会跳过校验并给出警告。

如果同步到 Gitee 的 Github Action 出现`remote: error: GE007: Your push would publish a private email address.`这个报错，则在 Gitee `设置`->`邮箱管理` , √ 去掉

![8.png](assets/8.png)

将 Github 同步到 Gitlab 也是一样的方法  
[第 4 步方法](#4gitee-配置-ssh-公钥)改为：  
左上角点击头像，`Preferences`->`SSH Keys`->`Add new key`，在 Title 输入`GITEE_RSA_PUBLIC_KEY`，Key 输入上面生成 SSH 的`公钥`

![9.png](assets/9.png)

如果同步到 Gitlab 的 Github Action 运行报错时可以在项目中的`Settings`->`Repository`->`Protected branches`右边的`Expand`,把`Allowed to force push`按钮打开，或者点`Unprotect`

![10.png](assets/10.png)
