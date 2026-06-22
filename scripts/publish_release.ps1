param(
    [string]$Remote = "origin",
    [string]$Version
)

$ErrorActionPreference = "Stop"

function Get-VersionFromSource {
    $versionFile = Join-Path $PSScriptRoot "..\\src\\version.py"
    $match = Select-String -Path $versionFile -Pattern "__version__\s*=\s*['""]([^'""]+)['""]" | Select-Object -First 1
    if (-not $match) {
        throw "无法从 src/version.py 读取版本号"
    }
    return $match.Matches[0].Groups[1].Value
}

function Set-VersionInSource {
    param(
        [string]$Version
    )

    $versionFile = Join-Path $PSScriptRoot "..\\src\\version.py"
    $content = Get-Content $versionFile -Raw
    $updated = [regex]::Replace(
        $content,
        "__version__\s*=\s*['""][^'""]+['""]",
        "__version__ = '$Version'",
        1
    )
    if ($updated -eq $content) {
        throw "无法更新 src/version.py 中的版本号"
    }
    Set-Content $versionFile -Value $updated -Encoding UTF8
}

function Update-VersionJson {
    param(
        [string]$Version,
        [string]$Repo
    )

    $versionJsonPath = Join-Path $PSScriptRoot "..\\version.json"
    $json = Get-Content $versionJsonPath -Raw | ConvertFrom-Json

    $json.version = $Version

    $tag = "v$Version"
    $json.release_url = "https://github.com/$Repo/releases/tag/$tag"
    $json.downloads = [ordered]@{
        windows_x86_64 = "https://github.com/$Repo/releases/download/$tag/Toolbox_${Version}_windows_x86_64.exe"
        linux_x86_64   = "https://github.com/$Repo/releases/download/$tag/Toolbox-${Version}_linux_x86_64.AppImage"
        linux_arm64    = "https://github.com/$Repo/releases/download/$tag/Toolbox-${Version}_linux_arm64.AppImage"
        mac_x86_64     = "https://github.com/$Repo/releases/download/$tag/Toolbox_${Version}_apple_x86_64.dmg"
        mac_arm64      = "https://github.com/$Repo/releases/download/$tag/Toolbox_${Version}_apple_arm64.dmg"
    }
    $chinaTimeZone = [System.TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
    $publishedAt = [System.TimeZoneInfo]::ConvertTime((Get-Date), $chinaTimeZone)
    $json.published_at = $publishedAt.ToString("yyyy-MM-ddTHH:mm:sszzz")

    $json | ConvertTo-Json -Depth 10 | Set-Content $versionJsonPath -Encoding UTF8
}

function Get-NextPatchVersion {
    param(
        [string]$CurrentVersion
    )

    if ($CurrentVersion -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
        throw "当前版本号 '$CurrentVersion' 不是 x.y.z 格式，无法自动递增，请手动输入发布版本。"
    }
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    $patch = [int]$Matches[3] + 1
    return "$major.$minor.$patch"
}

function Resolve-ReleaseVersion {
    param(
        [string]$CurrentVersion,
        [string]$SpecifiedVersion
    )

    Write-Host "当前版本: $CurrentVersion"
    $targetVersion = $SpecifiedVersion
    if (-not $targetVersion) {
        $defaultVersion = Get-NextPatchVersion -CurrentVersion $CurrentVersion
        $targetVersion = Read-Host "请输入要发布的版本号（直接回车使用默认版本 $defaultVersion）"
    }
    if (-not $targetVersion) {
        $targetVersion = $defaultVersion
    }
    $targetVersion = $targetVersion.Trim()
    if (-not $targetVersion) {
        throw "版本号不能为空。"
    }
    return $targetVersion
}

function Assert-CleanWorktree {
    $status = git status --porcelain
    if ($status) {
        throw "当前 git 工作区不是干净状态，请先提交或清理改动后再发版。"
    }
}

function Main {
    $repo = (Select-String -Path (Join-Path $PSScriptRoot "..\\src\\version.py") -Pattern "GITHUB_REPO\s*=\s*['""]([^'""]+)['""]" | Select-Object -First 1).Matches[0].Groups[1].Value
    if (-not $repo) {
        throw "无法从 src/version.py 读取 GITHUB_REPO"
    }

    Assert-CleanWorktree

    $currentVersion = Get-VersionFromSource
    $version = Resolve-ReleaseVersion -CurrentVersion $currentVersion -SpecifiedVersion $Version
    if ($version -ne $currentVersion) {
        Set-VersionInSource -Version $version
    }
    $tag = "v$version"
    $existingTag = git tag --list $tag
    if ($existingTag) {
        throw "Git tag $tag 已存在，请先处理后再发版。"
    }

    Update-VersionJson -Version $version -Repo $repo

    git add src/version.py version.json
    git commit -m "release: $tag"

    git tag -a $tag -m "Release $tag"
    git push $Remote HEAD
    git push $Remote $tag

    Write-Host "已推送 $tag，GitHub Actions 将开始构建 Windows/Linux/macOS 多架构 release。"
}

Main
