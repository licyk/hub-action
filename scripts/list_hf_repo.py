import os
from huggingface_hub import HfApi



api = HfApi()
file_list = []
repo_file_list = []
repo_id = os.environ.get("repo_id", "licyk/image_training_set")
repo_type = os.environ.get("repo_type", "dataset")

print(f"获取 {repo_id} (类型: {repo_type}) 的文件列表中")
repo_files = api.list_repo_files(
    repo_id=repo_id,
    repo_type=repo_type
)

print("统计文件列表中")
for file in repo_files:
    file_list.append(file.split("/")[0])

file_list = list(set(file_list))

for file_name in file_list:
    count = 0
    for file in repo_files:
        if file.startswith(f"{file_name}/"):
            count += 1

    repo_file_list.append([file_name, count, "文件夹" if count > 0 else "文件"])

repo_file_list = sorted(repo_file_list)

print("=" * 100)
print(f"- {'文件名':<50} - {'数量':<10} - {'类型':<10}")
print(f"-{'----------':<50} -{'--------':<10} -{'--------':<10}")

for item in repo_file_list:
    name, size, type_ = item
    print(f"{name:<50} {size:<10} {type_:<10}")

print("=" * 100)
