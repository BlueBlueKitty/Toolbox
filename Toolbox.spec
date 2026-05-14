# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import glob
from osgeo import __file__ as gdal_file
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

# --- 1. 平台与环境检测 ---
IS_WINDOWS = sys.platform.startswith('win')
IS_MACOS = sys.platform == 'darwin'
# Linux 下开启 strip 以减小二进制体积
STRIP = False if IS_WINDOWS else True
# Windows 下禁用 UPX 以避免打包缓慢且大量 DLL 无法压缩 (CFG 保护)
UPX_ENABLED = False if IS_WINDOWS else True
# 动态确定名称：Windows / macOS / Linux
if IS_WINDOWS:
    target_name = 'Toolbox_win'
elif IS_MACOS:
    target_name = 'Toolbox_macos'
else:
    target_name = 'Toolbox_linux'

# 通过环境变量控制是否生成单文件
ONEFILE = os.environ.get('ONEFILE', '0') == '1'

# --- 2. 资源路径配置 ---
datas = [
    ('resources', 'resources'),
]
binaries = []

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

# --- 3.1 GDAL 动态库收集（重点：Conda 环境） ---
# 先收集 PyInstaller 对 osgeo 能识别到的动态库
binaries += collect_dynamic_libs('osgeo')

def _append_conda_libs(lib_dir, patterns):
    if not lib_dir or not os.path.isdir(lib_dir):
        return
    for pattern in patterns:
        for file_path in glob.glob(os.path.join(lib_dir, pattern)):
            if os.path.isfile(file_path):
                binaries.append((file_path, '.'))

if _conda_prefix:
    if IS_WINDOWS:
        _conda_lib_dir = os.path.join(_conda_prefix, 'Library', 'bin')
        _append_conda_libs(_conda_lib_dir, [
            'gdal*.dll',
            'proj*.dll',
            'geos*.dll',
            'sqlite3*.dll',
            'libcurl*.dll',
            'tiff*.dll',
            'jpeg*.dll',
            'png*.dll',
            'zstd*.dll',
            'deflate*.dll',
            'webp*.dll',
            'lzma*.dll',
            'expat*.dll',
            'iconv*.dll',
        ])
    elif not IS_MACOS:
        _conda_lib_dir = os.path.join(_conda_prefix, 'lib')
        _append_conda_libs(_conda_lib_dir, [
            'libgdal.so*',
            'libproj.so*',
            'libgeos*.so*',
            'libsqlite3.so*',
            'libcurl.so*',
            'libtiff.so*',
            'libjpeg.so*',
            'libpng*.so*',
            'libzstd.so*',
            'libdeflate.so*',
            'libwebp*.so*',
            'liblzma.so*',
            'libexpat.so*',
            'libiconv.so*',
            'libnsl.so*',
        ])

# 图标适配
icon_path = os.path.join('resources', 'toolbox.ico') if IS_WINDOWS else None
macos_bundle_icon = os.environ.get('MACOS_BUNDLE_ICON', '').strip() or os.path.join('resources', 'toolbox.icns')
if IS_MACOS and not os.path.exists(macos_bundle_icon):
    macos_bundle_icon = None

# 自定义运行时钩子 (确保初始化 GDAL/PROJ 环境变量)
_runtime_hooks = ['hooks/pyi_rth_proj.py']

# --- 4. 打包核心配置 ---
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
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
    ] + collect_submodules('osgeo'),
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
        upx=UPX_ENABLED,
        console=False,
        icon=[icon_path] if icon_path else None,
    )
    if IS_MACOS:
        app = BUNDLE(
            exe,
            name='Toolbox.app',
            icon=macos_bundle_icon,
            bundle_identifier='com.bluebluekitty.toolbox',
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
        upx=UPX_ENABLED,
        console=False,
        icon=[icon_path] if icon_path else None,
    )
    
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=UPX_ENABLED,
        name=target_name, # 这里是生成的文件夹名称：Toolbox_win / Toolbox_macos / Toolbox_linux
    )
    if IS_MACOS:
        app = BUNDLE(
            coll,
            name='Toolbox.app',
            icon=macos_bundle_icon,
            bundle_identifier='com.bluebluekitty.toolbox',
        )
