'''
Author: Yibo Yuan 2633669459@qq.com
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

# 导入自定义colormap注册模块（确保colormap在使用前已注册）
try:
    from ..utils.custom_colormaps import register_custom_colormaps
    register_custom_colormaps()
except:
    pass


class ColormapDelegate(QStyledItemDelegate):
    """Colormap下拉框的自定义代理，显示颜色渐变"""
    
    def paint(self, painter, option, index):
        """绘制项目"""
        # 获取colormap名称
        colormap_name = index.data(Qt.DisplayRole)
        
        # 检查是否是分隔符（以"━"开头）
        is_separator = colormap_name.startswith('━')
        
        # 绘制背景
        if option.state & QStyle.State_Selected and not is_separator:
            painter.fillRect(option.rect, option.palette.highlight())
        
        # 如果是分隔符，只绘制文本（居中，加粗）
        if is_separator:
            # 使用windowText颜色确保在深色模式下也能看清
            painter.setPen(option.palette.windowText().color())
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(option.rect, Qt.AlignCenter, colormap_name)
            return
        
        # 绘制colormap渐变条 + 名称
        gradient_rect = option.rect.adjusted(5, 5, -64, -5)
        
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

        text_rect = option.rect.adjusted(gradient_rect.right() + 6, 0, -8, 0)
        painter.setPen(option.palette.text().color())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, colormap_name)
        
    def sizeHint(self, option, index):
        """返回项目大小提示"""
        return QSize(150, 24)


class ColormapComboBox(QComboBox):
    """Colormap选择器，显示颜色渐变，按分类组织"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 设置自定义代理
        self.setItemDelegate(ColormapDelegate())
        
        # 存储所有可用的colormap（不包括分隔符）
        self.available_colormaps = []
        
        # ========== 常用 ==========
        self.addItem("━━━ 常用 ━━━")
        self.model().item(self.count() - 1).setEnabled(False)  # 禁用分隔符
        common_colormaps = ['gray', 'viridis', 'jet', 'seismic', 'GMT_polar', 'GMT_dem4', 'terrain']
        self.addItems(common_colormaps)
        self.available_colormaps.extend(common_colormaps)
        
        # 设置默认选择为gray（第一个常用colormap）
        self.setCurrentText('gray')
        
        # ========== GMT系列（地球科学常用）==========
        self.addItem("━━━ GMT系列 ━━━")
        self.model().item(self.count() - 1).setEnabled(False)
        # GMT colormap来自CPT文件
        gmt_colormaps = [
            'GMT_polar',       # 极地
            'GMT_seis'  ,      # 地震
            'GMT_cyclic',      # 周期性
            'GMT_ocean',       # 海洋
            'GMT_sealand',     # 海陆
            'GMT_dem1',        # DEM1地形
            'GMT_dem2',        # DEM2地形
            'GMT_dem3',        # DEM3地形
            'GMT_dem4',        # DEM4地形（晕渲推荐）
            'GMT_relief',      # 地形起伏
            'GMT_topo',        # 地形高程
            'GMT_globe',       # 全球地形
            'GMT_gebco',       # GEBCO海底地形
            'GMT_haxby',       # Haxby海底地形
            'GMT_elevation',   # 高程
            'gist_earth',      # 地球（matplotlib）
            'terrain',         # 地形（matplotlib）
            'ocean',           # 海洋（matplotlib）
        ]
        self.addItems(gmt_colormaps)
        self.available_colormaps.extend(gmt_colormaps)
        
        # ========== Matplotlib感知均匀 ==========
        self.addItem("━━━ Matplotlib感知均匀 ━━━")
        self.model().item(self.count() - 1).setEnabled(False)
        perceptual_colormaps = ['viridis', 'plasma', 'inferno', 'magma', 'cividis']
        self.addItems(perceptual_colormaps)
        self.available_colormaps.extend(perceptual_colormaps)
        
        # ========== Matplotlib序列 ==========
        self.addItem("━━━ Matplotlib序列 ━━━")
        self.model().item(self.count() - 1).setEnabled(False)
        sequential_colormaps = ['Blues', 'Greens', 'Greys', 'Oranges', 'Purples', 'Reds',
                                 'YlGn', 'YlGnBu', 'YlOrBr', 'YlOrRd',
                                 'BuGn', 'BuPu', 'GnBu', 'OrRd', 'PuBu', 'PuBuGn', 
                                 'PuRd', 'RdPu']
        self.addItems(sequential_colormaps)
        self.available_colormaps.extend(sequential_colormaps)
        
        # ========== Matplotlib发散 ==========
        self.addItem("━━━ Matplotlib发散 ━━━")
        self.model().item(self.count() - 1).setEnabled(False)
        diverging_colormaps = ['BrBG', 'PRGn', 'PiYG', 'PuOr', 'RdBu', 'RdGy', 
                                'RdYlBu', 'RdYlGn', 'Spectral', 'coolwarm', 'bwr', 'seismic']
        self.addItems(diverging_colormaps)
        self.available_colormaps.extend(diverging_colormaps)
        
        # ========== 其他传统 ==========
        self.addItem("━━━ 其他传统 ━━━")
        self.model().item(self.count() - 1).setEnabled(False)
        misc_colormaps = ['jet', 'hot', 'cool', 'spring', 'summer', 'autumn', 'winter',
                          'bone', 'copper', 'pink', 'rainbow', 'afmhot',
                          'gist_gray', 'gist_yarg', 'gist_heat', 'gist_stern',
                          'gnuplot', 'gnuplot2', 'brg', 'hsv', 'twilight']
        self.addItems(misc_colormaps)
        self.available_colormaps.extend(misc_colormaps)
        
        # 设置下拉框样式
        self.setIconSize(QSize(98, 16))
        self.setMinimumWidth(120)
        self._apply_icons()
        
    def get_current_colormap(self):
        """获取当前选中的colormap名称（排除分隔符）"""
        current = self.currentText()
        if current in self.available_colormaps:
            return current
        # 如果当前是分隔符，返回第一个有效colormap
        return self.available_colormaps[0] if self.available_colormaps else 'gray'

    def _apply_icons(self):
        for index in range(self.count()):
            name = self.itemText(index)
            if name not in self.available_colormaps:
                continue
            self.setItemIcon(index, QIcon(self._create_colormap_pixmap(name)))

    def _create_colormap_pixmap(self, colormap_name: str) -> QPixmap:
        width, height = 94, 14
        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        rect = pixmap.rect()
        if MATPLOTLIB_AVAILABLE and colormap_name != "gray":
            try:
                cmap = cm.get_cmap(colormap_name)
                x = np.linspace(0, 1, width)
                colors = cmap(x)
                rgb = (colors[:, :3] * 255).astype(np.uint8)
                rgb = np.repeat(rgb[np.newaxis, :, :], height, axis=0)
                rgb = np.ascontiguousarray(rgb)
                image = QImage(rgb.data, width, height, width * 3, QImage.Format_RGB888)
                painter.drawImage(rect, image)
            except Exception:
                gradient = QLinearGradient(rect.topLeft(), rect.topRight())
                gradient.setColorAt(0, Qt.black)
                gradient.setColorAt(1, Qt.white)
                painter.fillRect(rect, gradient)
        else:
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            gradient.setColorAt(0, Qt.black)
            gradient.setColorAt(1, Qt.white)
            painter.fillRect(rect, gradient)
        painter.end()
        return pixmap
