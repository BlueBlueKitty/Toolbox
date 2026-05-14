#!/bin/bash
#-----------------------------------------------------------------------------
# Linux 构建脚本 - 生成 AppImage
# 用法: ./scripts/build_linux.sh [--onefile] [--clean] [--arch x86_64|arm64]
#   --onefile: 使用 PyInstaller 单文件模式（不推荐用于 AppImage）
#   --clean:   清理之前的构建产物
#-----------------------------------------------------------------------------

set -e  # 出错即退出
set -o pipefail

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 早期参数解析阶段需要的基础输出函数（后续会覆盖为完整版本）
error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# 项目信息
APP_NAME="Toolbox"
APP_ID="com.toolbox.app"

# 路径定义（必须先于版本号读取）
SCRIPT_PATH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_DIR="$(cd "${SCRIPT_PATH_DIR}/.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
DIST_DIR="${SCRIPT_DIR}/dist"
APPDIR="${BUILD_DIR}/AppDir"
APPIMAGE_TOOL=""
APPIMAGE_ARCH="x86_64"
APPIMAGE_TOOL_URL=""
ACTIVE_ENV_NAME=""

# 从 src/version.py 动态读取版本号
VERSION_FILE="${SCRIPT_DIR}/src/version.py"
if [ -f "${VERSION_FILE}" ]; then
    APP_VERSION=$(grep -oP "__version__\s*=\s*['\"]\K[^'\"]+" "${VERSION_FILE}")
    if [ -z "${APP_VERSION}" ]; then
        APP_VERSION="0.0.0"
        echo -e "${YELLOW}[WARN]${NC} 无法从 version.py 解析版本号，使用默认版本"
    fi
else
    APP_VERSION="0.0.0"
    echo -e "${YELLOW}[WARN]${NC} 未找到 version.py，使用默认版本"
fi

# 解析参数
ONEFILE=0
CLEAN=0
TARGET_ARCH=""
while [ $# -gt 0 ]; do
    case "$1" in
        --onefile)
            ONEFILE=1
            shift
            ;;
        --clean)
            CLEAN=1
            shift
            ;;
        --arch=*)
            TARGET_ARCH="${1#*=}"
            shift
            ;;
        --arch)
            [ $# -ge 2 ] || error "--arch 需要参数: x86_64 或 arm64"
            TARGET_ARCH="$2"
            shift 2
            ;;
        -h|--help)
            echo "用法: $0 [--onefile] [--clean] [--arch x86_64|arm64]"
            echo "  --onefile: 使用 PyInstaller 单文件模式"
            echo "  --clean:   清理之前的构建产物"
            echo "  --arch:    构建架构（默认使用当前机器架构）"
            exit 0
            ;;
        *)
            error "未知参数: $1"
            ;;
    esac
done

HOST_ARCH="$(uname -m)"
case "${HOST_ARCH}" in
    x86_64|amd64) HOST_ARCH="x86_64" ;;
    aarch64|arm64) HOST_ARCH="arm64" ;;
esac

if [ -z "${TARGET_ARCH}" ]; then
    TARGET_ARCH="${HOST_ARCH}"
fi

case "${TARGET_ARCH}" in
    x86_64)
        APPIMAGE_ARCH="x86_64"
        APPIMAGE_TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
        PACKAGE_ARCH="linux_x86_64"
        ;;
    arm64)
        APPIMAGE_ARCH="aarch64"
        APPIMAGE_TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-aarch64.AppImage"
        PACKAGE_ARCH="linux_arm64"
        ;;
    *)
        error "不支持的 --arch 参数: ${TARGET_ARCH}（仅支持 x86_64 或 arm64）"
        ;;
esac
APPIMAGE_TOOL="appimagetool-${APPIMAGE_ARCH}"

if [ "${TARGET_ARCH}" != "${HOST_ARCH}" ]; then
    warn "当前主机架构为 ${HOST_ARCH}，目标架构为 ${TARGET_ARCH}。请确保构建环境与目标架构一致。"
fi

# 打印带颜色的信息
info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# 激活 Python 环境
activate_python_env() {
    info "检查 Python 环境..."

    if [ -n "${CONDA_PREFIX:-}" ] && command -v python >/dev/null 2>&1; then
        ACTIVE_ENV_NAME="conda:${CONDA_DEFAULT_ENV:-$(basename "${CONDA_PREFIX}")}"
        success "使用已激活的 Conda 环境: ${ACTIVE_ENV_NAME} ($(command -v python))"
        return
    fi

    if [ -f "${SCRIPT_DIR}/.venv/bin/activate" ]; then
        info "未检测到 Conda 环境，回退到 .venv ..."
        # shellcheck disable=SC1091
        source "${SCRIPT_DIR}/.venv/bin/activate"
        ACTIVE_ENV_NAME="venv:.venv"
        success "虚拟环境已激活: $(which python)"
        return
    fi

    warn "未检测到已激活的 Conda 环境，也未找到 .venv"
    warn "请先准备构建环境，例如："
    echo "  conda env create -n toolbox-build -f environment-build.yml"
    echo "  conda activate toolbox-build"
    echo "  或"
    echo "  python3 -m venv .venv"
    echo "  source .venv/bin/activate"
    error "请激活 Conda 或 .venv 环境后再运行构建脚本"
}

# 检查依赖
check_dependencies() {
    info "检查依赖..."
    
    # 检查 Python 版本
    PYTHON_VERSION=$(python --version 2>&1 | awk '{print $2}')
    info "Python 版本: $PYTHON_VERSION"
    
    # 检查 PyInstaller
    if ! python -c "import PyInstaller" &> /dev/null; then
        warn "未找到 PyInstaller，正在安装..."
        pip install pyinstaller
    fi
    
    success "依赖检查完成"
}

scan_appdir_architecture() {
    info "检查 AppDir 二进制架构..."

    local output_file="${BUILD_DIR}/appdir-architecture.txt"
    local mismatch_file="${BUILD_DIR}/appdir-architecture-mismatch.txt"
    : > "${output_file}"
    : > "${mismatch_file}"

    while IFS= read -r -d '' file_path; do
        local file_info
        file_info="$(file -L "${file_path}")"
        printf '%s\n' "${file_info}" >> "${output_file}"

        case "${TARGET_ARCH}" in
            x86_64)
                if printf '%s\n' "${file_info}" | grep -Eq 'aarch64|ARM aarch64'; then
                    printf '%s\n' "${file_info}" >> "${mismatch_file}"
                fi
                ;;
            arm64)
                if printf '%s\n' "${file_info}" | grep -Eq 'x86-64'; then
                    printf '%s\n' "${file_info}" >> "${mismatch_file}"
                fi
                ;;
        esac
    done < <(find "${APPDIR}" -type f \( -perm -111 -o -name '*.so' -o -name '*.so.*' \) -print0)

    if [ -s "${mismatch_file}" ]; then
        warn "检测到与目标架构不匹配的 AppDir 二进制："
        cat "${mismatch_file}"
        error "AppDir 存在混合架构文件，请先清理错误架构依赖后再生成 AppImage"
    fi

    success "AppDir 架构检查通过"
}

scan_appdir_missing_dependencies() {
    info "检查 AppDir 关键二进制依赖是否缺失..."

    local report_file="${BUILD_DIR}/appdir-ldd-report.txt"
    local missing_file="${BUILD_DIR}/appdir-ldd-missing.txt"
    : > "${report_file}"
    : > "${missing_file}"

    local targets=()
    if [ -f "${APPDIR}/Toolbox_linux" ]; then
        targets+=("${APPDIR}/Toolbox_linux")
    fi

    while IFS= read -r -d '' candidate; do
        targets+=("${candidate}")
    done < <(find "${APPDIR}" -type f \( -name "_gdal*.so*" -o -name "libgdal.so*" \) -print0)

    if [ ${#targets[@]} -eq 0 ]; then
        warn "未在 AppDir 中找到可检查的 GDAL 目标文件，跳过 ldd 缺失检查"
        return
    fi

    local target
    for target in "${targets[@]}"; do
        echo ">>> ${target}" >> "${report_file}"
        if ldd "${target}" >> "${report_file}" 2>&1; then
            :
        else
            warn "ldd 检查失败: ${target}"
        fi
        echo "" >> "${report_file}"
    done

    grep "not found" "${report_file}" > "${missing_file}" || true
    if [ -s "${missing_file}" ]; then
        warn "检测到缺失依赖："
        cat "${missing_file}"
        error "AppDir 依赖检查失败（存在 not found）"
    fi

    success "AppDir 关键依赖检查通过"
}

# 下载 appimagetool
download_appimagetool() {
    if [ ! -f "${BUILD_DIR}/${APPIMAGE_TOOL}" ]; then
        info "下载 appimagetool..."
        mkdir -p "${BUILD_DIR}"
        wget -q --show-progress -O "${BUILD_DIR}/${APPIMAGE_TOOL}" "${APPIMAGE_TOOL_URL}"
        chmod +x "${BUILD_DIR}/${APPIMAGE_TOOL}"
        success "appimagetool 下载完成"
    else
        info "appimagetool 已存在，跳过下载"
    fi
}

# 清理构建产物
clean_build() {
    info "清理构建产物..."
    rm -rf "${BUILD_DIR}/Toolbox"
    rm -rf "${BUILD_DIR}/Toolbox_linux"
    rm -rf "${APPDIR}"
    rm -rf "${DIST_DIR}"/*.AppImage
    success "清理完成"
}

# 使用 PyInstaller 打包
build_with_pyinstaller() {
    info "使用 PyInstaller 打包..."
    
    cd "${SCRIPT_DIR}"
    
    # 设置环境变量
    if [ $ONEFILE -eq 1 ]; then
        export ONEFILE=1
        info "使用单文件模式"
    else
        export ONEFILE=0
        info "使用目录模式"
    fi
    
    # 运行 PyInstaller，并保留完整日志供 CI 排查。
    # 某些环境下会出现“Build complete!”后进程在退出阶段异常(如 double free)，
    # 此时产物已生成，不能让 set -e 直接中断后续 AppImage 步骤。
    PYINSTALLER_LOG="${BUILD_DIR}/pyinstaller-linux.log"
    set +e
    python -m PyInstaller --clean --noconfirm Toolbox.spec 2>&1 | tee "${PYINSTALLER_LOG}"
    PYINSTALLER_EXIT=${PIPESTATUS[0]}
    set -e
    
    # 检查构建产物是否存在
    if [ -d "${DIST_DIR}/Toolbox_linux" ] && [ -f "${DIST_DIR}/Toolbox_linux/Toolbox_linux" ]; then
        if [ "${PYINSTALLER_EXIT}" -ne 0 ]; then
            if grep -q "Build complete!" "${PYINSTALLER_LOG}"; then
                warn "PyInstaller 退出码: ${PYINSTALLER_EXIT}，但日志显示 Build complete 且产物存在，继续后续步骤"
            else
                warn "PyInstaller 退出码: ${PYINSTALLER_EXIT}"
                warn "PyInstaller 日志末尾 80 行:"
                tail -n 80 "${PYINSTALLER_LOG}"
                error "PyInstaller 打包失败"
            fi
        fi
        success "PyInstaller 打包完成"
    else
        warn "PyInstaller 退出码: ${PYINSTALLER_EXIT}"
        if [ -f "${PYINSTALLER_LOG}" ]; then
            warn "PyInstaller 日志末尾 80 行:"
            tail -n 80 "${PYINSTALLER_LOG}"
        fi
        error "PyInstaller 打包失败：未找到输出文件"
    fi
}

# 创建 AppDir 结构
create_appdir() {
    info "创建 AppDir 结构..."
    
    # 清理旧的 AppDir
    rm -rf "${APPDIR}"
    mkdir -p "${APPDIR}"
    
    # 复制 PyInstaller 输出到 AppDir
    if [ -d "${DIST_DIR}/Toolbox_linux" ]; then
        cp -r "${DIST_DIR}/Toolbox_linux"/* "${APPDIR}/"
    else
        error "未找到 PyInstaller 输出目录: ${DIST_DIR}/Toolbox_linux"
    fi
    
    # ================================================================
    # 【关键】移除 GTK 相关的插件和库，避免与系统 GTK 冲突导致崩溃
    # ================================================================
    info "移除 GTK 相关插件和库以避免兼容性问题..."
    
    # 移除 Qt 的 GTK 平台主题插件（这是导致崩溃的主要原因）
    rm -f "${APPDIR}/_internal/PySide6/Qt/plugins/platformthemes/libqgtk3.so"
    rm -f "${APPDIR}/PySide6/Qt/plugins/platformthemes/libqgtk3.so"
    
    # 移除打包的 GTK 和 gdk-pixbuf 库（它们与系统图标格式不兼容）
    # 注意：不要删除 GDAL 依赖链（如 libnsl.so*、libproj.so*、libgdal.so*）
    rm -f "${APPDIR}/_internal/libgtk-3.so"*
    rm -f "${APPDIR}/_internal/libgdk_pixbuf-2.0.so"*
    rm -f "${APPDIR}/_internal/libgdk-3.so"*

    # 使用目标系统的 Mesa/GBM 图形栈，避免打包进来的 libgbm 与宿主机驱动符号不兼容
    rm -f "${APPDIR}/_internal/libgbm.so"*
    
    success "GTK 相关文件已移除"
    
    # 创建 AppRun 启动脚本
    cat > "${APPDIR}/AppRun" << 'EOF'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}

# 设置环境变量
export PATH="${HERE}:${PATH}"
export LD_LIBRARY_PATH="${HERE}/_internal:${LD_LIBRARY_PATH}"

# 设置 GDAL/PROJ 数据路径
if [ -d "${HERE}/_internal/gdal_data" ]; then
    export GDAL_DATA="${HERE}/_internal/gdal_data"
fi
if [ -d "${HERE}/_internal/proj_data" ]; then
    export PROJ_LIB="${HERE}/_internal/proj_data"
    export PROJ_DATA="${HERE}/_internal/proj_data"
fi

# 设置 Qt 插件路径
if [ -d "${HERE}/_internal/PySide6/Qt/plugins" ]; then
    export QT_PLUGIN_PATH="${HERE}/_internal/PySide6/Qt/plugins"
fi

# Qt 兼容性设置 - 解决不同系统上的图形渲染问题
export QT_XCB_GL_INTEGRATION=none
export LIBGL_ALWAYS_SOFTWARE=1
export QT_QUICK_BACKEND=software

# QtWebEngine 设置 - 禁用 GPU 加速
export QTWEBENGINE_DISABLE_SANDBOX=1
export QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --disable-software-rasterizer --no-sandbox --disable-dev-shm-usage"

# 强制使用 xcb 平台插件
export QT_QPA_PLATFORM=xcb

# 启动应用
exec "${HERE}/Toolbox_linux" "$@"
EOF
    chmod +x "${APPDIR}/AppRun"
    
    # 创建 .desktop 文件
    cat > "${APPDIR}/${APP_NAME}.desktop" << EOF
[Desktop Entry]
Type=Application
Name=${APP_NAME}
Exec=Toolbox_linux
Icon=toolbox
Categories=Utility;Science;
Comment=Toolbox Application for GIS and Remote Sensing
Terminal=false
EOF
    
    # 复制图标
    if [ -f "${SCRIPT_DIR}/resources/toolbox.ico" ]; then
        # 如果有 .ico 文件，尝试转换为 .png
        if command -v convert &> /dev/null; then
            convert "${SCRIPT_DIR}/resources/toolbox.ico" "${APPDIR}/toolbox.png"
        else
            # 使用默认图标或从 imgs 目录复制
            if [ -f "${SCRIPT_DIR}/imgs/main.png" ]; then
                cp "${SCRIPT_DIR}/imgs/main.png" "${APPDIR}/toolbox.png"
            fi
        fi
    elif [ -f "${SCRIPT_DIR}/imgs/main.png" ]; then
        cp "${SCRIPT_DIR}/imgs/main.png" "${APPDIR}/toolbox.png"
    fi
    
    # 如果没有图标文件，创建一个简单的占位图标
    if [ ! -f "${APPDIR}/toolbox.png" ]; then
        warn "未找到图标文件，使用空图标"
        # 创建一个 1x1 的透明 PNG 作为占位符
        echo -n "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==" | base64 -d > "${APPDIR}/toolbox.png"
    fi
    
    success "AppDir 结构创建完成"
}

# 生成 AppImage
create_appimage() {
    info "生成 AppImage..."
    
    mkdir -p "${DIST_DIR}"
    local appimage_name="${APP_NAME}-${APP_VERSION}_${PACKAGE_ARCH}.AppImage"

    scan_appdir_architecture
    
    # 使用 appimagetool 生成 AppImage。
    # 注意：AppImageKit 当前对 aarch64 的 ARCH 文本识别比较特殊，
    # 实际可识别的是 arm_aarch64，而不是常见的 aarch64 / arm64。
    local arch_candidates=("${APPIMAGE_ARCH}")
    if [ "${TARGET_ARCH}" = "arm64" ]; then
        arch_candidates=("arm_aarch64" "aarch64" "arm64")
    fi

    local last_rc=1
    local arch_value=""
    for arch_value in "${arch_candidates[@]}"; do
        info "尝试使用 ARCH=${arch_value} 生成 AppImage..."
        set +e
        ARCH="${arch_value}" "${BUILD_DIR}/${APPIMAGE_TOOL}" --appimage-extract-and-run "${APPDIR}" "${DIST_DIR}/${appimage_name}"
        last_rc=$?
        set -e
        if [ "${last_rc}" -eq 0 ]; then
            break
        fi
        warn "ARCH=${arch_value} 生成失败，退出码: ${last_rc}"
    done

    if [ "${last_rc}" -ne 0 ]; then
        error "AppImage 生成失败：已尝试 ARCH=${arch_candidates[*]}"
    fi
    
    # 设置可执行权限
    chmod +x "${DIST_DIR}/${appimage_name}"
    
    success "AppImage 生成完成: ${DIST_DIR}/${appimage_name}"
}

# 主函数
main() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  ${APP_NAME} Linux 构建脚本 v${APP_VERSION}  ${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    
    # 激活 Python 环境
    activate_python_env
    
    # 检查依赖
    check_dependencies
    
    # 如果指定了清理
    if [ $CLEAN -eq 1 ]; then
        clean_build
    fi
    
    # 下载 appimagetool
    download_appimagetool
    
    # PyInstaller 打包
    build_with_pyinstaller
    
    # 创建 AppDir
    create_appdir

    # 检查 AppDir 关键依赖，提前失败避免生成坏包
    scan_appdir_missing_dependencies
    
    # 生成 AppImage
    create_appimage
    
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  构建完成！${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "AppImage 文件位置: ${BLUE}${DIST_DIR}/${APP_NAME}-${APP_VERSION}_${PACKAGE_ARCH}.AppImage${NC}"
    echo ""
    echo "运行方式:"
    echo "  chmod +x ${DIST_DIR}/${APP_NAME}-${APP_VERSION}_${PACKAGE_ARCH}.AppImage"
    echo "  ./${DIST_DIR}/${APP_NAME}-${APP_VERSION}_${PACKAGE_ARCH}.AppImage"
}

# 运行主函数
main
