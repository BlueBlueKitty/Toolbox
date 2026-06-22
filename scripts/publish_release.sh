#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${PROJECT_ROOT}/src/version.py"
VERSION_JSON_PATH="${PROJECT_ROOT}/version.json"
REMOTE="origin"
SPECIFIED_VERSION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -r|--remote)
            REMOTE="$2"
            shift 2
            ;;
        -v|--version)
            SPECIFIED_VERSION="$2"
            shift 2
            ;;
        -*)
            echo "未知参数: $1" >&2
            exit 1
            ;;
        *)
            if [[ "${REMOTE}" == "origin" ]]; then
                REMOTE="$1"
            elif [[ -z "${SPECIFIED_VERSION}" ]]; then
                SPECIFIED_VERSION="$1"
            else
                echo "多余参数: $1" >&2
                exit 1
            fi
            shift
            ;;
    esac
done

get_version_from_source() {
    VERSION_FILE="${VERSION_FILE}" python3 - <<'PY'
import os
import re
from pathlib import Path

text = Path(os.environ["VERSION_FILE"]).read_text(encoding="utf-8")
match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
if not match:
    raise SystemExit("无法从 src/version.py 读取版本号")
print(match.group(1))
PY
}

set_version_in_source() {
    local version="$1"
    VERSION="${version}" VERSION_FILE="${VERSION_FILE}" python3 - <<'PY'
import os
import re
from pathlib import Path

version = os.environ["VERSION"]
version_file = Path(os.environ["VERSION_FILE"])
text = version_file.read_text(encoding="utf-8")
updated, count = re.subn(
    r"__version__\s*=\s*['\"][^'\"]+['\"]",
    f"__version__ = '{version}'",
    text,
    count=1,
)
if count != 1:
    raise SystemExit("无法更新 src/version.py 中的版本号")
version_file.write_text(updated, encoding="utf-8")
PY
}

get_repo_from_source() {
    VERSION_FILE="${VERSION_FILE}" python3 - <<'PY'
import os
import re
from pathlib import Path

text = Path(os.environ["VERSION_FILE"]).read_text(encoding="utf-8")
match = re.search(r"GITHUB_REPO\s*=\s*['\"]([^'\"]+)['\"]", text)
if not match:
    raise SystemExit("无法从 src/version.py 读取 GITHUB_REPO")
print(match.group(1))
PY
}

assert_clean_worktree() {
    if [[ -n "$(git status --porcelain)" ]]; then
        echo "当前 git 工作区不是干净状态，请先提交或清理改动后再发版。" >&2
        exit 1
    fi
}

update_version_json() {
    local version="$1"
    local repo="$2"
    local tag="v${version}"
    local published_at
    published_at="$(TZ=Asia/Shanghai date '+%Y-%m-%dT%H:%M:%S%z')"
    published_at="${published_at:0:22}:${published_at:22:2}"

    VERSION="${version}" REPO="${repo}" TAG="${tag}" PUBLISHED_AT="${published_at}" VERSION_JSON_PATH="${VERSION_JSON_PATH}" \
    python3 - <<'PY'
import json
import os
from pathlib import Path

version = os.environ["VERSION"]
repo = os.environ["REPO"]
tag = os.environ["TAG"]
published_at = os.environ["PUBLISHED_AT"]
version_json_path = Path(os.environ["VERSION_JSON_PATH"])

data = json.loads(version_json_path.read_text(encoding="utf-8"))
data["version"] = version
data["release_url"] = f"https://github.com/{repo}/releases/tag/{tag}"
data["downloads"] = {
    "windows_x86_64": f"https://github.com/{repo}/releases/download/{tag}/Toolbox_{version}_windows_x86_64.exe",
    "linux_x86_64": f"https://github.com/{repo}/releases/download/{tag}/Toolbox-{version}_linux_x86_64.AppImage",
    "linux_arm64": f"https://github.com/{repo}/releases/download/{tag}/Toolbox-{version}_linux_arm64.AppImage",
    "mac_x86_64": f"https://github.com/{repo}/releases/download/{tag}/Toolbox_{version}_apple_x86_64.dmg",
    "mac_arm64": f"https://github.com/{repo}/releases/download/{tag}/Toolbox_{version}_apple_arm64.dmg",
}
data["published_at"] = published_at
version_json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

get_next_patch_version() {
    local current_version="$1"
    CURRENT_VERSION="${current_version}" python3 - <<'PY'
import os
import re

current_version = os.environ["CURRENT_VERSION"]
match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current_version)
if not match:
    raise SystemExit(f"当前版本号 '{current_version}' 不是 x.y.z 格式，无法自动递增，请手动输入发布版本。")
major, minor, patch = map(int, match.groups())
print(f"{major}.{minor}.{patch + 1}")
PY
}

resolve_release_version() {
    local current_version="$1"
    local specified_version="${2:-}"
    local target_version default_version

    echo "当前版本: ${current_version}" >&2
    if [[ -n "${specified_version}" ]]; then
        target_version="${specified_version}"
    else
        default_version="$(get_next_patch_version "${current_version}")"
        read -r -p "请输入要发布的版本号（直接回车使用默认版本 ${default_version}）: " target_version
    fi
    if [[ -z "${target_version}" ]]; then
        target_version="${default_version}"
    fi
    target_version="$(printf '%s' "${target_version}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [[ -z "${target_version}" ]]; then
        echo "版本号不能为空。" >&2
        exit 1
    fi
    printf '%s\n' "${target_version}"
}

main() {
    assert_clean_worktree

    local repo current_version version tag
    repo="$(get_repo_from_source)"
    current_version="$(get_version_from_source)"
    version="$(resolve_release_version "${current_version}" "${SPECIFIED_VERSION}")"
    if [[ "${version}" != "${current_version}" ]]; then
        set_version_in_source "${version}"
    fi
    tag="v${version}"

    if [[ -n "$(git tag --list "${tag}")" ]]; then
        echo "Git tag ${tag} 已存在，请先处理后再发版。" >&2
        exit 1
    fi

    update_version_json "${version}" "${repo}"

    git add "${VERSION_FILE}" "${VERSION_JSON_PATH}"
    git commit -m "release: ${tag}"

    git tag -a "${tag}" -m "Release ${tag}"
    git push "${REMOTE}" HEAD
    git push "${REMOTE}" "${tag}"

    echo "已推送 ${tag}，GitHub Actions 将开始构建 Windows/Linux/macOS 多架构 release。"
}

main
