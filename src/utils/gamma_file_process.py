"""Utilities for reading GAMMA binary products."""

from __future__ import annotations

import os
import re
import glob
from typing import Tuple, Optional, Dict, List, Any

import numpy as np


# 支持的GAMMA数据格式
GAMMA_FORMATS = {
    "float32": "32位浮点数 (float32)",
    "float64": "64位浮点数 (float64)", 
    "int16": "16位整数 (int16)",
    "int32": "32位整数 (int32)",
    "uint8": "8位无符号整数 (uint8)",
    "cpxfloat32": "复数32位浮点 (cpxfloat32)",
    "cpxfloat64": "复数64位浮点 (cpxfloat64)",
    "cpxint16": "复数16位整数 (cpxint16)",
}


def _parse_format(bkformat: str) -> Tuple[np.dtype, bool]:
	"""Return dtype (big-endian) and whether input is complex pixel-interleaved."""

	if not isinstance(bkformat, str):
		raise TypeError("bkformat must be a string")

	fmt = bkformat.lower()
	if fmt == "mph":
		fmt = "cpxfloat32"
	if fmt == "hgt":
		raise ValueError("Use a dedicated HGT reader for hgt format")

	is_complex = fmt.startswith("cpx")
	base_fmt = fmt[3:] if is_complex else fmt

	try:
		dtype = np.dtype(base_fmt).newbyteorder(">")  # GAMMA uses big-endian storage
	except TypeError as exc:  # numpy raises TypeError for unknown formats
		raise ValueError(f"Unsupported bkformat '{bkformat}'") from exc

	return dtype, is_complex


def freadbkB(
	infile: str,
	lines: int,
	bkformat: str = "float32",
	r0: int = 0,
	rN: int = 0,
	c0: int = 0,
	cN: int = 0,
) -> Tuple[np.ndarray, int]:
	"""
	Read a GAMMA binary raster.

	Parameters mirror the original MATLAB ``freadbkB``:
	- infile: path to binary file.
	- lines: total number of rows in the file.
	- bkformat: data format (e.g., "float32", "cpxfloat32", "int16", "cpxint16").
	- r0, rN: 1-based start/end rows to read (0 means all rows).
	- c0, cN: 1-based start/end columns to read (0 means all columns).

	Returns
	-------
	data : np.ndarray
		Array shaped (rows, cols); complex output for ``cpx`` formats.
	count : int
		Number of elements read (complex pixels count as one).
	"""

	dtype, is_complex = _parse_format(bkformat)

	if lines < 1:
		raise ValueError("lines must be a positive integer")
	if not isinstance(infile, str):
		raise TypeError("infile must be a string path")

	bytes_per_elem = dtype.itemsize
	file_size = os.path.getsize(infile)

	elems_per_line = file_size / (bytes_per_elem * lines)
	if not elems_per_line.is_integer():
		raise ValueError("File size does not align with provided line count and format")
	width = int(elems_per_line)

	# Default to full extent if 0 is provided.
	if c0 == 0:
		c0, cN = 1, width
	if r0 == 0:
		r0, rN = 1, lines

	if r0 < 1 or rN < r0 or rN > lines:
		raise ValueError("Row indices are out of range")
	if c0 < 1 or cN < c0 or cN > width:
		raise ValueError("Column indices are out of range")

	read_all = r0 == 1 and rN == lines and c0 == 1 and cN == width

	def _read_chunk(fh, start: int, count: int) -> np.ndarray:
		fh.seek(start, os.SEEK_SET)
		return np.fromfile(fh, dtype=dtype, count=count)

	with open(infile, "rb") as fh:
		if read_all:
			data = np.fromfile(fh, dtype=dtype)
			out_lines = lines
		else:
			offset_elems = c0 - 1
			read_width = cN - c0 + 1
			out_lines = rN - r0 + 1

			if is_complex:
				offset_elems *= 2
				read_width *= 2  # real/imag interleaved

			rows = []
			stride_bytes = width * bytes_per_elem
			for row in range(r0 - 1, rN):
				start = row * stride_bytes + offset_elems * bytes_per_elem
				rows.append(_read_chunk(fh, start, read_width))
			data = np.concatenate(rows) if rows else np.array([], dtype=dtype)

	count = data.size

	if is_complex:
		if count % 2 != 0:
			raise ValueError("Complex data must have an even number of elements")
		real = data[0::2]
		imag = data[1::2]
		data = real.astype(np.float64, copy=False) + 1j * imag.astype(np.float64, copy=False)
		count //= 2

	if count % out_lines != 0:
		raise ValueError("Data cannot be reshaped into the requested number of lines")

	out_width = count // out_lines
	data = data.reshape((out_lines, out_width))

	return data, count


def parse_par_file(par_file: str) -> Dict[str, Any]:
    """
    解析GAMMA PAR文件，提取所有参数。
    
    Parameters
    ----------
    par_file : str
        PAR文件路径
    
    Returns
    -------
    dict
        包含所有参数的字典
    """
    if not os.path.exists(par_file):
        raise FileNotFoundError(f"PAR文件不存在: {par_file}")
    
    params = {}
    with open(par_file, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # 匹配 "key: value" 格式
            match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.+)$', line)
            if match:
                key = match.group(1).strip()
                value_str = match.group(2).strip()
                
                # 尝试解析为数值
                # 处理多个值的情况（取第一个值）
                first_value = value_str.split()[0] if value_str else ""
                try:
                    if '.' in first_value or 'e' in first_value.lower():
                        params[key] = float(first_value)
                    else:
                        params[key] = int(first_value)
                except ValueError:
                    params[key] = value_str
    
    return params


def get_dimensions_from_par(par_file: str) -> Tuple[int, int]:
    """
    从PAR文件中获取图像的行数和列数。
    
    Parameters
    ----------
    par_file : str
        PAR文件路径
    
    Returns
    -------
    tuple
        (width, height) 即 (列数, 行数)
    """
    params = parse_par_file(par_file)
    
    width = None
    height = None
    
    # 尝试 range_samples / azimuth_lines
    if 'range_samples' in params and 'azimuth_lines' in params:
        width = int(params['range_samples'])
        height = int(params['azimuth_lines'])
    # 尝试 width / nlines
    elif 'width' in params and 'nlines' in params:
        width = int(params['width'])
        height = int(params['nlines'])
    
    if width is None or height is None:
        raise ValueError(f"无法从PAR文件中提取图像尺寸: {par_file}")
    
    return width, height


def validate_dimensions(
    infile: str,
    width: int,
    height: int,
    bkformat: str = "float32"
) -> bool:
    """
    验证给定的行列数是否与文件大小匹配。
    
    Parameters
    ----------
    infile : str
        二进制文件路径
    width : int
        列数
    height : int
        行数
    bkformat : str
        数据格式
    
    Returns
    -------
    bool
        如果匹配返回True，否则返回False
    """
    dtype, is_complex = _parse_format(bkformat)
    bytes_per_elem = dtype.itemsize
    
    file_size = os.path.getsize(infile)
    
    # 复数数据每个像素包含两个元素（实部和虚部）
    pixels_per_elem = 2 if is_complex else 1
    expected_size = width * height * bytes_per_elem * pixels_per_elem
    
    return file_size == expected_size


def find_matching_par_file(binary_file: str) -> Optional[str]:
    """
    查找与二进制文件匹配的PAR文件。
    
    首先查找同名的.par文件，如果不存在则在同目录下查找其他.par文件。
    
    Parameters
    ----------
    binary_file : str
        二进制文件路径
    
    Returns
    -------
    str or None
        找到的PAR文件路径，如果没有找到返回None
    """
    directory = os.path.dirname(binary_file)
    base_name = os.path.basename(binary_file)
    
    # 1. 首先尝试同名的.par文件
    same_name_par = binary_file + ".par"
    if os.path.exists(same_name_par):
        return same_name_par
    
    # 2. 尝试去掉扩展名后加.par
    name_without_ext = os.path.splitext(binary_file)[0]
    par_without_ext = name_without_ext + ".par"
    if os.path.exists(par_without_ext):
        return par_without_ext
    
    # 3. 在目录中查找所有.par文件
    par_files = glob.glob(os.path.join(directory, "*.par"))
    if par_files:
        # 返回第一个找到的par文件（可能需要后续验证）
        return par_files[0]
    
    return None


def find_valid_par_for_binary(
    binary_file: str, 
    bkformat: str = "float32"
) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    """
    查找能够正确读取二进制文件的PAR文件。
    
    Parameters
    ----------
    binary_file : str
        二进制文件路径
    bkformat : str
        数据格式
    
    Returns
    -------
    tuple
        (par_file_path, (width, height)) 如果找到有效的PAR文件
        (None, None) 如果没有找到
    """
    directory = os.path.dirname(binary_file)
    
    # 收集所有候选PAR文件
    candidates = []
    
    # 1. 同名.par文件优先级最高
    same_name_par = binary_file + ".par"
    if os.path.exists(same_name_par):
        candidates.insert(0, same_name_par)
    
    # 2. 去掉扩展名后的.par
    name_without_ext = os.path.splitext(binary_file)[0]
    par_without_ext = name_without_ext + ".par"
    if os.path.exists(par_without_ext) and par_without_ext not in candidates:
        candidates.append(par_without_ext)
    
    # 3. 目录中的其他.par文件
    par_files = glob.glob(os.path.join(directory, "*.par"))
    for pf in par_files:
        if pf not in candidates:
            candidates.append(pf)
    
    # 逐个验证
    for par_file in candidates:
        try:
            width, height = get_dimensions_from_par(par_file)
            if validate_dimensions(binary_file, width, height, bkformat):
                return par_file, (width, height)
        except (ValueError, FileNotFoundError):
            continue
    
    return None, None


def read_gamma_binary(
    infile: str,
    bkformat: str = "float32",
    par_file: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    auto_find_par: bool = True
) -> Tuple[np.ndarray, int, int, Optional[str]]:
    """
    读取GAMMA二进制文件的高级接口。
    
    自动查找PAR文件获取尺寸，或使用用户提供的尺寸。
    
    Parameters
    ----------
    infile : str
        二进制文件路径
    bkformat : str
        数据格式
    par_file : str, optional
        指定的PAR文件路径
    width : int, optional
        手动指定的列数
    height : int, optional
        手动指定的行数
    auto_find_par : bool
        是否自动查找PAR文件
    
    Returns
    -------
    tuple
        (data, width, height, par_file_used)
        - data: 图像数据数组
        - width: 列数
        - height: 行数
        - par_file_used: 使用的PAR文件路径（如果有）
    
    Raises
    ------
    ValueError
        如果无法确定图像尺寸
    """
    par_file_used = None
    
    # 如果提供了width和height，直接使用
    if width is not None and height is not None:
        if not validate_dimensions(infile, width, height, bkformat):
            raise ValueError(f"提供的尺寸 {width}x{height} 与文件大小不匹配")
    else:
        # 尝试从PAR文件获取尺寸
        if par_file is not None:
            width, height = get_dimensions_from_par(par_file)
            par_file_used = par_file
            if not validate_dimensions(infile, width, height, bkformat):
                raise ValueError(f"PAR文件中的尺寸与二进制文件不匹配")
        elif auto_find_par:
            par_file_used, dims = find_valid_par_for_binary(infile, bkformat)
            if par_file_used is not None and dims is not None:
                width, height = dims
            else:
                raise ValueError(
                    "无法自动找到匹配的PAR文件。请手动指定尺寸或PAR文件。"
                )
        else:
            raise ValueError("必须提供width/height或par_file参数")
    
    # 使用现有的freadbkB函数读取数据
    data, _ = freadbkB(infile, height, bkformat)
    
    return data, width, height, par_file_used


def read_gamma_region(
    infile: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    width: int,
    height: int,
    bkformat: str = "float32"
) -> np.ndarray:
    """
    读取GAMMA二进制文件的指定矩形区域。
    
    Parameters
    ----------
    infile : str
        二进制文件路径
    x1, y1 : int
        左上角坐标（0-based）
    x2, y2 : int
        右下角坐标（0-based，不包含）
    width : int
        完整图像的列数
    height : int
        完整图像的行数
    bkformat : str
        数据格式
    
    Returns
    -------
    np.ndarray
        区域数据
    """
    # 转换为1-based索引（freadbkB使用1-based）
    r0 = y1 + 1
    rN = y2  # y2是不包含的，所以直接使用
    c0 = x1 + 1
    cN = x2  # x2是不包含的，所以直接使用
    
    data, _ = freadbkB(infile, height, bkformat, r0=r0, rN=rN, c0=c0, cN=cN)
    return data


def read_gamma_pixel(
    infile: str,
    x: int,
    y: int,
    width: int,
    height: int,
    bkformat: str = "float32"
) -> Any:
    """
    读取GAMMA二进制文件的单个像素值。
    
    Parameters
    ----------
    infile : str
        二进制文件路径
    x, y : int
        像素坐标（0-based）
    width : int
        完整图像的列数
    height : int
        完整图像的行数
    bkformat : str
        数据格式
    
    Returns
    -------
    scalar
        像素值（复数或实数）
    """
    dtype, is_complex = _parse_format(bkformat)
    bytes_per_elem = dtype.itemsize
    
    # 计算偏移量
    if is_complex:
        # 复数数据，每个像素包含两个元素
        pixel_offset = (y * width + x) * 2 * bytes_per_elem
        read_count = 2
    else:
        pixel_offset = (y * width + x) * bytes_per_elem
        read_count = 1
    
    with open(infile, 'rb') as fh:
        fh.seek(pixel_offset, os.SEEK_SET)
        data = np.fromfile(fh, dtype=dtype, count=read_count)
    
    if is_complex:
        return complex(data[0], data[1])
    else:
        return data[0]


def read_gamma_downsampled(
    infile: str,
    width: int,
    height: int,
    bkformat: str = "float32",
    max_size: int = 2048
) -> Tuple[np.ndarray, int]:
    """
    读取GAMMA二进制文件并降采样显示。
    
    通过跳行跳列的方式实现快速降采样预览。
    
    Parameters
    ----------
    infile : str
        二进制文件路径
    width : int
        完整图像的列数
    height : int
        完整图像的行数
    bkformat : str
        数据格式
    max_size : int
        最大显示尺寸
    
    Returns
    -------
    tuple
        (downsampled_data, downsample_factor)
    """
    max_dim = max(width, height)
    if max_dim <= max_size:
        # 无需降采样
        data, _ = freadbkB(infile, height, bkformat)
        return data, 1
    
    downsample_factor = int(np.ceil(max_dim / max_size))
    
    dtype, is_complex = _parse_format(bkformat)
    bytes_per_elem = dtype.itemsize
    
    # 计算降采样后的尺寸
    out_height = height // downsample_factor
    out_width = width // downsample_factor
    
    # 逐行读取并降采样
    if is_complex:
        result = np.zeros((out_height, out_width), dtype=np.complex128)
        elems_per_row = width * 2  # 实部+虚部
    else:
        result = np.zeros((out_height, out_width), dtype=dtype)
        elems_per_row = width
    
    row_bytes = elems_per_row * bytes_per_elem
    
    with open(infile, 'rb') as fh:
        for out_row in range(out_height):
            src_row = out_row * downsample_factor
            fh.seek(src_row * row_bytes, os.SEEK_SET)
            row_data = np.fromfile(fh, dtype=dtype, count=elems_per_row)
            
            if is_complex:
                # 重组为复数
                real = row_data[0::2]
                imag = row_data[1::2]
                complex_row = real.astype(np.float64) + 1j * imag.astype(np.float64)
                # 降采样列
                result[out_row, :] = complex_row[::downsample_factor][:out_width]
            else:
                result[out_row, :] = row_data[::downsample_factor][:out_width]
    
    return result, downsample_factor


def complex_to_phase(data: np.ndarray) -> np.ndarray:
    """
    将复数数据转换为相位。
    
    Parameters
    ----------
    data : np.ndarray
        复数数组
    
    Returns
    -------
    np.ndarray
        相位数组（弧度）
    """
    return np.angle(data)


def complex_to_amplitude(data: np.ndarray) -> np.ndarray:
    """
    将复数数据转换为幅度。
    
    Parameters
    ----------
    data : np.ndarray
        复数数组
    
    Returns
    -------
    np.ndarray
        幅度数组
    """
    return np.abs(data)


def complex_to_intensity(data: np.ndarray) -> np.ndarray:
    """
    将复数数据转换为强度（幅度的平方）。
    
    Parameters
    ----------
    data : np.ndarray
        复数数组
    
    Returns
    -------
    np.ndarray
        强度数组
    """
    return np.abs(data) ** 2


def is_gamma_binary_file(file_path: str) -> bool:
    """
    检查文件是否可能是GAMMA二进制文件。
    
    基于扩展名和文件特征进行判断。
    
    Parameters
    ----------
    file_path : str
        文件路径
    
    Returns
    -------
    bool
        如果可能是GAMMA二进制文件返回True
    """
    # 常见的GAMMA二进制文件扩展名
    gamma_extensions = {
        '.slc', '.mli', '.int', '.unw', '.cc', '.dem', '.sim', 
        '.hgt', '.flt', '.ramp', '.diff', '.lt', '.geo', '.ras',
        '.unw1', '.unw2', '.off'
    }
    
    # 检查扩展名
    ext = os.path.splitext(file_path)[1].lower()
    if ext in gamma_extensions:
        return True
    
    # 检查是否存在对应的.par文件
    if os.path.exists(file_path + ".par"):
        return True
    
    return False


def get_gamma_file_info(
    infile: str,
    bkformat: str = "float32"
) -> Dict[str, Any]:
    """
    获取GAMMA二进制文件的信息。
    
    Parameters
    ----------
    infile : str
        二进制文件路径
    bkformat : str
        数据格式
    
    Returns
    -------
    dict
        文件信息字典
    """
    info = {
        'file_path': infile,
        'file_name': os.path.basename(infile),
        'file_size': os.path.getsize(infile),
        'format': bkformat,
        'width': None,
        'height': None,
        'par_file': None,
        'is_complex': bkformat.startswith('cpx'),
    }
    
    # 尝试查找PAR文件
    par_file, dims = find_valid_par_for_binary(infile, bkformat)
    if par_file is not None and dims is not None:
        info['par_file'] = par_file
        info['width'], info['height'] = dims
    
    return info


