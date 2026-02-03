'''
Author: Yibo Yuan 2633669459@qq.com
Description: Colorbar组件，显示colormap和数值范围

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import numpy as np
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QImage, QPixmap, QPen, QColor, QFont

try:
    import matplotlib.cm as cm
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

# 导入自定义colormap注册模块（确保colormap在使用前已注册）
try:
    from ..utils.custom_colormaps import register_custom_colormaps
    register_custom_colormaps()
except:
    pass

class ColorbarWidget(QWidget):
    """
    Colorbar显示组件，用于显示colormap和对应的数值范围
    支持鼠标悬停时显示当前值的位置
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 设置固定宽度
        self.setFixedWidth(80)
        self.setMinimumHeight(200)
        
        # colormap设置
        self.colormap_name = 'gray'
        self.colormap_reversed = False
        
        # 数值范围
        self.vmin = 0.0
        self.vmax = 1.0
        
        # 当前鼠标悬停的值（None表示无悬停）
        self.current_value = None
        
        # 启用鼠标跟踪
        self.setMouseTracking(True)
        
    def set_colormap(self, colormap_name, reversed=False):
        """设置colormap"""
        self.colormap_name = colormap_name
        self.colormap_reversed = reversed
        self.update()
    
    def set_range(self, vmin, vmax):
        """设置数值范围"""
        self.vmin = vmin
        self.vmax = vmax
        self.update()
    
    def set_current_value(self, value):
        """设置当前值（用于鼠标悬停显示）"""
        if value is not None:
            # 确保值在范围内
            if self.vmin <= value <= self.vmax:
                self.current_value = value
            else:
                self.current_value = None
        else:
            self.current_value = None
        self.update()
    
    def paintEvent(self, event):
        """绘制colorbar"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 获取绘制区域
        width = self.width()
        height = self.height()
        
        # colorbar区域（左侧留30像素给文字，右侧留10像素）
        bar_left = 30
        bar_width = 20
        bar_top = 20
        bar_height = height - 40
        bar_right = bar_left + bar_width
        
        # 绘制colorbar
        if MATPLOTLIB_AVAILABLE and bar_height > 0:
            try:
                # 获取colormap
                cmap = cm.get_cmap(self.colormap_name)
                
                # 生成颜色数组（从上到下，对应从大到小）
                num_colors = bar_height
                if self.colormap_reversed:
                    y_values = np.linspace(0, 1, num_colors)
                else:
                    y_values = np.linspace(1, 0, num_colors)
                
                colors = cmap(y_values)
                
                # 转换为QImage（每行一个颜色）
                rgb_array = (colors[:, :3] * 255).astype(np.uint8)
                # 扩展到bar_width宽度
                rgb_array = np.repeat(rgb_array[:, np.newaxis, :], bar_width, axis=1)
                
                # 确保数据连续性
                rgb_array = np.ascontiguousarray(rgb_array)
                qimage = QImage(rgb_array.data, bar_width, bar_height, 
                               bar_width * 3, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qimage)
                
                painter.drawPixmap(bar_left, bar_top, pixmap)
                
            except Exception as e:
                print(f"绘制colorbar失败: {e}")
                # 绘制灰度渐变作为备用
                painter.fillRect(bar_left, bar_top, bar_width, bar_height, Qt.gray)
        else:
            # 无matplotlib或高度不足，绘制灰度渐变
            painter.fillRect(bar_left, bar_top, bar_width, bar_height, Qt.gray)
        
        # 绘制边框
        painter.setPen(QPen(Qt.black, 1))
        painter.drawRect(bar_left, bar_top, bar_width, bar_height)
        
        # 绘制刻度标签（显示7个刻度）
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(Qt.black)
        
        # 计算显示的刻度数量（最多7个）
        num_ticks = min(7, max(3, bar_height // 30))  # 根据colorbar高度调整，但最少7个
        
        for i in range(num_ticks):
            # 计算刻度值（从大到小）
            ratio = i / (num_ticks - 1)
            tick_value = self.vmax - ratio * (self.vmax - self.vmin)
            
            # 计算Y位置
            y_pos = bar_top + ratio * bar_height
            
            # 绘制刻度线
            painter.setPen(QPen(Qt.black, 1))
            painter.drawLine(bar_left - 2, int(y_pos), bar_left, int(y_pos))
            
            # 绘制刻度文本
            # 根据数值范围选择合适的精度
            value_range = abs(self.vmax - self.vmin)
            if value_range > 1000:
                text = f"{tick_value:.0f}"
            elif value_range > 10:
                text = f"{tick_value:.1f}"
            elif value_range > 0.1:
                text = f"{tick_value:.2f}"
            else:
                text = f"{tick_value:.3f}"
            
            painter.drawText(0, int(y_pos) - 10, bar_left - 4, 20,
                           Qt.AlignRight | Qt.AlignVCenter, text)
        
        # 绘制当前值指示线
        if self.current_value is not None:
            # 计算当前值在colorbar中的位置（从上到下对应从大到小）
            ratio = (self.vmax - self.current_value) / (self.vmax - self.vmin)
            ratio = np.clip(ratio, 0, 1)
            y_pos = bar_top + ratio * bar_height
            
            # 绘制横线
            painter.setPen(QPen(Qt.white, 2))
            painter.drawLine(bar_left, int(y_pos), bar_right, int(y_pos))
            painter.setPen(QPen(Qt.black, 1))
            painter.drawLine(bar_left, int(y_pos), bar_right, int(y_pos))
            
            # 绘制当前值标签（右侧）
            painter.setPen(Qt.red)
            font.setBold(True)
            painter.setFont(font)
            
            # 根据数值范围选择合适的精度
            value_range = abs(self.vmax - self.vmin)
            if value_range > 1000:
                value_text = f"{self.current_value:.0f}"
            elif value_range > 10:
                value_text = f"{self.current_value:.1f}"
            elif value_range > 0.1:
                value_text = f"{self.current_value:.2f}"
            else:
                value_text = f"{self.current_value:.3f}"
            
            painter.drawText(bar_right + 5, int(y_pos) - 10, 45, 20,
                           Qt.AlignLeft | Qt.AlignVCenter, value_text)
