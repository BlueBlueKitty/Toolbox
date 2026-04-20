"""
魔法棒参数面板。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QSizePolicy,
    QToolButton,
    QWidget,
    QDialogButtonBox,
)

from src.segmentation.models import MagicWandParams


class MagicWandPanel(QGroupBox):
    params_changed = Signal(object)
    merge_preview_changed = Signal(bool)
    brush_size_changed = Signal(int)
    confirm_requested = Signal()
    cancel_requested = Signal()
    slider_config_changed = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__("参数", parent)
        self._material_icon_family = self._load_material_icon_font()
        self._slider_configs: dict[str, dict[str, int]] = {
            "tolerance": {"min": 0, "max": 100, "default": 15},
            "brush_size": {"min": 1, "max": 100, "default": 12},
        }
        layout = QFormLayout(self)
        layout.setLabelAlignment(Qt.AlignLeft)
        layout.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        layout.setFieldGrowthPolicy(QFormLayout.FieldsStayAtSizeHint)

        top_row = QHBoxLayout()
        self.single_preview_button = QToolButton()
        self.single_preview_button.setText("单次选区")
        self.single_preview_button.setFixedWidth(110)
        self.single_preview_button.setCheckable(True)
        self.single_preview_button.setChecked(True)
        self.single_preview_button.setToolTip("每次点击都会生成一个新的选区，不与上一次结果合并。")
        self.merge_preview_button = QToolButton()
        self.merge_preview_button.setText("合并选区")
        self.merge_preview_button.setFixedWidth(110)
        self.merge_preview_button.setCheckable(True)
        self.merge_preview_button.setToolTip("新的选区会叠加到当前未确认结果上，适合多次补选。")
        top_row.addStretch(1)
        top_row.addWidget(self.single_preview_button)
        top_row.addWidget(self.merge_preview_button)
        top_row.addStretch(1)
        top_row_widget = QWidget()
        top_row_widget.setLayout(top_row)
        layout.addRow(top_row_widget)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("RGB", "rgb")
        self.mode_combo.addItem("R", "r")
        self.mode_combo.addItem("G", "g")
        self.mode_combo.addItem("B", "b")
        self.mode_combo.addItem("H", "h")
        self.mode_combo.addItem("S", "s")
        self.mode_combo.addItem("V", "v")
        self.mode_combo.setToolTip("选择颜色比较通道。RGB 最稳妥，单通道适合颜色差异明显的目标。")
        layout.addRow(self._form_label("比较模式", "选择颜色比较通道。RGB 最稳妥，单通道适合颜色差异明显的目标。"), self.mode_combo)

        self.connectivity_combo = QComboBox()
        self.connectivity_combo.addItems(["4", "8"])
        self.connectivity_combo.setCurrentText("8")
        self.connectivity_combo.setToolTip("控制区域生长的连通方式。8 连通更容易连上对角相邻像素。")
        layout.addRow(self._form_label("连通方式", "控制区域生长的连通方式。8 连通更容易连上对角相邻像素。"), self.connectivity_combo)

        self.tolerance_slider, self.tolerance_value = self._make_slider(0, 100, 15)
        self.tolerance_slider.setToolTip("允许与种子点颜色的最大差值。越大，选中的区域越多。")
        tolerance_label, self.tolerance_settings_btn = self._form_label_with_settings(
            "阈值容差",
            "允许与种子点颜色的最大差值。越大，选中的区域越多。",
            "tolerance",
        )
        layout.addRow(tolerance_label, self._slider_row(self.tolerance_slider, self.tolerance_value))

        self.min_area_edit = QLineEdit("16")
        self.min_area_edit.setFixedWidth(80)
        self.min_area_edit.setToolTip("小于该面积的识别结果会被忽略，用于过滤零碎噪声。")
        layout.addRow(self._form_label("最小面积", "小于该面积的识别结果会被忽略，用于过滤零碎噪声。"), self.min_area_edit)

        self.fill_small_holes_radio = QRadioButton("小孔洞")
        self.fill_small_holes_radio.setChecked(True)
        self.fill_small_holes_radio.setToolTip("只填补面积较小的内部孔洞，适合保留大孔洞结构。")
        self.fill_all_holes_radio = QRadioButton("所有孔洞")
        self.fill_all_holes_radio.setToolTip("使用更快的整区孔洞填补算法，直接填满所有被包围的孔洞。")
        self.fill_holes_group = QButtonGroup(self)
        self.fill_holes_group.setExclusive(True)
        self.fill_holes_group.addButton(self.fill_small_holes_radio)
        self.fill_holes_group.addButton(self.fill_all_holes_radio)
        fill_holes_row = QWidget()
        fill_holes_layout = QHBoxLayout(fill_holes_row)
        fill_holes_layout.setContentsMargins(0, 0, 0, 0)
        fill_holes_layout.setSpacing(12)
        fill_holes_layout.addWidget(self.fill_small_holes_radio)
        fill_holes_layout.addWidget(self.fill_all_holes_radio)
        fill_holes_layout.addStretch(1)
        layout.addRow(self._form_label("填补孔洞", "控制识别结果中内部孔洞的填补方式。"), fill_holes_row)

        self.brush_size_slider, self.brush_size_value = self._make_slider(1, 100, 12)
        self.brush_size_slider.setToolTip("笔刷和橡皮擦的直径。")
        brush_label, self.brush_settings_btn = self._form_label_with_settings(
            "笔刷/橡皮擦粗细",
            "控制笔刷和橡皮擦的作用范围。",
            "brush_size",
        )
        layout.addRow(
            brush_label,
            self._slider_row(self.brush_size_slider, self.brush_size_value),
        )

        self.confirm_button = QPushButton("确认预览")
        self.cancel_button = QPushButton("取消预览")
        self.confirm_button.setFixedWidth(110)
        self.cancel_button.setFixedWidth(110)
        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addStretch(1)
        action_layout.addWidget(self.confirm_button)
        action_layout.addWidget(self.cancel_button)
        action_layout.addStretch(1)
        layout.addRow(action_row)

        self.single_preview_button.clicked.connect(lambda checked: self._set_merge_preview(not checked))
        self.merge_preview_button.clicked.connect(lambda checked: self._set_merge_preview(checked))
        self.brush_size_slider.valueChanged.connect(self.brush_size_changed.emit)
        for widget in [
            self.mode_combo,
            self.connectivity_combo,
            self.tolerance_slider,
            self.min_area_edit,
            self.fill_small_holes_radio,
            self.fill_all_holes_radio,
        ]:
            signal = getattr(widget, "valueChanged", None) or getattr(widget, "currentTextChanged", None) or getattr(widget, "toggled", None) or getattr(widget, "textChanged", None)
            signal.connect(self._emit_params)
        self.confirm_button.clicked.connect(self.confirm_requested)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.refresh_icons()

    def brush_size(self) -> int:
        return int(self.brush_size_slider.value())

    def params(self) -> MagicWandParams:
        try:
            min_area = max(1, int(self.min_area_edit.text().strip() or "1"))
        except ValueError:
            min_area = 16
        return MagicWandParams(
            tolerance=self.tolerance_slider.value(),
            connectivity=int(self.connectivity_combo.currentText()),
            min_area=min_area,
            similarity_mode=str(self.mode_combo.currentData()),
            fill_small_holes=self.fill_small_holes_radio.isChecked(),
            fill_all_holes=self.fill_all_holes_radio.isChecked(),
        )

    def merge_preview_enabled(self) -> bool:
        return self.merge_preview_button.isChecked()

    def _set_merge_preview(self, enabled: bool) -> None:
        self.merge_preview_button.blockSignals(True)
        self.single_preview_button.blockSignals(True)
        self.merge_preview_button.setChecked(enabled)
        self.single_preview_button.setChecked(not enabled)
        self.merge_preview_button.blockSignals(False)
        self.single_preview_button.blockSignals(False)
        self.merge_preview_changed.emit(enabled)

    def _emit_params(self, *_args) -> None:
        self.params_changed.emit(self.params())

    def _make_slider(self, minimum: int, maximum: int, value: int) -> tuple[QSlider, QLabel]:
        slider = QSlider(Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        slider.setFixedWidth(110)
        label = QLabel(str(value))
        label.setMinimumWidth(48)
        slider.valueChanged.connect(lambda current, target=label: target.setText(str(current)))
        return slider, label

    def get_slider_configs(self) -> dict[str, dict[str, int]]:
        return {
            key: {
                "min": int(cfg.get("min", 0)),
                "max": int(cfg.get("max", 100)),
                "default": int(cfg.get("default", cfg.get("min", 0))),
            }
            for key, cfg in self._slider_configs.items()
        }

    def apply_slider_configs(self, configs: dict[str, dict[str, int]] | None, *, emit_change: bool = False) -> None:
        merged = self.get_slider_configs()
        if isinstance(configs, dict):
            for key in ("tolerance", "brush_size"):
                incoming = configs.get(key)
                if not isinstance(incoming, dict):
                    continue
                try:
                    minimum = int(incoming.get("min", merged[key]["min"]))
                    maximum = int(incoming.get("max", merged[key]["max"]))
                    default = int(incoming.get("default", merged[key]["default"]))
                except (TypeError, ValueError):
                    continue
                if minimum > maximum:
                    minimum, maximum = maximum, minimum
                default = max(minimum, min(maximum, default))
                merged[key] = {"min": minimum, "max": maximum, "default": default}
        self._slider_configs = merged
        self._apply_slider_config_to_widget("tolerance")
        self._apply_slider_config_to_widget("brush_size")
        self.tolerance_slider.setValue(self._slider_configs["tolerance"]["default"])
        self.brush_size_slider.setValue(self._slider_configs["brush_size"]["default"])
        if emit_change:
            self.slider_config_changed.emit("all", self.get_slider_configs())

    def _apply_slider_config_to_widget(self, key: str) -> None:
        slider = self.tolerance_slider if key == "tolerance" else self.brush_size_slider
        cfg = self._slider_configs[key]
        current = int(slider.value())
        slider.blockSignals(True)
        slider.setRange(cfg["min"], cfg["max"])
        slider.setValue(max(cfg["min"], min(cfg["max"], current)))
        slider.blockSignals(False)
        if key == "tolerance":
            self.tolerance_value.setText(str(slider.value()))
        else:
            self.brush_size_value.setText(str(slider.value()))

    def _open_slider_range_dialog(self, key: str) -> None:
        cfg = self._slider_configs[key]
        dialog = QDialog(self)
        dialog.setWindowTitle("设置滑动范围")
        form = QFormLayout(dialog)
        min_spin = QSpinBox(dialog)
        min_spin.setRange(-1000000, 1000000)
        min_spin.setValue(cfg["min"])
        max_spin = QSpinBox(dialog)
        max_spin.setRange(-1000000, 1000000)
        max_spin.setValue(cfg["max"])
        default_spin = QSpinBox(dialog)
        default_spin.setRange(-1000000, 1000000)
        default_spin.setValue(cfg["default"])
        form.addRow("最小值", min_spin)
        form.addRow("最大值", max_spin)
        form.addRow("默认值", default_spin)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
        form.addRow(buttons)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        if dialog.exec() != QDialog.Accepted:
            return
        minimum = int(min_spin.value())
        maximum = int(max_spin.value())
        if minimum > maximum:
            minimum, maximum = maximum, minimum
        default = int(default_spin.value())
        default = max(minimum, min(maximum, default))
        self._slider_configs[key] = {"min": minimum, "max": maximum, "default": default}
        self._apply_slider_config_to_widget(key)
        slider = self.tolerance_slider if key == "tolerance" else self.brush_size_slider
        slider.setValue(default)
        self.slider_config_changed.emit(key, self.get_slider_configs())
        self._emit_params()

    def _slider_row(self, slider, label: QLabel) -> QWidget:
        widget = QWidget()
        widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, 0, Qt.AlignLeft)
        layout.addSpacing(8)
        layout.addWidget(label)
        layout.addStretch(1)
        return widget

    def _form_label_with_settings(self, text: str, tooltip: str, slider_key: str) -> tuple[QWidget, QToolButton]:
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        label = QLabel(text)
        label.setToolTip(tooltip)
        row.addWidget(label)
        setting_btn = QToolButton(widget)
        setting_btn.setToolTip("设置最小值/最大值/默认值")
        setting_btn.setAutoRaise(True)
        setting_btn.clicked.connect(lambda: self._open_slider_range_dialog(slider_key))
        row.addWidget(setting_btn)
        row.addStretch(1)
        return widget, setting_btn

    def refresh_icons(self) -> None:
        settings_icon = self._material_icon("settings")
        if settings_icon.isNull():
            return
        self.tolerance_settings_btn.setIcon(settings_icon)
        self.brush_settings_btn.setIcon(settings_icon)

    def _form_label(self, text: str, tooltip: str) -> QLabel:
        label = QLabel(text)
        label.setToolTip(tooltip)
        return label

    def _load_material_icon_font(self) -> str | None:
        font_path = Path(__file__).resolve().parents[2] / "resources" / "fonts" / "MaterialIcons-Regular.ttf"
        if not font_path.exists():
            return None
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id < 0:
            return None
        families = QFontDatabase.applicationFontFamilies(font_id)
        return families[0] if families else None

    def _material_icon(self, icon_name: str) -> QIcon:
        if not self._material_icon_family:
            return QIcon()
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = QFont(self._material_icon_family)
        font.setPixelSize(18)
        painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, icon_name)
        painter.end()
        return QIcon(pixmap)
