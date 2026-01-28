#!/bin/bash

# 1. 环境准备
source .venv/bin/activate

# 2. 清理旧产物
rm -rf build dist

# 3. 运行 PyInstaller
# 我们使用环境变量 ONEFILE=0 来确保生成目录模式
ONEFILE=0 python -m PyInstaller Toolbox.spec --clean --noconfirm

# 4. 压缩生成的目录
# -r 表示递归，-q 表示安静模式
if [ -d "dist/Toolbox_linux" ]; then
    echo "正在压缩 Toolbox_linux..."
    cd dist
    tar -cJf Toolbox_linux.tar.xz Toolbox_linux/
    cd ..
    echo "打包成功: Toolbox_linux.zip"
else
    echo "错误: 未找到打包目录 dist/Toolbox_linux"
    exit 1
fi