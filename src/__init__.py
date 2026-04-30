"""
Toolbox 源代码包

这个包包含了应用的所有源代码，包括：
- 主窗口实现
- 对话框
- 工具函数
"""

# 延迟导入，避免在导入其他模块时触发 PySide6 依赖
def get_main_window():
    from .main_window import MainWindow
    return MainWindow