'''
Author: Yibo Yuan 2633669459@qq.com
Description: 图像渲染设置组件，包含拉伸方式、Gamma值、最大最小值、Colormap设置等

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import numpy as np
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QLabel,
                               QComboBox, QDoubleSpinBox, QCheckBox, QPushButton,
                               QGroupBox, QGridLayout, QSpinBox, QFrame, QApplication)
from PySide6.QtCore import QEvent, Signal, Qt


class DeferredApplyDoubleSpinBox(QDoubleSpinBox):
    """在回车、步进或失焦时提交输入值，避免键入过程中频繁重绘。"""

    committed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setKeyboardTracking(False)
        self._committed_value = super().value()
        self.lineEdit().returnPressed.connect(self._commit_if_changed)

    def setValue(self, value):
        super().setValue(value)
        self._committed_value = super().value()

    def stepBy(self, steps):
        old_value = super().value()
        super().stepBy(steps)
        if super().value() != old_value:
            self._committed_value = super().value()
            self.committed.emit()

    def focusOutEvent(self, event):
        if self.lineEdit().isModified():
            self._commit_if_changed()
        super().focusOutEvent(event)

    def _commit_if_changed(self):
        self.interpretText()
        new_value = super().value()
        if new_value != self._committed_value:
            self._committed_value = new_value
            self.committed.emit()

    def commit_pending(self):
        if self.lineEdit().isModified():
            self._commit_if_changed()
            self.lineEdit().setModified(False)


class RenderSettingsWidget(QWidget):
    """图像渲染设置组件"""
    
    # 信号：设置变更时发出
    settings_changed = Signal()
    
    # 信号：建议切换colormap（用于晕渲地貌模式）
    suggest_colormap = Signal(str)  # 参数为建议的colormap名称
    
    # 拉伸方式定义
    STRETCH_MIN_MAX = "最大最小"
    STRETCH_PERCENT = "百分比截断"
    STRETCH_STD_DEV = "标准差"
    STRETCH_HISTOGRAM = "直方图均衡化"
    
    STRETCH_MODES = [
        STRETCH_MIN_MAX,
        STRETCH_PERCENT,
        STRETCH_STD_DEV,
        STRETCH_HISTOGRAM,
    ]
    
    def __init__(self, parent=None, compact=False):
        """
        Args:
            parent: 父窗口
            compact: 是否使用紧凑布局
        """
        super().__init__(parent)
        self.compact = compact
        self._block_signals = False
        
        # 默认设置
        self._stretch_mode = self.STRETCH_MIN_MAX  # 默认使用最大最小拉伸
        self._percent_low = 2.0
        self._percent_high = 98.0
        self._std_dev_n = 2.0
        self._gamma = 1.0
        self._value_min = 0.0
        self._value_max = 1.0
        self._auto_range = True
        self._colormap_reversed = False
        self._band_r = 1
        self._band_g = 2
        self._band_b = 3
        self._num_bands = 1
        self._display_mode = "灰度"  # "灰度" 或 "RGB"
        self._smooth_display = False  # 默认禁用平滑变换，显示栅格边界
        
        self._create_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        
    def _create_ui(self):
        """创建用户界面"""
        if self.compact:
            self._create_compact_ui()
        else:
            self._create_full_ui()
            
    def _create_compact_ui(self):
        """创建紧凑布局（用于工具栏）
        
        布局顺序：波段选择 | Colormap反向 | 拉伸 | 最大最小值 | Gamma
        注意：控件创建后由父组件来布局，这里只创建控件
        """
        # 不设置布局，因为父组件会直接使用这些控件
        # layout = QHBoxLayout(self)
        
        # ============ 1. 波段选择（最前面）============
        self.band_widget = QWidget()
        band_layout = QHBoxLayout(self.band_widget)
        band_layout.setContentsMargins(0, 0, 0, 0)
        band_layout.setSpacing(2)
        
        # 显示模式
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItems(["灰度", "RGB", "晕渲地貌"])
        self.display_mode_combo.setCurrentText(self._display_mode)
        self.display_mode_combo.currentTextChanged.connect(self._on_display_mode_changed)
        self.display_mode_combo.setToolTip("显示模式")
        self.display_mode_combo.setMaximumWidth(80)
        band_layout.addWidget(self.display_mode_combo)
        
        # 灰度波段选择
        self.gray_band_label = QLabel("波段:")
        band_layout.addWidget(self.gray_band_label)
        self.gray_band_spin = QSpinBox()
        self.gray_band_spin.setRange(1, 1)
        self.gray_band_spin.setValue(1)
        self.gray_band_spin.setMaximumWidth(50)
        self.gray_band_spin.valueChanged.connect(self._on_settings_changed)
        self.gray_band_spin.setToolTip("显示的波段")
        band_layout.addWidget(self.gray_band_spin)
        
        # RGB波段选择
        self.rgb_label = QLabel("R:")
        band_layout.addWidget(self.rgb_label)
        self.band_r_spin = QSpinBox()
        self.band_r_spin.setRange(1, 1)
        self.band_r_spin.setValue(1)
        self.band_r_spin.setMaximumWidth(45)
        self.band_r_spin.valueChanged.connect(self._on_settings_changed)
        band_layout.addWidget(self.band_r_spin)
        
        self.g_label = QLabel("G:")
        band_layout.addWidget(self.g_label)
        self.band_g_spin = QSpinBox()
        self.band_g_spin.setRange(1, 1)
        self.band_g_spin.setValue(2)
        self.band_g_spin.setMaximumWidth(45)
        self.band_g_spin.valueChanged.connect(self._on_settings_changed)
        band_layout.addWidget(self.band_g_spin)
        
        self.b_label = QLabel("B:")
        band_layout.addWidget(self.b_label)
        self.band_b_spin = QSpinBox()
        self.band_b_spin.setRange(1, 1)
        self.band_b_spin.setValue(3)
        self.band_b_spin.setMaximumWidth(45)
        self.band_b_spin.valueChanged.connect(self._on_settings_changed)
        band_layout.addWidget(self.band_b_spin)
        
        # ============ 2. Colormap反向============
        self.reverse_check = QCheckBox("反向")
        self.reverse_check.setChecked(self._colormap_reversed)
        self.reverse_check.stateChanged.connect(self._on_settings_changed)
        self.reverse_check.setToolTip("反转颜色映射")
        
        # ============ 3. 拉伸方式 ============
        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(self.STRETCH_MODES)
        self.stretch_combo.setCurrentText(self._stretch_mode)
        self.stretch_combo.currentTextChanged.connect(self._on_stretch_changed)
        self.stretch_combo.setToolTip("图像拉伸方式")
        self.stretch_combo.setMaximumWidth(100)
        
        # 拉伸参数（动态显示）
        self.stretch_param_widget = QWidget()
        stretch_param_layout = QHBoxLayout(self.stretch_param_widget)
        stretch_param_layout.setContentsMargins(0, 0, 0, 0)
        stretch_param_layout.setSpacing(2)
        
        # 百分比参数
        self.percent_low_spin = QDoubleSpinBox()
        self.percent_low_spin.setRange(0, 50)
        self.percent_low_spin.setValue(self._percent_low)
        self.percent_low_spin.setSuffix("%")
        self.percent_low_spin.setKeyboardTracking(False)
        self.percent_low_spin.setMaximumWidth(65)
        self.percent_low_spin.valueChanged.connect(self._on_settings_changed)
        self.percent_low_spin.setToolTip("低端截断百分比")
        
        self.percent_high_spin = QDoubleSpinBox()
        self.percent_high_spin.setRange(50, 100)
        self.percent_high_spin.setValue(self._percent_high)
        self.percent_high_spin.setSuffix("%")
        self.percent_high_spin.setKeyboardTracking(False)
        self.percent_high_spin.setMaximumWidth(65)
        self.percent_high_spin.valueChanged.connect(self._on_settings_changed)
        self.percent_high_spin.setToolTip("高端截断百分比")
        
        self.percent_dash_label = QLabel("-")
        stretch_param_layout.addWidget(self.percent_low_spin)
        stretch_param_layout.addWidget(self.percent_dash_label)
        stretch_param_layout.addWidget(self.percent_high_spin)
        
        # 标准差参数
        self.std_dev_label = QLabel("标准差数量")
        self.std_dev_spin = QDoubleSpinBox()
        self.std_dev_spin.setRange(0.5, 10)
        self.std_dev_spin.setValue(self._std_dev_n)
        self.std_dev_spin.setSingleStep(0.5)
        self.std_dev_spin.setKeyboardTracking(False)
        self.std_dev_spin.setPrefix("")
        self.std_dev_spin.setMaximumWidth(70)
        self.std_dev_spin.valueChanged.connect(self._on_settings_changed)
        self.std_dev_spin.setToolTip("标准差倍数")
        stretch_param_layout.addWidget(self.std_dev_label)
        stretch_param_layout.addWidget(self.std_dev_spin)
        
        # ============ 4. 数值范围（最大最小值）============
        self.auto_range_check = QCheckBox("手动范围")
        self.auto_range_check.setChecked(not self._auto_range)
        self.auto_range_check.stateChanged.connect(self._on_auto_range_changed)
        self.auto_range_check.setToolTip("勾选后使用手动输入的最大最小值；未勾选时按拉伸方式自动计算固定范围")
        
        self.min_spin = DeferredApplyDoubleSpinBox()
        self.min_spin.setRange(-1e10, 1e10)
        self.min_spin.setValue(self._value_min)
        self.min_spin.setMaximumWidth(90)
        self.min_spin.setDecimals(4)
        self.min_spin.setEnabled(not self._auto_range)
        self.min_spin.committed.connect(self._on_settings_changed)
        self.min_spin.setToolTip("最小值")
        
        self.range_dash_label = QLabel("-")
        
        self.max_spin = DeferredApplyDoubleSpinBox()
        self.max_spin.setRange(-1e10, 1e10)
        self.max_spin.setValue(self._value_max)
        self.max_spin.setMaximumWidth(90)
        self.max_spin.setDecimals(4)
        self.max_spin.setEnabled(not self._auto_range)
        self.max_spin.committed.connect(self._on_settings_changed)
        self.max_spin.setToolTip("最大值")
        
        # ============ 5. Gamma值（最后）============
        self.gamma_spin = QDoubleSpinBox()
        self.gamma_spin.setRange(0.1, 5.0)
        self.gamma_spin.setValue(self._gamma)
        self.gamma_spin.setSingleStep(0.1)
        self.gamma_spin.setKeyboardTracking(False)
        self.gamma_spin.setMaximumWidth(60)
        self.gamma_spin.valueChanged.connect(self._on_settings_changed)
        self.gamma_spin.setToolTip("Gamma校正值 (1.0=无校正)")
        
        # 更新参数控件可见性
        self._update_stretch_params_visibility()
        self._update_band_controls_visibility()
        
    def _create_full_ui(self):
        """创建完整布局（用于设置面板）"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 拉伸设置组
        stretch_group = QGroupBox("拉伸设置")
        stretch_layout = QGridLayout(stretch_group)
        
        stretch_layout.addWidget(QLabel("拉伸方式:"), 0, 0)
        self.stretch_combo = QComboBox()
        self.stretch_combo.addItems(self.STRETCH_MODES)
        self.stretch_combo.setCurrentText(self._stretch_mode)
        self.stretch_combo.currentTextChanged.connect(self._on_stretch_changed)
        stretch_layout.addWidget(self.stretch_combo, 0, 1, 1, 2)
        
        # 百分比参数
        stretch_layout.addWidget(QLabel("截断百分比:"), 1, 0)
        self.percent_low_spin = QDoubleSpinBox()
        self.percent_low_spin.setRange(0, 50)
        self.percent_low_spin.setValue(self._percent_low)
        self.percent_low_spin.setSuffix("%")
        self.percent_low_spin.setKeyboardTracking(False)
        self.percent_low_spin.valueChanged.connect(self._on_settings_changed)
        stretch_layout.addWidget(self.percent_low_spin, 1, 1)
        
        self.percent_high_spin = QDoubleSpinBox()
        self.percent_high_spin.setRange(50, 100)
        self.percent_high_spin.setValue(self._percent_high)
        self.percent_high_spin.setSuffix("%")
        self.percent_high_spin.setKeyboardTracking(False)
        self.percent_high_spin.valueChanged.connect(self._on_settings_changed)
        stretch_layout.addWidget(self.percent_high_spin, 1, 2)
        
        # 标准差参数
        stretch_layout.addWidget(QLabel("标准差倍数:"), 2, 0)
        self.std_dev_spin = QDoubleSpinBox()
        self.std_dev_spin.setRange(0.5, 10)
        self.std_dev_spin.setValue(self._std_dev_n)
        self.std_dev_spin.setSingleStep(0.5)
        self.std_dev_spin.setKeyboardTracking(False)
        self.std_dev_spin.valueChanged.connect(self._on_settings_changed)
        stretch_layout.addWidget(self.std_dev_spin, 2, 1, 1, 2)
        
        layout.addWidget(stretch_group)
        
        # Gamma和数值范围组
        value_group = QGroupBox("数值设置")
        value_layout = QGridLayout(value_group)
        
        value_layout.addWidget(QLabel("Gamma:"), 0, 0)
        self.gamma_spin = QDoubleSpinBox()
        self.gamma_spin.setRange(0.1, 5.0)
        self.gamma_spin.setValue(self._gamma)
        self.gamma_spin.setSingleStep(0.1)
        self.gamma_spin.setKeyboardTracking(False)
        self.gamma_spin.valueChanged.connect(self._on_settings_changed)
        value_layout.addWidget(self.gamma_spin, 0, 1, 1, 2)
        
        self.auto_range_check = QCheckBox("手动范围")
        self.auto_range_check.setChecked(not self._auto_range)
        self.auto_range_check.stateChanged.connect(self._on_auto_range_changed)
        value_layout.addWidget(self.auto_range_check, 1, 0)
        
        value_layout.addWidget(QLabel("最小值:"), 2, 0)
        self.min_spin = DeferredApplyDoubleSpinBox()
        self.min_spin.setRange(-1e10, 1e10)
        self.min_spin.setValue(self._value_min)
        self.min_spin.setEnabled(not self._auto_range)
        self.min_spin.committed.connect(self._on_settings_changed)
        value_layout.addWidget(self.min_spin, 2, 1, 1, 2)
        
        value_layout.addWidget(QLabel("最大值:"), 3, 0)
        self.max_spin = DeferredApplyDoubleSpinBox()
        self.max_spin.setRange(-1e10, 1e10)
        self.max_spin.setValue(self._value_max)
        self.max_spin.setEnabled(not self._auto_range)
        self.max_spin.committed.connect(self._on_settings_changed)
        value_layout.addWidget(self.max_spin, 3, 1, 1, 2)
        
        self.reverse_check = QCheckBox("Colormap反向")
        self.reverse_check.setChecked(self._colormap_reversed)
        self.reverse_check.stateChanged.connect(self._on_settings_changed)
        value_layout.addWidget(self.reverse_check, 4, 0, 1, 3)
        
        layout.addWidget(value_group)
        
        # 波段选择组
        band_group = QGroupBox("波段设置")
        band_layout = QGridLayout(band_group)
        
        band_layout.addWidget(QLabel("显示模式:"), 0, 0)
        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItems(["灰度", "RGB", "晕渲地貌"])
        self.display_mode_combo.setCurrentText(self._display_mode)
        self.display_mode_combo.currentTextChanged.connect(self._on_display_mode_changed)
        band_layout.addWidget(self.display_mode_combo, 0, 1, 1, 2)
        
        # 灰度波段
        self.gray_band_label = QLabel("显示波段:")
        band_layout.addWidget(self.gray_band_label, 1, 0)
        self.gray_band_spin = QSpinBox()
        self.gray_band_spin.setRange(1, 1)
        self.gray_band_spin.setValue(1)
        self.gray_band_spin.valueChanged.connect(self._on_settings_changed)
        band_layout.addWidget(self.gray_band_spin, 1, 1, 1, 2)
        
        # RGB波段
        self.rgb_label = QLabel("RGB波段:")
        band_layout.addWidget(self.rgb_label, 2, 0)
        
        rgb_widget = QWidget()
        rgb_layout = QHBoxLayout(rgb_widget)
        rgb_layout.setContentsMargins(0, 0, 0, 0)
        
        rgb_layout.addWidget(QLabel("R:"))
        self.band_r_spin = QSpinBox()
        self.band_r_spin.setRange(1, 1)
        self.band_r_spin.setValue(1)
        self.band_r_spin.valueChanged.connect(self._on_settings_changed)
        rgb_layout.addWidget(self.band_r_spin)
        
        rgb_layout.addWidget(QLabel("G:"))
        self.band_g_spin = QSpinBox()
        self.band_g_spin.setRange(1, 1)
        self.band_g_spin.setValue(2)
        self.band_g_spin.valueChanged.connect(self._on_settings_changed)
        rgb_layout.addWidget(self.band_g_spin)
        
        rgb_layout.addWidget(QLabel("B:"))
        self.band_b_spin = QSpinBox()
        self.band_b_spin.setRange(1, 1)
        self.band_b_spin.setValue(3)
        self.band_b_spin.valueChanged.connect(self._on_settings_changed)
        rgb_layout.addWidget(self.band_b_spin)
        
        band_layout.addWidget(rgb_widget, 2, 1, 1, 2)
        
        # 晕渲地貌参数
        self.hillshade_azimuth_label = QLabel("方位角:")
        band_layout.addWidget(self.hillshade_azimuth_label, 3, 0)
        self.hillshade_azimuth_spin = QDoubleSpinBox()
        self.hillshade_azimuth_spin.setRange(0, 360)
        self.hillshade_azimuth_spin.setValue(315)
        self.hillshade_azimuth_spin.setKeyboardTracking(False)
        self.hillshade_azimuth_spin.setSuffix("°")
        self.hillshade_azimuth_spin.setToolTip("光照方位角 (0=北, 90=东, 180=南, 270=西)")
        self.hillshade_azimuth_spin.valueChanged.connect(self._on_settings_changed)
        band_layout.addWidget(self.hillshade_azimuth_spin, 3, 1, 1, 2)
        
        self.hillshade_altitude_label = QLabel("高度角:")
        band_layout.addWidget(self.hillshade_altitude_label, 4, 0)
        self.hillshade_altitude_spin = QDoubleSpinBox()
        self.hillshade_altitude_spin.setRange(0, 90)
        self.hillshade_altitude_spin.setValue(45)
        self.hillshade_altitude_spin.setKeyboardTracking(False)
        self.hillshade_altitude_spin.setSuffix("°")
        self.hillshade_altitude_spin.setToolTip("光照高度角")
        self.hillshade_altitude_spin.valueChanged.connect(self._on_settings_changed)
        band_layout.addWidget(self.hillshade_altitude_spin, 4, 1, 1, 2)
        
        self.hillshade_zfactor_label = QLabel("Z缩放:")
        band_layout.addWidget(self.hillshade_zfactor_label, 5, 0)
        self.hillshade_zfactor_spin = QDoubleSpinBox()
        self.hillshade_zfactor_spin.setRange(0.000001, 100.0)
        self.hillshade_zfactor_spin.setValue(1.0)
        self.hillshade_zfactor_spin.setSingleStep(0.1)
        self.hillshade_zfactor_spin.setKeyboardTracking(False)
        self.hillshade_zfactor_spin.setDecimals(6)
        self.hillshade_zfactor_spin.setToolTip("高程缩放因子\n投影坐标系: 通常为1.0\n地理坐标系: 自动调整，或手动微调")
        self.hillshade_zfactor_spin.valueChanged.connect(self._on_settings_changed)
        band_layout.addWidget(self.hillshade_zfactor_spin, 5, 1, 1, 2)
        
        layout.addWidget(band_group)
        
        # 弹性空间
        layout.addStretch()
        
        self._update_stretch_params_visibility()
        self._update_band_controls_visibility()
        
    def _update_stretch_params_visibility(self):
        """更新拉伸参数控件的可见性"""
        mode = self.stretch_combo.currentText()
        
        # 百分比参数
        show_percent = (mode == self.STRETCH_PERCENT)
        self.percent_low_spin.setVisible(show_percent)
        self.percent_high_spin.setVisible(show_percent)
        if hasattr(self, 'percent_dash_label'):
            self.percent_dash_label.setVisible(show_percent)
        
        # 标准差参数
        show_std = (mode == self.STRETCH_STD_DEV)
        if hasattr(self, "std_dev_label"):
            self.std_dev_label.setVisible(show_std)
        self.std_dev_spin.setVisible(show_std)
        
    def _update_band_controls_visibility(self):
        """更新波段控件的可见性"""
        is_gray = self._display_mode == "灰度"
        is_hillshade = self._display_mode == "晕渲地貌"
        
        # 灰度模式和晕渲地貌模式都显示灰度波段选择
        self.gray_band_label.setVisible(is_gray or is_hillshade)
        self.gray_band_spin.setVisible(is_gray or is_hillshade)
        
        # RGB模式控件
        is_rgb = not is_gray and not is_hillshade
        self.rgb_label.setVisible(is_rgb)
        self.band_r_spin.setVisible(is_rgb)
        self.band_g_spin.setVisible(is_rgb)
        self.band_b_spin.setVisible(is_rgb)
        
        # G和B标签
        if hasattr(self, 'g_label'):
            self.g_label.setVisible(is_rgb)
        if hasattr(self, 'b_label'):
            self.b_label.setVisible(is_rgb)
        
        # 晕渲地貌参数（只在full UI中显示）
        if hasattr(self, 'hillshade_azimuth_label'):
            self.hillshade_azimuth_label.setVisible(is_hillshade)
            self.hillshade_azimuth_spin.setVisible(is_hillshade)
            self.hillshade_altitude_label.setVisible(is_hillshade)
            self.hillshade_altitude_spin.setVisible(is_hillshade)
            self.hillshade_zfactor_label.setVisible(is_hillshade)
            self.hillshade_zfactor_spin.setVisible(is_hillshade)
        self._update_mode_dependent_enabled()

    def _update_mode_dependent_enabled(self):
        """RGB 模式下禁用不适用的渲染参数。"""
        is_rgb = self._display_mode == "RGB"
        for widget_name in (
            "reverse_check",
            "stretch_combo",
            "stretch_param_widget",
            "auto_range_check",
            "min_spin",
            "max_spin",
            "gamma_spin",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(not is_rgb)
    
    def _on_stretch_changed(self, mode):
        """拉伸方式变更"""
        self._stretch_mode = mode
        self._update_stretch_params_visibility()
        self._emit_settings_changed()
        
    def _on_auto_range_changed(self, state):
        """手动范围复选框变更。"""
        checked = (state == Qt.Checked.value if hasattr(Qt.Checked, 'value') else state == Qt.Checked)
        self._auto_range = not checked
        self.min_spin.setEnabled(not self._auto_range)
        self.max_spin.setEnabled(not self._auto_range)
        self._emit_settings_changed()
        
    def _on_display_mode_changed(self, mode):
        """显示模式变更"""
        self._display_mode = mode
        self._update_band_controls_visibility()
        
        # 如果切换到晕渲地貌模式，建议使用terrain colormap
        if mode == "晕渲地貌":
            self.suggest_colormap.emit("terrain")
        
        self._emit_settings_changed()
        
    def _on_settings_changed(self):
        """通用设置变更处理"""
        self._emit_settings_changed()
        
    def _emit_settings_changed(self):
        """发出设置变更信号"""
        if not self._block_signals:
            self.settings_changed.emit()
    
    # ==================== 属性获取方法 ====================
    
    def get_stretch_mode(self):
        """获取拉伸方式"""
        value = self.stretch_combo.currentText()
        return self.STRETCH_MIN_MAX if value == "无拉伸" else value
    
    def get_percent_clip(self):
        """获取百分比截断参数 (low, high)"""
        return (self.percent_low_spin.value(), self.percent_high_spin.value())
    
    def get_std_dev_n(self):
        """获取标准差倍数"""
        return self.std_dev_spin.value()
    
    def get_gamma(self):
        """获取Gamma值"""
        return self.gamma_spin.value()
    
    def is_auto_range(self):
        """是否按拉伸方式自动计算固定范围。"""
        return not self.auto_range_check.isChecked()
    
    def get_value_range(self):
        """获取数值范围 (min, max)"""
        return (self.min_spin.value(), self.max_spin.value())
    
    def is_colormap_reversed(self):
        """是否反转colormap"""
        return self.reverse_check.isChecked()
    
    def is_smooth_display(self):
        """获取是否启用平滑显示"""
        return self._smooth_display
    
    def set_smooth_display(self, enabled):
        """设置是否启用平滑显示"""
        self._smooth_display = enabled
    
    def get_display_mode(self):
        """获取显示模式"""
        return self.display_mode_combo.currentText()
    
    def get_gray_band(self):
        """获取灰度模式显示的波段（1-based）"""
        return self.gray_band_spin.value()
    
    def get_rgb_bands(self):
        """获取RGB波段 (r, g, b)，都是1-based"""
        return (self.band_r_spin.value(), self.band_g_spin.value(), self.band_b_spin.value())
    
    def get_hillshade_params(self):
        """获取晕渲地貌参数"""
        # 如果没有hillshade控件（compact模式），返回默认值
        if not hasattr(self, 'hillshade_azimuth_spin'):
            return {'azimuth': 315.0, 'altitude': 45.0, 'z_factor': 1.0}
        
        return {
            'azimuth': self.hillshade_azimuth_spin.value(),
            'altitude': self.hillshade_altitude_spin.value(),
            'z_factor': self.hillshade_zfactor_spin.value(),
        }
    
    def get_all_settings(self):
        """获取所有设置"""
        self.commit_pending_edits()
        value_range = self.get_value_range()
        return {
            'stretch_mode': self.get_stretch_mode(),
            'percent_clip': self.get_percent_clip(),
            'std_dev_n': self.get_std_dev_n(),
            'gamma': self.get_gamma(),
            'auto_range': self.is_auto_range(),
            'value_range': value_range,
            'value_min': value_range[0],
            'value_max': value_range[1],
            'colormap_reversed': self.is_colormap_reversed(),
            'display_mode': self.get_display_mode(),
            'gray_band': self.get_gray_band(),
            'rgb_bands': self.get_rgb_bands(),
            'hillshade_params': self.get_hillshade_params(),
            'smooth_display': self.is_smooth_display(),
        }

    def commit_pending_edits(self):
        """提交正在编辑但尚未失焦的数值输入。"""
        if hasattr(self, "min_spin"):
            self.min_spin.commit_pending()
        if hasattr(self, "max_spin"):
            self.max_spin.commit_pending()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress and hasattr(self, "min_spin"):
            focus = QApplication.focusWidget()
            focused_spin = None
            for spin in (self.min_spin, self.max_spin):
                try:
                    line_edit = spin.lineEdit()
                except RuntimeError:
                    continue
                try:
                    if focus is spin or focus is line_edit:
                        focused_spin = spin
                        break
                except RuntimeError:
                    continue
            if focused_spin is not None and not self._is_descendant(obj, focused_spin):
                try:
                    focused_spin.commit_pending()
                    focused_spin.clearFocus()
                except RuntimeError:
                    pass
        return super().eventFilter(obj, event)

    def _is_descendant(self, obj, parent):
        widget = obj if isinstance(obj, QWidget) else None
        while widget is not None:
            if widget is parent:
                return True
            widget = widget.parentWidget()
        return False
    
    # ==================== 属性设置方法 ====================
    
    def set_num_bands(self, num_bands):
        """设置波段数量"""
        self._num_bands = num_bands
        self._block_signals = True
        
        self.gray_band_spin.setRange(1, max(1, num_bands))
        self.band_r_spin.setRange(1, max(1, num_bands))
        self.band_g_spin.setRange(1, max(1, num_bands))
        self.band_b_spin.setRange(1, max(1, num_bands))
        
        # 如果是多波段，设置默认RGB
        if num_bands >= 3:
            self.band_r_spin.setValue(1)
            self.band_g_spin.setValue(2)
            self.band_b_spin.setValue(3)
        else:
            self.band_r_spin.setValue(1)
            self.band_g_spin.setValue(1)
            self.band_b_spin.setValue(1)
        
        # 如果只有单波段，只能使用灰度或晕渲地貌模式
        if num_bands == 1 and self.display_mode_combo.currentText() == "RGB":
            self.display_mode_combo.setCurrentText("灰度")
        self.display_mode_combo.setEnabled(True)
            
        self._block_signals = False
        self._update_band_controls_visibility()

    def reset_to_defaults(self, num_bands=None):
        """重置为新图像的默认渲染控制，保留全局平滑显示偏好。"""
        smooth_display = self._smooth_display
        self._block_signals = True
        if num_bands is not None:
            self._num_bands = int(max(1, num_bands))
            self.gray_band_spin.setRange(1, self._num_bands)
            self.band_r_spin.setRange(1, self._num_bands)
            self.band_g_spin.setRange(1, self._num_bands)
            self.band_b_spin.setRange(1, self._num_bands)
        else:
            self._num_bands = max(1, self._num_bands)

        self.display_mode_combo.setCurrentText("灰度")
        self.gray_band_spin.setValue(1)
        self.band_r_spin.setValue(1)
        self.band_g_spin.setValue(2 if self._num_bands >= 2 else 1)
        self.band_b_spin.setValue(3 if self._num_bands >= 3 else 1)
        self.stretch_combo.setCurrentText(self.STRETCH_MIN_MAX)
        self.percent_low_spin.setValue(2.0)
        self.percent_high_spin.setValue(98.0)
        self.std_dev_spin.setValue(2.0)
        self.gamma_spin.setValue(1.0)
        self.auto_range_check.setChecked(False)
        self.min_spin.setEnabled(False)
        self.max_spin.setEnabled(False)
        self.min_spin.setValue(0.0)
        self.max_spin.setValue(1.0)
        self.reverse_check.setChecked(False)
        self._smooth_display = smooth_display
        self._block_signals = False
        self._update_stretch_params_visibility()
        self._update_band_controls_visibility()
    
    def set_value_range(self, min_val, max_val):
        """设置数值范围（会自动填入到最大最小值输入框）"""
        self._block_signals = True
        self.min_spin.setValue(min_val)
        self.max_spin.setValue(max_val)
        self._value_min = min_val
        self._value_max = max_val
        self._block_signals = False

    def set_image_stats(self, min_val, max_val):
        """设置图像统计信息（最小值、最大值），用于填充到输入框"""
        self._block_signals = True
        # 设置数值范围到输入框
        self.min_spin.setValue(min_val)
        self.max_spin.setValue(max_val)
        self._value_min = min_val
        self._value_max = max_val
        self._block_signals = False
        
    def set_stretch_mode(self, mode):
        """设置拉伸方式"""
        normalized = self.STRETCH_MIN_MAX if mode == "无拉伸" else mode
        if normalized in self.STRETCH_MODES:
            self.stretch_combo.setCurrentText(normalized)
            
    def set_gamma(self, gamma):
        """设置Gamma值"""
        self._block_signals = True
        self.gamma_spin.setValue(gamma)
        self._gamma = gamma
        self._block_signals = False


def apply_render_settings(image_array, settings, nodata_value=None, geotransform=None, projection=None, downsample_factor=1):
    """
    根据渲染设置处理图像数组
    
    Args:
        image_array: numpy数组，可以是2D或3D
        settings: 渲染设置字典（从RenderSettingsWidget.get_all_settings()获取）
        nodata_value: Nodata值
        geotransform: GDAL地理变换参数
        projection: 投影信息（WKT格式）
        downsample_factor: 降采样因子，用于调整地理变换参数
    Returns:
        处理后的numpy数组，用于显示
    """
    if image_array is None:
        return None
        
    arr = image_array.copy().astype(np.float64)
    
    # 创建有效数据掩码
    valid_mask = np.isfinite(arr)
    if nodata_value is not None:
        valid_mask = valid_mask & (arr != nodata_value)
    
    # 处理波段选择
    display_mode = settings.get('display_mode', '灰度')
    
    if arr.ndim == 3:
        num_bands = arr.shape[2]
        if display_mode == 'RGB' and num_bands >= 3:
            r, g, b = settings.get('rgb_bands', (1, 2, 3))
            arr = np.stack([
                arr[:, :, r - 1],
                arr[:, :, g - 1],
                arr[:, :, b - 1]
            ], axis=-1)
            # 对每个通道分别处理，返回float32 (0.0-1.0)
            result = np.zeros(arr.shape, dtype=np.float32)
            for i in range(3):
                channel = arr[:, :, i]
                channel_valid = np.isfinite(channel)
                if nodata_value is not None:
                    channel_valid = channel_valid & (channel != nodata_value)
                result[:, :, i] = _apply_stretch_to_channel(
                    channel, channel_valid, settings, nodata_value
                )
            return result
        elif display_mode == '晕渲地貌':
            # 晕渲地貌模式，选择单波段作为DEM
            band = settings.get('gray_band', 1)
            band = min(band, num_bands) - 1
            arr = arr[:, :, band]
            valid_mask = np.isfinite(arr)
            if nodata_value is not None:
                valid_mask = valid_mask & (arr != nodata_value)
        else:
            # 灰度模式，选择单波段
            band = settings.get('gray_band', 1)
            band = min(band, num_bands) - 1
            arr = arr[:, :, band]
            valid_mask = np.isfinite(arr)
            if nodata_value is not None:
                valid_mask = valid_mask & (arr != nodata_value)
    
    # 2D数组处理
    if display_mode == '晕渲地貌':
        
        # 晕渲地貌模式：计算hillshade并叠加
        from ..utils.image_io import calculate_hillshade
        
        # 获取hillshade参数
        hillshade_params = settings.get('hillshade_params', {})
        azimuth = hillshade_params.get('azimuth', 315.0)
        altitude = hillshade_params.get('altitude', 45.0)
        z_factor = hillshade_params.get('z_factor', 1.0)
        
        # 计算hillshade
        hillshade = calculate_hillshade(arr, azimuth=azimuth, altitude=altitude, 
                                       z_factor=z_factor, nodata_value=nodata_value,
                                       geotransform=geotransform, projection=projection)
        
        result = apply_hillshade_blend(arr, valid_mask, settings, nodata_value, hillshade)
        
        # # 只显示hillshade
        # result = hillshade.astype(np.float32)
        
        return result
    else:
        return _apply_stretch_to_channel(arr, valid_mask, settings, nodata_value)


def _apply_stretch_to_channel(arr, valid_mask, settings, nodata_value):
    """对单通道应用拉伸，返回float32 (0.0-1.0)"""
    result = np.zeros(arr.shape, dtype=np.float32)
    
    if not np.any(valid_mask):
        return result
        
    valid_data = arr[valid_mask]
    stretch_mode = settings.get('stretch_mode', '最大最小')
    
    # 计算拉伸范围
    if settings.get('auto_range', True):
        if stretch_mode == "无拉伸" or stretch_mode == RenderSettingsWidget.STRETCH_MIN_MAX:
            vmin, vmax = np.min(valid_data), np.max(valid_data)
        elif stretch_mode == RenderSettingsWidget.STRETCH_PERCENT:
            low, high = settings.get('percent_clip', (2.0, 98.0))
            vmin = np.percentile(valid_data, low)
            vmax = np.percentile(valid_data, high)
        elif stretch_mode == RenderSettingsWidget.STRETCH_STD_DEV:
            n = settings.get('std_dev_n', 2.0)
            mean = np.mean(valid_data)
            std = np.std(valid_data)
            vmin = mean - n * std
            vmax = mean + n * std
        elif stretch_mode == RenderSettingsWidget.STRETCH_HISTOGRAM:
            # 直方图均衡化需要特殊处理
            return _apply_histogram_equalization(arr, valid_mask, settings)
        else:
            vmin, vmax = np.min(valid_data), np.max(valid_data)
    else:
        vmin, vmax = settings.get('value_range', (0, 1))
    
    # 归一化到0-1
    if vmax > vmin:
        normalized = (arr - vmin) / (vmax - vmin)
    else:
        normalized = np.zeros_like(arr, dtype=np.float32)
    
    # 裁剪到0-1范围
    normalized = np.clip(normalized, 0.0, 1.0)
    
    # 应用Gamma校正
    gamma = settings.get('gamma', 1.0)
    if gamma != 1.0:
        normalized = np.power(normalized, 1.0 / gamma)
    
    # 是否反向
    if settings.get('colormap_reversed', False):
        normalized = 1.0 - normalized
    
    # 保持float32精度，不转换到uint8
    result[valid_mask] = normalized[valid_mask].astype(np.float32)
    
    return result


def apply_hillshade_blend(arr, valid_mask, settings, nodata_value, hillshade):
    """Apply the same DEM stretch * hillshade blend used by hillshade display mode."""
    dem_stretched = _apply_stretch_to_channel(arr, valid_mask, settings, nodata_value)
    if dem_stretched.ndim == 3 and hillshade.ndim == 2:
        hillshade = hillshade[:, :, np.newaxis]
    result_final = dem_stretched * hillshade
    return np.clip(result_final, 0.0, 1.0).astype(np.float32)


def _apply_histogram_equalization(arr, valid_mask, settings):
    """应用直方图均衡化，返回float32 (0.0-1.0)"""
    result = np.zeros(arr.shape, dtype=np.float32)
    
    if not np.any(valid_mask):
        return result
    
    valid_data = arr[valid_mask]
    
    # 归一化到0-255用于计算直方图（临时使用，提高直方图分辨率）
    vmin, vmax = np.min(valid_data), np.max(valid_data)
    if vmax > vmin:
        normalized_temp = ((arr - vmin) / (vmax - vmin) * 255).astype(np.uint8)
    else:
        normalized_temp = np.zeros_like(arr, dtype=np.uint8)
    
    # 计算直方图
    valid_normalized = normalized_temp[valid_mask]
    hist, bins = np.histogram(valid_normalized.flatten(), 256, [0, 256])
    
    # 计算累积分布函数
    cdf = hist.cumsum()
    cdf_min = cdf[cdf > 0].min()
    cdf_max = cdf.max()
    
    if cdf_max > cdf_min:
        # 归一化CDF到0.0-1.0（保持float32精度）
        cdf_normalized = (cdf - cdf_min).astype(np.float32) / (cdf_max - cdf_min)
        
        # 应用均衡化
        result[valid_mask] = cdf_normalized[valid_normalized]
    
    # 应用Gamma校正
    gamma = settings.get('gamma', 1.0)
    if gamma != 1.0:
        result = np.power(result, 1.0 / gamma)
    
    # 是否反向
    if settings.get('colormap_reversed', False):
        result[valid_mask] = 1.0 - result[valid_mask]
    
    return result.astype(np.float32)
