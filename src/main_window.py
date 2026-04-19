'''
Author: Yibo Yuan 2633669459@qq.com

Copyright (c) 2025 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import webbrowser
from pathlib import Path
from PySide6.QtWidgets import (QMainWindow, QApplication, QMessageBox, QDialog,
                               QWidget, QVBoxLayout, QGridLayout, QPushButton, QLabel,
                               QGroupBox, QScrollArea, QMenuBar, QMenu, QProgressDialog)
from PySide6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor
from PySide6.QtCore import Qt, QThread, Signal, QSettings, QTimer, QEvent
import sys
import traceback

# 导入自定义对话框
from src.dialogs import (TiffBoundarySettingsDialog, PixelTimeSeriesViewerDialog,
                         LocalImageViewerDialog, RasterDataAcquisitionDialog,
                         ImageSegmentationDialog)

# 导入工具函数
from src.utils import tiff_boundary_to_vector
from src.utils import AppImageInstaller
from src.utils import UpdateChecker, UpdateError, NetworkError
from src.utils.display_pyramid import (
    cleanup_pyramid_cache_async,
    default_cache_dir,
    get_cache_dir,
    set_cache_dir,
)
from src.version import __version__, APP_DISPLAY_NAME
from src.segmentation.project_manager import get_settings as get_segmentation_settings


class UpdateCheckWorker(QThread):
    update_found = Signal(dict)
    no_update = Signal()
    error_occurred = Signal(str, bool)

    def run(self):
        checker = UpdateChecker()
        try:
            update_info = checker.check_for_updates()
            if update_info:
                self.update_found.emit(update_info)
            else:
                self.no_update.emit()
        except NetworkError as e:
            self.error_occurred.emit(str(e), True)
        except UpdateError as e:
            self.error_occurred.emit(str(e), False)
        except Exception as e:
            self.error_occurred.emit(str(e), False)


class MainWindow(QMainWindow):
    def __init__(self):
        super(MainWindow, self).__init__()
        
        # 初始化设置
        self.settings = QSettings("Toolbox", "RemoteSensingToolbox")
        self.segmentation_settings = get_segmentation_settings()
        
        # 显示金字塔阈值：超过该体积的图像会为渲染预览建立 overview。
        self.pyramid_threshold_mb = self.settings.value("display/pyramid_threshold_mb", 1024, type=int)
        if int(self.pyramid_threshold_mb) == 64:
            self.pyramid_threshold_mb = 512
            self.settings.setValue("display/pyramid_threshold_mb", self.pyramid_threshold_mb)
        self.geotiff_full_render_cache_limit_mb = self.pyramid_threshold_mb
        self._open_tool_windows = set()
        cleanup_pyramid_cache_async(7)
        
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

        # 自动检查更新（后台执行）
        self._latest_update_info = None
        self._update_worker = None
        QTimer.singleShot(800, self._start_auto_update_check)

    def _create_menu_bar(self):
        """创建菜单栏"""
        menubar = self.menuBar()
        
        # 设置菜单
        settings_menu = menubar.addMenu("设置")
        
        # 显示设置
        display_settings_action = QAction("显示设置...", self)
        display_settings_action.triggered.connect(self._on_display_settings)
        settings_menu.addAction(display_settings_action)
        
        # 帮助菜单
        help_menu = menubar.addMenu("帮助")
        self.help_menu_action = help_menu.menuAction()
        self._help_menu_text = "帮助"
        self._init_help_menu_dot(menubar)
        
        # 检查更新
        self.check_update_action = QAction("检查更新...", self)
        self.check_update_action.triggered.connect(self._on_check_update)
        help_menu.addAction(self.check_update_action)
        
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

        # 图像分割工具按钮
        self.button_image_segmentation = QPushButton("图像分割工具")
        self.button_image_segmentation.setMinimumHeight(50)
        self.button_image_segmentation.setStyleSheet("""
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
        self.button_image_segmentation.clicked.connect(self.on_button_image_segmentation_click)
        image_analysis_layout.addWidget(self.button_image_segmentation, 1, 0, 1, 2)
        
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
        
        # 栅格数据获取按钮
        self.button_dem_acquisition = QPushButton("栅格数据获取工具")
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
            dialog = LocalImageViewerDialog(self, pyramid_threshold_mb=self.pyramid_threshold_mb)
            self._show_tool_window(dialog)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开图像局部查看器失败: {str(e)}")
            traceback.print_exc()
    
    def on_button_pixel_time_series_viewer_click(self):
        """
        像素时序查看器按钮点击事件
        """
        try:
            dialog = PixelTimeSeriesViewerDialog(self, pyramid_threshold_mb=self.pyramid_threshold_mb)
            self._show_tool_window(dialog)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开像素时序查看器失败: {str(e)}")
            traceback.print_exc()
    
    def on_button_tiff_boundary_to_vector_click(self):
        """
        按钮点击事件的处理逻辑，弹出参数设置对话框并执行转换（执行逻辑已迁移到对话框类）
        """
        try:
            dialog = TiffBoundarySettingsDialog(self)
            self._show_tool_window(dialog)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"发生异常: {str(e)}")
            traceback.print_exc()

    def on_button_image_segmentation_click(self):
        """
        图像分割工具按钮点击事件
        """
        try:
            dialog = ImageSegmentationDialog(self, pyramid_threshold_mb=self.pyramid_threshold_mb)
            self._show_tool_window(dialog)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开图像分割工具失败: {str(e)}")
            traceback.print_exc()
    
    def on_button_dem_acquisition_click(self):
        """
        栅格数据获取工具按钮点击事件
        """
        try:
            dialog = RasterDataAcquisitionDialog(self)
            self._show_tool_window(dialog)
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开栅格数据获取工具失败: {str(e)}")
            traceback.print_exc()

    def _show_tool_window(self, dialog):
        """以非模态方式显示工具窗口，并持有引用直到关闭。"""
        if dialog.parent() is self:
            dialog.setParent(None)

        dialog.setWindowFlag(Qt.Window, True)
        dialog.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        dialog.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        dialog.setWindowFlag(Qt.WindowCloseButtonHint, True)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        self._open_tool_windows.add(dialog)
        dialog.destroyed.connect(lambda *_args, ref=dialog: self._open_tool_windows.discard(ref))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

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

    def _init_help_menu_dot(self, menubar: QMenuBar):
        """在菜单栏上覆盖一个小红点，避免图标吞文字"""
        self.help_menu_dot = QLabel(menubar)
        self.help_menu_dot.setFixedSize(8, 8)
        self.help_menu_dot.setStyleSheet("background-color: #e74c3c; border-radius: 4px;")
        self.help_menu_dot.setVisible(False)
        self.help_menu_dot.raise_()
        menubar.installEventFilter(self)

    def _update_help_menu_dot_position(self):
        if not getattr(self, "help_menu_action", None):
            return
        menubar = self.menuBar()
        rect = menubar.actionGeometry(self.help_menu_action)
        if rect.isNull():
            self.help_menu_dot.setVisible(False)
            return
        x = rect.right() - 8
        y = rect.top() + 4
        self.help_menu_dot.move(x, y)

    def eventFilter(self, obj, event):
        if obj is self.menuBar() and event.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest, QEvent.ActionChanged):
            if getattr(self, "help_menu_dot", None):
                self._update_help_menu_dot_position()
        return super().eventFilter(obj, event)

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
                self._set_update_indicator(True, update_info)
                self._show_update_dialog(update_info, checker)
            else:
                self._set_update_indicator(False, None)
                QMessageBox.information(
                    self, 
                    "检查更新", 
                    f"当前已是最新版本 (v{__version__})"
                )
                
        except Exception as e:
            QMessageBox.critical(self, "错误", f"检查更新时发生错误: {str(e)}")
            traceback.print_exc()

    def _start_auto_update_check(self):
        """启动自动检查更新（后台线程）"""
        try:
            self.statusBar().showMessage("正在检查更新...")
            self._update_worker = UpdateCheckWorker()
            self._update_worker.update_found.connect(self._on_auto_update_found)
            self._update_worker.no_update.connect(self._on_auto_no_update)
            self._update_worker.error_occurred.connect(self._on_auto_update_error)
            self._update_worker.start()
        except Exception:
            # 自动检查失败不影响主程序
            self.statusBar().clearMessage()

    def _on_auto_update_found(self, update_info: dict):
        self.statusBar().clearMessage()
        self._set_update_indicator(True, update_info)
        # 自动提示更新内容
        checker = UpdateChecker()
        self._show_update_dialog(update_info, checker)

    def _on_auto_no_update(self):
        self.statusBar().clearMessage()

    def _on_auto_update_error(self, message: str, is_network: bool):
        # 自动检查失败不打扰用户，仅在状态栏提示
        self.statusBar().showMessage("自动检查更新失败")
        QTimer.singleShot(5000, self.statusBar().clearMessage)

    def _set_update_indicator(self, has_update: bool, update_info: dict | None):
        """在菜单中显示/隐藏小红点提醒"""
        if has_update:
            red_dot = self._get_red_dot_icon()
            self.check_update_action.setIcon(red_dot)
            if hasattr(self, "help_menu_dot") and self.help_menu_dot:
                self.help_menu_dot.setVisible(True)
                self._update_help_menu_dot_position()
            self.check_update_action.setText("检查更新...  (有新版本)")
            if update_info:
                version = update_info.get('version', '')
                changelog = update_info.get('body', '')
                tip = f"发现新版本 v{version}\n" + (changelog[:200] + ("..." if len(changelog) > 200 else ""))
                self.check_update_action.setToolTip(tip)
            self._latest_update_info = update_info
        else:
            self.check_update_action.setIcon(QIcon())
            if hasattr(self, "help_menu_dot") and self.help_menu_dot:
                self.help_menu_dot.setVisible(False)
            self.check_update_action.setText("检查更新...")
            self.check_update_action.setToolTip("")
            self._latest_update_info = None

    def _get_red_dot_icon(self) -> QIcon:
        """生成小红点图标"""
        size = 10
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(QColor("#e74c3c"))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, size - 1, size - 1)
        painter.end()
        return QIcon(pixmap)
    
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
<p>一个遥感数据处理工具箱，提供遥感数据显示、处理、获取等功能。</p>
<p><b>作者:</b> Yibo Yuan</p>
<p><b>邮箱:</b> 2633669459@qq.com</p>
<p><b>开源地址:</b> <a href="https://github.com/BlueBlueKitty/Toolbox">GitHub</a></p>
<p>© 2026 Yibo Yuan. All Rights Reserved.</p>
"""
        QMessageBox.about(self, f"关于 {APP_DISPLAY_NAME}", about_text)
    
    def _on_display_settings(self):
        """显示设置对话框"""
        from PySide6.QtWidgets import (
            QDialog,
            QVBoxLayout,
            QLabel,
            QSpinBox,
            QDialogButtonBox,
            QHBoxLayout,
            QCheckBox,
            QLineEdit,
            QFileDialog,
        )
        
        dialog = QDialog(self)
        dialog.setWindowTitle("显示设置")
        dialog.setMinimumWidth(400)
        
        layout = QVBoxLayout(dialog)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        def parameter_label(text: str, tooltip: str) -> QLabel:
            label = QLabel(f'{text}<sup><span style="color:#3498db;"> ⓘ</span></sup>')
            label.setTextFormat(Qt.RichText)
            label.setToolTip(tooltip)
            label.setCursor(Qt.WhatsThisCursor)
            return label
        
        threshold_layout = QHBoxLayout()
        threshold_label = parameter_label(
            "金字塔渲染的栅格文件体积阈值:",
            "GeoTIFF、GAMMA、HDF5/NetCDF 等栅格数据大于该阈值时会自动建立或使用金字塔进行渲染，以保证缩放和平移流畅。\n"
            "阈值越小，越早建立金字塔；首次打开大数据可能需要一些时间，但后续浏览会更丝滑。"
        )
        threshold_layout.addWidget(threshold_label)
        
        threshold_spinbox = QSpinBox()
        threshold_spinbox.setRange(1, 102400)
        threshold_spinbox.setSingleStep(128)
        threshold_spinbox.setValue(max(1, int(self.pyramid_threshold_mb)))
        threshold_spinbox.setSuffix(" MB")
        threshold_spinbox.setMinimumWidth(120)
        threshold_layout.addWidget(threshold_spinbox)
        threshold_layout.addStretch()
        layout.addLayout(threshold_layout)
        
        layout.addSpacing(10)

        cache_layout = QVBoxLayout()
        cache_label = parameter_label(
            "显示缓存目录:",
            "晕渲地貌的山体阴影、转dB后的结果等显示缓存都会写入该目录。"
        )
        cache_layout.addWidget(cache_label)

        cache_path_layout = QHBoxLayout()
        cache_dir_edit = QLineEdit(str(get_cache_dir()))
        cache_dir_edit.setMinimumWidth(260)
        cache_path_layout.addWidget(cache_dir_edit, 1)

        browse_button = QPushButton("浏览...")
        reset_cache_button = QPushButton("恢复默认")

        def browse_cache_dir():
            start_dir = cache_dir_edit.text().strip() or str(default_cache_dir())
            selected = QFileDialog.getExistingDirectory(dialog, "选择显示缓存目录", start_dir)
            if selected:
                cache_dir_edit.setText(selected)

        browse_button.clicked.connect(browse_cache_dir)
        reset_cache_button.clicked.connect(lambda: cache_dir_edit.setText(str(default_cache_dir())))
        cache_path_layout.addWidget(browse_button)
        cache_path_layout.addWidget(reset_cache_button)
        cache_layout.addLayout(cache_path_layout)
        layout.addLayout(cache_layout)

        layout.addSpacing(10)
        
        # 平滑显示设置
        smooth_display = self.settings.value("display/smooth_display", False, type=bool)
        smooth_layout = QHBoxLayout()
        smooth_label = parameter_label(
            "平滑显示:",
            "启用后，图像缩放时使用双线性插值，显示更平滑。\n"
            "禁用后，显示栅格边界，适合查看像素细节。"
        )
        smooth_layout.addWidget(smooth_label)
        smooth_check = QCheckBox("启用双线性插值")
        smooth_check.setChecked(smooth_display)
        smooth_layout.addWidget(smooth_check)
        smooth_layout.addStretch()
        layout.addLayout(smooth_layout)
        
        layout.addStretch()
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        if dialog.exec() == QDialog.Accepted:
            cache_dir_text = cache_dir_edit.text().strip()
            cache_dir_path = (
                Path(os.path.expandvars(os.path.expanduser(cache_dir_text))).resolve()
                if cache_dir_text
                else default_cache_dir()
            )
            try:
                cache_dir_path.mkdir(parents=True, exist_ok=True)
                if not os.access(cache_dir_path, os.W_OK):
                    raise OSError("目录不可写")
            except OSError as exc:
                QMessageBox.critical(self, "缓存目录不可用", f"无法使用缓存目录:\n{cache_dir_path}\n\n{exc}")
                return

            self.pyramid_threshold_mb = threshold_spinbox.value()
            smooth_display = smooth_check.isChecked()
            self.geotiff_full_render_cache_limit_mb = self.pyramid_threshold_mb
            self.settings.setValue("display/pyramid_threshold_mb", self.pyramid_threshold_mb)
            self.settings.setValue("display/smooth_display", smooth_display)
            cache_dir_path = set_cache_dir(cache_dir_path)
            self.segmentation_settings.setValue("segmentation/geotiff_full_render_cache_limit_mb", self.geotiff_full_render_cache_limit_mb)
            QMessageBox.information(
                self, 
                "设置已保存",
                f"栅格显示阈值: {self.pyramid_threshold_mb} MB\n"
                f"显示缓存目录: {cache_dir_path}\n"
                f"平滑显示: {'已启用' if smooth_display else '已禁用'}\n"
                "图像分割工具的整图渲染缓存阈值已同步使用该值。\n\n"
                "新设置将在下次打开图像或生成缓存时生效。"
            )


if __name__ == "__main__":
    # 创建应用程序实例
    app = QApplication(sys.argv)

    # 创建主窗口实例
    window = MainWindow()
    window.show()

    # 运行应用程序
    sys.exit(app.exec())
