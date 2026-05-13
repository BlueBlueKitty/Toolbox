#!/usr/bin/env bash
#
# macOS 构建脚本 - 生成 DMG
# 用法:
#   ./scripts/build_macos.sh
#   ./scripts/build_macos.sh --clean
#   ./scripts/build_macos.sh --onefile
#
# 说明:
# - 默认使用 PyInstaller 的目录模式（与现有 spec 一致）
# - 通过 dmgbuild 离线写入 Finder 布局（不在脚本中挂载 DMG）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${PROJECT_ROOT}/dist"
BUILD_DIR="${PROJECT_ROOT}/build"

APP_NAME="Toolbox"
PYI_TARGET_NAME="Toolbox_macos"
APP_BUNDLE_NAME="Toolbox.app"
ICON_PNG="${PROJECT_ROOT}/resources/toolbox.png"
ICON_ICNS="${PROJECT_ROOT}/resources/toolbox.icns"
DMG_BACKGROUND_PNG="${PROJECT_ROOT}/resources/dmg_background.png"
ONEFILE="0"
CLEAN="0"
CUSTOMIZE_FINDER_LAYOUT="1"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }

usage() {
    cat <<EOF
用法: ./scripts/build_macos.sh [选项]

选项:
  --clean      清理 build/dist 后再构建
  --onefile    使用 PyInstaller 单文件模式（不推荐）
  --finder     为 DMG 设置 Finder 拖拽布局（默认启用）
  --no-finder  跳过 DMG Finder 拖拽布局
  -h, --help   显示帮助
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean) CLEAN="1"; shift ;;
        --onefile) ONEFILE="1"; shift ;;
        --finder) CUSTOMIZE_FINDER_LAYOUT="1"; shift ;;
        --no-finder) CUSTOMIZE_FINDER_LAYOUT="0"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) error "未知参数: $1" ;;
    esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
    error "该脚本仅支持在 macOS 上运行。"
fi

cd "${PROJECT_ROOT}"
mkdir -p "${DIST_DIR}" "${BUILD_DIR}"

if [[ "${CLEAN}" == "1" ]]; then
    info "清理旧构建产物..."
    rm -rf "${BUILD_DIR}" "${DIST_DIR}/${PYI_TARGET_NAME}" "${DIST_DIR}/${APP_BUNDLE_NAME}" "${DIST_DIR}/${APP_NAME}-"*.dmg
fi

activate_venv() {
    if [[ -f "${PROJECT_ROOT}/.venv/bin/activate" ]]; then
        # shellcheck disable=SC1091
        source "${PROJECT_ROOT}/.venv/bin/activate"
        success "虚拟环境已激活: $(which python)"
    else
        warn "未找到 .venv 虚拟环境"
        warn "请先创建虚拟环境："
        echo "  python3 -m venv .venv"
        echo "  source .venv/bin/activate"
        echo "  pip install -r requirements.txt"
        error "请创建虚拟环境后再运行构建脚本"
    fi
}

check_dependencies() {
    info "检查依赖..."
    local py_ver
    py_ver="$(python --version 2>&1)"
    info "Python 版本: ${py_ver}"

    if ! python -c "import PyInstaller" >/dev/null 2>&1; then
        warn "未找到 PyInstaller，正在安装..."
        pip install pyinstaller
    fi

    command -v hdiutil >/dev/null 2>&1 || error "未找到 hdiutil"
    if ! python -c "import dmgbuild" >/dev/null 2>&1; then
        warn "未找到 dmgbuild，正在安装..."
        python -m pip install dmgbuild
    fi
    success "依赖检查完成"
}

activate_venv
check_dependencies

prepare_macos_icon() {
    if [[ -f "${ICON_ICNS}" ]]; then
        success "使用预生成的 macOS 图标: ${ICON_ICNS}"
    elif [[ -f "${ICON_PNG}" ]]; then
        warn "未找到 ${ICON_ICNS}，本次构建将继续但应用图标可能缺失"
    else
        warn "未找到图标资源: ${ICON_PNG} / ${ICON_ICNS}，本次构建将继续但应用图标可能缺失"
    fi
}

prepare_macos_icon

info "开始 PyInstaller 打包..."
export ONEFILE
export MACOS_BUNDLE_ICON="${ICON_ICNS}"
python -m PyInstaller --noconfirm Toolbox.spec

PYI_OUT_DIR="${DIST_DIR}/${PYI_TARGET_NAME}"
APP_BUNDLE_PATH="${DIST_DIR}/${APP_BUNDLE_NAME}"
if [[ -d "${APP_BUNDLE_PATH}" ]]; then
    info "检测到 macOS 应用包: ${APP_BUNDLE_PATH}"
elif [[ "${ONEFILE}" == "1" ]]; then
    PYI_OUT_FILE="${DIST_DIR}/${PYI_TARGET_NAME}"
    [[ -f "${PYI_OUT_FILE}" ]] || error "未找到单文件产物: ${PYI_OUT_FILE}"
    warn "当前构建未生成 .app 包，DMG 将使用可执行文件形态。"
else
    [[ -d "${PYI_OUT_DIR}" ]] || error "未找到目录模式产物: ${PYI_OUT_DIR}"
    warn "当前构建未生成 .app 包，DMG 将使用目录形态。建议检查 Toolbox.spec 中 BUNDLE 配置。"
fi
success "PyInstaller 打包完成"

APP_VERSION="$(python - <<'PY'
from src.version import __version__
print(__version__)
PY
)"

DMG_NAME="${APP_NAME}-${APP_VERSION}-macos-universal.dmg"
DMG_PATH="${DIST_DIR}/${DMG_NAME}"
DMG_STAGING="${BUILD_DIR}/dmg_staging"
DMG_SETTINGS="${BUILD_DIR}/dmg_settings.py"

info "准备 DMG 临时目录..."
rm -rf "${DMG_STAGING}"
mkdir -p "${DMG_STAGING}"

if [[ "${ONEFILE}" == "1" ]]; then
    if [[ -d "${APP_BUNDLE_PATH}" ]]; then
        cp -R "${APP_BUNDLE_PATH}" "${DMG_STAGING}/${APP_BUNDLE_NAME}"
    else
        cp -f "${DIST_DIR}/${PYI_TARGET_NAME}" "${DMG_STAGING}/${APP_NAME}"
        chmod +x "${DMG_STAGING}/${APP_NAME}"
    fi
else
    if [[ -d "${APP_BUNDLE_PATH}" ]]; then
        cp -R "${APP_BUNDLE_PATH}" "${DMG_STAGING}/${APP_BUNDLE_NAME}"
    else
        cp -R "${PYI_OUT_DIR}" "${DMG_STAGING}/${APP_NAME}"
    fi
fi

rm -f "${DMG_STAGING}/Applications"
ln -s /Applications "${DMG_STAGING}/Applications"

if [[ "${CUSTOMIZE_FINDER_LAYOUT}" == "1" && -f "${DMG_BACKGROUND_PNG}" ]]; then
    mkdir -p "${DMG_STAGING}/.background"
    cp -f "${DMG_BACKGROUND_PNG}" "${DMG_STAGING}/.background/dmg_background.png"
elif [[ "${CUSTOMIZE_FINDER_LAYOUT}" == "1" ]]; then
    warn "未找到 DMG 背景图: ${DMG_BACKGROUND_PNG}，将使用 dmgbuild 内置箭头背景"
fi

info "生成 dmgbuild 配置..."
cat > "${DMG_SETTINGS}" <<PY
import os

format = "UDBZ"
size = None
files = ["${DMG_STAGING}/${APP_BUNDLE_NAME}"]
symlinks = {"Applications": "/Applications"}
badge_icon = "${ICON_ICNS}" if os.path.exists("${ICON_ICNS}") else None
icon = badge_icon

if "${CUSTOMIZE_FINDER_LAYOUT}" == "1":
    background = "${DMG_STAGING}/.background/dmg_background.png" if os.path.exists("${DMG_STAGING}/.background/dmg_background.png") else "builtin-arrow"
    window_rect = ((120, 120), (660, 420))
    icon_size = 128
    text_size = 16
    show_status_bar = False
    show_tab_view = False
    show_toolbar = False
    show_pathbar = False
    show_sidebar = False
    default_view = "icon-view"
    arrange_by = None
    icon_locations = {
        "${APP_BUNDLE_NAME}": (150, 190),
        "Applications": (470, 190),
    }
else:
    background = None
PY

info "生成发布 DMG: ${DMG_PATH}"
rm -f "${DMG_PATH}"
python -m dmgbuild -s "${DMG_SETTINGS}" "${APP_NAME}" "${DMG_PATH}"
[[ -f "${DMG_PATH}" ]] || error "DMG 写入 dist 失败: ${DMG_PATH}"

success "DMG 构建完成: ${DMG_PATH}"
echo
echo -e "可分发文件: ${GREEN}${DMG_PATH}${NC}"
