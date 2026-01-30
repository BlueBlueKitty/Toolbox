#!/bin/bash

# AppImage Installation Script for Toolbox
# This script installs an AppImage to the system applications menu

set -e

# Function to display usage
usage() {
    echo "用法: $0 <AppImage_path>"
    echo "  <AppImage_path> AppImage 文件的路径"
    exit 1
}

# Parse arguments
APPIMAGE_PATH=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -* )
            echo "未知选项: $1"
            usage
            ;;
        *)
            if [[ -z "$APPIMAGE_PATH" ]]; then
                APPIMAGE_PATH="$1"
            else
                echo "Multiple AppImage paths provided"
                usage
            fi
            shift
            ;;
    esac
done

if [[ -z "$APPIMAGE_PATH" ]]; then
    echo "错误: 未提供 AppImage 文件路径"
    usage
fi

# Convert to absolute path
APPIMAGE_PATH=$(realpath "$APPIMAGE_PATH")

if [[ ! -f "$APPIMAGE_PATH" ]]; then
    echo "错误: 未找到 AppImage 文件: $APPIMAGE_PATH"
    exit 1
fi

# Check if AppImage is executable
if [[ ! -x "$APPIMAGE_PATH" ]]; then
    echo "正在将 AppImage 设置为可执行..."
    chmod +x "$APPIMAGE_PATH"
fi

# Extract app name from filename (remove extension and version if present)
APP_NAME=$(basename "$APPIMAGE_PATH" | sed 's/\.AppImage$//' | sed 's/-[0-9]\+\.[0-9]\+\.[0-9]\+.*$//')

# Create directories
mkdir -p ~/.local/share/applications
mkdir -p ~/.local/share/icons/hicolor/256x256/apps

# Extract icon from AppImage
echo "从 AppImage 提取图标..."
ICON_PATH="$HOME/.local/share/icons/hicolor/256x256/apps/${APP_NAME}.png"

# Try to extract icon using AppImage's --appimage-extract option
if "$APPIMAGE_PATH" --appimage-extract >/dev/null 2>&1; then
    # Look for icon in extracted directory
    EXTRACT_DIR="squashfs-root"
    ICON_FILE=$(find "$EXTRACT_DIR" -name "*.png" -o -name "*.svg" -o -name "*.xpm" | head -1)
    if [[ -n "$ICON_FILE" ]]; then
        cp "$ICON_FILE" "$ICON_PATH"
    else
        # 备用：创建默认图标
        echo "AppImage 中未找到图标，正在创建默认图标..."
        convert -size 256x256 xc:blue -fill white -pointsize 72 -gravity center -annotate +0+0 "TB" "$ICON_PATH" 2>/dev/null || echo "未找到 ImageMagick，跳过图标创建"
    fi
    rm -rf "$EXTRACT_DIR"
else
    # Fallback if extraction fails
    echo "无法提取图标，正在创建默认图标..."
    convert -size 256x256 xc:blue -fill white -pointsize 72 -gravity center -annotate +0+0 "TB" "$ICON_PATH" 2>/dev/null || echo "未找到 ImageMagick，跳过图标创建"
fi

# Create .desktop file
DESKTOP_FILE="$HOME/.local/share/applications/${APP_NAME}.desktop"

cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=$APP_NAME
Comment=Toolbox Application
Exec=env QT_QPA_PLATFORM=xcb "$APPIMAGE_PATH"
Icon=$ICON_PATH
Terminal=false
StartupWMClass=$APP_NAME
Categories=Utility;Application;
EOF

# Desktop shortcut creation removed as per user request

# 更新桌面数据库
if command -v update-desktop-database >/dev/null 2>&1; then

# 尝试刷新图标缓存与桌面菜单，使图标/条目尽快出现在应用菜单中
echo "正在尝试刷新桌面与图标缓存..."
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi
if command -v xdg-desktop-menu >/dev/null 2>&1; then
    xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
fi
# KDE 桌面刷新缓存
if command -v kbuildsycoca5 >/dev/null 2>&1; then
    kbuildsycoca5 --noincremental >/dev/null 2>&1 || true
fi
    update-desktop-database ~/.local/share/applications
fi

echo "AppImage 安装成功！"
echo "应用： $APP_NAME"
echo "注意：可能需要注销并重新登录以在菜单中看到应用。"
echo "提示：已尝试刷新桌面与图标缓存；某些桌面环境仍可能需要注销或重启会话才能显示图标。"