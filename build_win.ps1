# 1. 激活环境
.\.venv\Scripts\activate.ps1

# 2. 清理旧产物
if (Test-Path dist) { Remove-Item -Recurse -Force dist }
if (Test-Path build) { Remove-Item -Recurse -Force build }

# 3. 运行 PyInstaller
$env:ONEFILE = "0"
python -m PyInstaller Toolbox.spec --clean --noconfirm