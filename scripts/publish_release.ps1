param(
    [string]$Remote = "origin"
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

function Update-VersionJson {
    param(
        [string]$Version,
        [string]$Repo
    )

    $versionJsonPath = Join-Path $PSScriptRoot "..\\version.json"
    $json = Get-Content $versionJsonPath -Raw | ConvertFrom-Json

    if ($json.version -ne $Version) {
        throw "version.json 中的 version ($($json.version)) 与 src/version.py ($Version) 不一致"
    }

    $tag = "v$Version"
    $json.release_url = "https://github.com/$Repo/releases/tag/$tag"
    $json.downloads.windows = "https://github.com/$Repo/releases/download/$tag/Toolbox-$Version-x86_64-Setup.exe"
    $json.downloads.linux = "https://github.com/$Repo/releases/download/$tag/Toolbox-$Version-x86_64.AppImage"
    $chinaTimeZone = [System.TimeZoneInfo]::FindSystemTimeZoneById("China Standard Time")
    $publishedAt = [System.TimeZoneInfo]::ConvertTime((Get-Date), $chinaTimeZone)
    $json.published_at = $publishedAt.ToString("yyyy-MM-ddTHH:mm:sszzz")

    $json | ConvertTo-Json -Depth 10 | Set-Content $versionJsonPath -Encoding UTF8
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

    $version = Get-VersionFromSource
    $tag = "v$version"
    $existingTag = git tag --list $tag
    if ($existingTag) {
        throw "Git tag $tag 已存在，请先处理后再发版。"
    }

    Update-VersionJson -Version $version -Repo $repo

    git add version.json
    git commit -m "release: $tag"

    git tag -a $tag -m "Release $tag"
    git push $Remote HEAD
    git push $Remote $tag

    Write-Host "已推送 $tag，GitHub Actions 将开始构建 Windows 和 Linux release。"
}

Main
