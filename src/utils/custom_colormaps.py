'''
Author: Yibo Yuan 2633669459@qq.com
Description: 自定义colormap注册模块，从GMT CPT文件加载colormap

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import re

try:
    import matplotlib
    import matplotlib.cm as cm
    from matplotlib.colors import LinearSegmentedColormap
    import numpy as np
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

# 标记是否已经注册过colormap
_COLORMAPS_REGISTERED = False


def load_cpt_file(cpt_path):
    """从GMT CPT文件加载colormap
    
    Args:
        cpt_path: CPT文件路径
        
    Returns:
        LinearSegmentedColormap对象，如果失败则返回None
    """
    if not os.path.exists(cpt_path):
        return None
    
    try:
        # matplotlib颜色名称映射（用于解析颜色名称）
        from matplotlib import colors as mcolors
        
        colors = []
        positions = []
        
        def parse_color(color_str):
            """解析颜色字符串，支持R/G/B格式和颜色名称"""
            color_str = color_str.strip()
            
            # 检查是否为R/G/B格式
            if '/' in color_str:
                parts = color_str.split('/')
                if len(parts) == 3:
                    r = float(parts[0]) / 255.0
                    g = float(parts[1]) / 255.0
                    b = float(parts[2]) / 255.0
                    return (r, g, b)
            
            # 尝试解析为颜色名称
            try:
                rgb = mcolors.to_rgb(color_str)
                return rgb
            except:
                pass
            
            return None
        
        with open(cpt_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # 跳过注释和空行
                if not line or line.startswith('#'):
                    continue
                
                # 跳过B/F/N行（背景/前景/NaN颜色）
                if line.startswith(('B', 'F', 'N')):
                    continue
                
                parts = line.split()
                
                # 格式1: z0 r0 g0 b0 z1 r1 g1 b1（8个数字）
                if len(parts) >= 8:
                    try:
                        z0 = float(parts[0])
                        r0, g0, b0 = float(parts[1])/255.0, float(parts[2])/255.0, float(parts[3])/255.0
                        z1 = float(parts[4])
                        r1, g1, b1 = float(parts[5])/255.0, float(parts[6])/255.0, float(parts[7])/255.0
                        
                        # 添加起始颜色
                        if not positions or positions[-1] != z0:
                            positions.append(z0)
                            colors.append((r0, g0, b0))
                        # 添加结束颜色
                        positions.append(z1)
                        colors.append((r1, g1, b1))
                        continue
                    except ValueError:
                        pass
                
                # 格式2: z0 color0 z1 color1（4个部分，颜色可以是名称或R/G/B）
                if len(parts) >= 4:
                    try:
                        z0 = float(parts[0])
                        color0 = parse_color(parts[1])
                        z1 = float(parts[2])
                        color1 = parse_color(parts[3])
                        
                        if color0 is None or color1 is None:
                            continue
                        
                        # 添加起始颜色
                        if not positions or positions[-1] != z0:
                            positions.append(z0)
                            colors.append(color0)
                        # 添加结束颜色
                        positions.append(z1)
                        colors.append(color1)
                    except ValueError:
                        continue
        
        if not colors:
            return None
        
        # 归一化位置到0-1范围
        positions = np.array(positions)
        min_pos, max_pos = positions.min(), positions.max()
        if max_pos > min_pos:
            positions = (positions - min_pos) / (max_pos - min_pos)
        
        # 构建colormap字典
        cdict = {'red': [], 'green': [], 'blue': []}
        for i, (pos, (r, g, b)) in enumerate(zip(positions, colors)):
            cdict['red'].append((pos, r, r))
            cdict['green'].append((pos, g, g))
            cdict['blue'].append((pos, b, b))
        
        # 创建colormap
        cmap_name = os.path.splitext(os.path.basename(cpt_path))[0]
        cmap = LinearSegmentedColormap(cmap_name, cdict)
        return cmap
        
    except Exception:
        # 静默处理加载失败
        return None


def register_custom_colormaps():
    """注册所有自定义colormap（从CPT文件加载）"""
    global _COLORMAPS_REGISTERED
    
    # 如果已经注册过，直接返回
    if _COLORMAPS_REGISTERED:
        return
    
    if not MATPLOTLIB_AVAILABLE:
        return
    
    # 获取resources/gmt_cpt目录路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    cpt_dir = os.path.join(project_root, 'resources', 'gmt_cpt')
    
    if not os.path.exists(cpt_dir):
        return
    
    # 加载所有CPT文件
    registered = []
    for filename in os.listdir(cpt_dir):
        if filename.endswith('.cpt'):
            cpt_path = os.path.join(cpt_dir, filename)
            cmap = load_cpt_file(cpt_path)
            
            if cmap is not None:
                cmap_name = os.path.splitext(filename)[0]
                try:
                    # 注册到matplotlib（兼容不同版本）
                    if hasattr(matplotlib, 'colormaps'):
                        matplotlib.colormaps.register(cmap, name=cmap_name)
                    elif hasattr(cm, 'register_cmap'):
                        cm.register_cmap(name=cmap_name, cmap=cmap)
                    else:
                        cm.cmap_d[cmap_name] = cmap
                    registered.append(cmap_name)
                except Exception:
                    # 静默处理已注册的colormap
                    pass
    
    # 标记已注册
    _COLORMAPS_REGISTERED = True


# 模块导入时自动注册
register_custom_colormaps()
