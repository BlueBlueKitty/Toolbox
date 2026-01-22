'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-22
Description: 图像查看器组件，支持缩放、拖动、颜色映射等功能

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import numpy as np
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, 
                               QMenu, QApplication)
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QCursor

try:
    import matplotlib.cm as cm
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("警告: matplotlib未安装，部分colormap功能将不可用")


class ImageViewer(QGraphicsView):
    """
    图像查看器组件，支持：
    - 鼠标滚轮缩放
    - 中键拖动平移
    - 左键点击像素
    - 选择colormap
    """
    
    # 自定义信号：当用户点击图像时发出，参数为(x, y)坐标
    pixel_clicked = Signal(int, int)
    
    # 视图变换信号（用于同步多个查看器）
    view_transformed = Signal(object)  # 发送transform对象
    
    # 鼠标样式变化信号（用于同步鼠标样式）
    cursor_changed = Signal(object)  # 发送cursor对象
    
    # 滚动条位置变化信号
    scroll_changed = Signal(int, int)  # 发送(h_value, v_value)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 创建场景
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        # 图像项
        self.image_item = None
        
        # 存储原始图像数据
        self.image_array = None  # numpy array
        self.is_normalized = False  # 是否已归一化
        
        # 当前colormap
        self.current_colormap = 'gray'
        
        # 可用的colormap列表
        self.available_colormaps = [
            'gray', 'viridis', 'plasma', 'inferno', 'magma', 'cividis',
            'jet', 'hot', 'cool', 'spring', 'summer', 'autumn', 'winter',
            'bone', 'copper', 'pink', 'hsv', 'twilight', 'terrain', 'ocean'
        ]
        
        # 设置场景属性
        self.setRenderHint(QPainter.Antialiasing, False)  # 禁用抗锯齿
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.NoDrag)  # 禁用默认拖动
        
        # 缩放参数
        self.zoom_factor = 1.15
        self.min_zoom = 0.1
        self.max_zoom = 1000.0  # 无限放大
        self.current_zoom = 1.0
        
        # 拖动状态
        self.is_panning = False
        self.pan_start_pos = None
        
        # 同步标志
        self.is_syncing = False
        
        # 设置鼠标样式为箭头
        self.viewport().setCursor(Qt.ArrowCursor)
        
    def set_image_from_array(self, image_array):
        """
        从numpy数组设置图像
        
        Args:
            image_array: numpy数组，可以是:
                - 2D数组 (H, W): 灰度图像
                - 3D数组 (H, W, 3): RGB图像
                - 3D数组 (H, W, C): 多波段图像
        """
        self.image_array = image_array
        self.is_normalized = False
        self._update_display()
        
    def _normalize_array(self, arr):
        """将数组归一化到0-255范围"""
        if arr.dtype == np.uint8:
            return arr
        
        # 对于浮点数组，归一化到0-255
        arr_min = np.nanmin(arr)
        arr_max = np.nanmax(arr)
        
        if arr_max - arr_min == 0:
            return np.zeros_like(arr, dtype=np.uint8)
        
        normalized = ((arr - arr_min) / (arr_max - arr_min) * 255).astype(np.uint8)
        return normalized
    
    def _update_display(self):
        """更新图像显示"""
        if self.image_array is None:
            return
        
        # 清空场景
        self.scene.clear()
        self.image_item = None
        
        arr = self.image_array.copy()
        
        # 处理不同维度的数组
        if arr.ndim == 2:
            # 2D灰度图像
            display_arr = self._apply_colormap(arr)
        elif arr.ndim == 3:
            if arr.shape[2] == 3:
                # RGB图像
                display_arr = self._normalize_array(arr)
            elif arr.shape[2] == 1:
                # 单波段
                display_arr = self._apply_colormap(arr[:, :, 0])
            else:
                # 多波段图像，显示第一个波段
                display_arr = self._apply_colormap(arr[:, :, 0])
        else:
            raise ValueError(f"不支持的数组维度: {arr.ndim}")
        
        # 转换为QImage，禁用平滑插值以显示栅格边界
        height, width = display_arr.shape[:2]
        
        if display_arr.ndim == 2:
            # 灰度图
            qimage = QImage(display_arr.data, width, height, width, QImage.Format_Grayscale8)
        else:
            # RGB图
            bytes_per_line = 3 * width
            qimage = QImage(display_arr.data, width, height, bytes_per_line, QImage.Format_RGB888)
        
        # 创建QPixmap并添加到场景
        pixmap = QPixmap.fromImage(qimage)
        self.image_item = QGraphicsPixmapItem(pixmap)
        # 禁用平滑变换，显示栅格边界
        self.image_item.setTransformationMode(Qt.FastTransformation)
        self.scene.addItem(self.image_item)
        
        # 适应视图
        self.fit_in_view()
        
    def _apply_colormap(self, arr):
        """应用colormap到2D数组"""
        # 归一化
        normalized = self._normalize_array(arr)
        
        if self.current_colormap == 'gray' or not MATPLOTLIB_AVAILABLE:
            return normalized
        
        # 使用matplotlib的colormap
        cmap = cm.get_cmap(self.current_colormap)
        # 归一化到0-1
        norm_arr = normalized.astype(float) / 255.0
        # 应用colormap
        rgba = cmap(norm_arr)
        # 转换为0-255的RGB
        rgb = (rgba[:, :, :3] * 255).astype(np.uint8)
        return rgb
    
    def set_colormap(self, colormap_name):
        """设置colormap"""
        if colormap_name in self.available_colormaps:
            self.current_colormap = colormap_name
            self._update_display()
    
    def fit_in_view(self):
        """适应视图大小"""
        if self.image_item:
            self.fitInView(self.image_item, Qt.KeepAspectRatio)
            self.current_zoom = 1.0
    
    def zoom_in(self):
        """放大"""
        if self.current_zoom * self.zoom_factor <= self.max_zoom:
            self.scale(self.zoom_factor, self.zoom_factor)
            self.current_zoom *= self.zoom_factor
            if not self.is_syncing:
                self.view_transformed.emit(self.transform())
    
    def zoom_out(self):
        """缩小"""
        if self.current_zoom / self.zoom_factor >= self.min_zoom:
            self.scale(1 / self.zoom_factor, 1 / self.zoom_factor)
            self.current_zoom /= self.zoom_factor
            if not self.is_syncing:
                self.view_transformed.emit(self.transform())
    
    def wheelEvent(self, event):
        """鼠标滚轮事件：缩放"""
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()
    
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.LeftButton and self.image_item:
            # 左键：点击像素
            # 获取场景坐标
            scene_pos = self.mapToScene(event.pos())
            
            # 转换为图像坐标
            if self.image_item.contains(scene_pos):
                item_pos = self.image_item.mapFromScene(scene_pos)
                x = int(item_pos.x())
                y = int(item_pos.y())
                
                # 检查坐标是否在图像范围内
                if (0 <= x < self.image_array.shape[1] and 
                    0 <= y < self.image_array.shape[0]):
                    # 发送信号
                    self.pixel_clicked.emit(x, y)
        
        elif event.button() == Qt.MiddleButton:
            # 中键：开始拖动
            self.is_panning = True
            self.pan_start_pos = event.pos()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            if not self.is_syncing:
                self.cursor_changed.emit(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.is_panning and self.pan_start_pos is not None:
            # 中键拖动
            delta = event.pos() - self.pan_start_pos
            self.pan_start_pos = event.pos()
            
            # 移动视图
            h_bar = self.horizontalScrollBar()
            v_bar = self.verticalScrollBar()
            h_bar.setValue(h_bar.value() - delta.x())
            v_bar.setValue(v_bar.value() - delta.y())
            
            if not self.is_syncing:
                # 发送滚动条位置信号
                self.scroll_changed.emit(h_bar.value(), v_bar.value())
            
            event.accept()
        else:
            super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.MiddleButton:
            # 结束拖动
            self.is_panning = False
            self.pan_start_pos = None
            self.viewport().setCursor(Qt.ArrowCursor)
            if not self.is_syncing:
                self.cursor_changed.emit(Qt.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)
    
    def sync_transform(self, transform):
        """同步视图变换（从另一个查看器）"""
        self.is_syncing = True
        self.setTransform(transform)
        self.is_syncing = False
    
    def sync_cursor(self, cursor):
        """同步鼠标样式（从另一个查看器）"""
        self.is_syncing = True
        self.viewport().setCursor(cursor)
        self.is_syncing = False
    
    def sync_scroll(self, h_value, v_value):
        """同步滚动条位置（从另一个查看器）"""
        self.is_syncing = True
        self.horizontalScrollBar().setValue(h_value)
        self.verticalScrollBar().setValue(v_value)
        self.is_syncing = False
    
    def get_image_size(self):
        """获取图像尺寸"""
        if self.image_array is not None:
            return self.image_array.shape[:2]  # (height, width)
        return None
    
    def get_pixel_value(self, x, y):
        """获取指定位置的像素值"""
        if self.image_array is not None:
            if (0 <= x < self.image_array.shape[1] and 
                0 <= y < self.image_array.shape[0]):
                return self.image_array[y, x]
        return None
