'''
Author: Yibo Yuan 2633669459@qq.com
Description: 版本信息定义
    这是应用程序版本信息的唯一来源。

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

__version__ = '1.6.0'
__author__ = 'Yibo Yuan'
__email__ = '2633669459@qq.com'

# GitHub 仓库信息
GITHUB_REPO = 'BlueBlueKitty/Toolbox'
# 使用 raw.githubusercontent.com 访问仓库中的版本信息文件（避免API速率限制）
VERSION_JSON_URL = f'https://raw.githubusercontent.com/{GITHUB_REPO}/main/version.json'
# GitHub Releases 页面（用于手动下载）
GITHUB_RELEASES_URL = f'https://github.com/{GITHUB_REPO}/releases'

# 应用程序信息
APP_NAME = 'Toolbox'
APP_DISPLAY_NAME = '遥感工具箱'
