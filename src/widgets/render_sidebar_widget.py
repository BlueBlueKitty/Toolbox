"""
统一渲染侧边栏与绑定控制器。
"""

from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.rendering.config import RasterRenderConfig
from src.rendering.models import RenderRequest
from src.rendering.style_auto_selector import DefaultRenderStyleFactory
from src.rendering.styles import (
    ColorRampSettings,
    HillshadeRenderStyle,
    LayerDisplaySettings,
    MultibandRenderStyle,
    PalettedRenderStyle,
    SinglebandGrayRenderStyle,
    SinglebandPseudoColorRenderStyle,
    UniqueValueItem,
    UniqueValueRenderStyle,
    deserialize_style_bundle,
    legacy_config_to_style,
    migrate_style_on_renderer_switch,
    serialize_style_bundle,
    style_to_legacy_config,
)
from src.widgets.colormap_combobox import ColormapComboBox
from src.widgets.render_settings_widget import RenderSettingsWidget

RENDER_MODE_MULTIBAND = "多波段"
RENDER_MODE_SINGLEBAND = "单波段"
RENDER_MODE_CATEGORICAL = "唯一值/调色板"
RENDER_MODE_HILLSHADE = "晕渲地貌"

RENDER_MODE_ITEMS = [
    RENDER_MODE_MULTIBAND,
    RENDER_MODE_SINGLEBAND,
    RENDER_MODE_CATEGORICAL,
    RENDER_MODE_HILLSHADE,
]

BLEND_MODE_ITEMS = [
    ("source_over", "正常"),
    ("multiply", "正片叠底"),
    ("screen", "滤色"),
    ("overlay", "叠加"),
    ("plus", "相加"),
]

RESAMPLING_ITEMS = [
    ("nearest", "最近邻"),
    ("bilinear", "双线性"),
    ("cubic", "三次卷积"),
]

RELIEF_BLEND_MODE_ITEMS = [
    ("multiply", "正片叠底"),
    ("overlay", "叠加"),
    ("screen", "滤色"),
]

_CATEGORY_COLORS = [
    (31, 119, 180, 255),
    (255, 127, 14, 255),
    (44, 160, 44, 255),
    (214, 39, 40, 255),
    (148, 103, 189, 255),
    (140, 86, 75, 255),
    (227, 119, 194, 255),
    (127, 127, 127, 255),
    (188, 189, 34, 255),
    (23, 190, 207, 255),
]


class LegacyRenderSettingsAdapter:
    @staticmethod
    def layer_to_legacy_config(layer):
        return style_to_legacy_config(layer.render_style, layer.display_settings)

    @staticmethod
    def apply_layer_to_widget(widget: "RenderSidebarWidget", layer) -> None:
        if layer is None:
            return
        config = LegacyRenderSettingsAdapter.layer_to_legacy_config(layer)
        widget._apply_render_mode_to_sidebar(_render_mode_from_layer(layer))
        widget.render_settings.blockSignals(True)
        widget.render_settings.set_num_bands(max(1, int(layer.metadata.band_count or 1)))
        if hasattr(widget.render_settings, "display_mode_combo"):
            widget.render_settings.display_mode_combo.setCurrentText(config.display_mode)
        widget.render_settings.gray_band_spin.setValue(int(config.gray_band))
        widget.render_settings.band_r_spin.setValue(int(config.rgb_bands[0]))
        widget.render_settings.band_g_spin.setValue(int(config.rgb_bands[1]))
        widget.render_settings.band_b_spin.setValue(int(config.rgb_bands[2]))
        widget.render_settings.set_stretch_mode(config.stretch_mode)
        widget.render_settings.percent_low_spin.setValue(float(config.percent_clip[0]))
        widget.render_settings.percent_high_spin.setValue(float(config.percent_clip[1]))
        widget.render_settings.std_dev_spin.setValue(float(config.std_dev_n))
        widget.render_settings.gamma_spin.setValue(float(config.gamma))
        widget.render_settings.auto_range_check.setChecked(not bool(config.auto_range))
        widget.render_settings.set_value_range(float(config.value_range[0]), float(config.value_range[1]))
        widget.render_settings.reverse_check.setChecked(bool(config.colormap_reversed))
        widget.render_settings.blockSignals(False)
        widget._categorical_block = True
        try:
            widget.gray_band_combo.setCurrentIndex(max(0, int(config.gray_band) - 1))
            widget.band_r_combo.setCurrentIndex(max(0, int(config.rgb_bands[0]) - 1))
            widget.band_g_combo.setCurrentIndex(max(0, int(config.rgb_bands[1]) - 1))
            widget.band_b_combo.setCurrentIndex(max(0, int(config.rgb_bands[2]) - 1))
        finally:
            widget._categorical_block = False
        widget.colormap_combo.blockSignals(True)
        widget.colormap_combo.setCurrentText(config.colormap_name)
        widget.colormap_combo.blockSignals(False)
        channel_gamma = tuple(getattr(layer.render_style, "channel_gamma", (float(config.gamma),) * 3))
        widget.gamma_r_spin.blockSignals(True)
        widget.gamma_g_spin.blockSignals(True)
        widget.gamma_b_spin.blockSignals(True)
        widget.gamma_r_spin.setValue(float(channel_gamma[0] if len(channel_gamma) > 0 else config.gamma))
        widget.gamma_g_spin.setValue(float(channel_gamma[1] if len(channel_gamma) > 1 else config.gamma))
        widget.gamma_b_spin.setValue(float(channel_gamma[2] if len(channel_gamma) > 2 else config.gamma))
        widget.gamma_r_spin.blockSignals(False)
        widget.gamma_g_spin.blockSignals(False)
        widget.gamma_b_spin.blockSignals(False)

        widget.color_ramp_reverse_check.blockSignals(True)
        widget.color_ramp_reverse_check.setChecked(bool(getattr(getattr(layer.render_style, "color_ramp", None), "reversed", False)))
        widget.color_ramp_reverse_check.blockSignals(False)
        widget._source_nodata_value = getattr(layer.metadata, "nodata", None)

        if hasattr(widget, "hillshade_azimuth_spin"):
            widget.hillshade_azimuth_spin.blockSignals(True)
            widget.hillshade_altitude_spin.blockSignals(True)
            widget.hillshade_zfactor_spin.blockSignals(True)
            if isinstance(layer.render_style, HillshadeRenderStyle):
                widget.hillshade_azimuth_spin.setValue(float(layer.render_style.azimuth))
                widget.hillshade_altitude_spin.setValue(float(layer.render_style.altitude))
                widget.hillshade_zfactor_spin.setValue(float(layer.render_style.z_factor))
            else:
                widget.hillshade_azimuth_spin.setValue(315.0)
                widget.hillshade_altitude_spin.setValue(45.0)
                widget.hillshade_zfactor_spin.setValue(1.0)
            widget.hillshade_azimuth_spin.blockSignals(False)
            widget.hillshade_altitude_spin.blockSignals(False)
            widget.hillshade_zfactor_spin.blockSignals(False)

        widget.apply_display_settings(layer.display_settings)
        widget.populate_categorical_table(layer.render_style)
        widget._update_section_visibility()

    @staticmethod
    def widget_to_legacy_config(widget: "RenderSidebarWidget") -> RasterRenderConfig:
        settings = widget.render_settings.get_all_settings()
        config = RasterRenderConfig()
        selected_mode = widget.current_render_mode()
        if selected_mode == RENDER_MODE_MULTIBAND:
            config.display_mode = "RGB"
        elif selected_mode == RENDER_MODE_HILLSHADE:
            config.display_mode = "晕渲地貌"
        else:
            config.display_mode = "灰度"
        config.gray_band = settings["gray_band"]
        config.rgb_bands = tuple(settings["rgb_bands"])
        config.gamma = settings["gamma"]
        config.stretch_mode = settings["stretch_mode"]
        config.percent_clip = tuple(settings["percent_clip"])
        config.std_dev_n = settings["std_dev_n"]
        config.auto_range = settings["auto_range"]
        config.value_range = tuple(settings["value_range"])
        config.colormap_reversed = widget.color_ramp_reverse_check.isChecked() if selected_mode in {RENDER_MODE_SINGLEBAND, RENDER_MODE_HILLSHADE} else False
        config.colormap_name = widget.colormap_combo.currentText() if selected_mode in {RENDER_MODE_SINGLEBAND, RENDER_MODE_HILLSHADE} else "gray"
        config.smooth_display = settings.get("smooth_display", False)
        return config


class RenderBindingBase(QObject):
    changed = Signal()

    def available_targets(self) -> list[tuple[str, str]]:
        return []

    def current_target_id(self) -> str | None:
        raise NotImplementedError

    def set_current_target(self, target_id: str) -> None:
        raise NotImplementedError

    def current_layer(self):
        raise NotImplementedError

    def current_layer_manager(self):
        raise NotImplementedError

    def refresh_signals(self) -> list:
        return [self.changed]


class SingleCanvasRenderBinding(RenderBindingBase):
    def __init__(self, canvas, *, target_id: str = "single_canvas", target_label: str = "唯一图层"):
        super().__init__()
        self.canvas = canvas
        self._target_id = target_id
        self._target_label = target_label
        self.canvas.layer_manager.active_layer_changed.connect(self.changed.emit)
        self.canvas.layer_manager.layer_style_changed.connect(lambda *_: self.changed.emit())
        self.canvas.layer_manager.layer_display_changed.connect(lambda *_: self.changed.emit())

    def available_targets(self) -> list[tuple[str, str]]:
        return [(self._target_id, self._target_label)]

    def current_target_id(self) -> str | None:
        return self._target_id

    def set_current_target(self, target_id: str) -> None:
        if target_id == self._target_id:
            self.canvas.layer_manager.set_active_layer(self.canvas.BASE_LAYER_ID)
            self.changed.emit()

    def current_layer(self):
        state = self.canvas.layer_manager.layer(self.canvas.BASE_LAYER_ID)
        return None if state is None else state.layer

    def current_layer_manager(self):
        return self.canvas.layer_manager


class MultiCanvasRenderBinding(RenderBindingBase):
    def __init__(self, target_canvases: dict[str, object], target_labels: dict[str, str] | None = None):
        super().__init__()
        self._target_canvases = dict(target_canvases)
        self._target_labels = dict(target_labels or {})
        self._current_target_id = next(iter(self._target_canvases.keys()), None)
        for target_id, canvas in self._target_canvases.items():
            canvas.canvas_left_clicked.connect(lambda tid=target_id: self.set_current_target(tid))
            canvas.layer_manager.active_layer_changed.connect(self.changed.emit)
            canvas.layer_manager.layer_style_changed.connect(lambda *_: self.changed.emit())
            canvas.layer_manager.layer_display_changed.connect(lambda *_: self.changed.emit())

    def available_targets(self) -> list[tuple[str, str]]:
        return [
            (target_id, self._target_labels.get(target_id, target_id))
            for target_id in self._target_canvases.keys()
        ]

    def current_target_id(self) -> str | None:
        return self._current_target_id

    def set_current_target(self, target_id: str) -> None:
        if target_id not in self._target_canvases:
            return
        self._current_target_id = target_id
        canvas = self._target_canvases[target_id]
        canvas.layer_manager.set_active_layer(canvas.BASE_LAYER_ID)
        self.changed.emit()

    def current_layer(self):
        canvas = self._target_canvases.get(self._current_target_id)
        if canvas is None:
            return None
        state = canvas.layer_manager.layer(canvas.BASE_LAYER_ID)
        return None if state is None else state.layer

    def current_layer_manager(self):
        canvas = self._target_canvases.get(self._current_target_id)
        return None if canvas is None else canvas.layer_manager


class LayerManagerRenderBinding(RenderBindingBase):
    def __init__(self, layer_manager):
        super().__init__()
        self.layer_manager = layer_manager
        self.layer_manager.active_layer_changed.connect(self.changed.emit)
        self.layer_manager.layer_style_changed.connect(lambda *_: self.changed.emit())
        self.layer_manager.layer_display_changed.connect(lambda *_: self.changed.emit())
        self.layer_manager.layer_order_changed.connect(self.changed.emit)

    def current_target_id(self) -> str | None:
        return self.layer_manager.active_layer_id()

    def set_current_target(self, target_id: str) -> None:
        self.layer_manager.set_active_layer(target_id)
        self.changed.emit()

    def current_layer(self):
        state = self.layer_manager.layer(self.layer_manager.active_layer_id())
        return None if state is None else state.layer

    def current_layer_manager(self):
        return self.layer_manager


class RenderSidebarWidget(QWidget):
    renderer_type_changed = Signal(str)
    target_changed = Signal(str)
    display_settings_changed = Signal()
    style_reset_requested = Signal()
    style_save_requested = Signal()
    style_load_requested = Signal()
    auto_scan_unique_requested = Signal()
    categorical_style_changed = Signal()
    db_toggled = Signal(bool)

    def __init__(self, mode: str = "simple", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.setMinimumWidth(172)
        self.setMaximumWidth(280)
        self._categorical_block = False
        self._source_nodata_value = None
        self._band_count = 1
        self._manual_range_just_enabled = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        self.target_combo = QComboBox()
        self.target_combo.currentIndexChanged.connect(self._emit_target_changed)

        self.renderer_group = QGroupBox("渲染")
        renderer_layout = QFormLayout(self.renderer_group)
        renderer_layout.setContentsMargins(6, 6, 6, 6)
        renderer_layout.setHorizontalSpacing(6)
        renderer_layout.setVerticalSpacing(4)
        renderer_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        if self.mode == "multi_target":
            renderer_layout.addRow("窗口:", self.target_combo)
        self.renderer_combo = QComboBox()
        self.renderer_combo.currentTextChanged.connect(self._emit_renderer_type_changed)
        renderer_layout.addRow("类型:", self.renderer_combo)
        self.nodata_override_check = QCheckBox("")
        self.nodata_override_check.toggled.connect(self._on_nodata_override_toggled)
        self.nodata_override_check.toggled.connect(lambda _checked: self.display_settings_changed.emit())
        self.nodata_edit = QLineEdit()
        self.nodata_edit.setPlaceholderText("Null")
        self.nodata_edit.setMinimumWidth(56)
        self.nodata_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.nodata_edit.editingFinished.connect(lambda: self.display_settings_changed.emit())
        nodata_row = QWidget()
        nodata_row_layout = QHBoxLayout(nodata_row)
        nodata_row_layout.setContentsMargins(0, 0, 0, 0)
        nodata_row_layout.setSpacing(4)
        nodata_label = QLabel("NoData:")
        nodata_row_layout.addWidget(self.nodata_override_check, 0, Qt.AlignLeft)
        nodata_row_layout.addWidget(nodata_label, 0, Qt.AlignLeft)
        nodata_row_layout.addWidget(self.nodata_edit, 1)
        renderer_layout.addRow(nodata_row)
        layout.addWidget(self.renderer_group)

        self.display_group = QGroupBox("图层显示")
        display_layout = QFormLayout(self.display_group)
        self.visible_check = QCheckBox("显示图层")
        self.visible_check.toggled.connect(lambda _checked: self.display_settings_changed.emit())
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.0, 100.0)
        self.opacity_spin.setSuffix("%")
        self.opacity_spin.setDecimals(1)
        self.opacity_spin.setValue(100.0)
        self.opacity_spin.valueChanged.connect(lambda _value: self.display_settings_changed.emit())
        self.blend_mode_combo = QComboBox()
        for value, label in BLEND_MODE_ITEMS:
            self.blend_mode_combo.addItem(label, value)
        self.blend_mode_combo.currentIndexChanged.connect(lambda _index: self.display_settings_changed.emit())
        self.alpha_band_spin = QSpinBox()
        self.alpha_band_spin.setRange(0, 999)
        self.alpha_band_spin.valueChanged.connect(lambda _value: self.display_settings_changed.emit())
        self.mask_enabled_check = QCheckBox("启用 Mask")
        self.mask_enabled_check.toggled.connect(lambda _checked: self.display_settings_changed.emit())
        self.resampling_in_combo = QComboBox()
        self.resampling_out_combo = QComboBox()
        for combo in (self.resampling_in_combo, self.resampling_out_combo):
            for value, label in RESAMPLING_ITEMS:
                combo.addItem(label, value)
            combo.currentIndexChanged.connect(lambda _index: self.display_settings_changed.emit())
        self.display_group.setVisible(False)

        self.status_group = QGroupBox("说明")
        status_layout = QVBoxLayout(self.status_group)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        status_layout.addWidget(self.status_label)
        self.status_group.setVisible(False)

        self.render_settings = RenderSettingsWidget(compact=True)
        self.render_settings.auto_range_check.setText("")

        self.controls_group = QGroupBox("参数")
        controls_layout = QVBoxLayout(self.controls_group)
        controls_layout.setContentsMargins(6, 6, 6, 6)
        controls_layout.setSpacing(4)
        # 隐藏旧显示模式下拉（灰度/RGB/晕渲地貌），由“渲染类型”统一控制
        self.render_settings.display_mode_combo.setVisible(False)
        # 波段选择改为下拉列表
        self.gray_band_combo = QComboBox()
        self.band_r_combo = QComboBox()
        self.band_g_combo = QComboBox()
        self.band_b_combo = QComboBox()
        for combo in (self.gray_band_combo, self.band_r_combo, self.band_g_combo, self.band_b_combo, self.renderer_combo):
            combo.setMinimumWidth(62)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for combo in (self.gray_band_combo, self.band_r_combo, self.band_g_combo, self.band_b_combo):
            combo.currentIndexChanged.connect(self._on_band_combo_changed)
        band_form = QFormLayout()
        self._band_form = band_form
        band_form.setContentsMargins(0, 0, 0, 0)
        band_form.setSpacing(3)
        band_form.setHorizontalSpacing(6)
        band_form.setVerticalSpacing(3)
        band_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        band_form.addRow("波段:", self.gray_band_combo)
        band_form.addRow("R:", self.band_r_combo)
        band_form.addRow("G:", self.band_g_combo)
        band_form.addRow("B:", self.band_b_combo)
        controls_layout.addLayout(band_form)
        stretch_row = QWidget()
        stretch_row_layout = QHBoxLayout(stretch_row)
        stretch_row_layout.setContentsMargins(0, 0, 0, 0)
        stretch_row_layout.setSpacing(4)
        stretch_row_layout.addWidget(QLabel("拉伸方式"))
        self.render_settings.stretch_combo.setMinimumWidth(80)
        self.render_settings.stretch_combo.setMaximumWidth(16777215)
        self.render_settings.stretch_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        stretch_row_layout.addWidget(self.render_settings.stretch_combo, 1)
        for spin in (
            self.render_settings.percent_low_spin,
            self.render_settings.percent_high_spin,
            self.render_settings.std_dev_spin,
        ):
            spin.setMaximumWidth(16777215)
            spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.color_group = QWidget()
        color_layout = QVBoxLayout(self.color_group)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(3)
        self.colormap_combo = ColormapComboBox()
        self.color_ramp_reverse_check = QCheckBox("反向")
        self.color_ramp_reverse_check.toggled.connect(self._emit_legacy_editor_changed)
        color_row = QWidget()
        color_row_layout = QHBoxLayout(color_row)
        color_row_layout.setContentsMargins(0, 0, 0, 0)
        color_row_layout.setSpacing(6)
        color_row_layout.addWidget(QLabel("色带"))
        color_row_layout.addWidget(self.colormap_combo, 1)
        self.colormap_combo.setMinimumWidth(84)
        self.colormap_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        color_layout.addWidget(color_row)
        color_reverse_row = QWidget()
        color_reverse_layout = QHBoxLayout(color_reverse_row)
        color_reverse_layout.setContentsMargins(0, 0, 0, 0)
        color_reverse_layout.setSpacing(4)
        color_reverse_layout.addWidget(self.color_ramp_reverse_check)
        color_reverse_layout.addStretch(1)
        color_layout.addWidget(color_reverse_row)
        controls_layout.addWidget(self.color_group)
        controls_layout.addWidget(stretch_row)
        controls_layout.addWidget(self.render_settings.stretch_param_widget)
        self.render_settings.stretch_param_widget.setMinimumWidth(72)

        self.range_group = QWidget()
        range_layout = QVBoxLayout(self.range_group)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(4)
        minmax_row = QWidget()
        minmax_layout = QHBoxLayout(minmax_row)
        minmax_layout.setContentsMargins(0, 0, 0, 0)
        minmax_layout.setSpacing(3)
        minmax_layout.addWidget(self.render_settings.auto_range_check)
        minmax_layout.addWidget(QLabel("最小"))
        minmax_layout.addWidget(self.render_settings.min_spin)
        minmax_layout.addWidget(QLabel("最大"))
        minmax_layout.addWidget(self.render_settings.max_spin)
        self.render_settings.auto_range_check.setText("")
        self.render_settings.auto_range_check.toggled.connect(self._on_manual_range_toggled)
        self.render_settings.min_spin.setMinimumWidth(56)
        self.render_settings.max_spin.setMinimumWidth(56)
        self.render_settings.min_spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.render_settings.max_spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        gamma_row = QWidget()
        tone_layout = QHBoxLayout(gamma_row)
        tone_layout.setContentsMargins(0, 0, 0, 0)
        tone_layout.setSpacing(3)
        self.gamma_r_spin = QDoubleSpinBox()
        self.gamma_g_spin = QDoubleSpinBox()
        self.gamma_b_spin = QDoubleSpinBox()
        for spin in (self.render_settings.gamma_spin, self.gamma_r_spin, self.gamma_g_spin, self.gamma_b_spin):
            spin.setRange(0.1, 5.0)
            spin.setSingleStep(0.1)
            spin.valueChanged.connect(self._emit_legacy_editor_changed)
        tone_layout.addWidget(QLabel("Gamma"))
        tone_layout.addWidget(self.render_settings.gamma_spin, 1)
        tone_layout.addWidget(self.gamma_r_spin, 1)
        tone_layout.addWidget(self.gamma_g_spin, 1)
        tone_layout.addWidget(self.gamma_b_spin, 1)
        self.db_check = QCheckBox("转dB")
        self.db_check.toggled.connect(lambda checked: self.db_toggled.emit(bool(checked)))
        self.render_settings.gamma_spin.setMinimumWidth(48)
        self.render_settings.gamma_spin.setMaximumWidth(16777215)
        self.gamma_r_spin.setMinimumWidth(48)
        self.gamma_r_spin.setMaximumWidth(16777215)
        self.gamma_g_spin.setMinimumWidth(48)
        self.gamma_g_spin.setMaximumWidth(16777215)
        self.gamma_b_spin.setMinimumWidth(48)
        self.gamma_b_spin.setMaximumWidth(16777215)
        self.render_settings.gamma_spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.gamma_r_spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.gamma_g_spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.gamma_b_spin.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        range_layout.addWidget(minmax_row)
        range_layout.addWidget(gamma_row)
        db_row = QWidget()
        db_layout = QHBoxLayout(db_row)
        db_layout.setContentsMargins(0, 0, 0, 0)
        db_layout.setSpacing(3)
        db_layout.addWidget(self.db_check)
        db_layout.addStretch(1)
        range_layout.addWidget(db_row)
        controls_layout.addWidget(self.range_group)
        self.colormap_combo.currentTextChanged.connect(self._emit_legacy_editor_changed)
        layout.addWidget(self.controls_group)

        self.hillshade_group = QGroupBox("晕渲参数")
        hillshade_layout = QVBoxLayout(self.hillshade_group)
        hillshade_layout.setContentsMargins(6, 6, 6, 6)
        hillshade_layout.setSpacing(4)
        self.hillshade_azimuth_spin = QDoubleSpinBox()
        self.hillshade_azimuth_spin.setRange(0.0, 360.0)
        self.hillshade_azimuth_spin.setValue(315.0)
        self.hillshade_azimuth_spin.setSuffix("°")
        self.hillshade_altitude_spin = QDoubleSpinBox()
        self.hillshade_altitude_spin.setRange(0.0, 90.0)
        self.hillshade_altitude_spin.setValue(45.0)
        self.hillshade_altitude_spin.setSuffix("°")
        self.hillshade_zfactor_spin = QDoubleSpinBox()
        self.hillshade_zfactor_spin.setRange(0.000001, 100.0)
        self.hillshade_zfactor_spin.setDecimals(6)
        self.hillshade_zfactor_spin.setValue(1.0)
        az_alt_row = QWidget()
        az_alt_layout = QHBoxLayout(az_alt_row)
        az_alt_layout.setContentsMargins(0, 0, 0, 0)
        az_alt_layout.setSpacing(4)
        az_alt_layout.addWidget(QLabel("方位角"))
        az_alt_layout.addWidget(self.hillshade_azimuth_spin, 1)
        az_alt_layout.addWidget(QLabel("高度角"))
        az_alt_layout.addWidget(self.hillshade_altitude_spin, 1)
        z_row = QWidget()
        z_row_layout = QHBoxLayout(z_row)
        z_row_layout.setContentsMargins(0, 0, 0, 0)
        z_row_layout.setSpacing(4)
        z_row_layout.addWidget(QLabel("Z比例因子"))
        z_row_layout.addWidget(self.hillshade_zfactor_spin, 1)
        hillshade_layout.addWidget(az_alt_row)
        hillshade_layout.addWidget(z_row)
        layout.addWidget(self.hillshade_group)

        self.categorical_group = QGroupBox("分类渲染")
        categorical_layout = QVBoxLayout(self.categorical_group)
        categorical_toolbar = QHBoxLayout()
        self.auto_scan_button = QPushButton("自动扫描")
        self.auto_scan_button.clicked.connect(self.auto_scan_unique_requested.emit)
        self.undefined_color_button = QPushButton("未定义颜色")
        self.undefined_color_button.clicked.connect(self._choose_undefined_color)
        categorical_toolbar.addWidget(self.auto_scan_button)
        categorical_toolbar.addWidget(self.undefined_color_button)
        categorical_layout.addLayout(categorical_toolbar)
        self.categorical_table = QTableWidget(0, 4)
        self.categorical_table.setHorizontalHeaderLabels(["值", "标签", "颜色", "可见"])
        self.categorical_table.horizontalHeader().setStretchLastSection(True)
        self.categorical_table.itemChanged.connect(self._on_categorical_item_changed)
        self.categorical_table.cellDoubleClicked.connect(self._on_categorical_color_double_clicked)
        categorical_layout.addWidget(self.categorical_table)
        layout.addWidget(self.categorical_group)

        self.style_group = QGroupBox("样式")
        style_layout = QHBoxLayout(self.style_group)
        style_layout.setContentsMargins(6, 6, 6, 6)
        style_layout.setSpacing(6)
        self.reset_style_button = QPushButton("重置")
        self.save_style_button = QPushButton("保存")
        self.load_style_button = QPushButton("加载")
        for btn in (self.reset_style_button, self.save_style_button, self.load_style_button):
            btn.setMinimumWidth(56)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.reset_style_button.clicked.connect(self.style_reset_requested.emit)
        self.save_style_button.clicked.connect(self.style_save_requested.emit)
        self.load_style_button.clicked.connect(self.style_load_requested.emit)
        style_layout.addWidget(self.reset_style_button)
        style_layout.addWidget(self.save_style_button)
        style_layout.addWidget(self.load_style_button)
        layout.addWidget(self.style_group)

        self.image_info_group = QGroupBox("图像基本信息")
        image_info_layout = QVBoxLayout(self.image_info_group)
        image_info_layout.setContentsMargins(6, 6, 6, 6)
        image_info_layout.setSpacing(3)
        self.image_info_content = QWidget()
        info_form = QFormLayout(self.image_info_content)
        info_form.setContentsMargins(0, 0, 0, 0)
        info_form.setHorizontalSpacing(6)
        info_form.setVerticalSpacing(2)
        self._info_labels: dict[str, QLabel] = {}
        for key, title in (
            ("path", "路径"),
            ("size", "尺寸"),
            ("crs", "坐标系"),
            ("resolution", "分辨率"),
            ("bands", "波段数"),
            ("dtype", "数据类型"),
            ("nodata", "NoData"),
            ("minmax", "Min/Max"),
        ):
            value = QLabel("-")
            value.setWordWrap(False)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            value.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            value.setMinimumWidth(1)
            self._info_labels[key] = value
            info_form.addRow(f"{title}:", value)
        self.image_info_scroll = QScrollArea()
        self.image_info_scroll.setWidgetResizable(False)
        self.image_info_scroll.setWidget(self.image_info_content)
        self.image_info_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.image_info_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.image_info_content.setMinimumWidth(420)
        self.image_info_scroll.setMinimumHeight(170)
        self.image_info_scroll.setMaximumHeight(300)
        image_info_layout.addWidget(self.image_info_scroll)
        layout.addWidget(self.image_info_group)
        layout.addStretch(1)

        self.target_combo.setVisible(self.mode == "multi_target")
        for spin in (
            self.render_settings.min_spin,
            self.render_settings.max_spin,
            self.render_settings.gamma_spin,
            self.render_settings.percent_low_spin,
            self.render_settings.percent_high_spin,
            self.render_settings.std_dev_spin,
            self.gamma_r_spin,
            self.gamma_g_spin,
            self.gamma_b_spin,
            self.hillshade_azimuth_spin,
            self.hillshade_altitude_spin,
            self.hillshade_zfactor_spin,
        ):
            spin.setButtonSymbols(QAbstractSpinBox.NoButtons)
        for spin in (
            self.render_settings.percent_low_spin,
            self.render_settings.percent_high_spin,
            self.render_settings.std_dev_spin,
            self.render_settings.min_spin,
            self.render_settings.max_spin,
            self.render_settings.gamma_spin,
            self.gamma_r_spin,
            self.gamma_g_spin,
            self.gamma_b_spin,
        ):
            spin.setMinimumWidth(46)
        self._refresh_renderer_type_items(1, preferred_mode=None)
        for widget in (
            self.render_settings,
            self.hillshade_azimuth_spin,
            self.hillshade_altitude_spin,
            self.hillshade_zfactor_spin,
        ):
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self._emit_legacy_editor_changed)
            elif hasattr(widget, "currentIndexChanged"):
                widget.currentIndexChanged.connect(self._emit_legacy_editor_changed)
        self.set_raster_edit_enabled(False, None)
        self._update_section_visibility()

    def _emit_renderer_type_changed(self, text: str) -> None:
        self._apply_render_mode_to_sidebar(text)
        self.renderer_type_changed.emit(text)

    def _emit_legacy_editor_changed(self, *_args) -> None:
        self.render_settings.settings_changed.emit()

    def _emit_target_changed(self, index: int) -> None:
        if index < 0:
            return
        target_id = self.target_combo.itemData(index)
        if target_id:
            self.target_changed.emit(str(target_id))

    def _on_band_combo_changed(self, *_args) -> None:
        if self._categorical_block:
            return
        self.render_settings.gray_band_spin.setValue(max(1, self.gray_band_combo.currentIndex() + 1))
        self.render_settings.band_r_spin.setValue(max(1, self.band_r_combo.currentIndex() + 1))
        self.render_settings.band_g_spin.setValue(max(1, self.band_g_combo.currentIndex() + 1))
        self.render_settings.band_b_spin.setValue(max(1, self.band_b_combo.currentIndex() + 1))
        self._emit_legacy_editor_changed()

    def _on_nodata_override_toggled(self, checked: bool) -> None:
        self.nodata_edit.setEnabled(bool(checked))
        self.nodata_edit.setStyleSheet("" if checked else "color: gray;")
        if not checked:
            self.nodata_edit.blockSignals(True)
            self.nodata_edit.setText("Null" if self._source_nodata_value is None else str(self._source_nodata_value))
            self.nodata_edit.blockSignals(False)

    def _on_manual_range_toggled(self, checked: bool) -> None:
        manual = bool(checked)
        self._manual_range_just_enabled = manual
        self.render_settings.min_spin.setEnabled(manual)
        self.render_settings.max_spin.setEnabled(manual)
        self.render_settings.min_spin.setStyleSheet("" if manual else "color: gray;")
        self.render_settings.max_spin.setStyleSheet("" if manual else "color: gray;")

    def consume_manual_range_just_enabled(self) -> bool:
        value = bool(self._manual_range_just_enabled)
        self._manual_range_just_enabled = False
        return value

    def _on_categorical_item_changed(self, _item) -> None:
        if self._categorical_block:
            return
        self.categorical_style_changed.emit()

    def _on_categorical_color_double_clicked(self, row: int, column: int) -> None:
        if column != 2:
            return
        item = self.categorical_table.item(row, column)
        if item is None:
            return
        color = QColor(item.data(Qt.UserRole) or "#000000")
        chosen = QColorDialog.getColor(color, self, "选择颜色")
        if not chosen.isValid():
            return
        self._categorical_block = True
        try:
            _update_color_item(item, chosen)
        finally:
            self._categorical_block = False
        self.categorical_style_changed.emit()

    def _choose_undefined_color(self) -> None:
        current = QColor(self.undefined_color_button.property("rgba_hex") or "#00000000")
        chosen = QColorDialog.getColor(current, self, "选择未定义值颜色")
        if not chosen.isValid():
            return
        self.set_undefined_color(tuple(chosen.getRgb()))
        self.categorical_style_changed.emit()

    def set_target_options(self, options: list[tuple[str, str]]) -> None:
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        for target_id, label in options or []:
            self.target_combo.addItem(label, target_id)
        self.target_combo.blockSignals(False)
        self.target_combo.setVisible(self.mode == "multi_target")

    def set_current_target(self, target_id: str | None) -> None:
        if target_id is None:
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentIndex(-1)
            self.target_combo.blockSignals(False)
            return
        index = self.target_combo.findData(target_id)
        if index >= 0:
            self.target_combo.blockSignals(True)
            self.target_combo.setCurrentIndex(index)
            self.target_combo.blockSignals(False)

    def set_layer(self, layer) -> None:
        if not isValid(self):
            return
        if layer is None:
            self._source_nodata_value = None
            self.renderer_combo.blockSignals(True)
            self.renderer_combo.setCurrentIndex(-1)
            self.renderer_combo.blockSignals(False)
            self.populate_categorical_table(None)
            self._set_image_info(None)
            self._update_section_visibility()
            return
        if not all(
            isValid(widget)
            for widget in (self.renderer_combo, self.gray_band_combo, self.band_r_combo, self.band_g_combo, self.band_b_combo)
        ):
            return
        self._source_nodata_value = getattr(layer.metadata, "nodata", None)
        self._band_count = max(1, int(layer.metadata.band_count or 1))
        self._populate_band_combos(self._band_count)
        self._refresh_renderer_type_items(self._band_count, preferred_mode=_render_mode_from_layer(layer))
        self.renderer_combo.blockSignals(True)
        self.renderer_combo.setCurrentText(_render_mode_from_layer(layer))
        self.renderer_combo.blockSignals(False)
        LegacyRenderSettingsAdapter.apply_layer_to_widget(self, layer)
        self._set_image_info(layer)

    def set_raster_edit_enabled(self, enabled: bool, message: str | None = None) -> None:
        for widget in (
            self.renderer_combo,
            self.nodata_edit,
            self.nodata_override_check,
            self.gray_band_combo,
            self.band_r_combo,
            self.band_g_combo,
            self.band_b_combo,
            self.render_settings.stretch_combo,
            self.render_settings.stretch_param_widget,
            self.render_settings.auto_range_check,
            self.render_settings.min_spin,
            self.render_settings.max_spin,
            self.render_settings.gamma_spin,
            self.gamma_r_spin,
            self.gamma_g_spin,
            self.gamma_b_spin,
            self.colormap_combo,
            self.color_ramp_reverse_check,
            self.hillshade_azimuth_spin,
            self.hillshade_altitude_spin,
            self.hillshade_zfactor_spin,
            self.categorical_table,
            self.auto_scan_button,
            self.undefined_color_button,
            self.reset_style_button,
            self.save_style_button,
            self.load_style_button,
        ):
            widget.setEnabled(enabled)
        self.status_label.setText("")
        self.status_group.setVisible(False)
        self._update_section_visibility()

    def current_render_mode(self) -> str:
        return self.renderer_combo.currentText() or RENDER_MODE_SINGLEBAND

    def current_singleband_symbol(self) -> str:
        return "pseudocolor"

    def _apply_render_mode_to_sidebar(self, mode: str) -> None:
        if self.renderer_combo.currentText() != mode:
            self.renderer_combo.blockSignals(True)
            self.renderer_combo.setCurrentText(mode)
            self.renderer_combo.blockSignals(False)
        old_block = getattr(self.render_settings, "_block_signals", False)
        self.render_settings._block_signals = True
        if mode == RENDER_MODE_MULTIBAND:
            self.render_settings.display_mode_combo.setCurrentText("RGB")
        elif mode == RENDER_MODE_HILLSHADE:
            self.render_settings.display_mode_combo.setCurrentText("晕渲地貌")
        else:
            self.render_settings.display_mode_combo.setCurrentText("灰度")
        self.render_settings._block_signals = old_block
        self._update_section_visibility()

    def _update_section_visibility(self) -> None:
        mode = self.current_render_mode()
        show_multiband = mode == RENDER_MODE_MULTIBAND
        show_singleband = mode == RENDER_MODE_SINGLEBAND
        show_hillshade = mode == RENDER_MODE_HILLSHADE
        show_categorical = mode == RENDER_MODE_CATEGORICAL
        self.controls_group.setVisible(show_multiband or show_singleband or show_hillshade)
        self.range_group.setVisible(show_multiband or show_singleband or show_hillshade)
        self.color_group.setVisible(show_singleband or show_hillshade)
        self.hillshade_group.setVisible(show_hillshade)
        self.categorical_group.setVisible(show_categorical)

        self.render_settings.gray_band_label.setVisible(show_singleband or show_hillshade or show_categorical)
        self.render_settings.gray_band_spin.setVisible(show_singleband or show_hillshade or show_categorical)
        self.render_settings.rgb_label.setVisible(show_multiband)
        self.render_settings.band_r_spin.setVisible(show_multiband)
        self.render_settings.band_g_spin.setVisible(show_multiband)
        self.render_settings.band_b_spin.setVisible(show_multiband)
        if hasattr(self.render_settings, "g_label"):
            self.render_settings.g_label.setVisible(show_multiband)
        if hasattr(self.render_settings, "b_label"):
            self.render_settings.b_label.setVisible(show_multiband)

        self.gray_band_combo.setVisible(show_singleband or show_hillshade or show_categorical)
        self.band_r_combo.setVisible(show_multiband)
        self.band_g_combo.setVisible(show_multiband)
        self.band_b_combo.setVisible(show_multiband)
        self.gamma_r_spin.setVisible(show_multiband)
        self.gamma_g_spin.setVisible(show_multiband)
        self.gamma_b_spin.setVisible(show_multiband)
        self.render_settings.gamma_spin.setVisible(not show_multiband)
        self.db_check.setVisible(show_singleband)
        self.db_check.setEnabled(show_singleband)
        self.color_ramp_reverse_check.setVisible(show_singleband or show_hillshade)
        self.color_group.setVisible(show_singleband or show_hillshade)
        for combo, visible in (
            (self.gray_band_combo, show_singleband or show_hillshade or show_categorical),
            (self.band_r_combo, show_multiband),
            (self.band_g_combo, show_multiband),
            (self.band_b_combo, show_multiband),
        ):
            label = self._band_form.labelForField(combo)
            if label is not None:
                label.setVisible(visible)

    def apply_display_settings(self, display_settings: LayerDisplaySettings) -> None:
        self.nodata_override_check.blockSignals(True)
        self.nodata_edit.blockSignals(True)
        override_nodata = not bool(display_settings.nodata_policy.use_source_nodata)
        self.nodata_override_check.setChecked(override_nodata)
        shown_nodata = (
            display_settings.nodata_policy.value if override_nodata else self._source_nodata_value
        )
        self.nodata_edit.setText("Null" if shown_nodata is None else str(shown_nodata))
        self.nodata_edit.setEnabled(override_nodata)
        self.nodata_override_check.blockSignals(False)
        self.nodata_edit.blockSignals(False)
        self._on_manual_range_toggled(self.render_settings.auto_range_check.isChecked())
        self._manual_range_just_enabled = False

    def current_display_settings(self, fallback: LayerDisplaySettings) -> LayerDisplaySettings:
        nodata_value = self.nodata_edit.text().strip()
        override = self.nodata_override_check.isChecked()
        parsed_nodata = self._source_nodata_value
        use_source_nodata = not override
        if override:
            if nodata_value == "" or nodata_value.lower() == "null":
                parsed_nodata = None
            else:
                try:
                    parsed_nodata = float(nodata_value)
                except ValueError:
                    parsed_nodata = nodata_value
        return replace(
            fallback,
            nodata_policy=replace(
                fallback.nodata_policy,
                enabled=True,
                value=parsed_nodata,
                use_source_nodata=use_source_nodata,
            ),
        )

    def _set_image_info(self, layer) -> None:
        def _set(key: str, value) -> None:
            label = self._info_labels.get(key)
            if label is not None:
                label.setText("-" if value in (None, "", ()) else str(value))

        if layer is None:
            for key in self._info_labels:
                _set(key, "-")
            return
        meta = getattr(layer, "metadata", None)
        _set("name", getattr(layer, "name", None))
        _set("path", getattr(meta, "path", None))
        _set("size", f"{getattr(meta, 'width', '-') } x {getattr(meta, 'height', '-')}")
        _set("crs", _crs_brief(getattr(meta, "crs_wkt", None)))
        _set("resolution", getattr(meta, "resolution", None))
        _set("bands", getattr(meta, "band_count", None))
        _set("dtype", getattr(meta, "dtype", None))
        _set("nodata", getattr(meta, "nodata", None))
        minmax_value = None
        source = getattr(layer, "source", None)
        if source is not None and hasattr(source, "band_minmax"):
            try:
                mm = source.band_minmax(1)
                if mm is not None:
                    minmax_value = f"{float(mm[0]):.4f} / {float(mm[1]):.4f}"
            except Exception:
                minmax_value = None
        _set("minmax", minmax_value)

    def populate_categorical_table(self, style) -> None:
        self._categorical_block = True
        try:
            self.categorical_table.setRowCount(0)
            if isinstance(style, UniqueValueRenderStyle):
                for item in style.items:
                    self._append_category_row(item.value, item.label or str(item.value), item.color, item.visible)
                self.set_undefined_color(style.undefined_color)
            elif isinstance(style, PalettedRenderStyle):
                for index, color in enumerate(style.palette):
                    self._append_category_row(index, str(index), color, True)
                self.set_undefined_color(style.default_color)
            else:
                self.set_undefined_color((0, 0, 0, 0))
        finally:
            self._categorical_block = False

    def set_undefined_color(self, rgba: tuple[int, int, int, int]) -> None:
        rgba_hex = _rgba_to_hex(rgba)
        self.undefined_color_button.setProperty("rgba_hex", rgba_hex)
        self.undefined_color_button.setStyleSheet(f"background-color: {rgba_hex};")

    def current_categorical_payload(self) -> dict:
        items = []
        for row in range(self.categorical_table.rowCount()):
            value_item = self.categorical_table.item(row, 0)
            label_item = self.categorical_table.item(row, 1)
            color_item = self.categorical_table.item(row, 2)
            visible_item = self.categorical_table.item(row, 3)
            if value_item is None or color_item is None:
                continue
            items.append(
                UniqueValueItem(
                    value=_parse_table_value(value_item.text()),
                    label=(label_item.text() if label_item is not None else ""),
                    color=_hex_to_rgba(color_item.data(Qt.UserRole) or "#00000000"),
                    visible=visible_item is None or visible_item.checkState() == Qt.Checked,
                )
            )
        return {
            "items": tuple(items),
            "undefined_color": _hex_to_rgba(self.undefined_color_button.property("rgba_hex") or "#00000000"),
        }

    def _append_category_row(self, value, label: str, rgba, visible: bool) -> None:
        row = self.categorical_table.rowCount()
        self.categorical_table.insertRow(row)
        value_item = QTableWidgetItem(str(value))
        label_item = QTableWidgetItem(label)
        color_item = QTableWidgetItem()
        visible_item = QTableWidgetItem("")
        visible_item.setFlags(visible_item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        visible_item.setCheckState(Qt.Checked if visible else Qt.Unchecked)
        self.categorical_table.setItem(row, 0, value_item)
        self.categorical_table.setItem(row, 1, label_item)
        self.categorical_table.setItem(row, 2, color_item)
        self.categorical_table.setItem(row, 3, visible_item)
        _update_color_item(color_item, QColor(*rgba[:4]))

    def _populate_band_combos(self, band_count: int) -> None:
        if not all(isValid(combo) for combo in (self.gray_band_combo, self.band_r_combo, self.band_g_combo, self.band_b_combo)):
            return
        self._categorical_block = True
        labels = [f"波段 {index}" for index in range(1, max(1, band_count) + 1)]
        for combo in (self.gray_band_combo, self.band_r_combo, self.band_g_combo, self.band_b_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(labels)
            combo.blockSignals(False)
        self.gray_band_combo.setCurrentIndex(0)
        self.band_r_combo.setCurrentIndex(0)
        self.band_g_combo.setCurrentIndex(1 if band_count >= 2 else 0)
        self.band_b_combo.setCurrentIndex(2 if band_count >= 3 else 0)
        self._categorical_block = False

    def _refresh_renderer_type_items(self, band_count: int, preferred_mode: str | None) -> None:
        if band_count <= 1:
            options = [RENDER_MODE_SINGLEBAND, RENDER_MODE_CATEGORICAL, RENDER_MODE_HILLSHADE]
        else:
            options = [RENDER_MODE_MULTIBAND, RENDER_MODE_SINGLEBAND]
        current = preferred_mode if preferred_mode in options else options[0]
        self.renderer_combo.blockSignals(True)
        self.renderer_combo.clear()
        self.renderer_combo.addItems(options)
        self.renderer_combo.setCurrentText(current)
        self.renderer_combo.blockSignals(False)

    def set_db_checked(self, checked: bool) -> None:
        self.db_check.blockSignals(True)
        self.db_check.setChecked(bool(checked))
        self.db_check.blockSignals(False)


class RenderSidebarController:
    def __init__(self, widget: RenderSidebarWidget, binding):
        self.widget = widget
        self.binding = binding
        self._block_updates = False
        self._disposed = False
        self.widget.target_changed.connect(self._on_target_changed)
        self.widget.renderer_type_changed.connect(self._on_renderer_type_changed)
        self.widget.render_settings.settings_changed.connect(self._on_editor_changed)
        self.widget.render_settings.suggest_colormap.connect(self._on_suggest_colormap)
        self.widget.display_settings_changed.connect(self._on_editor_changed)
        self.widget.style_reset_requested.connect(self._reset_style)
        self.widget.style_save_requested.connect(self._save_style)
        self.widget.style_load_requested.connect(self._load_style)
        self.widget.auto_scan_unique_requested.connect(self._auto_scan_unique_values)
        self.widget.categorical_style_changed.connect(self._on_editor_changed)
        for signal in self.binding.refresh_signals():
            signal.connect(self.refresh)
        self.widget.destroyed.connect(self.dispose)
        self.refresh()

    def dispose(self, *_args) -> None:
        if self._disposed:
            return
        self._disposed = True
        try:
            for signal in self.binding.refresh_signals():
                signal.disconnect(self.refresh)
        except Exception:
            pass

    def _alive(self) -> bool:
        return (not self._disposed) and self.widget is not None and isValid(self.widget)

    def close(self) -> None:
        self.dispose()

    def refresh(self) -> None:
        if self._block_updates or not self._alive():
            return
        try:
            options = self.binding.available_targets()
            if options:
                self.widget.set_target_options(options)
            self.widget.set_current_target(self.binding.current_target_id())
            layer = self.binding.current_layer()
            if layer is None:
                self.widget.set_layer(None)
                self.widget.set_raster_edit_enabled(False, None)
                return
            self._block_updates = True
            self.widget.set_layer(layer)
            self.widget.set_raster_edit_enabled(True)
        except RuntimeError:
            self.dispose()
        finally:
            self._block_updates = False

    def _on_target_changed(self, target_id: str) -> None:
        if self._block_updates or not self._alive():
            return
        self.binding.set_current_target(target_id)
        self.refresh()

    def _on_renderer_type_changed(self, renderer_type: str) -> None:
        if self._block_updates or not self._alive():
            return
        layer = self.binding.current_layer()
        manager = self.binding.current_layer_manager()
        if layer is None or manager is None:
            return
        self._block_updates = True
        try:
            normalized_renderer = _renderer_type_for_widget(self.widget, layer)
            new_style = migrate_style_on_renderer_switch(layer.render_style, normalized_renderer, metadata=layer.metadata)
            if normalized_renderer == "hillshade":
                if isinstance(new_style, HillshadeRenderStyle):
                    new_style = replace(new_style, color_ramp=ColorRampSettings(name="terrain"))
                self.widget.colormap_combo.blockSignals(True)
                self.widget.colormap_combo.setCurrentText("terrain")
                self.widget.colormap_combo.blockSignals(False)
            manager.set_render_style(layer.id, new_style)
        finally:
            self._block_updates = False
        self.refresh()

    def _on_suggest_colormap(self, colormap_name: str) -> None:
        if self._block_updates or not self._alive():
            return
        self.widget.colormap_combo.setCurrentText(colormap_name)

    def _build_style_from_widget(self, layer):
        target_mode = self.widget.current_render_mode()
        if target_mode == RENDER_MODE_CATEGORICAL:
            payload = self.widget.current_categorical_payload()
            if getattr(layer.render_style, "renderer_type", "") == "paletted" or getattr(layer.metadata, "has_color_table", False):
                palette = tuple(item.color for item in payload["items"])
                return PalettedRenderStyle(
                    band_indices=(int(self.widget.render_settings.gray_band_spin.value()),),
                    palette=palette,
                    default_color=payload["undefined_color"],
                )
            return UniqueValueRenderStyle(
                band_indices=(int(self.widget.render_settings.gray_band_spin.value()),),
                items=payload["items"],
                undefined_color=payload["undefined_color"],
            )

        config = LegacyRenderSettingsAdapter.widget_to_legacy_config(self.widget)
        style = legacy_config_to_style(config, layer.metadata)
        renderer_type = _renderer_type_for_widget(self.widget, layer)
        if getattr(style, "renderer_type", "") != renderer_type:
            style = migrate_style_on_renderer_switch(style, renderer_type, metadata=layer.metadata)
        if renderer_type == "singleband_pseudocolor":
            if isinstance(style, SinglebandGrayRenderStyle):
                style = SinglebandPseudoColorRenderStyle(
                    band_indices=style.band_indices,
                    gamma=style.gamma,
                    stretch=style.stretch,
                    color_ramp=ColorRampSettings(
                        name=self.widget.colormap_combo.currentText(),
                        reversed=self.widget.color_ramp_reverse_check.isChecked(),
                        discrete=False,
                    ),
                )
            elif isinstance(style, SinglebandPseudoColorRenderStyle):
                style = replace(
                    style,
                    color_ramp=replace(
                        style.color_ramp,
                        name=self.widget.colormap_combo.currentText(),
                        reversed=self.widget.color_ramp_reverse_check.isChecked(),
                        discrete=False,
                    ),
                )
        if renderer_type == "multiband" and isinstance(style, MultibandRenderStyle):
            style = replace(
                style,
                gamma=float(
                    (
                        self.widget.gamma_r_spin.value()
                        + self.widget.gamma_g_spin.value()
                        + self.widget.gamma_b_spin.value()
                    )
                    / 3.0
                ),
                channel_gamma=(
                    float(self.widget.gamma_r_spin.value()),
                    float(self.widget.gamma_g_spin.value()),
                    float(self.widget.gamma_b_spin.value()),
                ),
            )

        color_ramp = getattr(style, "color_ramp", None)
        if isinstance(style, SinglebandPseudoColorRenderStyle) and color_ramp is not None:
            return replace(
                style,
                color_ramp=replace(
                    color_ramp,
                    name=self.widget.colormap_combo.currentText(),
                    reversed=self.widget.color_ramp_reverse_check.isChecked(),
                    discrete=False,
                ),
            )
        if isinstance(style, HillshadeRenderStyle):
            return replace(
                style,
                color_ramp=ColorRampSettings(
                    name=self.widget.colormap_combo.currentText(),
                    reversed=self.widget.color_ramp_reverse_check.isChecked(),
                    discrete=False,
                ),
                azimuth=float(self.widget.hillshade_azimuth_spin.value()),
                altitude=float(self.widget.hillshade_altitude_spin.value()),
                z_factor=float(self.widget.hillshade_zfactor_spin.value()),
                relief_blend_mode="multiply",
            )
        return style

    def _on_editor_changed(self, *_args) -> None:
        if self._block_updates or not self._alive():
            return
        layer = self.binding.current_layer()
        manager = self.binding.current_layer_manager()
        if layer is None or manager is None:
            return
        self._block_updates = True
        try:
            style = self._build_style_from_widget(layer)
            if self.widget.consume_manual_range_just_enabled():
                style = self._resolve_manual_minmax_style(layer, style)
            display_settings = self.widget.current_display_settings(layer.display_settings)
            manager.set_render_style(layer.id, style)
            manager.set_display_settings(layer.id, display_settings)
        finally:
            self._block_updates = False

    def _resolve_manual_minmax_style(self, layer, style):
        stretch = getattr(style, "stretch", None)
        source = getattr(layer, "source", None)
        if stretch is None or source is None:
            return style
        if str(getattr(stretch, "stretch_type", "")) != "最大最小":
            return style
        band_indices = tuple(getattr(style, "band_indices", ()) or (1,))
        ranges = []
        settings = {"stretch_mode": "最大最小", "percent_clip": (2.0, 98.0), "std_dev_n": 2.0}
        for band_index in band_indices[:3]:
            value_range = None
            try:
                if hasattr(source, "band_value_range"):
                    value_range = source.band_value_range(int(band_index), settings)
            except Exception:
                value_range = None
            if value_range is None:
                try:
                    if hasattr(source, "band_minmax"):
                        value_range = source.band_minmax(int(band_index))
                except Exception:
                    value_range = None
            if value_range is not None:
                ranges.append((float(value_range[0]), float(value_range[1])))
        if not ranges:
            return style
        min_value = min(item[0] for item in ranges)
        max_value = max(item[1] for item in ranges)
        resolved_stretch = replace(stretch, auto_range=False, min_value=min_value, max_value=max_value)
        self.widget.render_settings.min_spin.blockSignals(True)
        self.widget.render_settings.max_spin.blockSignals(True)
        self.widget.render_settings.min_spin.setValue(float(min_value))
        self.widget.render_settings.max_spin.setValue(float(max_value))
        self.widget.render_settings.min_spin.blockSignals(False)
        self.widget.render_settings.max_spin.blockSignals(False)
        return replace(style, stretch=resolved_stretch)

    def _reset_style(self) -> None:
        layer = self.binding.current_layer()
        manager = self.binding.current_layer_manager()
        if layer is None or manager is None:
            return
        style = DefaultRenderStyleFactory.create(layer.metadata)
        display_settings = DefaultRenderStyleFactory.create_display_settings(layer.metadata)
        manager.set_render_style(layer.id, style)
        manager.set_display_settings(layer.id, display_settings)
        self.refresh()

    def _save_style(self) -> None:
        layer = self.binding.current_layer()
        if layer is None:
            return
        path, _ = QFileDialog.getSaveFileName(self.widget, "保存样式", "", "JSON (*.json)")
        if not path:
            return
        payload = serialize_style_bundle(layer.render_style, layer.display_settings)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

    def _load_style(self) -> None:
        layer = self.binding.current_layer()
        manager = self.binding.current_layer_manager()
        if layer is None or manager is None:
            return
        path, _ = QFileDialog.getOpenFileName(self.widget, "加载样式", "", "JSON (*.json)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        style, display_settings = deserialize_style_bundle(payload)
        manager.set_render_style(layer.id, style)
        manager.set_display_settings(layer.id, display_settings)
        self.refresh()

    def _auto_scan_unique_values(self) -> None:
        layer = self.binding.current_layer()
        manager = self.binding.current_layer_manager()
        if layer is None or manager is None:
            return
        source = getattr(layer, "source", None)
        if source is None:
            return
        width = int(layer.metadata.width)
        height = int(layer.metadata.height)
        sample_side = min(1024, max(width, height))
        request = RenderRequest(
            x=0.0,
            y=0.0,
            width=float(width),
            height=float(height),
            screen_width=min(sample_side, width),
            screen_height=min(sample_side, height),
            bands=(int(self.widget.render_settings.gray_band_spin.value()),),
        )
        if hasattr(source, "read_block"):
            data = source.read_block(request, style=layer.render_style).data
        elif hasattr(source, "read_window_native"):
            data = source.read_window_native(0, 0, min(sample_side, width), min(sample_side, height))
        else:
            return
        arr = np.asarray(data)
        if arr.ndim == 3:
            band = max(0, int(self.widget.render_settings.gray_band_spin.value()) - 1)
            arr = arr[:, :, min(band, arr.shape[2] - 1)]
        valid = np.isfinite(arr)
        nodata = layer.metadata.nodata
        if nodata is not None:
            try:
                if np.isnan(nodata):
                    valid &= ~np.isnan(arr)
                else:
                    valid &= arr != nodata
            except Exception:
                valid &= arr != nodata
        values = np.unique(arr[valid])[:256]
        items = []
        for index, value in enumerate(values):
            items.append(
                UniqueValueItem(
                    value=(value.item() if hasattr(value, "item") else value),
                    label=str(value.item() if hasattr(value, "item") else value),
                    color=_CATEGORY_COLORS[index % len(_CATEGORY_COLORS)],
                )
            )
        manager.set_render_style(
            layer.id,
            UniqueValueRenderStyle(
                band_indices=(int(self.widget.render_settings.gray_band_spin.value()),),
                items=tuple(items),
                undefined_color=(0, 0, 0, 0),
            ),
        )
        self.widget.renderer_combo.setCurrentText(RENDER_MODE_CATEGORICAL)
        self.refresh()


def _render_mode_from_layer(layer) -> str:
    renderer_type = getattr(getattr(layer, "render_style", None), "renderer_type", "")
    if renderer_type == "multiband":
        return RENDER_MODE_MULTIBAND
    if renderer_type == "hillshade":
        return RENDER_MODE_HILLSHADE
    if renderer_type in {"unique_value", "paletted"}:
        return RENDER_MODE_CATEGORICAL
    return RENDER_MODE_SINGLEBAND


def _renderer_type_for_widget(widget: RenderSidebarWidget, layer) -> str:
    mode = widget.current_render_mode()
    if mode == RENDER_MODE_MULTIBAND:
        return "multiband"
    if mode == RENDER_MODE_HILLSHADE:
        return "hillshade"
    if mode == RENDER_MODE_CATEGORICAL:
        if getattr(layer.metadata, "has_color_table", False) or getattr(getattr(layer, "render_style", None), "renderer_type", "") == "paletted":
            return "paletted"
        return "unique_value"
    return "singleband_pseudocolor"


def _set_combo_by_data(combo: QComboBox, value) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _rgba_to_hex(rgba) -> str:
    r, g, b, a = [int(v) for v in tuple(rgba)[:4]]
    return f"#{r:02x}{g:02x}{b:02x}{a:02x}"


def _hex_to_rgba(text: str) -> tuple[int, int, int, int]:
    color = QColor(text)
    if not color.isValid():
        return 0, 0, 0, 0
    return tuple(color.getRgb())


def _update_color_item(item: QTableWidgetItem, color: QColor) -> None:
    item.setData(Qt.UserRole, color.name(QColor.HexArgb))
    item.setText(color.name(QColor.HexArgb))
    item.setBackground(color)


def _parse_table_value(text: str):
    raw = text.strip()
    if raw == "":
        return raw
    try:
        if "." in raw or "e" in raw.lower():
            return float(raw)
        return int(raw)
    except ValueError:
        return raw


def _crs_brief(crs_wkt: str | None) -> str:
    text = (crs_wkt or "").strip()
    if not text:
        return "-"
    import re

    epsg = re.search(r'AUTHORITY\["EPSG","(\d+)"\]', text)
    name_match = re.search(r'^(?:PROJCRS|GEOGCRS|PROJCS|GEOGCS)\["([^"]+)"', text)
    epsg_text = f"EPSG:{epsg.group(1)}" if epsg else ""
    name_text = name_match.group(1) if name_match else ""
    if epsg_text and name_text:
        return f"{epsg_text} | {name_text}"
    if epsg_text:
        return epsg_text
    if name_text:
        return name_text
    return text[:120] + ("..." if len(text) > 120 else "")
