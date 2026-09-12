#!/bin/python3
import json
import os
import subprocess


REPO_ROOT = os.path.abspath(".")
GIT_CREATION_TIMES = {}


def load_git_creation_times(root):
    """First commit that mentioned each path, walking history oldest-first."""
    try:
        output = subprocess.check_output(
            ["git", "log", "--reverse", "--name-only", "--pretty=format:COMMIT %ct"],
            cwd=root,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return {}

    times = {}
    current_ts = None
    for line in output.splitlines():
        if line.startswith("COMMIT "):
            current_ts = int(line.split()[1])
            continue
        if not line or current_ts is None:
            continue
        path = line
        while path and path != ".":
            times.setdefault(path, current_ts)
            path = os.path.dirname(path)
    return times


def rel_repo_path(path):
    return os.path.relpath(os.path.abspath(path), REPO_ROOT).replace(os.sep, "/")


def creation_time(path):
    rel = rel_repo_path(path)
    if rel in GIT_CREATION_TIMES:
        return GIT_CREATION_TIMES[rel]
    try:
        st = os.stat(path)
        birth = getattr(st, "st_birthtime", None)
        if birth:
            return int(birth)
        return int(st.st_mtime)
    except OSError:
        return 0


def generate_json(directory):
    result = {"path": os.path.basename(directory), "list": [], "files": []}

    metainfo_path = os.path.join(directory, "metainfo.json")
    if os.path.isfile(metainfo_path):
        with open(metainfo_path, "r") as metainfo_file:
            result = sort_json_fields({**result, **dict(json.load(metainfo_file))})

    children = []
    for entry in os.scandir(directory):
        if entry.is_dir() and entry.name != ".git" and entry.name != ".github":
            children.append((entry.path, generate_json(entry.path)))
        elif entry.is_file() and entry.name != "metainfo.json":
            result["files"].append(entry.name)

    if children and all("version" in child for _, child in children):
        # Newest first so list[0] is the most recently added version.
        children.sort(key=lambda item: (-creation_time(item[0]), item[1].get("path", "")))

    result["list"] = [child for _, child in children]

    if not result["list"]:
        del result["list"]
    if not result["files"]:
        del result["files"]

    return result


def custom_sort(item):
    key, value = item
    # 将列表放在最后，其余字段按字母顺序排序
    if isinstance(value, list):
        return 4
    if key=="name":
        return 0
    if key=="description":
        return 1
    if key=="version":
        return 2
    return 3


def sort_json_fields(json_obj):
    # 对JSON对象的字段进行排序
    sorted_items = sorted(json_obj.items(), key=custom_sort)
    # 构建一个新的有序字典
    sorted_json_obj = {k: v for k, v in sorted_items}
    return sorted_json_obj

def main():
    global GIT_CREATION_TIMES

    directory = "."  # Starting from the current directory
    output_file = "extensions.json"
    GIT_CREATION_TIMES = load_git_creation_times(REPO_ROOT)

    json_structure = generate_json(directory)

    # Remove the top-level directory ('.')
    if json_structure["path"] == "." and "list" in json_structure:
        json_structure = json_structure["list"]



    with open(output_file, "w") as f:
        json.dump(json_structure, f, indent=4)

    print(f"JSON structure has been written to {output_file}")


if __name__ == "__main__":
    main()
