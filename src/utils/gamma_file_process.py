"""Utilities for reading GAMMA binary products."""

from __future__ import annotations

import os
from typing import Tuple

import numpy as np


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

