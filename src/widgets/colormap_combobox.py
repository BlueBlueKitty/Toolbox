'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-22
Description: Colormap选择器组件

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import numpy as np
from PySide6.QtWidgets import QComboBox, QStyledItemDelegate, QStyleOptionViewItem, QStyle
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QImage, QIcon, QPainter, QLinearGradient

try:
    import matplotlib.cm as cm
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False


class ColormapDelegate(QStyledItemDelegate):
    """Colormap下拉框的自定义代理，显示颜色渐变"""
    
    def paint(self, painter, option, index):
        """绘制项目"""
        # 获取colormap名称
        colormap_name = index.data(Qt.DisplayRole)
        
        # 绘制背景
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        
        # 绘制colormap渐变条
        gradient_rect = option.rect.adjusted(5, 5, -100, -5)
        
        if MATPLOTLIB_AVAILABLE and colormap_name != 'gray':
            try:
                # 使用matplotlib生成colormap
                cmap = cm.get_cmap(colormap_name)
                
                # 创建渐变图像
                width = gradient_rect.width()
                height = gradient_rect.height()
                
                if width > 0 and height > 0:
                    # 生成颜色数组（从左到右）
                    x = np.linspace(0, 1, width)
                    colors = cmap(x)
                    
                    # 转换为QImage（RGB格式）
                    rgb_array = (colors[:, :3] * 255).astype(np.uint8)
                    # 扩展到整个高度（每一行都是相同的颜色渐变）
                    rgb_array = np.repeat(rgb_array[np.newaxis, :, :], height, axis=0)
                    
                    # 确保数据连续性
                    rgb_array = np.ascontiguousarray(rgb_array)
                    qimage = QImage(rgb_array.data, width, height, width * 3, QImage.Format_RGB888)
                    pixmap = QPixmap.fromImage(qimage)
                    
                    painter.drawPixmap(gradient_rect, pixmap)
            except:
                # 如果失败，绘制灰度渐变
                gradient = QLinearGradient(gradient_rect.topLeft(), gradient_rect.topRight())
                gradient.setColorAt(0, Qt.black)
                gradient.setColorAt(1, Qt.white)
                painter.fillRect(gradient_rect, gradient)
        else:
            # 灰度colormap
            gradient = QLinearGradient(gradient_rect.topLeft(), gradient_rect.topRight())
            gradient.setColorAt(0, Qt.black)
            gradient.setColorAt(1, Qt.white)
            painter.fillRect(gradient_rect, gradient)
        
        # 绘制文本
        text_rect = option.rect.adjusted(gradient_rect.width() + 10, 0, -5, 0)
        painter.setPen(option.palette.text().color())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, colormap_name)
    
    def sizeHint(self, option, index):
        """返回项目大小提示"""
        return QSize(200, 30)


class ColormapComboBox(QComboBox):
    """Colormap选择器，显示颜色渐变"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 设置自定义代理
        self.setItemDelegate(ColormapDelegate())
        
        # 添加colormap选项
        self.available_colormaps = [
            'gray', 'viridis', 'plasma', 'inferno', 'magma', 'cividis',
            'jet', 'hot', 'cool', 'spring', 'summer', 'autumn', 'winter',
            'bone', 'copper', 'pink', 'hsv', 'twilight', 'terrain', 'ocean'
        ]
        
        self.addItems(self.available_colormaps)
        
        # 设置下拉框样式
        self.setIconSize(QSize(150, 20))
        self.setMinimumWidth(200)
