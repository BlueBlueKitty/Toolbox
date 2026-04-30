"""
Toolbox 源代码包

这个包包含了应用的所有源代码，包括：
- 主窗口实现
- 对话框
- 工具函数
"""

# 延迟导入，避免在导入其他模块时触发 PySide6 依赖
def get_main_window():
    """get_main_window。

    功能:
        承担当前方法对应的业务逻辑。
    参数:
        无。
    返回:
        None: 方法执行结果。
    异常:
        Exception: 依赖组件或输入异常时可能抛出。
    """
    from .main_window import MainWindow
    return MainWindow