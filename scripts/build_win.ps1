#-----------------------------------------------------------------------------
# Windows 构建脚本 - 生成 exe
# 用法: .\scripts\build_win.ps1 [-OneFile] [-Clean] [-CreateInstaller]
#   -OneFile:        使用 PyInstaller 单文件模式
#   -Clean:          清理之前的构建产物
#   -CreateInstaller: 使用 NSIS 创建安装程序
#-----------------------------------------------------------------------------

param(
    [switch]$OneFile,
    [switch]$Clean,
    [switch]$CreateInstaller,
    [switch]$Help
)

# 项目信息
$APP_NAME = "Toolbox"
$APP_PUBLISHER = "Yibo Yuan"

# 从 src/version.py 动态读取版本号
$PROJECT_ROOT = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$VERSION_FILE = Join-Path $PROJECT_ROOT "src\version.py"
if (Test-Path $VERSION_FILE) {
    # 使用 Select-String 避免复杂的正则转义
    $versionLine = Select-String -Path $VERSION_FILE -Pattern "__version__" | Select-Object -First 1
    if ($versionLine) {
        $lineText = $versionLine.Line
        # 分别匹配单引号和双引号
        if ($lineText -match "=\s*'([^']+)'") {
            $APP_VERSION = $matches[1]
        }
        elseif ($lineText -match '=\s*"([^"]+)"') {
            $APP_VERSION = $matches[1]
        }
        else {
            $APP_VERSION = "0.0.0"
            Write-Host "[WARN] 无法从 version.py 解析版本号，使用默认版本" -ForegroundColor Yellow
        }
    }
    else {
        $APP_VERSION = "0.0.0"
        Write-Host "[WARN] version.py 中未找到 __version__" -ForegroundColor Yellow
    }
}
else {
    $APP_VERSION = "0.0.0"
    Write-Host "[WARN] 未找到 version.py，使用默认版本" -ForegroundColor Yellow
}

# 路径定义
$SCRIPT_DIR = $PROJECT_ROOT
$BUILD_DIR = Join-Path $PROJECT_ROOT "build"
$DIST_DIR = Join-Path $PROJECT_ROOT "dist"
$OUTPUT_DIR = Join-Path $DIST_DIR "Toolbox_win"

# 颜色输出函数
function Write-Info {
    param([string]$Message)
    Write-Host "[INFO] " -ForegroundColor Blue -NoNewline
    Write-Host $Message
}

function Write-Success {
    param([string]$Message)
    Write-Host "[SUCCESS] " -ForegroundColor Green -NoNewline
    Write-Host $Message
}

function Write-Warn {
    param([string]$Message)
    Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline
    Write-Host $Message
}

function Write-Error-Custom {
    param([string]$Message)
    Write-Host "[ERROR] " -ForegroundColor Red -NoNewline
    Write-Host $Message
    exit 1
}

# 显示帮助
if ($Help) {
    Write-Host @"
用法: .\scripts\build_win.ps1 [选项]

选项:
  -OneFile          使用 PyInstaller 单文件模式（生成单个 exe 文件）
  -Clean            清理之前的构建产物
  -CreateInstaller  使用 NSIS 创建安装程序（需要安装 NSIS）
  -Help             显示此帮助信息

示例:
  .\scripts\build_win.ps1                    # 默认目录模式打包
  .\scripts\build_win.ps1 -OneFile           # 单文件模式打包
  .\scripts\build_win.ps1 -Clean             # 清理后打包
  .\scripts\build_win.ps1 -CreateInstaller   # 打包并创建安装程序
"@
    exit 0
}

# 检查依赖
function Check-Dependencies {
    Write-Info "检查依赖..."
    
    # 检查 Python
    try {
        $pythonVersion = python --version 2>&1
        Write-Info "Python 版本: $pythonVersion"
    }
    catch {
        Write-Error-Custom "未找到 Python，请先安装 Python 3.10+"
    }
    
    # 检查 PyInstaller
    try {
        python -c "import PyInstaller" 2>&1 | Out-Null
        Write-Info "PyInstaller 已安装"
    }
    catch {
        Write-Warn "未找到 PyInstaller，正在安装..."
        pip install pyinstaller
    }
    
    Write-Success "依赖检查完成"
}

# 清理构建产物
function Clean-Build {
    Write-Info "清理构建产物..."
    
    $pathsToClean = @(
        (Join-Path $BUILD_DIR "Toolbox"),
        (Join-Path $BUILD_DIR "Toolbox_win"),
        (Join-Path $DIST_DIR "Toolbox_win"),
        (Join-Path $DIST_DIR "Toolbox_win.exe"),
        (Join-Path $DIST_DIR "${APP_NAME}-${APP_VERSION}-x86_64-Setup.exe")
    )
    
    foreach ($path in $pathsToClean) {
        if (Test-Path $path) {
            Remove-Item -Path $path -Recurse -Force
            Write-Info "已删除: $path"
        }
    }
    
    Write-Success "清理完成"
}

# 使用 PyInstaller 打包
function Build-WithPyInstaller {
    Write-Info "使用 PyInstaller 打包..."
    
    Set-Location $SCRIPT_DIR
    
    # 设置环境变量
    if ($OneFile) {
        $env:ONEFILE = "1"
        Write-Info "使用单文件模式"
    }
    else {
        $env:ONEFILE = "0"
        Write-Info "使用目录模式"
    }
    
    # 运行 PyInstaller，并将完整日志写入文件，便于在 CI 中排查失败原因
    $pyinstallerLog = Join-Path $BUILD_DIR "pyinstaller-windows.log"
    $pyinstallerErrLog = Join-Path $BUILD_DIR "pyinstaller-windows.stderr.log"
    if (-not (Test-Path $BUILD_DIR)) {
        New-Item -ItemType Directory -Path $BUILD_DIR | Out-Null
    }
    if (Test-Path $pyinstallerLog) {
        Remove-Item $pyinstallerLog -Force
    }
    if (Test-Path $pyinstallerErrLog) {
        Remove-Item $pyinstallerErrLog -Force
    }

    $process = Start-Process `
        -FilePath "python" `
        -ArgumentList "-m", "PyInstaller", "--clean", "--noconfirm", "Toolbox.spec" `
        -WorkingDirectory $SCRIPT_DIR `
        -RedirectStandardOutput $pyinstallerLog `
        -RedirectStandardError $pyinstallerErrLog `
        -NoNewWindow `
        -PassThru `
        -Wait

    if (Test-Path $pyinstallerLog) {
        Get-Content $pyinstallerLog | Write-Host
    }
    if (Test-Path $pyinstallerErrLog) {
        Get-Content $pyinstallerErrLog | Tee-Object -FilePath $pyinstallerLog -Append | Write-Host
    }
    $pyinstallerExitCode = $process.ExitCode
    
    # 检查构建产物是否存在
    $expectedOutput = if ($OneFile) { 
        Join-Path $DIST_DIR "Toolbox_win.exe" 
    }
    else { 
        Join-Path $DIST_DIR "Toolbox_win\Toolbox_win.exe" 
    }
    
    if (Test-Path $expectedOutput) {
        Write-Success "PyInstaller 打包完成"
    }
    else {
        if (Test-Path $pyinstallerLog) {
            Write-Warn "PyInstaller 未生成预期输出，以下为日志末尾 80 行："
            Get-Content $pyinstallerLog | Select-Object -Last 80 | Write-Host
        }
        if ($pyinstallerExitCode -ne 0) {
            Write-Warn "PyInstaller 退出码: $pyinstallerExitCode"
        }
        Write-Error-Custom "PyInstaller 打包失败：未找到输出文件 $expectedOutput"
    }
}

# 创建 ZIP 压缩包
function Create-ZipPackage {
    Write-Info "创建 ZIP 压缩包..."
    
    $sourceDir = Join-Path $DIST_DIR "Toolbox_win"
    $zipFile = Join-Path $DIST_DIR "${APP_NAME}-${APP_VERSION}-win-x64.zip"
    
    if (Test-Path $sourceDir) {
        # 删除旧的 ZIP 文件
        if (Test-Path $zipFile) {
            Remove-Item $zipFile -Force
        }
        
        # 创建 ZIP
        Compress-Archive -Path "$sourceDir\*" -DestinationPath $zipFile -CompressionLevel Optimal
        
        Write-Success "ZIP 压缩包创建完成: $zipFile"
    }
    else {
        Write-Warn "未找到打包输出目录，跳过 ZIP 创建"
    }
}

# 创建 NSIS 安装程序脚本
function Create-NSISScript {
    $nsisScript = @"
; NSIS 安装程序脚本
; ${APP_NAME} Installer

!include "MUI2.nsh"
!include "LogicLib.nsh"

; 基本信息
Name "${APP_NAME}"
OutFile "..\dist\${APP_NAME}-${APP_VERSION}-x86_64-Setup.exe"
InstallDir "`$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "Software\${APP_NAME}" "Install_Dir"
RequestExecutionLevel admin

; 界面设置
!define MUI_ABORTWARNING
!define MUI_ICON "..\resources\toolbox.ico"
!define MUI_UNICON "..\resources\toolbox.ico"

; 安装页面
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

; 卸载页面
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

; 语言
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Function EnsureToolboxNotRunning
    ; Detect running old version process
    nsExec::ExecToStack 'cmd /C tasklist /FI "IMAGENAME eq Toolbox_win.exe" | find /I "Toolbox_win.exe" >nul'
    Pop `$0

    `${If} `$0 == 0
        MessageBox MB_ICONEXCLAMATION|MB_YESNO "检测到旧版本 ${APP_NAME} 正在运行。`$\n`$\n是否现在关闭旧版本并继续安装？" IDYES close_old_version IDNO cancel_install

        close_old_version:
            nsExec::ExecToStack 'cmd /C taskkill /IM Toolbox_win.exe /F'
            Pop `$1
            Sleep 1000

            ; Verify process is closed before file copy
            nsExec::ExecToStack 'cmd /C tasklist /FI "IMAGENAME eq Toolbox_win.exe" | find /I "Toolbox_win.exe" >nul'
            Pop `$2
            `${If} `$2 == 0
                MessageBox MB_ICONSTOP "无法自动关闭正在运行的 ${APP_NAME}，请手动关闭后重新安装。"
                Abort
            `${EndIf}

            Goto done

        cancel_install:
            Abort
    `${EndIf}

    done:
FunctionEnd

; 安装部分
Section "安装 ${APP_NAME}" SecMain
    Call EnsureToolboxNotRunning
    SetOutPath `$INSTDIR
    
    ; 复制所有文件
    File /r "..\dist\Toolbox_win\*.*"
    
    ; 写入卸载信息
    WriteRegStr HKLM "Software\${APP_NAME}" "Install_Dir" "`$INSTDIR"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "UninstallString" '"`$INSTDIR\uninstall.exe"'
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "Publisher" "${APP_PUBLISHER}"
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoModify" 1
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}" "NoRepair" 1
    WriteUninstaller "`$INSTDIR\uninstall.exe"
    
    ; 创建开始菜单快捷方式
    CreateDirectory "`$SMPROGRAMS\${APP_NAME}"
    CreateShortcut "`$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "`$INSTDIR\Toolbox_win.exe"
    CreateShortcut "`$SMPROGRAMS\${APP_NAME}\卸载 ${APP_NAME}.lnk" "`$INSTDIR\uninstall.exe"
    
    ; 创建桌面快捷方式
    CreateShortcut "`$DESKTOP\${APP_NAME}.lnk" "`$INSTDIR\Toolbox_win.exe"
SectionEnd

; 卸载部分
Section "Uninstall"
    ; 删除注册表项
    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"
    DeleteRegKey HKLM "Software\${APP_NAME}"
    
    ; 删除快捷方式
    Delete "`$SMPROGRAMS\${APP_NAME}\*.*"
    RMDir "`$SMPROGRAMS\${APP_NAME}"
    Delete "`$DESKTOP\${APP_NAME}.lnk"
    
    ; 删除安装目录
    RMDir /r "`$INSTDIR"
SectionEnd
"@
    
    $nsisPath = Join-Path $PSScriptRoot "installer.nsi"
    $nsisScript | Out-File -FilePath $nsisPath -Encoding UTF8
    
    Write-Info "NSIS 脚本已创建: $nsisPath"
    return $nsisPath
}

# 创建安装程序
function Create-Installer {
    Write-Info "创建安装程序..."
    
    # 检查 NSIS 是否安装
    $nsisPath = ""
    $possiblePaths = @(
        "C:\Program Files (x86)\NSIS\makensis.exe",
        "C:\Program Files\NSIS\makensis.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\NSIS\makensis.exe")
    )
    
    foreach ($path in $possiblePaths) {
        if (Test-Path $path) {
            $nsisPath = $path
            break
        }
    }
    
    if (-not $nsisPath) {
        # 尝试从 PATH 中查找
        try {
            $nsisPath = (Get-Command makensis -ErrorAction Stop).Source
        }
        catch {
            Write-Warn "未找到 NSIS，跳过安装程序创建"
            Write-Warn "请从 https://nsis.sourceforge.io/Download 下载并安装 NSIS"
            return
        }
    }
    
    Write-Info "找到 NSIS: $nsisPath"
    
    # 创建 LICENSE 文件（如果不存在）
    $licensePath = Join-Path $PROJECT_ROOT "LICENSE"
    if (-not (Test-Path $licensePath)) {
        "MIT License`n`nCopyright (c) 2025 ${APP_PUBLISHER}`n`nPermission is hereby granted..." | Out-File -FilePath $licensePath -Encoding UTF8
    }
    
    # 创建 NSIS 脚本
    $nsisScript = Create-NSISScript
    
    # 运行 NSIS（明确按 UTF-8 解析脚本，避免中文字符导致编码错误）
    & $nsisPath "/INPUTCHARSET" "UTF8" $nsisScript
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "安装程序创建完成: dist\${APP_NAME}-${APP_VERSION}-x86_64-Setup.exe"
    }
    else {
        Write-Warn "NSIS 编译失败，请检查脚本"
    }
}

# 显示构建摘要
function Show-Summary {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  构建完成！" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    
    if ($OneFile) {
        $exePath = Join-Path $DIST_DIR "Toolbox_win.exe"
        if (Test-Path $exePath) {
            $size = (Get-Item $exePath).Length / 1MB
            Write-Host "单文件 EXE: " -NoNewline
            Write-Host $exePath -ForegroundColor Cyan
            Write-Host ("大小: {0:N2} MB" -f $size)
        }
    }
    else {
        $dirPath = Join-Path $DIST_DIR "Toolbox_win"
        if (Test-Path $dirPath) {
            $size = (Get-ChildItem $dirPath -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
            Write-Host "输出目录: " -NoNewline
            Write-Host $dirPath -ForegroundColor Cyan
            Write-Host ("总大小: {0:N2} MB" -f $size)
        
            $installerPath = Join-Path $DIST_DIR "${APP_NAME}-${APP_VERSION}-x86_64-Setup.exe"
            if (Test-Path $installerPath) {
                $installerSize = (Get-Item $installerPath).Length / 1MB
                Write-Host ""
                Write-Host "安装程序: " -NoNewline
                Write-Host $installerPath -ForegroundColor Cyan
                Write-Host ("大小: {0:N2} MB" -f $installerSize)
            }
        }
    }
    
    Write-Host ""
    Write-Host "运行方式:" -ForegroundColor Yellow
    if ($OneFile) {
        Write-Host "  .\dist\Toolbox_win.exe"
    }
    else {
        Write-Host "  .\dist\Toolbox_win\Toolbox_win.exe"
    }
}

# 主函数
function Main {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Green
    Write-Host "  $APP_NAME Windows 构建脚本 v$APP_VERSION" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    Write-Host ""
    
    # 检查依赖
    Check-Dependencies
    
    # 如果指定了清理
    if ($Clean) {
        Clean-Build
    }
    
    # PyInstaller 打包
    Build-WithPyInstaller
    
    
    # 默认创建 NSIS 安装程序（目录模式）
    if (-not $OneFile) {
        if ($CreateInstaller) {
            Create-Installer
        }
        else {
            # 默认创建安装程序
            Write-Info "默认创建 NSIS 安装程序（使用 -CreateInstaller:`$false 可禁用）"
            Create-Installer
        }
    }
    
    # 显示构建摘要
    Show-Summary
}

# 运行主函数
Main
