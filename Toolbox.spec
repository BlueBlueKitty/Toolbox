# -*- mode: python ; coding: utf-8 -*-

import os
import sys
from osgeo import __file__ as gdal_file

# --- 1. 平台与环境检测 ---
IS_WINDOWS = sys.platform.startswith('win')
# Linux 下开启 strip 以减小二进制体积
STRIP = False if IS_WINDOWS else True
# 动态确定名称：Windows 下为 Toolbox_win，Linux 下为 Toolbox_linux
target_name = 'Toolbox_win' if IS_WINDOWS else 'Toolbox_linux'

# 通过环境变量控制是否生成单文件
ONEFILE = os.environ.get('ONEFILE', '0') == '1'

# --- 2. 资源路径配置 ---
datas = [
    ('resources', 'resources'),
]

# --- 3. GDAL/PROJ 数据路径适配 ---
_osgeo_dir = os.path.dirname(gdal_file)
_conda_prefix = os.environ.get('CONDA_PREFIX', '')

# 尝试从 pip 环境获取
_gdal_data_pip = os.path.join(_osgeo_dir, 'data', 'gdal')
_proj_data_pip = os.path.join(_osgeo_dir, 'data', 'proj')

# 尝试从 Conda 环境获取 (区分 Windows 和 Linux 路径结构)
if IS_WINDOWS:
    _gdal_data_conda = os.path.join(_conda_prefix, 'Library', 'share', 'gdal')
    _proj_data_conda = os.path.join(_conda_prefix, 'Library', 'share', 'proj')
else:
    _gdal_data_conda = os.path.join(_conda_prefix, 'share', 'gdal')
    _proj_data_conda = os.path.join(_conda_prefix, 'share', 'proj')

def get_exist_path(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None

_gdal_data = get_exist_path([_gdal_data_pip, _gdal_data_conda])
_proj_data = get_exist_path([_proj_data_pip, _proj_data_conda])

if _gdal_data:
    datas.append((_gdal_data, 'gdal_data'))
if _proj_data:
    datas.append((_proj_data, 'proj_data'))

# 图标适配：Linux 通常不嵌入图标，Windows 必须用 .ico
icon_path = os.path.join('resources', 'toolbox.ico') if IS_WINDOWS else None

# 自定义运行时钩子 (确保初始化 GDAL/PROJ 环境变量)
_runtime_hooks = ['hooks/pyi_rth_proj.py']

# --- 4. 打包核心配置 ---
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'PySide6.QtUiTools',
        'numpy._core._exceptions',
        'numpy._core._multiarray_umath',
        'numpy._core._dtype_ctypes',
        'h5py.defs',
        'h5py.utils',
        'h5py.h5ac',
        'h5py._proxy',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=_runtime_hooks,
    excludes=['PyQt5', 'PyQt6'],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

if ONEFILE:
    # 单文件模式
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name=target_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=STRIP,
        upx=True,
        console=False,
        icon=[icon_path] if icon_path else None,
    )
else:
    # 目录模式 (方便后续压缩为 .zip)
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=target_name, # 这里是生成的 .exe 或二进制文件名
        debug=False,
        bootloader_ignore_signals=False,
        strip=STRIP,
        upx=True,
        console=False,
        icon=[icon_path] if icon_path else None,
    )
    
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        name=target_name, # 这里是生成的文件夹名称：Toolbox_win 或 Toolbox_linux
    )