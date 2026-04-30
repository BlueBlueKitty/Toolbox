"""
底部操作进度显示组件。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget


class OperationProgressWidget(QWidget):
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.message_label = QLabel("就绪")
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(True)
        self.progress_bar.setTextVisible(True)

        layout.addWidget(self.message_label, 1)
        layout.addWidget(self.progress_bar, 0)

        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self.reset)

    def start_task(self, message: str, maximum: int = 0) -> None:
        """start_task。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            message (str): 输入参数。
            maximum (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self._reset_timer.stop()
        self.message_label.setText(message)
        self.progress_bar.setVisible(True)
        if maximum <= 0:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setValue(0)
        else:
            self.progress_bar.setRange(0, maximum)
            self.progress_bar.setValue(0)

    def set_progress(self, value: int, message: str | None = None, maximum: int | None = None) -> None:
        """set_progress。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            value (int): 输入参数。
            message (str | None): 输入参数。
            maximum (int | None): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        if message:
            self.message_label.setText(message)
        if maximum is not None:
            self.progress_bar.setRange(0, maximum if maximum > 0 else 0)
        self.progress_bar.setVisible(True)
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(max(self.progress_bar.minimum(), min(value, self.progress_bar.maximum())))

    def finish_task(self, message: str = "完成", auto_reset_ms: int = 1500) -> None:
        """finish_task。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            message (str): 输入参数。
            auto_reset_ms (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.progress_bar.setVisible(True)
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(self.progress_bar.maximum())
        self.message_label.setText(message)
        self._reset_timer.start(max(0, auto_reset_ms))

    def fail_task(self, message: str, auto_reset_ms: int = 3000) -> None:
        """fail_task。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            message (str): 输入参数。
            auto_reset_ms (int): 输入参数。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.progress_bar.setVisible(True)
        self.message_label.setText(message)
        self._reset_timer.start(max(0, auto_reset_ms))

    def reset(self) -> None:
        """reset。

        功能:
            承担当前方法对应的业务逻辑。
        参数:
            无。
        返回:
            None: 方法执行结果。
        异常:
            Exception: 依赖组件或输入异常时可能抛出。
        """
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.message_label.setText("就绪")
