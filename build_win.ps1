# 1. 激活环境
.\.venv_win\Scripts\activate.ps1

# 2. 清理旧产物
if (Test-Path Toolbox_win.zip) { Remove-Item Toolbox_win.zip }

# 3. 运行 PyInstaller
$env:ONEFILE="0"
python -m PyInstaller Toolbox.spec --clean --noconfirm

# 4. 压缩生成的目录
if (Test-Path "dist\Toolbox_win") {
    Write-Host "正在压缩 Toolbox_win..."
    Compress-Archive -Path "dist\Toolbox_win" -DestinationPath "Toolbox_win.zip" -Force
    Write-Host "打包成功: Toolbox_win.zip"
} else {
    Write-Error "错误: 未找到打包目录 dist\Toolbox_win"
    exit 1
}