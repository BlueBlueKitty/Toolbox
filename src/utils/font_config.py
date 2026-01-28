"""
字体配置工具模块
用于在 Windows 和 Linux 平台上统一配置中文字体
"""
import os
import sys


def get_font_path():
    """
    获取打包后的字体文件路径
    
    Returns:
        str: 字体文件的绝对路径
    """
    # 检查是否在打包环境下运行
    if hasattr(sys, "_MEIPASS"):
        # 打包环境：从临时解压目录读取
        base_path = sys._MEIPASS
    else:
        # 开发环境：从项目根目录读取
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    
    font_path = os.path.join(base_path, "resources", "fonts", "NotoSansCJKsc-Regular.otf")
    return font_path


def configure_matplotlib_font():
    """
    配置 Matplotlib 使用思源黑体
    在导入 matplotlib.pyplot 后调用此函数
    """
    try:
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        
        font_path = get_font_path()
        
        # 检查字体文件是否存在
        if os.path.exists(font_path):
            # 添加字体到 matplotlib 字体管理器
            font_manager.fontManager.addfont(font_path)
            
            # 设置字体优先级列表（思源黑体优先，回退到系统字体）
            plt.rcParams['font.sans-serif'] = [
                'Noto Sans CJK SC',      # 思源黑体（Linux 系统安装名称）
                'Microsoft YaHei',       # Windows 微软雅黑
                'SimHei',                # Windows 黑体
                'WenQuanYi Micro Hei',   # Linux 文泉驿微米黑
                'sans-serif'
            ]
            plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
            
            return True
        else:
            # 字体文件不存在时，使用系统字体优先级列表
            print(f"警告: 字体文件未找到 {font_path}，使用系统字体")
            plt.rcParams['font.sans-serif'] = [
                'Microsoft YaHei',
                'SimHei',
                'Noto Sans CJK SC',
                'WenQuanYi Micro Hei',
                'sans-serif'
            ]
            plt.rcParams['axes.unicode_minus'] = False
            return False
            
    except ImportError:
        print("警告: matplotlib 未安装，跳过字体配置")
        return False
    except Exception as e:
        print(f"警告: 配置 matplotlib 字体时出错: {e}")
        return False


def configure_pyside6_font(app):
    """
    配置 PySide6/Qt 应用全局字体
    
    Args:
        app: QApplication 实例
    
    Returns:
        bool: 配置是否成功
    """
    try:
        from PySide6.QtGui import QFontDatabase, QFont
        
        font_path = get_font_path()
        
        # 检查字体文件是否存在
        if os.path.exists(font_path):
            # 添加字体到 Qt 字体数据库
            font_id = QFontDatabase.addApplicationFont(font_path)
            
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                
                if families:
                    # 创建字体对象并设置优先级列表
                    font = QFont()
                    font.setFamilies([
                        families[0],              # 加载的思源黑体
                        "Microsoft YaHei",        # Windows 微软雅黑
                        "Noto Sans CJK SC",       # Linux 思源黑体
                        "WenQuanYi Micro Hei",    # Linux 文泉驿微米黑
                        "sans-serif"
                    ])
                    app.setFont(font)
                    
                    return True
                else:
                    print(f"警告: 无法获取字体族名称")
            else:
                print(f"警告: 添加字体失败 {font_path}")
        else:
            print(f"警告: 字体文件未找到 {font_path}，使用系统默认字体")
        
        # 字体文件不存在或加载失败时，使用系统字体优先级列表
        font = QFont()
        font.setFamilies([
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "WenQuanYi Micro Hei",
            "sans-serif"
        ])
        app.setFont(font)
        return False
        
    except Exception as e:
        print(f"警告: 配置 PySide6 字体时出错: {e}")
        return False
