#!/bin/bash
#-----------------------------------------------------------------------------
# AppImage 运行测试脚本
# 用法: ./test_appimage.sh [AppImage文件路径]
#-----------------------------------------------------------------------------

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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

# 查找 AppImage
find_appimage() {
    local search_paths=(
        "./dist/"
        "../dist/"
        "./"
        "../"
    )

    for path in "${search_paths[@]}"; do
        if [ -d "$path" ]; then
            local appimage_files=("$path"/*.AppImage)
            if [ -f "${appimage_files[0]}" ]; then
                echo "${appimage_files[0]}"
                return 0
            fi
        fi
    done

    return 1
}

# 测试 AppImage 基本信息
test_appimage_info() {
    local appimage_path="$1"

    info "测试 AppImage 基本信息..."

    # 检查文件类型
    if file "$appimage_path" | grep -q "AppImage"; then
        success "✓ 文件是有效的 AppImage"
    else
        error "✗ 文件不是有效的 AppImage"
    fi

    # 检查执行权限
    if [ -x "$appimage_path" ]; then
        success "✓ AppImage 具有执行权限"
    else
        warn "✗ AppImage 没有执行权限"
        chmod +x "$appimage_path"
        success "已设置执行权限"
    fi

    # 显示文件大小
    local size=$(du -h "$appimage_path" | cut -f1)
    info "AppImage 大小: $size"
}

# 测试 AppImage 运行 (非交互式)
test_appimage_run() {
    local appimage_path="$1"

    info "测试 AppImage 运行..."

    # 设置超时时间 (10秒)，避免应用卡住
    timeout 10s "$appimage_path" --help 2>/dev/null || true

    # 检查进程是否启动
    if pgrep -f "Toolbox" > /dev/null; then
        success "✓ AppImage 成功启动"
        # 等待一会儿让应用完全启动
        sleep 2
        # 终止进程
        pkill -f "Toolbox" 2>/dev/null || true
        success "✓ AppImage 进程已终止"
    else
        warn "⚠ 无法检测到应用进程，可能已正常退出"
    fi
}

# 测试 AppImage 内容
test_appimage_contents() {
    local appimage_path="$1"

    info "测试 AppImage 内容..."

    # 提取 AppImage 内容进行检查
    local temp_dir=$(mktemp -d)

    # 使用 --appimage-extract 提取内容
    if "$appimage_path" --appimage-extract > /dev/null 2>&1; then
        success "✓ AppImage 内容提取成功"

        # 检查主要文件
        if [ -f "squashfs-root/Toolbox_linux" ]; then
            success "✓ 找到主执行文件"
        else
            warn "⚠ 未找到主执行文件"
        fi

        if [ -d "squashfs-root/gdal_data" ]; then
            success "✓ 找到 GDAL 数据"
        else
            warn "⚠ 未找到 GDAL 数据"
        fi

        if [ -d "squashfs-root/proj_data" ]; then
            success "✓ 找到 PROJ 数据"
        else
            warn "⚠ 未找到 PROJ 数据"
        fi

        # 清理
        rm -rf squashfs-root
    else
        warn "⚠ 无法提取 AppImage 内容"
    fi

    # 清理临时目录
    rm -rf "$temp_dir"
}

# 主函数
main() {
    local appimage_path=""

    # 获取 AppImage 路径
    if [ $# -eq 0 ]; then
        info "未指定 AppImage 文件，自动查找..."
        appimage_path=$(find_appimage)
        if [ $? -ne 0 ] || [ -z "$appimage_path" ]; then
            error "未找到 AppImage 文件"
        fi
    else
        appimage_path="$1"
    fi

    if [ ! -f "$appimage_path" ]; then
        error "AppImage 文件不存在: $appimage_path"
    fi

    info "测试 AppImage: $appimage_path"
    echo

    # 运行测试
    test_appimage_info "$appimage_path"
    echo
    test_appimage_contents "$appimage_path"
    echo
    test_appimage_run "$appimage_path"
    echo

    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  AppImage 测试完成${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo
    info "如果所有测试都通过，AppImage 应该可以正常使用"
    info "如果遇到问题，请检查构建日志或系统依赖"
}

# 运行主函数
main "$@"