'''
Author: Yibo Yuan 2633669459@qq.com

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import webbrowser
from PySide6.QtWidgets import (QMainWindow, QApplication, QMessageBox, QDialog,
                               QWidget, QVBoxLayout, QGridLayout, QPushButton, QLabel,
                               QGroupBox, QScrollArea, QMenuBar, QMenu, QProgressDialog)
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import Qt, QThread, Signal
import sys
import traceback

# 导入自定义对话框
from src.dialogs import (TiffBoundarySettingsDialog, PixelTimeSeriesViewerDialog,
                         LocalImageViewerDialog, DEMAcquisitionDialog)

# 导入工具函数
from src.utils import tiff_boundary_to_vector
from src.utils import AppImageInstaller
from src.utils import UpdateChecker, UpdateError, NetworkError
from src.version import __version__, APP_DISPLAY_NAME


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        
        # 设置窗口属性
        self.setWindowTitle("遥感工具箱")
        self.resize(800, 600)
        
        # 获取项目根目录
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(current_dir)
        
        # 设置应用程序图标
        icon_path = os.path.join(project_root, 'resources', 'toolbox.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # 创建UI
        self._create_menu_bar()
        self._create_ui()

        # 检查是否需要安装 AppImage
        self._check_appimage_install()

    def _create_menu_bar(self):
        """创建菜单栏"""
        menubar = self.menuBar()
        
        # 帮助菜单
        help_menu = menubar.addMenu("帮助")
        
        # 检查更新
        check_update_action = QAction("检查更新...", self)
        check_update_action.triggered.connect(self._on_check_update)
        help_menu.addAction(check_update_action)
        
        help_menu.addSeparator()
        
        # 关于
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _create_ui(self):
        """创建用户界面"""
        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(15)
        
        # 标题
        title_label = QLabel("遥感工具箱")
        title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #2c3e50;")
        title_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(title_label)
        
        # 副标题
        subtitle_label = QLabel("Remote Sensing Toolbox")
        subtitle_label.setStyleSheet("font-size: 14px; color: #7f8c8d;")
        subtitle_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(subtitle_label)
        
        main_layout.addSpacing(20)
        
        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.NoFrame)
        
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(20)
        
        # =============== 图像分析工具组 ===============
        image_analysis_group = QGroupBox("图像分析工具")
        image_analysis_group.setStyleSheet("""
            QGroupBox {
                font-size: 16px;
                font-weight: bold;
                border: 2px solid #3498db;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 5px;
                color: #3498db;
            }
        """)
        
        image_analysis_layout = QGridLayout()
        image_analysis_layout.setSpacing(10)
        
        # 图像局部查看器按钮
        self.button_local_image_viewer = QPushButton("图像局部查看器")
        self.button_local_image_viewer.setMinimumHeight(50)
        self.button_local_image_viewer.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #21618c;
            }
        """)
        self.button_local_image_viewer.clicked.connect(self.on_button_local_image_viewer_click)
        image_analysis_layout.addWidget(self.button_local_image_viewer, 0, 0)
        
        # 像素时序查看器按钮
        self.button_pixel_time_series_viewer = QPushButton("像素时序查看器")
        self.button_pixel_time_series_viewer.setMinimumHeight(50)
        self.button_pixel_time_series_viewer.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #21618c;
            }
        """)
        self.button_pixel_time_series_viewer.clicked.connect(self.on_button_pixel_time_series_viewer_click)
        image_analysis_layout.addWidget(self.button_pixel_time_series_viewer, 0, 1)
        
        image_analysis_group.setLayout(image_analysis_layout)
        scroll_layout.addWidget(image_analysis_group)
        
        # =============== 栅格处理工具组 ===============
        raster_tools_group = QGroupBox("栅格处理工具")
        raster_tools_group.setStyleSheet("""
            QGroupBox {
                font-size: 16px;
                font-weight: bold;
                border: 2px solid #27ae60;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 5px;
                color: #27ae60;
            }
        """)
        
        raster_tools_layout = QGridLayout()
        raster_tools_layout.setSpacing(10)
        
        # TIFF边界转矢量按钮
        self.button_tiff_boundary_to_vector = QPushButton("TIFF边界转矢量")
        self.button_tiff_boundary_to_vector.setMinimumHeight(50)
        self.button_tiff_boundary_to_vector.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #229954;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        self.button_tiff_boundary_to_vector.clicked.connect(self.on_button_tiff_boundary_to_vector_click)
        raster_tools_layout.addWidget(self.button_tiff_boundary_to_vector, 0, 0)
        
        # DEM数据获取按钮
        self.button_dem_acquisition = QPushButton("DEM数据获取")
        self.button_dem_acquisition.setMinimumHeight(50)
        self.button_dem_acquisition.setStyleSheet("""
            QPushButton {
                background-color: #27ae60;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 10px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #229954;
            }
            QPushButton:pressed {
                background-color: #1e8449;
            }
        """)
        self.button_dem_acquisition.clicked.connect(self.on_button_dem_acquisition_click)
        raster_tools_layout.addWidget(self.button_dem_acquisition, 0, 1)
        
        raster_tools_group.setLayout(raster_tools_layout)
        scroll_layout.addWidget(raster_tools_group)
        
        # 添加弹性空间
        scroll_layout.addStretch()
        
        scroll_area.setWidget(scroll_widget)
        main_layout.addWidget(scroll_area)
        
        # 底部信息
        info_label = QLabel("© 2026 Yibo Yuan. All Rights Reserved.")
        info_label.setStyleSheet("font-size: 12px; color: #95a5a6;")
        info_label.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(info_label)
    
    def on_button_local_image_viewer_click(self):
        """
        图像局部查看器按钮点击事件
        """
        try:
            # 创建并显示图像局部查看器对话框
            dialog = LocalImageViewerDialog(self)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开图像局部查看器失败: {str(e)}")
            traceback.print_exc()
    
    def on_button_pixel_time_series_viewer_click(self):
        """
        像素时序查看器按钮点击事件
        """
        try:
            # 创建并显示像素时序查看器对话框
            dialog = PixelTimeSeriesViewerDialog(self)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开像素时序查看器失败: {str(e)}")
            traceback.print_exc()
    
    def on_button_tiff_boundary_to_vector_click(self):
        """
        按钮点击事件的处理逻辑，弹出参数设置对话框并执行转换（执行逻辑已迁移到对话框类）
        """
        try:
            dialog = TiffBoundarySettingsDialog(self)
            result = dialog.exec()
            if result == QDialog.Accepted:
                dialog.execute_conversion()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"发生异常: {str(e)}")
            traceback.print_exc()
    
    def on_button_dem_acquisition_click(self):
        """
        DEM数据获取按钮点击事件
        """
        try:
            # 创建并显示DEM数据获取对话框
            dialog = DEMAcquisitionDialog(self)
            dialog.exec()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开DEM数据获取工具失败: {str(e)}")
            traceback.print_exc()

    def _check_appimage_install(self):
        """检查 AppImage 运行状态并询问是否安装"""
        try:
            installer = AppImageInstaller()
            if installer.is_running_as_appimage() and not installer.is_installed():
                # 延时一点显示，让主界面先出来
                from PySide6.QtCore import QTimer
                QTimer.singleShot(500, lambda: self._ask_install_appimage(installer))
        except Exception:
            # 静默失败，不影响主程序
            pass

    def _ask_install_appimage(self, installer):
        """询问用户是否安装 AppImage"""
        reply = QMessageBox.question(
            self, 
            "安装应用", 
            "检测到您正在运行 AppImage 版本，是否将应用安装到系统菜单中？\n\n"
            "安装后，您可以直接从应用启动器启动本软件。",
            QMessageBox.Yes | QMessageBox.No, 
            QMessageBox.Yes
        )
        
        if reply == QMessageBox.Yes:
            if installer.install(self):
                QMessageBox.information(
                    self, 
                    "安装成功", 
                    "应用已成功安装到系统菜单！\n\n"
                    "提示：如果没有立即看到图标，可能需要注销并重新登录。"
                )
            # 如果安装失败，installer.install 内部会显示错误框，这里不需要处理

    def _on_check_update(self):
        """检查更新菜单项点击事件"""
        try:
            checker = UpdateChecker()
            
            # 显示检查中提示
            self.statusBar().showMessage("正在检查更新...")
            QApplication.processEvents()
            
            try:
                update_info = checker.check_for_updates()
            except NetworkError as e:
                QMessageBox.warning(self, "网络错误", str(e))
                self.statusBar().clearMessage()
                return
            except UpdateError as e:
                QMessageBox.critical(self, "检查更新失败", str(e))
                self.statusBar().clearMessage()
                return
            
            self.statusBar().clearMessage()
            
            if update_info:
                self._show_update_dialog(update_info, checker)
            else:
                QMessageBox.information(
                    self, 
                    "检查更新", 
                    f"当前已是最新版本 (v{__version__})"
                )
                
        except Exception as e:
            QMessageBox.critical(self, "错误", f"检查更新时发生错误: {str(e)}")
            traceback.print_exc()
    
    def _show_update_dialog(self, update_info: dict, checker: UpdateChecker):
        """显示更新对话框"""
        version = update_info['version']
        name = update_info.get('name', f'v{version}')
        body = update_info.get('body', '暂无更新说明')
        download_url = update_info.get('download_url')
        html_url = update_info.get('html_url', '')
        
        message = f"发现新版本: {name}\n\n"
        message += f"当前版本: v{__version__}\n"
        message += f"最新版本: v{version}\n\n"
        message += "更新说明:\n"
        message += body[:500] + ("..." if len(body) > 500 else "")
        
        if download_url:
            reply = QMessageBox.question(
                self,
                "发现新版本",
                message + "\n\n是否立即下载并安装？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Yes:
                self._download_and_install_update(download_url, checker)
        else:
            # 没有当前平台的下载链接，引导到浏览器
            reply = QMessageBox.question(
                self,
                "发现新版本",
                message + "\n\n未找到当前平台的安装包，是否打开下载页面？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes and html_url:
                webbrowser.open(html_url)
    
    def _download_and_install_update(self, url: str, checker: UpdateChecker):
        """下载并安装更新"""
        # 创建进度对话框
        progress = QProgressDialog("正在下载更新...", "取消", 0, 100, self)
        progress.setWindowTitle("下载更新")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        
        downloaded_file = None
        
        def update_progress(downloaded: int, total: int):
            if progress.wasCanceled():
                raise UpdateError("用户取消下载")
            percent = int((downloaded / total) * 100) if total > 0 else 0
            progress.setValue(percent)
            progress.setLabelText(f"正在下载更新... ({downloaded // 1024}KB / {total // 1024}KB)")
            QApplication.processEvents()
        
        try:
            downloaded_file = checker.download_update(url, update_progress)
            progress.close()
            
            # 询问是否立即安装
            reply = QMessageBox.question(
                self,
                "下载完成",
                "更新已下载完成，是否立即安装？\n\n"
                "安装将关闭当前程序。",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Yes:
                if checker.apply_update(downloaded_file):
                    # 退出应用
                    QApplication.quit()
                    
        except NetworkError as e:
            progress.close()
            QMessageBox.warning(self, "下载失败", str(e))
        except UpdateError as e:
            progress.close()
            if "取消" not in str(e):
                QMessageBox.critical(self, "更新失败", str(e))
        except Exception as e:
            progress.close()
            QMessageBox.critical(self, "错误", f"下载更新时发生错误: {str(e)}")
            traceback.print_exc()
    
    def _on_about(self):
        """关于菜单项点击事件"""
        about_text = f"""<h2>{APP_DISPLAY_NAME}</h2>
<p>版本: v{__version__}</p>
<p>一个地理信息处理工具箱，提供栅格数据处理、矢量数据处理等功能。</p>
<p><b>作者:</b> Yibo Yuan</p>
<p><b>邮箱:</b> 2633669459@qq.com</p>
<p><b>开源地址:</b> <a href="https://github.com/BlueBlueKitty/Toolbox">GitHub</a></p>
<p>© 2026 Yibo Yuan. All Rights Reserved.</p>
"""
        QMessageBox.about(self, f"关于 {APP_DISPLAY_NAME}", about_text)


if __name__ == "__main__":
    # 创建应用程序实例
    app = QApplication(sys.argv)

    # 创建主窗口实例
    window = MainWindow()
    window.show()

    # 运行应用程序
    sys.exit(app.exec())