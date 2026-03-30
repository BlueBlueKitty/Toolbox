'''
Author: Yibo Yuan 2633669459@qq.com
Description: 图像查看器组件，支持缩放、拖动、颜色映射等功能

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import numpy as np
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
                               QGraphicsLineItem, QGraphicsEllipseItem, QMenu,
                               QApplication)
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QCursor, QPen, QBrush, QTransform

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
    - 鼠标移动时显示像素值
    - 大图像降采样预览
    """
    
    # 自定义信号：当用户点击图像时发出，参数为(x, y)坐标
    pixel_clicked = Signal(int, int)
    
    # 鼠标移动时显示像素值，参数为(x, y, value)
    mouse_moved = Signal(int, int, object)
    
    # 视图变换信号（用于同步多个查看器）
    view_transformed = Signal(object)  # 发送transform对象
    
    # 鼠标样式变化信号（用于同步鼠标样式）
    cursor_changed = Signal(object)  # 发送cursor对象
    
    # 滚动条位置变化信号
    scroll_changed = Signal(int, int)  # 发送(h_value, v_value)
    
    def __init__(self, parent=None):
        """
        Args:
            parent: 父窗口
        """
        super().__init__(parent)
        
        # 创建场景
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        
        # 图像项
        self.image_item = None
        
        # 存储原始图像数据（可能是降采样后的）
        self.image_array = None  # numpy array (用于显示)
        self.is_normalized = False  # 是否已归一化
        
        # 原始图像尺寸（未降采样的真实尺寸）
        self.original_width = 0
        self.original_height = 0
        
        # 降采样比例
        self.downsample_factor = 1.0
        
        # 当前colormap
        self.current_colormap = 'gray'
        
        # 当前的colormap名称和反向设置
        self.current_colormap = 'gray'
        self.colormap_reversed = False
        
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
        
        # Nodata值
        self.nodata_value = None
        
        # 地理变换信息
        self.geotransform = None
        self.projection = None
        self.scene_world_rect = None
        self.image_world_rect = None
        
        # 渲染设置
        self.render_settings = None  # 来自RenderSettingsWidget的设置字典
        self.colormap_reversed = False  # colormap是否反向
        
        # 启用鼠标跟踪以捕捉移动事件
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        
        # 设置鼠标样式为箭头
        self.viewport().setCursor(Qt.ArrowCursor)

        # 持久选点标记
        self.selected_pixel = None
        self._selected_pixel_items = []
    
    def set_image_from_array(self, image_array, original_size=None):
        """
        从numpy数组设置图像
        
        Args:
            image_array: numpy数组，可以是:
                - 2D数组 (H, W): 灰度图像
                - 3D数组 (H, W, 3): RGB图像
                - 3D数组 (H, W, C): 多波段图像
            original_size: 原始图像尺寸 (width, height)，如果提供则表示image_array是降采样后的
        """
        self.image_array = image_array
        self.is_normalized = False
        
        # 设置原始尺寸和降采样比例
        if original_size is not None:
            self.original_width, self.original_height = original_size
            self.downsample_factor = self.original_width / image_array.shape[1]
        else:
            self.original_height, self.original_width = image_array.shape[:2]
            self.downsample_factor = 1.0
        
        self._update_display()

    def set_scene_mapping(self, scene_world_rect=None, image_world_rect=None):
        """
        设置图像在场景中的摆放范围。

        Args:
            scene_world_rect: 整个场景范围 (x, y, width, height)
            image_world_rect: 当前图像范围 (x, y, width, height)
        """
        self.scene_world_rect = scene_world_rect
        self.image_world_rect = image_world_rect
        if self.image_array is not None:
            self._update_display()

    def capture_view_state(self):
        """捕获当前视图状态。"""
        return {
            'transform': self.transform(),
            'h_value': self.horizontalScrollBar().value(),
            'v_value': self.verticalScrollBar().value(),
        }

    def restore_view_state(self, state):
        """恢复视图状态。"""
        if not state:
            return

        self.is_syncing = True
        self.setTransform(state['transform'])
        self.horizontalScrollBar().setValue(state['h_value'])
        self.verticalScrollBar().setValue(state['v_value'])
        self.is_syncing = False

    def _get_current_scene_rect(self):
        """获取当前应使用的场景范围。"""
        if self.scene_world_rect is not None:
            x, y, width, height = self.scene_world_rect
            return QRectF(x, y, width, height)
        if self.image_item is not None:
            return self.image_item.sceneBoundingRect()
        return QRectF()

    def _map_scene_to_item(self, scene_pos):
        """将场景坐标映射为图像项坐标。"""
        if self.image_item is None:
            return None

        item_pos = self.image_item.mapFromScene(scene_pos)
        if self.image_item.boundingRect().contains(item_pos):
            return item_pos
        return None
        
    def _normalize_array(self, arr):
        """将数组归一化到0-255范围（注释：此函数主要用于非渲染设置模式）"""
        if arr.dtype == np.uint8:
            return arr
        
        # 对于浮点数组，保持float32精度直到最后
        arr_min = np.nanmin(arr)
        arr_max = np.nanmax(arr)
        
        if arr_max - arr_min == 0:
            return np.zeros_like(arr, dtype=np.uint8)
        
        # 最终转uint8
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
        
        # 如果有渲染设置，使用渲染设置处理
        if self.render_settings is not None:
            from .render_settings_widget import apply_render_settings
            display_mode = self.render_settings.get('display_mode', '灰度')
            
            # 应用渲染设置（传递geotransform、projection和降采样因子用于hillshade计算）
            processed = apply_render_settings(arr, self.render_settings, self.nodata_value,
                                             self.geotransform, self.projection, self.downsample_factor)
            
            # 创建alpha通道
            if arr.ndim == 2:
                alpha_channel = self._create_alpha_channel(arr)
            elif arr.ndim == 3:
                # 使用第一个波段创建alpha
                if display_mode == 'RGB' and arr.shape[2] >= 3:
                    r, g, b = self.render_settings.get('rgb_bands', (1, 2, 3))
                    # RGB模式下，任何一个通道是nodata都透明
                    alpha_channel = np.full(arr.shape[:2], 255, dtype=np.uint8)
                    for band_idx in [r-1, g-1, b-1]:
                        if band_idx < arr.shape[2]:
                            band_data = arr[:, :, band_idx]
                            invalid = ~np.isfinite(band_data)
                            if self.nodata_value is not None:
                                invalid = invalid | (band_data == self.nodata_value)
                            alpha_channel[invalid] = 0
                else:
                    band = self.render_settings.get('gray_band', 1)
                    band = min(band, arr.shape[2]) - 1
                    alpha_channel = self._create_alpha_channel(arr[:, :, band])
            else:
                alpha_channel = None
            
            # 应用colormap（如果是灰度模式）
            if processed.ndim == 2:
                display_arr, _ = self._apply_colormap_to_normalized(processed)
            else:
                # RGB模式，processed是float32 (0.0-1.0)，需要转成uint8
                display_arr = (processed * 255).astype(np.uint8)
                alpha_channel = None  # RGB模式不使用colormap
        else:
            # 原有逻辑
            # 处理不同维度的数组
            if arr.ndim == 2:
                # 2D灰度图像
                display_arr, alpha_channel = self._apply_colormap(arr)
            elif arr.ndim == 3:
                if arr.shape[2] == 3:
                    # RGB图像
                    display_arr = self._normalize_array(arr)
                    alpha_channel = None
                elif arr.shape[2] == 1:
                    # 单波段
                    display_arr, alpha_channel = self._apply_colormap(arr[:, :, 0])
                else:
                    # 多波段图像，显示第一个波段
                    display_arr, alpha_channel = self._apply_colormap(arr[:, :, 0])
            else:
                raise ValueError(f"不支持的数组维度: {arr.ndim}")
        
        # 转换为QImage，禁用平滑插值以显示栅格边界
        height, width = display_arr.shape[:2]
        
        if alpha_channel is not None:
            # 有alpha通道，创建RGBA图像
            if display_arr.ndim == 2:
                # 灰度图，转为RGB
                rgb_arr = np.stack([display_arr, display_arr, display_arr], axis=-1)
            else:
                rgb_arr = display_arr
            
            # 添加alpha通道
            rgba_arr = np.dstack([rgb_arr, alpha_channel])
            bytes_per_line = 4 * width
            qimage = QImage(rgba_arr.data, width, height, bytes_per_line, QImage.Format_RGBA8888)
        elif display_arr.ndim == 2:
            # 灰度图
            qimage = QImage(display_arr.data, width, height, width, QImage.Format_Grayscale8)
        else:
            # RGB图
            bytes_per_line = 3 * width
            qimage = QImage(display_arr.data, width, height, bytes_per_line, QImage.Format_RGB888)
        
        # 创建QPixmap并添加到场景
        pixmap = QPixmap.fromImage(qimage)
        self.image_item = QGraphicsPixmapItem(pixmap)
        # 根据设置决定变换模式
        smooth_display = self.render_settings.get('smooth_display', False) if self.render_settings else False
        if smooth_display:
            self.image_item.setTransformationMode(Qt.SmoothTransformation)
        else:
            self.image_item.setTransformationMode(Qt.FastTransformation)
        self.scene.addItem(self.image_item)

        if self.image_world_rect is not None:
            world_x, world_y, world_w, world_h = self.image_world_rect
            scale_x = world_w / max(width, 1)
            scale_y = world_h / max(height, 1)
            self.image_item.setPos(world_x, world_y)
            self.image_item.setTransform(QTransform.fromScale(scale_x, scale_y))

        scene_rect = self._get_current_scene_rect()
        if not scene_rect.isNull():
            self.scene.setSceneRect(scene_rect)
        self._rebuild_selected_pixel_marker()
        
        # 注意：不在这里调用fit_in_view，让调用者在合适的时机调用
    
    def _create_alpha_channel(self, arr):
        """创建alpha通道"""
        alpha_channel = np.full(arr.shape, 255, dtype=np.uint8)
        invalid_mask = ~np.isfinite(arr)
        if self.nodata_value is not None:
            invalid_mask = invalid_mask | (arr == self.nodata_value)
        alpha_channel[invalid_mask] = 0
        return alpha_channel
        
    def _apply_colormap(self, arr):
        """应用colormap到2D数组，返回RGB数组和alpha通道"""
        # 创建alpha通道，默认全不透明
        alpha_channel = np.full(arr.shape, 255, dtype=np.uint8)
        
        # 基础无效掩码：NaN和Inf
        invalid_mask = ~np.isfinite(arr)
        
        # 如果有Nodata值，也加入无效掩码
        if self.nodata_value is not None:
            nodata_mask = (arr == self.nodata_value)
            invalid_mask = invalid_mask | nodata_mask
            
        alpha_channel[invalid_mask] = 0  # 这些像素设为完全透明
        
        # 对非Nodata像素进行归一化（保持float32精度）
        valid_mask = alpha_channel > 0
        normalized = np.zeros_like(arr, dtype=np.float32)
        
        if np.any(valid_mask):
            valid_data = arr[valid_mask]
            
            if len(valid_data) > 0:
                arr_min = np.min(valid_data)
                arr_max = np.max(valid_data)
                
                if arr_max > arr_min:
                    # 归一化到0.0-1.0，保持float32精度
                    normalized[valid_mask] = ((valid_data - arr_min) / (arr_max - arr_min)).astype(np.float32)
        
        # 应用colormap反向
        if self.colormap_reversed:
            normalized[valid_mask] = 1.0 - normalized[valid_mask]
        
        if self.current_colormap == 'gray' or not MATPLOTLIB_AVAILABLE:
            # 灰度模式，这里才转成uint8
            gray_uint8 = (normalized * 255).astype(np.uint8)
            return gray_uint8, alpha_channel
        
        # 使用matplotlib的colormap（直接使用float32的normalized，不需要再除以255）
        cmap = cm.get_cmap(self.current_colormap)
        # 应用colormap（normalized已经是0-1范围）
        rgba = cmap(normalized)
        # 转换为0-255的RGB
        rgb = (rgba[:, :, :3] * 255).astype(np.uint8)
        
        return rgb, alpha_channel
    
    def _apply_colormap_to_normalized(self, normalized_arr):
        """对已归一化的数组应用colormap（用于渲染设置模式）
        normalized_arr: float32 (0.0-1.0)范围的数组
        """
        alpha_channel = np.full(normalized_arr.shape, 255, dtype=np.uint8)
        alpha_channel[normalized_arr == 0] = 0  # 假设0为无效值
        
        if self.current_colormap == 'gray' or not MATPLOTLIB_AVAILABLE:
            # 灰度模式，这里转成uint8
            gray_uint8 = (normalized_arr * 255).astype(np.uint8)
            return gray_uint8, alpha_channel
        
        # 使用matplotlib的colormap（normalized_arr已经是0.0-1.0范围）
        cmap = cm.get_cmap(self.current_colormap)
        # 应用colormap
        rgba = cmap(normalized_arr)
        # 转换为0-255的RGB
        rgb = (rgba[:, :, :3] * 255).astype(np.uint8)
        
        return rgb, alpha_channel
    
    def set_colormap(self, colormap_name):
        """设置colormap"""
        self.current_colormap = colormap_name
        self._update_display()
    
    def set_colormap_reversed(self, reversed):
        """设置colormap是否反向"""
        self.colormap_reversed = reversed
        if self.image_array is not None:
            self._update_display()
    
    def set_render_settings(self, settings):
        """设置渲染设置
        
        Args:
            settings: 渲染设置字典（从RenderSettingsWidget.get_all_settings()获取）
        """
        self.render_settings = settings
        if settings:
            self.colormap_reversed = settings.get('colormap_reversed', False)
        if self.image_array is not None:
            self._update_display()
    
    def set_nodata_value(self, nodata_value):
        """设置Nodata值
        
        Args:
            nodata_value: Nodata值，设为None表示取消Nodata设置
        """
        self.nodata_value = nodata_value
        if self.image_array is not None:
            self._update_display()
    
    def set_geotransform(self, geotransform, projection=None):
        """设置地理变换信息
        
        Args:
            geotransform: GDAL地理变换参数
            projection: 投影信息（WKT格式）
        """
        self.geotransform = geotransform
        self.projection = projection

    def set_selected_pixel(self, x, y):
        """设置选中的原始像素坐标并显示标记。"""
        self.selected_pixel = (int(x), int(y))
        self._rebuild_selected_pixel_marker()

    def clear_selected_pixel(self):
        """清除选点标记。"""
        self.selected_pixel = None
        self._clear_selected_pixel_marker()

    def _clear_selected_pixel_marker(self):
        """移除当前选点标记图元。"""
        for item in self._selected_pixel_items:
            try:
                if item.scene() is not None:
                    self.scene.removeItem(item)
            except RuntimeError:
                pass
        self._selected_pixel_items = []

    def _rebuild_selected_pixel_marker(self):
        """根据当前图像重建选点标记。"""
        self._clear_selected_pixel_marker()

        if self.selected_pixel is None or self.image_item is None or self.image_array is None:
            return

        x, y = self.selected_pixel
        if not (0 <= x < self.original_width and 0 <= y < self.original_height):
            return

        display_x = (x + 0.5) / self.downsample_factor
        display_y = (y + 0.5) / self.downsample_factor
        if not (0 <= display_x <= self.image_array.shape[1] and 0 <= display_y <= self.image_array.shape[0]):
            return

        marker_half = max(4.0, min(8.0, 6.0 / max(self.downsample_factor, 1e-6)))
        circle_radius = max(2.5, marker_half * 0.6)

        outer_pen = QPen(QColor(255, 255, 255), 2)
        outer_pen.setCosmetic(True)
        inner_pen = QPen(QColor(220, 20, 60), 1)
        inner_pen.setCosmetic(True)

        line_segments = [
            (display_x - marker_half, display_y, display_x + marker_half, display_y),
            (display_x, display_y - marker_half, display_x, display_y + marker_half),
        ]

        for x1, y1, x2, y2 in line_segments:
            outer_line = QGraphicsLineItem(x1, y1, x2, y2)
            outer_line.setPen(outer_pen)
            outer_line.setParentItem(self.image_item)
            self._selected_pixel_items.append(outer_line)

            inner_line = QGraphicsLineItem(x1, y1, x2, y2)
            inner_line.setPen(inner_pen)
            inner_line.setParentItem(self.image_item)
            self._selected_pixel_items.append(inner_line)

        circle = QGraphicsEllipseItem(
            display_x - circle_radius,
            display_y - circle_radius,
            circle_radius * 2,
            circle_radius * 2,
        )
        circle.setPen(inner_pen)
        circle.setBrush(QBrush(Qt.NoBrush))
        circle.setParentItem(self.image_item)
        self._selected_pixel_items.append(circle)
    
    def fit_in_view(self, delayed=False):
        """适应视图大小
        
        Args:
            delayed: 是否延迟执行，用于确保场景完全更新后再居中
        """
        if delayed:
            # 延迟执行，确保场景布局完成
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100, lambda: self.fit_in_view(delayed=False))
            return
        
        if self.image_item:
            scene_rect = self._get_current_scene_rect()
            self.scene.setSceneRect(scene_rect)
            if self.scene_world_rect is not None:
                self.fitInView(scene_rect, Qt.KeepAspectRatio)
            else:
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
            item_pos = self._map_scene_to_item(scene_pos)
            if item_pos is not None:
                # 转换为原始图像坐标（考虑降采样）
                x = int(item_pos.x() * self.downsample_factor)
                y = int(item_pos.y() * self.downsample_factor)
                
                # 检查坐标是否在原始图像范围内
                if (0 <= x < self.original_width and 
                    0 <= y < self.original_height):
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
            # 普通鼠标移动，更新像素值显示
            if self.image_item and self.image_array is not None:
                scene_pos = self.mapToScene(event.pos())
                item_pos = self._map_scene_to_item(scene_pos)
                if item_pos is not None:
                    # 转换为原始图像坐标（考虑降采样）
                    x = int(item_pos.x() * self.downsample_factor)
                    y = int(item_pos.y() * self.downsample_factor)
                    
                    if (0 <= x < self.original_width and 
                        0 <= y < self.original_height):
                        # 获取显示数组中的像素值（降采样后的位置）
                        display_x = int(item_pos.x())
                        display_y = int(item_pos.y())
                        if (0 <= display_x < self.image_array.shape[1] and
                            0 <= display_y < self.image_array.shape[0]):
                            value = self.image_array[display_y, display_x]
                            self.mouse_moved.emit(x, y, value)
            
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
        """获取原始图像尺寸"""
        if self.original_width > 0 and self.original_height > 0:
            return (self.original_height, self.original_width)  # (height, width)
        if self.image_array is not None:
            return self.image_array.shape[:2]  # (height, width)
        return None
    
    def get_pixel_value(self, x, y):
        """获取指定位置的像素值（从显示数组中，考虑降采样）"""
        if self.image_array is not None:
            # 转换为显示数组坐标
            display_x = int(x / self.downsample_factor)
            display_y = int(y / self.downsample_factor)
            if (0 <= display_x < self.image_array.shape[1] and 
                0 <= display_y < self.image_array.shape[0]):
                return self.image_array[display_y, display_x]
        return None
    
    def set_nodata_value(self, nodata_value):
        """设置Nodata值"""
        self.nodata_value = nodata_value
        if self.image_array is not None:
            self._update_display()


class ImageViewerSynchronizer:
    """
    图像查看器同步器，用于同步多个ImageViewer的视图变换和滚动位置
    """
    def __init__(self, viewers):
        """
        Args:
            viewers: ImageViewer列表
        """
        self.viewers = viewers
        self._connect_signals()
    
    def _connect_signals(self):
        """连接所有查看器的信号"""
        for viewer in self.viewers:
            viewer.view_transformed.connect(self._on_view_transformed)
            viewer.cursor_changed.connect(self._on_cursor_changed)
            viewer.scroll_changed.connect(self._on_scroll_changed)
    
    def _on_view_transformed(self, transform):
        """处理视图变换"""
        sender = self.sender()
        for viewer in self.viewers:
            if viewer != sender:
                viewer.sync_transform(transform)
    
    def _on_cursor_changed(self, cursor):
        """处理鼠标样式变化"""
        sender = self.sender()
        for viewer in self.viewers:
            if viewer != sender:
                viewer.sync_cursor(cursor)
    
    def _on_scroll_changed(self, h_value, v_value):
        """处理滚动条位置变化"""
        sender = self.sender()
        for viewer in self.viewers:
            if viewer != sender:
                viewer.sync_scroll(h_value, v_value)
