'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-22
Description: 交互式图像查看器组件，支持绘制矩形和折线

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import numpy as np
from PySide6.QtWidgets import QGraphicsRectItem, QGraphicsPathItem, QGraphicsEllipseItem
from PySide6.QtCore import Qt, Signal, QPointF, QRectF
from PySide6.QtGui import QPen, QColor, QPainterPath, QBrush

from .image_viewer import ImageViewer


class InteractiveImageViewer(ImageViewer):
    """
    交互式图像查看器，在ImageViewer基础上增加：
    - 绘制矩形
    - 绘制折线
    - 鼠标在折线上移动时显示位置
    """
    
    # 绘制模式
    MODE_NONE = 0
    MODE_RECT = 1
    MODE_POLYLINE = 2
    
    # 信号：绘制完成
    rect_drawn = Signal(object)  # QRectF
    polyline_drawn = Signal(list)  # [(x, y), ...]
    polyline_hover = Signal(int)  # 折线上的点索引
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 当前绘制模式
        self.draw_mode = self.MODE_NONE
        
        # 矩形绘制
        self.rect_start = None
        self.rect_item = None
        self.current_rect = None
        
        # 折线绘制
        self.polyline_points = []
        self.polyline_item = None
        self.polyline_markers = []  # 折线上的点标记
        self.hover_marker = None  # 鼠标悬停标记
        self.polyline_completed = False  # 折线是否已完成
        
        # 绘制样式
        self.rect_pen = QPen(QColor(255, 0, 0), 3, Qt.SolidLine)  # 加粗到3
        self.rect_pen.setCosmetic(True)  # 设置为cosmetic模式，线宽不随缩放变化
        
        self.polyline_pen = QPen(QColor(0, 255, 0), 3, Qt.SolidLine)  # 加粗到3
        self.polyline_pen.setCosmetic(True)  # 设置为cosmetic模式，线宽不随缩放变化
        
        self.marker_brush = QBrush(QColor(0, 255, 0))
        self.hover_brush = QBrush(QColor(255, 255, 0))
        
    def set_draw_mode(self, mode):
        """设置绘制模式"""
        self.draw_mode = mode
        if mode == self.MODE_RECT:
            self.clear_polyline()
        elif mode == self.MODE_POLYLINE:
            self.clear_rect()
        elif mode == self.MODE_NONE:
            pass
            
    def clear_rect(self):
        """清除矩形"""
        if self.rect_item:
            try:
                if self.rect_item.scene() is not None:
                    self.scene.removeItem(self.rect_item)
            except RuntimeError:
                pass  # 对象已被删除
            self.rect_item = None
        self.rect_start = None
        self.current_rect = None
        
    def clear_polyline(self):
        """清除折线"""
        if self.polyline_item:
            try:
                if self.polyline_item.scene() is not None:
                    self.scene.removeItem(self.polyline_item)
            except RuntimeError:
                pass
            self.polyline_item = None
        
        for marker in self.polyline_markers:
            try:
                if marker.scene() is not None:
                    self.scene.removeItem(marker)
            except RuntimeError:
                pass
        self.polyline_markers.clear()
        
        if self.hover_marker:
            try:
                if self.hover_marker.scene() is not None:
                    self.scene.removeItem(self.hover_marker)
            except RuntimeError:
                pass
            self.hover_marker = None
            
        self.polyline_points.clear()
        self.polyline_completed = False
    
    def mousePressEvent(self, event):
        """鼠标按下事件"""
        if event.button() == Qt.LeftButton and self.image_item and self.image_array is not None:
            scene_pos = self.mapToScene(event.pos())
            
            if self.image_item.contains(scene_pos):
                item_pos = self.image_item.mapFromScene(scene_pos)
                
                if self.draw_mode == self.MODE_RECT:
                    # 开始绘制矩形，先清除旧矩形
                    self.clear_rect()
                    self.rect_start = item_pos
                    event.accept()
                    return
                    
                elif self.draw_mode == self.MODE_POLYLINE:
                    x = int(item_pos.x())
                    y = int(item_pos.y())
                    
                    if (0 <= x < self.image_array.shape[1] and 
                        0 <= y < self.image_array.shape[0]):
                        # 如果折线已完成，开始新折线
                        if self.polyline_completed:
                            self.clear_polyline()
                            self.polyline_completed = False
                        
                        # 如果是第一个点，确保清除旧折线
                        if len(self.polyline_points) == 0:
                            self.clear_polyline()
                            self.polyline_completed = False
                        
                        self.polyline_points.append((x, y))
                        self._update_polyline()
                        event.accept()
                        return
        
        # 其他情况调用父类方法
        super().mousePressEvent(event)
    
    def mouseMoveEvent(self, event):
        """鼠标移动事件"""
        if self.image_array is None:
            super().mouseMoveEvent(event)
            return
            
        if self.draw_mode == self.MODE_RECT and self.rect_start is not None:
            # 绘制矩形中
            scene_pos = self.mapToScene(event.pos())
            if self.image_item and self.image_item.contains(scene_pos):
                item_pos = self.image_item.mapFromScene(scene_pos)
                self._update_rect(item_pos)
                event.accept()
                return
        
        elif self.draw_mode == self.MODE_POLYLINE and len(self.polyline_points) > 0 and not self.polyline_completed:
            # 折线绘制中，显示临时预览线
            scene_pos = self.mapToScene(event.pos())
            if self.image_item and self.image_item.contains(scene_pos):
                item_pos = self.image_item.mapFromScene(scene_pos)
                x = int(item_pos.x())
                y = int(item_pos.y())
                
                if (0 <= x < self.image_array.shape[1] and 
                    0 <= y < self.image_array.shape[0]):
                    # 更新折线显示（临时添加当前鼠标位置）
                    self._update_polyline_preview(x, y)
                    event.accept()
                    return
                
        elif self.draw_mode == self.MODE_NONE and self.polyline_completed and len(self.polyline_points) > 0:
            # 折线已完成，检测鼠标是否在折线附近
            scene_pos = self.mapToScene(event.pos())
            if self.image_item and self.image_item.contains(scene_pos):
                item_pos = self.image_item.mapFromScene(scene_pos)
                x = int(item_pos.x())
                y = int(item_pos.y())
                
                # 获取折线路径上的所有点
                path_points, _ = self.get_polyline_path_values()
                if path_points:
                    # 查找最近的点
                    min_dist = float('inf')
                    nearest_idx = -1
                    
                    for idx, (px, py) in enumerate(path_points):
                        dist = np.sqrt((x - px)**2 + (y - py)**2)
                        if dist < min_dist:
                            min_dist = dist
                            nearest_idx = idx
                    
                    # 如果距离小于阈值，显示悬停标记
                    if min_dist < 5:  # 5像素阈值
                        px, py = path_points[nearest_idx]
                        self._show_hover_marker_at(px, py)
                        self.polyline_hover.emit(nearest_idx)
                    else:
                        self._hide_hover_marker()
                else:
                    self._hide_hover_marker()
            else:
                # 鼠标不在图像上，隐藏悬停标记
                self._hide_hover_marker()
        
        # 调用父类方法以保持其他功能
        super().mouseMoveEvent(event)
    
    def mouseReleaseEvent(self, event):
        """鼠标释放事件"""
        if event.button() == Qt.LeftButton and self.draw_mode == self.MODE_RECT:
            if self.rect_start is not None:
                # 完成矩形绘制
                scene_pos = self.mapToScene(event.pos())
                if self.image_item.contains(scene_pos):
                    item_pos = self.image_item.mapFromScene(scene_pos)
                    self._update_rect(item_pos)
                    
                    # 发送信号
                    if self.current_rect:
                        self.rect_drawn.emit(self.current_rect)
                
                self.rect_start = None
                event.accept()
                return
        
        elif event.button() == Qt.RightButton and self.draw_mode == self.MODE_POLYLINE:
            # 右键完成折线绘制
            if len(self.polyline_points) > 1 and not self.polyline_completed:
                self.polyline_completed = True
                self.polyline_drawn.emit(self.polyline_points.copy())
                event.accept()
                return
        
        super().mouseReleaseEvent(event)
    
    def _update_rect(self, end_pos):
        """更新矩形显示"""
        if not self.rect_start or self.image_array is None:
            return
            
        # 计算矩形范围
        x1 = min(self.rect_start.x(), end_pos.x())
        y1 = min(self.rect_start.y(), end_pos.y())
        x2 = max(self.rect_start.x(), end_pos.x())
        y2 = max(self.rect_start.y(), end_pos.y())
        
        # 限制在图像范围内
        x1 = max(0, min(x1, self.image_array.shape[1] - 1))
        y1 = max(0, min(y1, self.image_array.shape[0] - 1))
        x2 = max(0, min(x2, self.image_array.shape[1]))
        y2 = max(0, min(y2, self.image_array.shape[0]))
        
        self.current_rect = QRectF(x1, y1, x2 - x1, y2 - y1)
        
        # 更新或创建矩形项
        if self.rect_item is None:
            self.rect_item = QGraphicsRectItem(self.current_rect)
            self.rect_item.setPen(self.rect_pen)
            self.rect_item.setBrush(QBrush(QColor(255, 0, 0, 30)))  # 添加半透明填充
            self.rect_item.setParentItem(self.image_item)  # 设置父项会自动添加到场景
        else:
            self.rect_item.setRect(self.current_rect)
    
    def _update_polyline(self, temp_point=None):
        """更新折线显示
        
        Args:
            temp_point: 临时预览点 (x, y)，用于绘制过程中的预览
        """
        if len(self.polyline_points) == 0:
            return
        
        # 移除旧的折线（安全检查避免C++对象已删除错误）
        if self.polyline_item:
            try:
                self.scene.removeItem(self.polyline_item)
            except RuntimeError:
                pass  # 对象已被删除
            self.polyline_item = None
        for marker in self.polyline_markers:
            try:
                self.scene.removeItem(marker)
            except RuntimeError:
                pass  # 对象已被删除
        self.polyline_markers.clear()
        
        # 创建已确定部分的折线路径（实线）
        path = QPainterPath()
        path.moveTo(QPointF(self.polyline_points[0][0], self.polyline_points[0][1]))
        
        for x, y in self.polyline_points[1:]:
            path.lineTo(QPointF(x, y))
        
        # 创建折线项（实线部分）
        self.polyline_item = QGraphicsPathItem(path)
        self.polyline_item.setPen(self.polyline_pen)
        self.polyline_item.setParentItem(self.image_item)  # 设置父项会自动添加到场景
        
        # 如果有临时点，创建单独的虚线预览
        if temp_point is not None and len(self.polyline_points) > 0:
            preview_path = QPainterPath()
            last_point = self.polyline_points[-1]
            preview_path.moveTo(QPointF(last_point[0], last_point[1]))
            preview_path.lineTo(QPointF(temp_point[0], temp_point[1]))
            
            preview_item = QGraphicsPathItem(preview_path)
            temp_pen = QPen(self.polyline_pen)
            temp_pen.setStyle(Qt.DashLine)
            preview_item.setPen(temp_pen)
            preview_item.setParentItem(self.image_item)
            # 将预览线添加到markers列表，以便下次更新时清除
            self.polyline_markers.append(preview_item)
        
        # 只在折线绘制完成后添加点标记，绘制中不显示标记避免重复
        if self.polyline_completed:
            for x, y in self.polyline_points:
                marker = QGraphicsEllipseItem(x - 3, y - 3, 6, 6)
                marker.setBrush(self.marker_brush)
                marker.setPen(QPen(Qt.NoPen))
                # 设置标记不随视图缩放而变化
                marker.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations, True)
                marker.setParentItem(self.image_item)  # 设置父项会自动添加到场景
                self.polyline_markers.append(marker)
    
    def _update_polyline_preview(self, x, y):
        """更新折线预览（绘制中）"""
        self._update_polyline(temp_point=(x, y))
    
    def _show_hover_marker(self, idx):
        """显示悬停标记（基于折点索引）"""
        if idx < 0 or idx >= len(self.polyline_points):
            return
        
        x, y = self.polyline_points[idx]
        self._show_hover_marker_at(x, y)
    
    def _show_hover_marker_at(self, x, y):
        """在指定坐标显示悬停标记"""
        try:
            if self.hover_marker is None:
                # 创建以原点为中心的圆形
                self.hover_marker = QGraphicsEllipseItem(-5, -5, 10, 10)
                self.hover_marker.setBrush(self.hover_brush)
                pen = QPen(QColor(255, 0, 0), 2)
                pen.setCosmetic(True)  # 设置为cosmetic模式
                self.hover_marker.setPen(pen)
                # 设置标记大小不随视图缩放而变化
                self.hover_marker.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations, True)
                self.hover_marker.setParentItem(self.image_item)  # 设置父项会自动添加到场景
                self.hover_marker.setPos(x, y)
            else:
                # 检查对象是否仍然有效
                if self.hover_marker.scene() is None:
                    # 对象已被删除，重新创建
                    self.hover_marker = QGraphicsEllipseItem(-5, -5, 10, 10)
                    self.hover_marker.setBrush(self.hover_brush)
                    pen = QPen(QColor(255, 0, 0), 2)
                    pen.setCosmetic(True)
                    self.hover_marker.setPen(pen)
                    self.hover_marker.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations, True)
                    self.hover_marker.setParentItem(self.image_item)
                    self.hover_marker.setPos(x, y)
                else:
                    # 使用setPos移动位置，而不是setRect
                    self.hover_marker.setPos(x, y)
                    self.hover_marker.setVisible(True)
        except RuntimeError:
            # 对象已被删除，重新创建
            self.hover_marker = QGraphicsEllipseItem(-5, -5, 10, 10)
            self.hover_marker.setBrush(self.hover_brush)
            pen = QPen(QColor(255, 0, 0), 2)
            pen.setCosmetic(True)
            self.hover_marker.setPen(pen)
            self.hover_marker.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations, True)
            self.hover_marker.setParentItem(self.image_item)
            self.hover_marker.setPos(x, y)
    
    def _hide_hover_marker(self):
        """隐藏悬停标记"""
        if self.hover_marker:
            self.hover_marker.setVisible(False)
    
    def get_rect_region(self):
        """获取矩形区域的图像数据"""
        if not self.current_rect or self.image_array is None:
            return None
        
        x1 = int(self.current_rect.x())
        y1 = int(self.current_rect.y())
        x2 = int(self.current_rect.x() + self.current_rect.width())
        y2 = int(self.current_rect.y() + self.current_rect.height())
        
        return self.image_array[y1:y2+1, x1:x2+1]
    
    def get_polyline_values(self):
        """获取折线上的像素值（只是折点）"""
        if len(self.polyline_points) == 0 or self.image_array is None:
            return None
        
        values = []
        for x, y in self.polyline_points:
            value = self.get_pixel_value(x, y)
            values.append(value)
        
        return values
    
    def get_polyline_path_values(self):
        """获取折线路径上所有像素的值（使用Bresenham算法）"""
        if len(self.polyline_points) < 2 or self.image_array is None:
            return None, None
        
        all_points = []
        all_values = []
        
        # 遍历每个线段
        for i in range(len(self.polyline_points) - 1):
            x0, y0 = self.polyline_points[i]
            x1, y1 = self.polyline_points[i + 1]
            
            # 使用Bresenham算法获取线段上的所有点
            points = self._bresenham_line(x0, y0, x1, y1)
            
            # 获取这些点的像素值
            for x, y in points:
                if (0 <= x < self.image_array.shape[1] and 
                    0 <= y < self.image_array.shape[0]):
                    all_points.append((x, y))
                    value = self.get_pixel_value(x, y)
                    all_values.append(value)
        
        return all_points, all_values
    
    def _bresenham_line(self, x0, y0, x1, y1):
        """使用Bresenham算法获取线段上的所有像素点"""
        points = []
        
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        
        x, y = x0, y0
        
        while True:
            points.append((x, y))
            
            if x == x1 and y == y1:
                break
            
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x += sx
            if e2 < dx:
                err += dx
                y += sy
        
        return points
