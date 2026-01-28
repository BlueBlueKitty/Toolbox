'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-22
Description: 图像局部查看器对话框

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import numpy as np
from pathlib import Path
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                               QFileDialog, QLabel, QMessageBox, QSplitter, 
                               QGroupBox, QButtonGroup, QRadioButton, QListWidget,
                               QDialogButtonBox, QInputDialog, QComboBox)
from PySide6.QtCore import Qt, QSettings

# 配置文件路径
def get_settings():
    config_dir = Path.home() / ".toolbox"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "local_image_viewer.ini"
    return QSettings(str(config_file), QSettings.IniFormat)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import traceback

from src.widgets import InteractiveImageViewer, ColormapComboBox
from src.utils.gamma_file_process import (
    GAMMA_FORMATS,
    read_gamma_downsampled,
    read_gamma_region,
    read_gamma_pixel,
    find_valid_par_for_binary,
    validate_dimensions,
    complex_to_phase,
    is_gamma_binary_file,
)
from src.utils.image_io import (
    read_tiff_downsampled,
    read_tiff_region,
    read_tiff_pixel,
    read_image_downsampled,
    read_image_region,
    list_h5_datasets,
    read_h5_dataset,
)
from src.dialogs.gamma_dialogs import GammaSingleFileDialog


class LocalImageViewerDialog(QDialog):
    """图像局部查看器对话框"""
    
    # 降采样配置
    MAX_DISPLAY_SIZE = 2048  # 显示时的最大尺寸
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        self.setWindowTitle("图像局部查看器")
        self.resize(1400, 800)
        
        # 图像数据
        self.image_data = None  # 降采样后的显示数据
        self.image_file = None
        self.nodata_value = None
        self.polyline_path_points = None  # 存储折线路径上的所有点
        
        # 大图像降采样相关
        self.original_width = None   # 原始图像宽度
        self.original_height = None  # 原始图像高度
        self.downsample_factor = 1   # 降采样因子
        self.is_tiff = False         # 是否为TIFF格式
        
        # GAMMA二进制文件相关
        self.is_gamma = False           # 是否为GAMMA二进制文件
        self.gamma_format = "float32"   # GAMMA数据格式
        self.gamma_par_file = None      # PAR文件路径
        
        # dB转换标志
        self._converted_to_db = False   # 是否已转换为dB
        
        # 创建UI
        self._create_ui()
        
    def _create_ui(self):
        """创建用户界面"""
        main_layout = QVBoxLayout(self)
        
        # 顶部控制区
        control_layout = QHBoxLayout()
        
        self.open_btn = QPushButton("打开图像")
        self.open_btn.clicked.connect(self.open_image)
        control_layout.addWidget(self.open_btn)
        
        self.open_gamma_btn = QPushButton("打开GAMMA文件")
        self.open_gamma_btn.clicked.connect(self.open_gamma_file)
        control_layout.addWidget(self.open_gamma_btn)
        
        self.open_h5_btn = QPushButton("打开h5文件")
        self.open_h5_btn.clicked.connect(self.open_h5_file)
        control_layout.addWidget(self.open_h5_btn)
        
        control_layout.addWidget(QLabel("颜色映射:"))
        self.colormap_combo = ColormapComboBox()
        self.colormap_combo.currentTextChanged.connect(self.on_colormap_changed)
        control_layout.addWidget(self.colormap_combo)
        
        self.set_nodata_btn = QPushButton("设置Nodata值")
        self.set_nodata_btn.clicked.connect(self.set_nodata_value)
        control_layout.addWidget(self.set_nodata_btn)
        
        self.to_db_btn = QPushButton("转为dB")
        self.to_db_btn.clicked.connect(self.convert_to_db)
        self.to_db_btn.setEnabled(False)
        control_layout.addWidget(self.to_db_btn)
        
        # 绘制模式选择
        control_layout.addWidget(QLabel("绘制模式:"))
        self.mode_group = QButtonGroup(self)
        
        self.mode_none_radio = QRadioButton("浏览")
        self.mode_none_radio.setChecked(True)
        self.mode_none_radio.clicked.connect(self.on_mode_changed)
        self.mode_group.addButton(self.mode_none_radio, 0)
        control_layout.addWidget(self.mode_none_radio)
        
        self.mode_rect_radio = QRadioButton("矩形")
        self.mode_rect_radio.clicked.connect(self.on_mode_changed)
        self.mode_group.addButton(self.mode_rect_radio, 1)
        control_layout.addWidget(self.mode_rect_radio)
        
        self.mode_polyline_radio = QRadioButton("折线")
        self.mode_polyline_radio.clicked.connect(self.on_mode_changed)
        self.mode_group.addButton(self.mode_polyline_radio, 2)
        control_layout.addWidget(self.mode_polyline_radio)
        
        self.clear_btn = QPushButton("清除绘制")
        self.clear_btn.clicked.connect(self.clear_drawing)
        control_layout.addWidget(self.clear_btn)
        
        control_layout.addStretch()
        
        main_layout.addLayout(control_layout)
        
        # 第二排：文件信息
        info_layout = QHBoxLayout()
        self.image_info_label = QLabel("未加载图像")
        info_layout.addWidget(self.image_info_label)
        info_layout.addStretch()
        main_layout.addLayout(info_layout)
        
        # 创建主分割器：左侧图像，右侧图表
        splitter = QSplitter(Qt.Horizontal)
        
        # ========== 左侧：图像查看区 ==========
        left_widget = QGroupBox("图像查看")
        left_layout = QVBoxLayout(left_widget)
        
        # 图像查看器
        self.image_viewer = InteractiveImageViewer()
        self.image_viewer.mouse_moved.connect(self.on_mouse_moved)
        self.image_viewer.rect_drawn.connect(self.on_rect_drawn)
        self.image_viewer.polyline_drawn.connect(self.on_polyline_drawn)
        self.image_viewer.polyline_hover.connect(self.on_polyline_hover)
        left_layout.addWidget(self.image_viewer)
        
        # 像素信息显示
        self.pixel_info_label = QLabel("像素信息: -")
        left_layout.addWidget(self.pixel_info_label)
        
        splitter.addWidget(left_widget)
        
        # ========== 右侧：图表显示区 ==========
        right_widget = QGroupBox("数据分析")
        right_layout = QVBoxLayout(right_widget)
        
        # Matplotlib图表
        self.figure = Figure(figsize=(6, 6))
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        
        right_layout.addWidget(self.toolbar)
        right_layout.addWidget(self.canvas)
        
        # 图表信息
        self.chart_info_label = QLabel("请绘制矩形或折线以查看数据")
        right_layout.addWidget(self.chart_info_label)
        
        splitter.addWidget(right_widget)
        
        # 设置分割器比例：图像窗口占4/5，图表窗口占1/5
        # 使用setSizes设置具体尺寸（像素）
        total_width = 1400
        splitter.setSizes([int(total_width * 0.6), int(total_width * 0.4)])
        
        main_layout.addWidget(splitter)
        
    def open_image(self):
        """打开图像文件"""
        # 读取上次打开的路径
        settings = get_settings()
        last_path = settings.value("last_file_path", "")
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开图像文件",
            last_path,
            "图像文件 (*.tif *.tiff *.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_file_path", os.path.dirname(file_path))
        
        try:
            self.image_file = file_path
            ext = os.path.splitext(file_path)[1].lower()
            self.is_tiff = ext in ['.tif', '.tiff']
            
            if self.is_tiff:
                # 使用image_io模块读取TIFF（支持降采样和金字塔）
                self.image_data, (self.original_width, self.original_height), self.downsample_factor = \
                    self._read_tiff_downsampled_local(file_path)
            else:
                # 使用image_io模块读取普通图像（支持降采样）
                self.image_data, (self.original_width, self.original_height), self.downsample_factor = \
                    self._read_image_downsampled_local(file_path)
            
            # 显示图像
            original_size = (self.original_width, self.original_height) if self.downsample_factor > 1 else None
            self.image_viewer.set_image_from_array(self.image_data, original_size=original_size)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 确保图像居中显示（使用延迟模式）
            self.image_viewer.fit_in_view(delayed=True)
            
            # 更新信息
            if self.downsample_factor > 1:
                info = f"{os.path.basename(file_path)} | 原始尺寸: {self.original_width}x{self.original_height}"
                info += f" | 显示: {self.image_data.shape[1]}x{self.image_data.shape[0]} (1/{self.downsample_factor})"
            else:
                info = f"{os.path.basename(file_path)} | 尺寸: {self.original_width}x{self.original_height}"
            
            # 波段数
            if self.image_data.ndim == 2:
                info += " | 单波段"
            elif self.image_data.ndim == 3:
                info += f" | {self.image_data.shape[2]}波段"
            
            if self.nodata_value is not None:
                info += f" | Nodata: {self.nodata_value}"
            
            self.image_info_label.setText(info)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 自动显示整个图像的直方图
            self.show_image_histogram()
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开图像失败: {str(e)}")
            traceback.print_exc()
    
    def _read_tiff_downsampled_local(self, file_path):
        """
        使用image_io模块读取TIFF图像，支持降采样和金字塔
        返回: (image_data, (original_width, original_height), downsample_factor)
        """
        data, nodata, original_size, factor = read_tiff_downsampled(file_path, self.MAX_DISPLAY_SIZE)
        if data is None:
            raise IOError(f"无法打开TIFF文件: {file_path}")
        self.nodata_value = nodata
        return data, original_size, factor
    
    def _read_image_downsampled_local(self, file_path):
        """
        使用image_io模块读取普通图像，支持降采样
        返回: (image_data, (original_width, original_height), downsample_factor)
        """
        data, original_size, factor = read_image_downsampled(file_path, self.MAX_DISPLAY_SIZE)
        if data is None:
            raise IOError(f"无法打开图像文件: {file_path}")
        self.nodata_value = None
        return data, original_size, factor
    
    def _read_original_region(self, x1, y1, x2, y2):
        """
        从原始图像文件读取指定区域的数据（用于精确分析）
        坐标是原始图像坐标
        """
        if self.image_file is None:
            return None
        
        # 确保坐标在有效范围内
        x1 = max(0, min(x1, self.original_width - 1))
        x2 = max(0, min(x2, self.original_width))
        y1 = max(0, min(y1, self.original_height - 1))
        y2 = max(0, min(y2, self.original_height))
        
        width = x2 - x1
        height = y2 - y1
        
        if width <= 0 or height <= 0:
            return None
        
        try:
            # GAMMA文件使用专用读取方法
            if self.is_gamma:
                return self._read_gamma_original_region(x1, y1, x2, y2)
            elif self.is_tiff:
                return read_tiff_region(self.image_file, x1, y1, x2, y2)
            else:
                return read_image_region(self.image_file, x1, y1, x2, y2)
        except Exception as e:
            traceback.print_exc()
            return None
    
    def _read_original_pixel(self, x, y):
        """
        从原始图像文件读取指定像素的值
        坐标是原始图像坐标
        """
        if self.image_file is None:
            return None
        
        # 确保坐标在有效范围内
        if x < 0 or x >= self.original_width or y < 0 or y >= self.original_height:
            return None
        
        try:
            # GAMMA文件使用专用读取方法
            if self.is_gamma:
                return self._read_gamma_original_pixel(x, y)
            elif self.is_tiff:
                return read_tiff_pixel(self.image_file, x, y)
            else:
                # 对于非TIFF图像，读取单个像素区域
                region = read_image_region(self.image_file, x, y, x+1, y+1)
                if region is not None:
                    if region.ndim == 2:
                        return region[0, 0]
                    else:
                        return region[0, 0, :]
                return None
        except Exception as e:
            traceback.print_exc()
            return None

    def on_colormap_changed(self, colormap_name):
        """颜色映射改变"""
        self.image_viewer.set_colormap(colormap_name)
    
    def on_mode_changed(self):
        """绘制模式改变"""
        mode_id = self.mode_group.checkedId()
        self.image_viewer.set_draw_mode(mode_id)
        
        # 切换到折线模式时，重置完成状态，允许绘制新折线
        if mode_id == 2:  # MODE_POLYLINE
            self.image_viewer.polyline_completed = False
    
    def clear_drawing(self):
        """清除绘制"""
        self.image_viewer.clear_rect()
        self.image_viewer.clear_polyline()
        
        # 恢复显示整个图像的直方图
        if self.image_data is not None:
            self.show_image_histogram()
        else:
            self.figure.clear()
            self.canvas.draw()
            self.chart_info_label.setText("请绘制矩形或折线以查看数据")
    
    def show_image_histogram(self):
        """显示整个图像的直方图"""
        if self.image_data is None:
            return
        
        try:
            # 准备数据列表
            data_list = []
            
            if self.image_data.ndim == 2:
                # 单波段图像
                flat_data = self.image_data.flatten()
                # 排除Nodata值
                if self.nodata_value is not None:
                    valid_data = flat_data[flat_data != self.nodata_value]
                else:
                    valid_data = flat_data
                data_list.append(valid_data)
                
            elif self.image_data.ndim == 3:
                # 多波段图像
                num_bands = self.image_data.shape[2]
                for band_idx in range(num_bands):
                    band_data = self.image_data[:, :, band_idx].flatten()
                    # 排除Nodata值
                    if self.nodata_value is not None:
                        valid_data = band_data[band_data != self.nodata_value]
                    else:
                        valid_data = band_data
                    data_list.append(valid_data)
            
            # 绘制直方图
            self.plot_histogram(data_list)
            self.chart_info_label.setText(f"整幅图像直方图: 共{sum(len(d) for d in data_list)}个像素")
            
        except Exception as e:
            print(f"显示直方图失败: {str(e)}")
            traceback.print_exc()
    
    def on_mouse_moved(self, x, y, value):
        """鼠标移动事件"""
        if value is not None:
            # 计算原始坐标
            if self.downsample_factor > 1:
                orig_x = int(x * self.downsample_factor)
                orig_y = int(y * self.downsample_factor)
                # 确保在有效范围内
                orig_x = min(orig_x, self.original_width - 1)
                orig_y = min(orig_y, self.original_height - 1)
                coord_str = f"原始坐标: ({orig_x}, {orig_y})"
            else:
                orig_x, orig_y = x, y
                coord_str = f"像素位置: ({x}, {y})"
            
            # 显示像素值
            if isinstance(value, (int, float, np.integer, np.floating)):
                self.pixel_info_label.setText(f"{coord_str} | 值: {value:.6g}")
            elif isinstance(value, np.ndarray):
                if value.ndim == 0:
                    self.pixel_info_label.setText(f"{coord_str} | 值: {value:.6g}")
                else:
                    value_str = ", ".join([f"{v:.6g}" for v in value])
                    self.pixel_info_label.setText(f"{coord_str} | 值: [{value_str}]")
        else:
            self.pixel_info_label.setText("像素信息: -")
    
    def on_rect_drawn(self, rect):
        """矩形绘制完成"""
        try:
            # 获取当前矩形的坐标（显示坐标）
            current_rect = self.image_viewer.current_rect
            if current_rect is None:
                return
            
            # 计算原始图像坐标
            if self.downsample_factor > 1:
                # 将显示坐标转换为原始坐标
                x1 = int(current_rect.x() * self.downsample_factor)
                y1 = int(current_rect.y() * self.downsample_factor)
                x2 = int((current_rect.x() + current_rect.width()) * self.downsample_factor)
                y2 = int((current_rect.y() + current_rect.height()) * self.downsample_factor)
                
                # 从原始文件读取区域数据
                region_data = self._read_original_region(x1, y1, x2, y2)
                if region_data is None:
                    # 如果无法读取原始数据，使用显示数据
                    region_data = self.image_viewer.get_rect_region()
            else:
                # 无降采样，直接使用显示数据
                region_data = self.image_viewer.get_rect_region()
            
            if region_data is None:
                return
            
            # 排除Nodata值
            if self.nodata_value is not None:
                mask = region_data != self.nodata_value
                if region_data.ndim == 3:
                    # 多波段，对每个波段应用mask
                    valid_data = []
                    for i in range(region_data.shape[2]):
                        band_data = region_data[:, :, i][mask[:, :, i]]
                        valid_data.append(band_data)
                else:
                    valid_data = [region_data[mask]]
            else:
                if region_data.ndim == 3:
                    valid_data = [region_data[:, :, i].flatten() for i in range(region_data.shape[2])]
                else:
                    valid_data = [region_data.flatten()]
            
            # 绘制直方图
            self.plot_histogram(valid_data)
            
        except Exception as e:
            QMessageBox.warning(self, "警告", f"绘制直方图失败: {str(e)}")
            traceback.print_exc()
    
    def on_polyline_drawn(self, points):
        """折线绘制完成"""
        try:
            # 获取折线路径上所有像素的显示坐标
            display_path_points, _ = self.image_viewer.get_polyline_path_values()
            if display_path_points is None or len(display_path_points) == 0:
                return
            
            # 根据降采样因子计算原始坐标和获取原始像素值
            if self.downsample_factor > 1:
                # 将显示坐标转换为原始坐标
                original_path_points = []
                path_values = []
                
                for (dx, dy) in display_path_points:
                    # 转换到原始坐标
                    ox = int(dx * self.downsample_factor)
                    oy = int(dy * self.downsample_factor)
                    
                    # 确保坐标在有效范围内
                    ox = min(ox, self.original_width - 1)
                    oy = min(oy, self.original_height - 1)
                    
                    original_path_points.append((ox, oy))
                    
                    # 从原始文件读取像素值
                    value = self._read_original_pixel(ox, oy)
                    path_values.append(value if value is not None else np.nan)
                
                # 存储原始坐标用于悬停标记（但显示时仍使用显示坐标）
                self.polyline_path_points = display_path_points  # 保持显示坐标用于图像标记
                self.polyline_original_points = original_path_points  # 原始坐标用于显示
            else:
                # 无降采样，直接使用
                _, path_values = self.image_viewer.get_polyline_path_values()
                self.polyline_path_points = display_path_points
                self.polyline_original_points = display_path_points
            
            # 排除Nodata值并绘制折线图
            self.plot_polyline(path_values, self.polyline_original_points)
            
        except Exception as e:
            QMessageBox.warning(self, "警告", f"绘制折线图失败: {str(e)}")
            traceback.print_exc()
    
    def on_polyline_hover(self, idx):
        """折线悬停事件（从图像传来）"""
        # 更新折线图中的悬停标记
        if hasattr(self, 'hover_line') and self.hover_line:
            try:
                self.hover_line.set_xdata([idx, idx])
                self.hover_line.set_visible(True)
                self.canvas.draw_idle()
            except:
                pass
    
    def on_chart_mouse_move(self, event):
        """图表鼠标移动事件（从图表传来）"""
        if event.inaxes is None:
            # 鼠标不在图表坐标轴内，隐藏悬停标记
            self.image_viewer._hide_hover_marker()
            if hasattr(self, 'hover_line') and self.hover_line:
                self.hover_line.set_visible(False)
                self.canvas.draw_idle()
            return
        
        # 获取鼠标位置的x坐标（索引）
        x_pos = int(round(event.xdata))
        
        # 检查索引是否有效
        if hasattr(self, 'polyline_path_points') and self.polyline_path_points:
            if 0 <= x_pos < len(self.polyline_path_points):
                try:
                    # 获取对应的图像坐标
                    px, py = self.polyline_path_points[x_pos]
                    
                    # 在图像上显示标记
                    self.image_viewer._show_hover_marker_at(px, py)
                    
                    # 更新悬停线
                    if hasattr(self, 'hover_line') and self.hover_line:
                        self.hover_line.set_xdata([x_pos, x_pos])
                        self.hover_line.set_visible(True)
                        self.canvas.draw_idle()
                except Exception as e:
                    # 忽略错误，避免弹窗
                    pass
            else:
                # 索引超出范围，隐藏悬停标记
                self.image_viewer._hide_hover_marker()
                if hasattr(self, 'hover_line') and self.hover_line:
                    self.hover_line.set_visible(False)
                    self.canvas.draw_idle()
        else:
            # 没有折线数据，隐藏悬停标记
            self.image_viewer._hide_hover_marker()
            if hasattr(self, 'hover_line') and self.hover_line:
                self.hover_line.set_visible(False)
                self.canvas.draw_idle()
    
    def plot_histogram(self, data_list):
        """绘制直方图（使用填充折线图）"""
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        
        # 绘制每个波段的直方图
        colors = ['red', 'green', 'blue', 'cyan', 'magenta', 'yellow']
        
        for i, data in enumerate(data_list):
            if len(data) == 0:
                continue
            
            # 过滤NaN和inf值
            finite_data = data[np.isfinite(data)]
            if len(finite_data) == 0:
                continue
            
            color = colors[i % len(colors)]
            label = f'波段{i+1}' if len(data_list) > 1 else '像素值'
            
            # 计算直方图（使用更多的bins使x间隔更细）
            counts, bins = np.histogram(finite_data, bins=200)
            bin_centers = (bins[:-1] + bins[1:]) / 2
            
            # 绘制填充折线图
            ax.fill_between(bin_centers, counts, alpha=0.5, color=color, label=label)
            ax.plot(bin_centers, counts, color=color, linewidth=2)
        
        ax.set_xlabel('像素值')
        ax.set_ylabel('频数')
        ax.set_title('矩形区域像素值直方图')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        self.figure.tight_layout()
        self.canvas.draw()
        
        self.chart_info_label.setText(f"直方图: 共{sum(len(d) for d in data_list)}个像素")
    
    def plot_polyline(self, values, points):
        """绘制折线图（改进版：平滑曲线+折点标记+填充）"""
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        
        indices = list(range(len(values)))
        
        # 获取原始折线折点位置
        if hasattr(self.image_viewer, 'polyline_points'):
            polyline_points = self.image_viewer.polyline_points
            # 找到折点在路径中的索引
            path_points, _ = self.image_viewer.get_polyline_path_values()
            corner_indices = []
            for corner_x, corner_y in polyline_points:
                for idx, (px, py) in enumerate(path_points):
                    if px == corner_x and py == corner_y:
                        corner_indices.append(idx)
                        break
        else:
            corner_indices = []
        
        # 检查数据类型
        if isinstance(values[0], (int, float, np.integer, np.floating)):
            # 单波段
            # 排除Nodata
            valid_indices = []
            valid_values = []
            for i, v in enumerate(values):
                if self.nodata_value is None or v != self.nodata_value:
                    valid_indices.append(i)
                    valid_values.append(v)
            
            # 绘制填充曲线
            ax.fill_between(valid_indices, valid_values, alpha=0.3, color='blue', label='像素值')
            ax.plot(valid_indices, valid_values, color='blue', linewidth=1)
            
            # 标记折点
            corner_x = [i for i in corner_indices if i in valid_indices]
            corner_y = [valid_values[valid_indices.index(i)] for i in corner_x]
            ax.scatter(corner_x, corner_y, color='red', s=40, zorder=5, marker='o', 
                      edgecolors='darkred', linewidths=1.5, label='折点')
            
        elif isinstance(values[0], np.ndarray):
            # 多波段
            num_bands = len(values[0])
            colors = ['red', 'green', 'blue', 'cyan', 'magenta', 'yellow']
            
            for band_idx in range(num_bands):
                valid_indices = []
                valid_values = []
                
                for i, v in enumerate(values):
                    val = v[band_idx]
                    if self.nodata_value is None or val != self.nodata_value:
                        valid_indices.append(i)
                        valid_values.append(val)
                
                color = colors[band_idx % len(colors)]
                
                # 绘制填充曲线
                ax.fill_between(valid_indices, valid_values, alpha=0.2, color=color)
                ax.plot(valid_indices, valid_values, color=color, linewidth=1, 
                       label=f'波段{band_idx+1}')
                
                # 标记折点
                corner_x = [i for i in corner_indices if i in valid_indices]
                corner_y = [valid_values[valid_indices.index(i)] for i in corner_x]
                ax.scatter(corner_x, corner_y, color=color, s=80, zorder=5, 
                          edgecolors='black', linewidths=1.5)
            
            # 如果是RGB（3波段），添加灰度值
            if num_bands == 3:
                gray_indices = []
                gray_values = []
                
                for i, v in enumerate(values):
                    if self.nodata_value is not None:
                        if any(v[j] == self.nodata_value for j in range(3)):
                            continue
                    
                    gray = 0.299 * v[0] + 0.587 * v[1] + 0.114 * v[2]
                    gray_indices.append(i)
                    gray_values.append(gray)
                
                # 绘制灰度值曲线
                ax.fill_between(gray_indices, gray_values, alpha=0.15, color='black')
                ax.plot(gray_indices, gray_values, color='black', linewidth=1, 
                       linestyle='--', alpha=0.7, label='灰度值')
                
                # 标记折点
                corner_x = [i for i in corner_indices if i in gray_indices]
                corner_y = [gray_values[gray_indices.index(i)] for i in corner_x]
                ax.scatter(corner_x, corner_y, color='black', s=100, zorder=5, 
                          marker='s', edgecolors='white', linewidths=1.5)
        
        # 添加悬停线
        y_min, y_max = ax.get_ylim()
        self.hover_line = ax.axvline(x=-1, color='yellow', linewidth=2, 
                                      linestyle='--', alpha=0.8, visible=False)
        
        # 自动调整y轴范围，让曲线更明显
        if y_max > y_min:
            margin = (y_max - y_min) * 0.1  # 添加10%的边距
            ax.set_ylim(y_min - margin, y_max + margin)
        
        ax.set_xlabel('折线点索引')
        ax.set_ylabel('像素值')
        ax.set_title('折线像素值变化')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        self.figure.tight_layout()
        self.canvas.draw()
        
        # 连接鼠标移动事件
        self.canvas.mpl_connect('motion_notify_event', self.on_chart_mouse_move)
        
        self.chart_info_label.setText(f"折线图: 共{len(points)}个点")    
    def open_h5_file(self):
        """打开h5文件（逐级选择）"""
        # 读取上次打开的路径
        settings = get_settings()
        last_path = settings.value("last_h5_path", "")
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开h5文件",
            last_path,
            "HDF5 Files (*.h5 *.hdf5);;所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_h5_path", os.path.dirname(file_path))
        
        try:
            # 使用image_io模块列出所有数据集
            datasets = list_h5_datasets(file_path, min_ndim=2)
            
            if not datasets:
                QMessageBox.warning(self, "警告", "h5文件中没有找到合适的图像数据集！")
                return
            
            # 逐级让用户选择数据集
            selected_dataset = self._show_dataset_selection_dialog(datasets)
            if not selected_dataset:
                return
            
            # 获取选中数据集的形状
            dataset_shape = None
            for name, _, shape in datasets:
                if name == selected_dataset:
                    dataset_shape = shape
                    break
            
            if dataset_shape is None:
                QMessageBox.critical(self, "错误", "无法获取数据集信息")
                return
            
            # 检查数据维度
            if len(dataset_shape) < 2:
                QMessageBox.warning(self, "警告", 
                    f"数据集 '{selected_dataset}' 不是图像数据（维度：{len(dataset_shape)}）")
                return
            elif len(dataset_shape) == 2:
                # 2D数据，直接加载
                frame_index = None
            elif len(dataset_shape) == 3:
                # 3D数据，判断是多波段还是多景
                first_dim = dataset_shape[0]
                # 如果第一维小于其他维度，可能是多波段（如RGB）
                if first_dim <= 4 and first_dim < dataset_shape[1] and first_dim < dataset_shape[2]:
                    # 可能是多波段，直接加载
                    frame_index = None
                else:
                    # 多景数据，让用户选择
                    frame_idx, ok = QInputDialog.getInt(
                        self, "选择帧", 
                        f"数据集包含 {first_dim} 景数据，请选择要显示的景（0-{first_dim-1}）:",
                        0, 0, first_dim-1)
                    if ok:
                        frame_index = frame_idx
                    else:
                        return
            else:
                QMessageBox.warning(self, "警告", 
                    f"数据集维度过高（{len(dataset_shape)}D），无法显示")
                return
            
            # 使用image_io模块读取数据集
            data, original_shape = read_h5_dataset(file_path, selected_dataset, frame_index)
            
            if data is None:
                QMessageBox.critical(self, "错误", "无法读取数据集")
                return
            
            # 验证是否为有效图像
            if data.ndim < 2:
                QMessageBox.warning(self, "警告", "读取的数据不是有效的图像")
                return
            
            # 设置图像数据和相关属性
            self.image_data = data
            self.image_file = file_path
            self.nodata_value = None
            self.is_gamma = False
            self.is_tiff = False
            self.original_width = original_shape[0] if original_shape else data.shape[1]
            self.original_height = original_shape[1] if original_shape else data.shape[0]
            self.downsample_factor = 1
            
            # 显示图像
            self.image_viewer.set_image_from_array(self.image_data)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 设置默认colormap为jet（h5数据）
            self.colormap_combo.setCurrentText('jet')
            
            # 确保图像居中显示（使用延迟模式）
            self.image_viewer.fit_in_view(delayed=True)
            
            # 更新信息
            shape = self.image_data.shape
            info_parts = [f"{os.path.basename(file_path)} [{selected_dataset}]"]
            
            if frame_index is not None:
                info_parts.append(f"帧: {frame_index}")
            
            if self.image_data.ndim == 2:
                info_parts.append(f"尺寸: {shape[1]}x{shape[0]} | 单波段")
            elif self.image_data.ndim == 3:
                info_parts.append(f"尺寸: {shape[1]}x{shape[0]} | {shape[2]}波段")
            else:
                info_parts.append(f"尺寸: {shape}")
            
            self.image_info_label.setText(" | ".join(info_parts))
            
            # 自动显示整个图像的直方图
            self.show_image_histogram()
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开h5文件失败: {str(e)}")
            traceback.print_exc()
    
    def _show_dataset_selection_dialog(self, datasets):
        """显示数据集选择对话框
        
        Args:
            datasets: 数据集列表，每项为(name, shape_str, shape)元组
            
        Returns:
            选中的数据集名称，或None
        """
        dialog = QDialog(self)
        dialog.setWindowTitle("选择数据集")
        dialog.resize(500, 400)
        
        layout = QVBoxLayout(dialog)
        
        label = QLabel("h5文件包含多个数据集，请双击要打开的数据集：")
        layout.addWidget(label)
        
        # 列表控件
        list_widget = QListWidget()
        for name, shape_str, _ in datasets:
            list_widget.addItem(f"{name}  {shape_str}")
        list_widget.setCurrentRow(0)
        layout.addWidget(list_widget)
        
        # 连接双击事件
        list_widget.doubleClicked.connect(dialog.accept)
        
        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)
        
        if dialog.exec() == QDialog.Accepted:
            selected_idx = list_widget.currentRow()
            if selected_idx >= 0:
                return datasets[selected_idx][0]
        
        return None
    
    def set_nodata_value(self):
        """设置Nodata值"""
        # 获取当前Nodata值
        if np.isnan(self.nodata_value) if isinstance(self.nodata_value, float) else False:
            current_text = "nan"
        else:
            current_text = str(self.nodata_value) if self.nodata_value is not None else ""
        
        # 弹出对话框让用户输入
        text, ok = QInputDialog.getText(self, "设置Nodata值", 
                                        "请输入Nodata值（nan表示NaN，留空表示取消设置）:",
                                        text=current_text)
        
        if ok:
            if text.strip() == "":
                # 取消Nodata设置
                self.nodata_value = None
                self.image_viewer.set_nodata_value(None)
                QMessageBox.information(self, "成功", "已取消Nodata值设置")
            else:
                try:
                    # 支持nan值
                    if text.lower().strip() == "nan":
                        nodata_value = np.nan
                    else:
                        nodata_value = float(text)
                    
                    self.nodata_value = nodata_value
                    self.image_viewer.set_nodata_value(nodata_value)
                    QMessageBox.information(self, "成功", f"已设置Nodata值为: {nodata_value}")
                except ValueError:
                    QMessageBox.warning(self, "错误", "请输入有效的数字或'nan'！")
            
            # 更新图像信息
            if self.image_file:
                shape = self.image_data.shape
                if self.image_data.ndim == 2:
                    info = f"{os.path.basename(self.image_file)} | 尺寸: {shape[1]}x{shape[0]} | 单波段"
                elif self.image_data.ndim == 3:
                    info = f"{os.path.basename(self.image_file)} | 尺寸: {shape[1]}x{shape[0]} | {shape[2]}波段"
                else:
                    info = f"{os.path.basename(self.image_file)} | 尺寸: {shape}"
                
                if self.nodata_value is not None:
                    info += f" | Nodata: {self.nodata_value}"
                
                self.image_info_label.setText(info)

    def open_gamma_file(self):
        """打开GAMMA二进制文件"""
        settings = get_settings()
        last_path = settings.value("last_gamma_path", "")
        last_format = settings.value("last_gamma_format", "float32")
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开GAMMA二进制文件",
            last_path,
            "所有文件 (*.*)"
        )
        
        if not file_path:
            return
        
        # 保存当前路径
        settings.setValue("last_gamma_path", os.path.dirname(file_path))
        
        try:
            # 先尝试以float32格式查找PAR文件
            auto_par_file, auto_dims = find_valid_par_for_binary(file_path, "float32")
            auto_format = "float32"
            
            # 如果没找到，再尝试cpxfloat32
            if auto_par_file is None:
                auto_par_file, auto_dims = find_valid_par_for_binary(file_path, "cpxfloat32")
                auto_format = "cpxfloat32"
            
            # 如果自动找到了PAR文件，直接使用
            if auto_par_file is not None and auto_dims is not None:
                gamma_format = auto_format
                width, height = auto_dims
                par_file_used = auto_par_file
                
                # 显示自动检测信息
                QMessageBox.information(self, "自动检测成功", 
                    f"自动检测到PAR文件: {os.path.basename(auto_par_file)}\n"
                    f"尺寸: {width} x {height}\n"
                    f"格式: {gamma_format}")
            else:
                # 没找到，弹出对话框让用户选择
                format_dialog = GammaSingleFileDialog(self, last_format, file_path)
                if format_dialog.exec() != QDialog.Accepted:
                    return
                
                gamma_format = format_dialog.get_selected_format()
                manual_width = format_dialog.get_manual_width()
                manual_height = format_dialog.get_manual_height()
                selected_par = format_dialog.get_selected_par()
                
                # 确定尺寸
                if manual_width is not None and manual_height is not None:
                    # 使用手动输入的尺寸
                    if not validate_dimensions(file_path, manual_width, manual_height, gamma_format):
                        QMessageBox.critical(self, "错误", 
                            f"输入的尺寸 {manual_width}x{manual_height} 与文件大小不匹配！")
                        return
                    width, height = manual_width, manual_height
                    par_file_used = None
                elif selected_par:
                    # 使用选择的PAR文件
                    from src.utils.gamma_file_process import get_dimensions_from_par
                    width, height = get_dimensions_from_par(selected_par)
                    if not validate_dimensions(file_path, width, height, gamma_format):
                        QMessageBox.critical(self, "错误", 
                            f"PAR文件中的尺寸 {width}x{height} 与二进制文件不匹配！")
                        return
                    par_file_used = selected_par
                else:
                    # 自动查找PAR文件
                    par_file_used, dims = find_valid_par_for_binary(file_path, gamma_format)
                    if par_file_used is None or dims is None:
                        QMessageBox.critical(self, "错误", 
                            "无法自动找到匹配的PAR文件！请手动指定尺寸或PAR文件。")
                        return
                    width, height = dims
            
            # 保存用户选择的格式
            settings.setValue("last_gamma_format", gamma_format)
            
            # 设置GAMMA相关属性
            self.is_gamma = True
            self.is_tiff = False
            self.gamma_format = gamma_format
            self.gamma_par_file = par_file_used
            self.original_width = width
            self.original_height = height
            self.image_file = file_path
            
            # 读取图像数据（使用降采样）
            data, downsample_factor = read_gamma_downsampled(
                file_path, width, height, gamma_format, self.MAX_DISPLAY_SIZE
            )
            
            self.downsample_factor = downsample_factor
            
            # 处理复数数据
            is_complex = gamma_format.startswith('cpx')
            if is_complex:
                # 默认显示相位
                self.image_data = complex_to_phase(data).astype(np.float32)
                data_type_str = "相位"
            else:
                self.image_data = data.astype(np.float32) if data.dtype != np.float32 else data
                data_type_str = "幅度"
            
            # GAMMA文件默认nodata为0
            self.nodata_value = 0
            
            # 显示图像
            original_size = (width, height) if downsample_factor > 1 else None
            self.image_viewer.set_image_from_array(self.image_data, original_size=original_size)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 设置默认colormap
            if is_complex:
                self.colormap_combo.setCurrentText('hsv')  # 相位使用hsv
            else:
                self.colormap_combo.setCurrentText('gray')
            
            # 确保图像居中显示（使用延迟模式）
            self.image_viewer.fit_in_view(delayed=True)
            
            # 更新信息
            info = f"{os.path.basename(file_path)} | GAMMA {gamma_format}"
            if downsample_factor > 1:
                info += f" | 原始: {width}x{height} | 显示: {self.image_data.shape[1]}x{self.image_data.shape[0]} (1/{downsample_factor})"
            else:
                info += f" | 尺寸: {width}x{height}"
            if is_complex:
                info += f" | 显示: {data_type_str}"
            if par_file_used:
                info += f" | PAR: {os.path.basename(par_file_used)}"
            
            self.image_info_label.setText(info)
            
            # 显示直方图
            self.show_image_histogram()
            
            # 启用转dB按钮
            self.to_db_btn.setEnabled(True)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"打开GAMMA文件失败: {str(e)}")
            traceback.print_exc()
    
    def _read_gamma_original_region(self, x1, y1, x2, y2):
        """
        从GAMMA二进制文件读取指定区域的数据（原始坐标）
        """
        if not self.is_gamma or self.image_file is None:
            return None
        
        try:
            data = read_gamma_region(
                self.image_file, x1, y1, x2, y2,
                self.original_width, self.original_height,
                self.gamma_format
            )
            
            # 处理复数数据
            if self.gamma_format.startswith('cpx'):
                data = complex_to_phase(data)
            
            data = data.astype(np.float32)
            
            # 如果已转换为dB，应用转换
            if self._converted_to_db:
                # 创建nodata mask
                nodata_mask = (data == 0)
                # 将<=0且不是nodata的值设为一个很小的正数
                min_positive = np.min(data[data > 0]) if np.any(data > 0) else 1e-10
                data[(data <= 0) & ~nodata_mask] = min_positive
                # 转换为dB，但保持nodata为0
                data = np.where(nodata_mask, 0, 10 * np.log10(data))
            
            return data
        except Exception as e:
            traceback.print_exc()
            return None
    
    def _read_gamma_original_pixel(self, x, y):
        """
        从GAMMA二进制文件读取指定像素的值（原始坐标）
        """
        if not self.is_gamma or self.image_file is None:
            return None
        
        try:
            value = read_gamma_pixel(
                self.image_file, x, y,
                self.original_width, self.original_height,
                self.gamma_format
            )
            
            # 处理复数数据
            if self.gamma_format.startswith('cpx'):
                value = np.angle(value)
            
            # 如果已转换为dB，应用转换
            if self._converted_to_db:
                if value == 0:
                    # 保持nodata为0
                    pass
                elif value > 0:
                    value = 10 * np.log10(value)
                else:
                    value = 10 * np.log10(1e-10)
            
            return value
        except Exception as e:
            traceback.print_exc()
            return None
    
    def convert_to_db(self):
        """将显示的图像转换为dB (10*log10)"""
        if self.image_data is None:
            return
        
        try:
            # 转换为dB
            # 避免log10(0)或负数，先做处理
            data_copy = self.image_data.copy()
            
            # 如果是GAMMA文件，特殊处理nodata值（0）
            if self.is_gamma:
                # 创建mask：标记nodata像素
                nodata_mask = (data_copy == 0)
                # 将<=0且不是nodata的值设为一个很小的正数
                min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
                data_copy[(data_copy <= 0) & ~nodata_mask] = min_positive
                # 转换为dB，但保持nodata为0
                db_data = np.where(nodata_mask, 0, 10 * np.log10(data_copy))
            else:
                # 非GAMMA数据，正常转换
                # 将<=0的值设为一个很小的正数
                min_positive = np.min(data_copy[data_copy > 0]) if np.any(data_copy > 0) else 1e-10
                data_copy[data_copy <= 0] = min_positive
                # 转换为dB
                db_data = 10 * np.log10(data_copy)
            
            # 更新图像数据
            self.image_data = db_data.astype(np.float32)
            
            # 如果是GAMMA文件，保持nodata为0
            if self.is_gamma:
                self.nodata_value = 0
            
            # 设置转换标志
            self._converted_to_db = True
            
            # 重新显示图像
            original_size = (self.original_width, self.original_height) if self.downsample_factor > 1 else None
            self.image_viewer.set_image_from_array(self.image_data, original_size=original_size)
            
            # 设置Nodata值到图像查看器
            self.image_viewer.set_nodata_value(self.nodata_value)
            
            # 更新信息标签（添加dB标记）
            current_info = self.image_info_label.text()
            if " | dB" not in current_info:
                self.image_info_label.setText(current_info + " | dB")
            
            # 重新显示直方图
            self.show_image_histogram()
            
            QMessageBox.information(self, "成功", "已转换为dB (10*log10)")
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"转换为dB失败: {str(e)}")
            traceback.print_exc()
