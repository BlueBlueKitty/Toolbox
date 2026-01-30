#!/bin/bash
#-----------------------------------------------------------------------------
# Linux 构建脚本 - 生成 AppImage
# 用法: ./build_linux.sh [--onefile] [--clean]
#   --onefile: 使用 PyInstaller 单文件模式（不推荐用于 AppImage）
#   --clean:   清理之前的构建产物
#-----------------------------------------------------------------------------

set -e  # 出错即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 项目信息
APP_NAME="Toolbox"
APP_VERSION="1.2.0"
APP_ID="com.toolbox.app"

# 路径定义
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
DIST_DIR="${SCRIPT_DIR}/dist"
APPDIR="${BUILD_DIR}/AppDir"
APPIMAGE_TOOL="appimagetool"
APPIMAGE_TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"

# 解析参数
ONEFILE=0
CLEAN=0
for arg in "$@"; do
    case $arg in
        --onefile)
            ONEFILE=1
            ;;
        --clean)
            CLEAN=1
            ;;
        -h|--help)
            echo "用法: $0 [--onefile] [--clean]"
            echo "  --onefile: 使用 PyInstaller 单文件模式"
            echo "  --clean:   清理之前的构建产物"
            exit 0
            ;;
    esac
done

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

# 激活虚拟环境
activate_venv() {
    info "检查虚拟环境..."
    
    # 检查 .venv 是否存在
    if [ -d "${SCRIPT_DIR}/.venv" ]; then
        info "找到虚拟环境，正在激活..."
        source "${SCRIPT_DIR}/.venv/bin/activate"
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
    
    # 运行 PyInstaller (使用虚拟环境中的 python)
    # 注意：GDAL 在退出时可能会导致核心转储，但这不影响构建结果
    # 因此我们不检查退出码，而是检查输出目录是否存在
    python -m PyInstaller --clean --noconfirm Toolbox.spec || true
    
    # 检查构建产物是否存在
    if [ -d "${DIST_DIR}/Toolbox_linux" ] && [ -f "${DIST_DIR}/Toolbox_linux/Toolbox_linux" ]; then
        success "PyInstaller 打包完成"
    else
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
    rm -f "${APPDIR}/_internal/libgtk-3.so"*
    rm -f "${APPDIR}/_internal/libgdk_pixbuf-2.0.so"*
    rm -f "${APPDIR}/_internal/libgdk-3.so"*
    
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
    
    # 使用 appimagetool 生成 AppImage
    ARCH=x86_64 "${BUILD_DIR}/${APPIMAGE_TOOL}" "${APPDIR}" "${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
    
    # 设置可执行权限
    chmod +x "${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
    
    success "AppImage 生成完成: ${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
}

# 主函数
main() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  ${APP_NAME} Linux 构建脚本 v${APP_VERSION}  ${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    
    # 激活虚拟环境
    activate_venv
    
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
    
    # 生成 AppImage
    create_appimage
    
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  构建完成！${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
    echo -e "AppImage 文件位置: ${BLUE}${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage${NC}"
    echo ""
    echo "运行方式:"
    echo "  chmod +x ${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
    echo "  ./${DIST_DIR}/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
}

# 运行主函数
main
