"""
通用栅格数据源解析、测试与拼接裁剪工具。
"""

from __future__ import annotations

import math
import os
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .dem_utils import calculate_area_km2
from .raster_source_config import LocalRasterSourceConfig

try:
    from osgeo import gdal
    GDAL_AVAILABLE = True
except ImportError:
    gdal = None
    GDAL_AVAILABLE = False


SUPPORTED_RASTER_EXTENSIONS = [".tif", ".tiff", ".hgt", ".img", ".vrt"]


def format_coordinate(value: float, pattern: str, is_latitude: bool) -> str:
    abs_value = abs(int(round(value)))
    if pattern in ("N00", "E000"):
        prefix = ("N" if value >= 0 else "S") if is_latitude else ("E" if value >= 0 else "W")
        width = 2 if is_latitude else 3
        return f"{prefix}{abs_value:0{width}d}"
    if pattern in ("N00_00", "E000_00"):
        prefix = ("N" if value >= 0 else "S") if is_latitude else ("E" if value >= 0 else "W")
        width = 2 if is_latitude else 3
        return f"{prefix}{abs_value:0{width}d}_00"
    if pattern in ("00N", "000E"):
        suffix = ("N" if value >= 0 else "S") if is_latitude else ("E" if value >= 0 else "W")
        width = 2 if is_latitude else 3
        return f"{abs_value:0{width}d}{suffix}"
    if pattern in ("-00", "-000"):
        width = 2 if is_latitude else 3
        sign = "-" if value < 0 else ""
        return f"{sign}{abs_value:0{width}d}"
    return str(int(round(value)))


def _token_display(pattern: str, label: str) -> str:
    return f"[{label}:{pattern}]"


def build_rule_preview(config: LocalRasterSourceConfig) -> str:
    preview = config.relative_path_template.replace("{tile}", config.tile_token_template)
    preview = preview.replace("{lat}", _token_display(config.latitude_format, "纬度"))
    preview = preview.replace("{lon}", _token_display(config.longitude_format, "经度"))
    return preview


@dataclass
class TileRecord:
    tile_id: str
    relative_path: str
    anchor_lat: float
    anchor_lon: float
    source_path: str
    raster_path: Optional[str] = None


@dataclass
class RasterSourceTestResult:
    success: bool
    message: str
    candidate_path: str = ""
    raster_path: str = ""
    details: List[str] = None

    def __post_init__(self):
        if self.details is None:
            self.details = []


class RasterTileNaming:
    @staticmethod
    def build_tile_id(config: LocalRasterSourceConfig, anchor_lat: float, anchor_lon: float) -> str:
        lat_text = format_coordinate(anchor_lat, config.latitude_format, True)
        lon_text = format_coordinate(anchor_lon, config.longitude_format, False)
        return config.tile_token_template.replace("{lat}", lat_text).replace("{lon}", lon_text)

    @staticmethod
    def anchor_from_cell(config: LocalRasterSourceConfig, south: float, north: float, west: float, east: float) -> Tuple[float, float]:
        anchor = config.naming_anchor
        if anchor == "左下角":
            return south, west
        if anchor == "左上角":
            return north, west
        if anchor == "右下角":
            return south, east
        return north, east

    @staticmethod
    def cell_for_point(config: LocalRasterSourceConfig, lat: float, lon: float) -> Tuple[float, float, float, float]:
        lat_idx = math.floor(lat / config.latitude_interval)
        lon_idx = math.floor(lon / config.longitude_interval)
        south = lat_idx * config.latitude_interval
        west = lon_idx * config.longitude_interval
        north = south + config.latitude_interval
        east = west + config.longitude_interval
        return south, north, west, east

    @classmethod
    def enumerate_tiles(cls, config: LocalRasterSourceConfig, south: float, north: float, west: float, east: float) -> List[TileRecord]:
        lat_start = math.floor(south / config.latitude_interval)
        lat_end = math.ceil(north / config.latitude_interval) - 1
        lon_start = math.floor(west / config.longitude_interval)
        lon_end = math.ceil(east / config.longitude_interval) - 1

        records: List[TileRecord] = []
        for lat_idx in range(lat_start, lat_end + 1):
            cell_south = lat_idx * config.latitude_interval
            cell_north = cell_south + config.latitude_interval
            for lon_idx in range(lon_start, lon_end + 1):
                cell_west = lon_idx * config.longitude_interval
                cell_east = cell_west + config.longitude_interval
                anchor_lat, anchor_lon = cls.anchor_from_cell(
                    config, cell_south, cell_north, cell_west, cell_east
                )
                tile_id = cls.build_tile_id(config, anchor_lat, anchor_lon)
                relative = config.relative_path_template.replace("{tile}", tile_id)
                relative = relative.replace("{lat}", format_coordinate(anchor_lat, config.latitude_format, True))
                relative = relative.replace("{lon}", format_coordinate(anchor_lon, config.longitude_format, False))
                records.append(
                    TileRecord(
                        tile_id=tile_id,
                        relative_path=relative,
                        anchor_lat=anchor_lat,
                        anchor_lon=anchor_lon,
                        source_path=os.path.join(config.root_dir, relative),
                    )
                )
        return records


class RasterArchiveHelper:
    @staticmethod
    def list_readable_rasters(archive_path: str) -> List[str]:
        with zipfile.ZipFile(archive_path, "r") as zf:
            return [
                name for name in zf.namelist()
                if os.path.splitext(name)[1].lower() in SUPPORTED_RASTER_EXTENSIONS and not name.endswith("/")
            ]

    @staticmethod
    def choose_raster(candidates: List[str], strategy: str) -> Optional[str]:
        if not candidates:
            return None
        scored = []
        for item in candidates:
            lower = item.lower()
            score = 0
            if strategy == "自动优先选择名称包含 DEM" and "dem" in lower:
                score += 100
            if strategy == "自动优先选择 .tif" and lower.endswith(".tif"):
                score += 100
            if lower.endswith(".tif"):
                score += 10
            if lower.endswith(".tiff"):
                score += 9
            if lower.endswith(".hgt"):
                score += 8
            scored.append((score, -len(item), item))
        scored.sort(reverse=True)
        return scored[0][2]

    @staticmethod
    def build_archive_vsimem_path(archive_path: str, inner_path: str) -> str:
        fixed_inner = inner_path.replace("\\", "/")
        return f"/vsizip/{archive_path.replace(os.sep, '/')}/{fixed_inner}"


class LocalRasterProcessor:
    def resolve_tile_source(self, config: LocalRasterSourceConfig, record: TileRecord) -> Tuple[bool, Optional[str], str]:
        if not os.path.exists(record.source_path):
            return False, None, "文件不存在"
        if not config.is_archive:
            return True, record.source_path, "普通栅格文件"
        try:
            candidates = RasterArchiveHelper.list_readable_rasters(record.source_path)
        except Exception as exc:
            return False, None, f"压缩包扫描失败: {exc}"
        inner = RasterArchiveHelper.choose_raster(candidates, config.zip_raster_strategy)
        if not inner:
            return False, None, "压缩包内未找到可读栅格"
        return True, RasterArchiveHelper.build_archive_vsimem_path(record.source_path, inner), f"压缩包内主栅格: {inner}"

    def collect_tiles(
        self,
        config: LocalRasterSourceConfig,
        south: float,
        north: float,
        west: float,
        east: float,
    ) -> Tuple[List[str], List[str], List[str]]:
        found_files: List[str] = []
        missing_tiles: List[str] = []
        details: List[str] = []
        for record in RasterTileNaming.enumerate_tiles(config, south, north, west, east):
            ok, raster_path, detail = self.resolve_tile_source(config, record)
            if ok and raster_path:
                found_files.append(raster_path)
            else:
                missing_tiles.append(record.tile_id)
            details.append(f"{record.tile_id}: {detail}")
        return found_files, missing_tiles, details

    def test_config(
        self,
        config: LocalRasterSourceConfig,
        lat: float,
        lon: float,
        get_extent: Callable[[str], Tuple[float, float, float, float]],
    ) -> RasterSourceTestResult:
        if not config.root_dir or not os.path.isdir(config.root_dir):
            return RasterSourceTestResult(False, "根目录错误或不存在")

        south, north, west, east = RasterTileNaming.cell_for_point(config, lat, lon)
        record = RasterTileNaming.enumerate_tiles(config, south, north, west, east)[0]
        result = RasterSourceTestResult(True, "测试通过", candidate_path=record.source_path)
        result.details.append(f"候选瓦片: {record.tile_id}")
        result.details.append(f"候选路径: {record.source_path}")

        ok, raster_path, detail = self.resolve_tile_source(config, record)
        result.details.append(detail)
        if not ok or not raster_path:
            hint = "，可能是命名锚点错误或路径规则错误" if "文件不存在" in detail else ""
            return RasterSourceTestResult(False, f"{detail}{hint}", candidate_path=record.source_path, details=result.details)

        result.raster_path = raster_path
        try:
            real_south, real_north, real_west, real_east = get_extent(raster_path)
        except Exception as exc:
            return RasterSourceTestResult(False, f"GDAL打开失败: {exc}", candidate_path=record.source_path, raster_path=raster_path, details=result.details)

        result.details.append(
            f"真实范围: 南={real_south:.6f}, 北={real_north:.6f}, 西={real_west:.6f}, 东={real_east:.6f}"
        )
        in_bounds = real_south <= lat <= real_north and real_west <= lon <= real_east
        if not in_bounds:
            return RasterSourceTestResult(
                False,
                "文件存在但地理范围不包含测试点，可能是命名锚点错误或经纬度间隔错误",
                candidate_path=record.source_path,
                raster_path=raster_path,
                details=result.details,
            )
        return result

    @staticmethod
    def get_raster_extent_wgs84(raster_path: str) -> Tuple[float, float, float, float]:
        from .dem_utils import LocalDEMProcessor

        return LocalDEMProcessor.get_raster_extent_wgs84(raster_path)

    @staticmethod
    def merge_tiles(input_files: List[str], output_path: str) -> bool:
        from .dem_utils import LocalDEMProcessor

        return LocalDEMProcessor.merge_dem_tiles(input_files, output_path)

    @staticmethod
    def clip_to_bounds(
        input_path: str,
        output_path: str,
        south: float,
        north: float,
        west: float,
        east: float,
        resample_method: str = "双线性插值",
    ) -> bool:
        from .dem_utils import LocalDEMProcessor

        return LocalDEMProcessor.clip_to_bounds(
            input_path, output_path, south, north, west, east, resample_method=resample_method
        )

    @staticmethod
    def clip_and_resample_to_reference(
        input_path: str,
        reference_path: str,
        output_path: str,
        resample_method: str = "双线性插值",
    ) -> bool:
        from .dem_utils import LocalDEMProcessor

        return LocalDEMProcessor.clip_and_resample_to_reference(
            input_path, reference_path, output_path, resample_method=resample_method
        )


class RasterSourceAutoDetector:
    LAT_TOKEN_PATTERNS = [
        ("N00_00", r"(N|S)\d{2}_\d{2}"),
        ("N00", r"(N|S)\d{2}"),
        ("00N", r"\d{2}(N|S)"),
        ("-00", r"-\d{2}"),
    ]
    LON_TOKEN_PATTERNS = [
        ("E000_00", r"(E|W)\d{3}_\d{2}"),
        ("E000", r"(E|W)\d{3}"),
        ("000E", r"\d{3}(E|W)"),
        ("-000", r"-\d{3}"),
    ]

    @classmethod
    def detect_from_sample(cls, sample_path: str) -> LocalRasterSourceConfig:
        import re

        path = Path(sample_path)
        name = path.stem
        suffix = path.suffix.lower()
        is_archive = suffix == ".zip"
        raster_extension = ".tif"
        if is_archive:
            archive_extension = suffix
        else:
            archive_extension = ".zip"
            raster_extension = suffix or ".tif"

        rel_parts = [path.name]
        root_dir = str(path.parent)
        parent_name = path.parent.name
        lat_match = lon_match = None
        lat_format = "N00"
        lon_format = "E000"

        search_text = path.as_posix()
        for fmt, pattern in cls.LAT_TOKEN_PATTERNS:
            m = re.search(pattern, search_text)
            if m:
                lat_match = m.group(0)
                lat_format = fmt
                break
        for fmt, pattern in cls.LON_TOKEN_PATTERNS:
            m = re.search(pattern, search_text)
            if m:
                lon_match = m.group(0)
                lon_format = fmt
                break
        if not lat_match or not lon_match:
            raise ValueError("无法从样例文件中识别经纬度命名规则")

        replaced_file = path.name.replace(lat_match, "{lat}").replace(lon_match, "{lon}")
        rel_parts = [replaced_file]
        tile_template = replaced_file
        if not is_archive:
            tile_template = os.path.splitext(tile_template)[0]

        coord_location = "文件名中"
        if lat_match in parent_name and lon_match in parent_name:
            root_dir = str(path.parent.parent)
            rel_parts = [path.parent.name.replace(lat_match, "{lat}").replace(lon_match, "{lon}"), path.name.replace(lat_match, "{lat}").replace(lon_match, "{lon}")]
            coord_location = "文件夹名和文件名中"
            folder_tile = rel_parts[0]
            if folder_tile == os.path.splitext(rel_parts[1])[0]:
                tile_template = folder_tile
        relative_path_template = "/".join(rel_parts)

        if "Copernicus" in search_text:
            latitude_format = "N00_00"
            longitude_format = "E000_00"
            raster_extension = ".tif"
        else:
            latitude_format = lat_format
            longitude_format = lon_format

        return LocalRasterSourceConfig(
            name=path.parent.name if coord_location != "文件名中" else path.stem,
            root_dir=root_dir,
            is_archive=is_archive,
            archive_extension=archive_extension,
            raster_extension=raster_extension,
            longitude_interval=1.0,
            latitude_interval=1.0,
            naming_anchor="左下角",
            relative_path_template=relative_path_template,
            tile_token_template=tile_template,
            latitude_format=latitude_format,
            longitude_format=longitude_format,
            coord_location=coord_location,
            zip_raster_strategy="自动优先选择名称包含 DEM",
            allow_missing_tiles=False,
            description="由样例文件自动识别生成",
            builtin=False,
            sample_path=sample_path,
        )
