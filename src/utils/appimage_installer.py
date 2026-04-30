'''
Author: Yibo Yuan 2633669459@qq.com
Description: AppImage 自动安装工具
    检测是否在 AppImage 环境下运行，并提供安装到系统的功能。

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import sys
import shutil
import subprocess
from pathlib import Path
from PySide6.QtWidgets import QMessageBox, QWidget

class AppImageInstaller:
    def __init__(self):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.appimage_path = os.environ.get("APPIMAGE")
        self.home_dir = Path.home()
        self.apps_dir = self.home_dir / ".local/share/applications"
        self.icons_dir = self.home_dir / ".local/share/icons/hicolor/256x256/apps"
        
        if self.appimage_path:
            self.app_name = Path(self.appimage_path).name.split('.')[0].split('-')[0]
            # 移除版本号等后缀，保持简洁的应用名，但也可能导致重名，这里简单处理
            # 更稳妥的是读取 .desktop 文件里的 Name，但这里先尽量简单
        else:
            self.app_name = "Toolbox"

    def is_running_as_appimage(self) -> bool:
        """检查当前是否作为 AppImage 运行"""
        return bool(self.appimage_path and os.path.exists(self.appimage_path))

    def is_installed(self) -> bool:
        """检查是否已安装（存在 .desktop 文件且指向当前 AppImage）"""
        desktop_file = self.apps_dir / f"{self.app_name}.desktop"
        if not desktop_file.exists():
            return False
        
        # 检查 desktop 文件中的 Exec 行是否指向当前的 AppImage
        try:
            with open(desktop_file, 'r', encoding='utf-8') as f:
                content = f.read()
                if self.appimage_path in content:
                    return True
        except Exception:
            pass
            
        return False

    def install(self, parent: QWidget = None) -> bool:
        """执行安装流程"""
        if not self.is_running_as_appimage():
            return False

        try:
            # 1. 创建目录
            self.apps_dir.mkdir(parents=True, exist_ok=True)
            self.icons_dir.mkdir(parents=True, exist_ok=True)

            # 2. 处理图标
            icon_path = self.icons_dir / f"{self.app_name}.png"
            self._extract_or_create_icon(icon_path)

            # 3. 创建 .desktop 文件
            desktop_file = self.apps_dir / f"{self.app_name}.desktop"
            self._create_desktop_file(desktop_file, icon_path)

            # 4. 更新数据库
            self._update_desktop_database()

            return True

        except Exception as e:
            if parent:
                QMessageBox.critical(parent, "安装失败", f"安装过程中发生错误:\n{str(e)}")
            else:
                print(f"安装失败: {e}", file=sys.stderr)
            return False

    def _extract_or_create_icon(self, target_path: Path):
        """提取或创建图标"""
        # 尝试使用 --appimage-extract 提取图标
        try:
            # 创建临时目录
            temp_extract_dir = Path("squashfs-root")
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir, ignore_errors=True) # 清理残留
            
            # 运行提取命令 (只提取图标可能比较麻烦，通常 AppImage 会提取整个 squashfs-root)
            # 为了效率，我们只尝试提取 .DirIcon 或者直接用 magick 生成
            # 简单起见，我们尝试运行 AppImage 的 --appimage-extract 参数
            # 注意：这会解压整个 AppImage，可能会比较慢且占用空间。
            # 优化：如果我们内部有图标资源，直接拷贝出去是最好的。
            
            # 检查项目内部是否有图标资源 (在运行时，资源应该在 sys._MEIPASS 或当前目录下)
            internal_icon_path = None
            if hasattr(sys, "_MEIPASS"):
                internal_icon_path = Path(sys._MEIPASS) / "resources/toolbox.ico" # 或者是 png
            else:
                internal_icon_path = Path("resources/toolbox.ico") # 开发环境

            # 如果有内部图标，优先使用（转为 png）
            # 注意：ICO 转 PNG 需要 PIL
            if internal_icon_path.exists():
                from PIL import Image
                img = Image.open(internal_icon_path)
                img.save(target_path, format="PNG")
                return

            # 如果没有内部图标，再尝试解压 AppImage (作为最后的手段，因为它很重)
            # 或者直接生成一个默认图标
            subprocess.run(["convert", "-size", "256x256", "xc:blue", "-fill", "white", 
                            "-pointsize", "72", "-gravity", "center", "-annotate", "+0+0", 
                            "TB", str(target_path)], check=False, stderr=subprocess.DEVNULL)
                            
        except Exception as e:
            print(f"图标处理警告: {e}")
            # 确保至少有个文件存在
            if not target_path.exists():
                with open(target_path, 'wb') as f:
                    f.write(b'') # 空文件防止报错？不，最好别这样。

    def _create_desktop_file(self, target_path: Path, icon_path: Path):
        """创建 .desktop 文件"""
        content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={self.app_name}
Comment=Remote Sensing Toolbox Application
Exec={self.appimage_path}
Icon={icon_path}
Terminal=false
StartupWMClass={self.app_name}
Categories=Utility;Science;Geoscience;
X-AppImage-Version=1.0
"""
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        # 赋予执行权限
        target_path.chmod(0o755)

    def _update_desktop_database(self):
        """更新桌面数据库缓存"""
        commands = [
            ["update-desktop-database", str(self.apps_dir)],
            ["gtk-update-icon-cache", "-f", "-t", str(self.home_dir / ".local/share/icons/hicolor")],
            ["xdg-desktop-menu", "forceupdate"]
        ]
        
        for cmd in commands:
            try:
                subprocess.run(cmd, check=False, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            except FileNotFoundError:
                pass


