#!/usr/bin/env bash

set -euo pipefail

REMOTE="${1:-origin}"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION_FILE="${PROJECT_ROOT}/src/version.py"
VERSION_JSON_PATH="${PROJECT_ROOT}/version.json"

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
if str(data.get("version", "")) != version:
    raise SystemExit(f"version.json 中的 version ({data.get('version')}) 与 src/version.py ({version}) 不一致")

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

main() {
    assert_clean_worktree

    local repo version tag
    repo="$(get_repo_from_source)"
    version="$(get_version_from_source)"
    tag="v${version}"

    if [[ -n "$(git tag --list "${tag}")" ]]; then
        echo "Git tag ${tag} 已存在，请先处理后再发版。" >&2
        exit 1
    fi

    update_version_json "${version}" "${repo}"

    git add "${VERSION_JSON_PATH}"
    git commit -m "release: ${tag}"

    git tag -a "${tag}" -m "Release ${tag}"
    git push "${REMOTE}" HEAD
    git push "${REMOTE}" "${tag}"

    echo "已推送 ${tag}，GitHub Actions 将开始构建 Windows/Linux/macOS 多架构 release。"
}

main
