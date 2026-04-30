'''
Author: Yibo Yuan 2633669459@qq.com
Description: Colorbar widget for showing colormap and numeric range.

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved.
'''

import numpy as np
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QImage, QPixmap, QPen, QFont

try:
    import matplotlib.cm as cm
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    from ..utils.custom_colormaps import register_custom_colormaps
    register_custom_colormaps()
except Exception:
    pass


class ColorbarWidget(QWidget):
    """
    Show the active colormap, value range, and hovered value indicator.
    """

    def __init__(self, parent=None):
        """__init__。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            parent (Any): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        super().__init__(parent)

        self.setFixedWidth(80)
        self.setMinimumHeight(200)

        self.colormap_name = 'gray'
        self.colormap_reversed = False
        self.vmin = 0.0
        self.vmax = 1.0
        self.current_value = None

        self.setMouseTracking(True)

    def set_colormap(self, colormap_name, reversed=False):
        """Set the active colormap."""
        self.colormap_name = colormap_name
        self.colormap_reversed = reversed
        self.update()

    def set_range(self, vmin, vmax):
        """Set the displayed numeric range."""
        self.vmin = float(vmin)
        self.vmax = float(vmax)
        self.update()

    def set_current_value(self, value):
        """Set the hovered value indicator."""
        if value is None:
            self.current_value = None
        else:
            value = float(value)
            range_min = min(self.vmin, self.vmax)
            range_max = max(self.vmin, self.vmax)
            self.current_value = value if range_min <= value <= range_max else None
        self.update()

    def _format_value(self, value):
        """根据范围自适应格式化数值。"""
        value_range = abs(self.vmax - self.vmin)
        if value_range > 1000:
            return f"{value:.0f}"
        if value_range > 10:
            return f"{value:.1f}"
        if value_range > 0.1:
            return f"{value:.2f}"
        return f"{value:.3f}"

    def paintEvent(self, event):
        """Paint the colorbar."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        height = self.height()
        bar_left = 30
        bar_width = 20
        bar_top = 20
        bar_height = height - 40
        bar_right = bar_left + bar_width

        if MATPLOTLIB_AVAILABLE and bar_height > 0:
            try:
                cmap = cm.get_cmap(self.colormap_name)
                num_colors = bar_height
                if self.colormap_reversed:
                    y_values = np.linspace(0, 1, num_colors)
                else:
                    y_values = np.linspace(1, 0, num_colors)

                colors = cmap(y_values)
                rgb_array = (colors[:, :3] * 255).astype(np.uint8)
                rgb_array = np.repeat(rgb_array[:, np.newaxis, :], bar_width, axis=1)
                rgb_array = np.ascontiguousarray(rgb_array)
                qimage = QImage(
                    rgb_array.data,
                    bar_width,
                    bar_height,
                    bar_width * 3,
                    QImage.Format_RGB888,
                )
                pixmap = QPixmap.fromImage(qimage)
                painter.drawPixmap(bar_left, bar_top, pixmap)
            except Exception as e:
                print(f"Failed to draw colorbar: {e}")
                painter.fillRect(bar_left, bar_top, bar_width, bar_height, Qt.gray)
        else:
            painter.fillRect(bar_left, bar_top, bar_width, bar_height, Qt.gray)

        painter.setPen(QPen(Qt.black, 1))
        painter.drawRect(bar_left, bar_top, bar_width, bar_height)

        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)
        painter.setPen(Qt.black)

        num_ticks = min(7, max(3, bar_height // 30))
        for i in range(num_ticks):
            ratio = i / (num_ticks - 1)
            tick_value = self.vmax - ratio * (self.vmax - self.vmin)
            y_pos = bar_top + ratio * bar_height

            painter.setPen(QPen(Qt.black, 1))
            painter.drawLine(bar_left - 2, int(y_pos), bar_left, int(y_pos))
            painter.drawText(
                0,
                int(y_pos) - 10,
                bar_left - 4,
                20,
                Qt.AlignRight | Qt.AlignVCenter,
                self._format_value(tick_value),
            )

        if self.current_value is not None:
            if self.vmax == self.vmin:
                ratio = 0.5
            else:
                ratio = (self.vmax - self.current_value) / (self.vmax - self.vmin)
                ratio = np.clip(ratio, 0, 1)
            y_pos = bar_top + ratio * bar_height

            painter.setPen(QPen(Qt.white, 2))
            painter.drawLine(bar_left, int(y_pos), bar_right, int(y_pos))
            painter.setPen(QPen(Qt.black, 1))
            painter.drawLine(bar_left, int(y_pos), bar_right, int(y_pos))

            painter.setPen(Qt.red)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                bar_right + 5,
                int(y_pos) - 10,
                45,
                20,
                Qt.AlignLeft | Qt.AlignVCenter,
                self._format_value(self.current_value),
            )
