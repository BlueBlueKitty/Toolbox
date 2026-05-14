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

def _append_unique_binary(file_path, target_dir):
    """避免重复添加同一文件到同一目标目录。"""
    normalized_src = os.path.normcase(os.path.abspath(file_path))
    normalized_dest = target_dir.replace('\\', '/')
    for existing_src, existing_dest in binaries:
        if os.path.normcase(os.path.abspath(existing_src)) == normalized_src and existing_dest.replace('\\', '/') == normalized_dest:
            return
    binaries.append((file_path, target_dir))

def _collect_windows_pe_dependency_closure(seed_files, search_dirs):
    """
    在 Windows/Conda 环境下，递归收集 _gdal.pyd / gdal.dll 的依赖闭包。
    这样可以只带上真正需要的 DLL，并把它们放到 osgeo 目录旁边，
    避免 _gdal.pyd 依赖父目录 DLL 搜索路径。
    """
    try:
        import pefile
    except Exception:
        return []

    dll_lookup = {}
    for directory in search_dirs:
        if not directory or not os.path.isdir(directory):
            continue
        for entry in os.listdir(directory):
            full_path = os.path.join(directory, entry)
            if os.path.isfile(full_path):
                dll_lookup.setdefault(entry.lower(), full_path)

    closure = []
    visited = set()
    pending = []
    for seed in seed_files:
        if seed and os.path.isfile(seed):
            pending.append(seed)

    while pending:
        current = pending.pop()
        current_key = os.path.normcase(os.path.abspath(current))
        if current_key in visited:
            continue
        visited.add(current_key)

        try:
            pe = pefile.PE(current, fast_load=True)
            pe.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']]
            )
        except Exception:
            continue

        for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', []):
            dll_name = entry.dll.decode(errors='ignore')
            dll_path = dll_lookup.get(dll_name.lower())
            if not dll_path:
                continue
            dll_key = os.path.normcase(os.path.abspath(dll_path))
            if dll_key in visited:
                continue
            closure.append(dll_path)
            pending.append(dll_path)

    return closure

def _append_conda_gdal_runtime_windows(conda_prefix, osgeo_dir):
    """
    Conda-forge 的 Windows GDAL 共享库位于 Library/bin，不和 _gdal.pyd 同目录。
    若仅放在 _internal 根目录，部分机器上 _gdal.pyd 会因为找不到 gdal.dll 而导入失败。
    这里将 GDAL 的依赖闭包放入 osgeo 目录，避免依赖运行时额外 DLL 搜索路径。
    """
    conda_bin = os.path.join(conda_prefix, 'Library', 'bin')
    if not os.path.isdir(conda_bin):
        return

    seed_files = []
    for pattern in ('_gdal*.pyd', '_ogr*.pyd', '_osr*.pyd'):
        seed_files.extend(glob.glob(os.path.join(osgeo_dir, pattern)))
    seed_files.append(os.path.join(conda_bin, 'gdal.dll'))
    seed_files = [p for p in seed_files if os.path.isfile(p)]
    if not seed_files:
        return

    dependency_closure = _collect_windows_pe_dependency_closure(
        seed_files,
        [osgeo_dir, conda_bin],
    )

    # 兜底：若 pefile 不可用或解析失败，至少收集 GDAL 常见依赖，避免 _gdal.pyd 裸奔。
    if not dependency_closure:
        fallback_patterns = [
            'gdal*.dll',
            'proj*.dll',
            'geos*.dll',
            'libcurl*.dll',
            'libssl*.dll',
            'libcrypto*.dll',
            'sqlite3*.dll',
            'libxml2*.dll',
            'xerces*.dll',
            'tiff*.dll',
            'jpeg*.dll',
            'png*.dll',
            'zlib*.dll',
            'zstd*.dll',
            'liblzma*.dll',
            'libexpat*.dll',
            'spatialite*.dll',
            'iconv*.dll',
        ]
        for pattern in fallback_patterns:
            dependency_closure.extend(glob.glob(os.path.join(conda_bin, pattern)))

    for dll_path in dependency_closure:
        _append_unique_binary(dll_path, 'osgeo')

if _conda_prefix:
    if IS_WINDOWS:
        _append_conda_gdal_runtime_windows(_conda_prefix, _osgeo_dir)
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
