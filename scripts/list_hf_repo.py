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


file_name_length = max([len(x[0]) for x in repo_file_list]) + 3
count_length = max([len(str(x[1])) for x in repo_file_list]) + 5
type_length = max([len(x[2]) for x in repo_file_list]) + 3

print("=" * (file_name_length + count_length + type_length + 10))
print(f"{'- 文件名':<{file_name_length - 1}} {'- 数量':<{count_length - 2}} {'- 类型':<{type_length}}")
print(f"{('-' * file_name_length):<{file_name_length}} {('-' * count_length):<{count_length}} {('-' * (type_length + 3)):<{type_length}}")

for name, size, type_ in repo_file_list:
    print(f"{name:<{file_name_length}} {size:<{count_length}} {type_:<{type_length}}")

print("=" * (file_name_length + count_length + type_length + 10))
