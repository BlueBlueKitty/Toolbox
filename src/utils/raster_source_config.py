"""
通用栅格数据源配置模型与持久化管理。
"""

from __future__ import annotations

import copy
import configparser
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


LAT_FORMAT_OPTIONS = ["N00", "N00_00", "00N", "-00"]
LON_FORMAT_OPTIONS = ["E000", "E000_00", "000E", "-000"]
COORD_LOCATION_OPTIONS = ["文件名中", "文件夹名中", "文件夹名和文件名中"]
ANCHOR_OPTIONS = ["左下角", "左上角", "右下角", "右上角"]
ZIP_STRATEGY_OPTIONS = [
    "自动优先选择名称包含 DEM",
    "自动优先选择 .tif",
    "自动选择第一个可读栅格",
]
RESAMPLE_METHOD_OPTIONS = [
    "双线性插值",
    "最邻近",
    "三次卷积",
    "三次样条",
    "Lanczos",
    "平均值",
    "众数",
]


def get_user_config_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Toolbox"
    return Path.home() / ".toolbox"


def _normalize_ext(value: str) -> str:
    if not value:
        return ""
    return value if value.startswith(".") else f".{value}"


@dataclass
class LocalRasterSourceConfig:
    name: str
    root_dir: str = ""
    is_archive: bool = False
    archive_extension: str = ".zip"
    raster_extension: str = ".tif"
    longitude_interval: float = 1.0
    latitude_interval: float = 1.0
    naming_anchor: str = "左下角"
    relative_path_template: str = "{tile}.tif"
    tile_token_template: str = "{lat}{lon}"
    latitude_format: str = "N00"
    longitude_format: str = "E000"
    coord_location: str = "文件名中"
    zip_raster_strategy: str = "自动优先选择名称包含 DEM"
    resample_method: str = "双线性插值"
    allow_missing_tiles: bool = False
    description: str = ""
    builtin: bool = False
    last_test_point: Optional[Tuple[float, float]] = None
    sample_path: str = ""

    def to_dict(self) -> Dict:
        data = asdict(self)
        data["archive_extension"] = _normalize_ext(self.archive_extension)
        data["raster_extension"] = _normalize_ext(self.raster_extension)
        return data

    @classmethod
    def from_dict(cls, data: Dict) -> "LocalRasterSourceConfig":
        data = dict(data)
        return cls(
            name=data.get("name", "未命名本地数据源"),
            root_dir=data.get("root_dir", ""),
            is_archive=bool(data.get("is_archive", False)),
            archive_extension=_normalize_ext(data.get("archive_extension", ".zip")),
            raster_extension=_normalize_ext(data.get("raster_extension", ".tif")),
            longitude_interval=float(data.get("longitude_interval", 1.0)),
            latitude_interval=float(data.get("latitude_interval", 1.0)),
            naming_anchor=data.get("naming_anchor", "左下角"),
            relative_path_template=data.get("relative_path_template", "{tile}.tif"),
            tile_token_template=data.get("tile_token_template", "{lat}{lon}"),
            latitude_format=data.get("latitude_format", "N00"),
            longitude_format=data.get("longitude_format", "E000"),
            coord_location=data.get("coord_location", "文件名中"),
            zip_raster_strategy=data.get("zip_raster_strategy", "自动优先选择名称包含 DEM"),
            resample_method=data.get("resample_method", "双线性插值"),
            allow_missing_tiles=bool(data.get("allow_missing_tiles", False)),
            description=data.get("description", ""),
            builtin=bool(data.get("builtin", False)),
            last_test_point=tuple(data["last_test_point"]) if data.get("last_test_point") else None,
            sample_path=data.get("sample_path", ""),
        )


@dataclass
class OnlineRasterSourceConfig:
    name: str
    platform_type: str = "OpenTopography"
    api_key: str = ""
    default_dataset: str = "SRTMGL3"
    extra_params: Dict[str, str] = field(default_factory=dict)
    description: str = ""
    builtin: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict) -> "OnlineRasterSourceConfig":
        data = dict(data)
        return cls(
            name=data.get("name", "未命名在线数据源"),
            platform_type=data.get("platform_type", "OpenTopography"),
            api_key=data.get("api_key", ""),
            default_dataset=data.get("default_dataset", "SRTMGL3"),
            extra_params=dict(data.get("extra_params", {})),
            description=data.get("description", ""),
            builtin=bool(data.get("builtin", False)),
        )


def build_default_local_sources() -> List[LocalRasterSourceConfig]:
    return [
        LocalRasterSourceConfig(
            name="SRTM",
            is_archive=True,
            archive_extension=".zip",
            raster_extension=".hgt",
            longitude_interval=1.0,
            latitude_interval=1.0,
            naming_anchor="左下角",
            relative_path_template="{tile}.SRTMGL1.hgt.zip",
            tile_token_template="{lat}{lon}",
            latitude_format="N00",
            longitude_format="E000",
            coord_location="文件名中",
            zip_raster_strategy="自动选择第一个可读栅格",
            resample_method="双线性插值",
            allow_missing_tiles=False,
            description="默认 SRTM 全球高程瓦片配置",
            builtin=True,
        ),
        LocalRasterSourceConfig(
            name="Copernicus DEM",
            is_archive=False,
            archive_extension=".zip",
            raster_extension=".tif",
            longitude_interval=1.0,
            latitude_interval=1.0,
            naming_anchor="左下角",
            relative_path_template="{tile}/{tile}.tif",
            tile_token_template="Copernicus_DSM_COG_10_{lat}_{lon}_DEM",
            latitude_format="N00_00",
            longitude_format="E000_00",
            coord_location="文件夹名和文件名中",
            zip_raster_strategy="自动优先选择名称包含 DEM",
            resample_method="双线性插值",
            allow_missing_tiles=False,
            description="默认 Copernicus DEM 瓦片配置",
            builtin=True,
        ),
    ]


def build_default_online_sources() -> List[OnlineRasterSourceConfig]:
    return [
        OnlineRasterSourceConfig(
            name="OpenTopography",
            platform_type="OpenTopography",
            default_dataset="SRTMGL3",
            description="默认 OpenTopography 在线下载配置",
            builtin=True,
        )
    ]


class RasterSourceConfigManager:
    def __init__(self):
        self.config_dir = get_user_config_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "raster_data_sources.json"
        self.data = self._load_or_initialize()

    def _load_or_initialize(self) -> Dict:
        legacy_values = self._load_legacy_settings()
        if not self.config_file.exists():
            data = self._default_payload()
            data = self._apply_legacy_values(data, legacy_values)
            self._save_data(data)
            return data

        try:
            with self.config_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = self._default_payload()

        data = self._merge_defaults(data)
        data = self._apply_legacy_values(data, legacy_values)
        self._save_data(data)
        return data

    def _default_payload(self) -> Dict:
        return {
            "local_sources": [item.to_dict() for item in build_default_local_sources()],
            "online_sources": [item.to_dict() for item in build_default_online_sources()],
            "ui_state": {
                "source_mode": "online",
                "selected_local_source": "SRTM",
                "selected_online_source": "OpenTopography",
                "last_output_dir": "",
            },
        }

    def _merge_defaults(self, data: Dict) -> Dict:
        merged = dict(self._default_payload())
        merged["ui_state"].update(data.get("ui_state", {}))

        local_map = {item["name"]: item for item in data.get("local_sources", []) if item.get("name")}
        for default_item in merged["local_sources"]:
            if default_item["name"] in local_map:
                current = local_map.pop(default_item["name"])
                default_item.update(self._merge_local_source(default_item, current))
        merged["local_sources"].extend(local_map.values())

        online_map = {item["name"]: item for item in data.get("online_sources", []) if item.get("name")}
        for default_item in merged["online_sources"]:
            if default_item["name"] in online_map:
                current = online_map.pop(default_item["name"])
                default_item.update(self._merge_online_source(default_item, current))
        merged["online_sources"].extend(online_map.values())
        return merged

    def _merge_local_source(self, default_item: Dict, current: Dict) -> Dict:
        if not default_item.get("builtin"):
            merged = dict(default_item)
            merged.update(current)
            return merged
        mutable_keys = {"root_dir", "resample_method", "allow_missing_tiles", "description", "last_test_point", "sample_path"}
        merged = dict(default_item)
        for key in mutable_keys:
            if key in current:
                merged[key] = current[key]
        return merged

    def _merge_online_source(self, default_item: Dict, current: Dict) -> Dict:
        if not default_item.get("builtin"):
            merged = dict(default_item)
            merged.update(current)
            return merged
        mutable_keys = {"api_key", "description", "default_dataset", "extra_params"}
        merged = dict(default_item)
        for key in mutable_keys:
            if key in current:
                merged[key] = current[key]
        return merged

    def _load_legacy_settings(self) -> Dict[str, str]:
        candidate_files = [
            Path.home() / ".toolbox" / "dem_acquisition.ini",
            self.config_dir / "dem_acquisition.ini",
        ]
        values: Dict[str, str] = {}
        parser = configparser.ConfigParser()
        for file_path in candidate_files:
            if not file_path.exists():
                continue
            try:
                parser.read(file_path, encoding="utf-8")
                section = parser["General"] if parser.has_section("General") else {}
                values["srtm_folder"] = section.get("srtm_folder", values.get("srtm_folder", ""))
                values["copernicus_folder"] = section.get("copernicus_folder", values.get("copernicus_folder", ""))
                values["api_key"] = section.get("api_key", values.get("api_key", ""))
                values["source_id"] = section.get("source_id", values.get("source_id", ""))
            except Exception:
                continue
        return values

    def _apply_legacy_values(self, data: Dict, legacy_values: Dict[str, str]) -> Dict:
        if not legacy_values:
            return data
        for item in data.get("local_sources", []):
            if item.get("name") == "SRTM" and legacy_values.get("srtm_folder") and not item.get("root_dir"):
                item["root_dir"] = legacy_values["srtm_folder"]
            if item.get("name") == "Copernicus DEM" and legacy_values.get("copernicus_folder") and not item.get("root_dir"):
                item["root_dir"] = legacy_values["copernicus_folder"]
        for item in data.get("online_sources", []):
            if item.get("name") == "OpenTopography" and legacy_values.get("api_key") and not item.get("api_key"):
                item["api_key"] = legacy_values["api_key"]
        if legacy_values.get("source_id") and "source_mode" not in data.get("ui_state", {}):
            data.setdefault("ui_state", {})["source_mode"] = "local" if legacy_values["source_id"] in {"0", "1"} else "online"
        return data

    def _save_data(self, data: Dict):
        with self.config_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save(self):
        self._save_data(self.data)

    def get_local_sources(self) -> List[LocalRasterSourceConfig]:
        return [LocalRasterSourceConfig.from_dict(item) for item in self.data.get("local_sources", [])]

    def get_online_sources(self) -> List[OnlineRasterSourceConfig]:
        return [OnlineRasterSourceConfig.from_dict(item) for item in self.data.get("online_sources", [])]

    def get_local_source(self, name: str) -> Optional[LocalRasterSourceConfig]:
        for item in self.get_local_sources():
            if item.name == name:
                return item
        return None

    def get_online_source(self, name: str) -> Optional[OnlineRasterSourceConfig]:
        for item in self.get_online_sources():
            if item.name == name:
                return item
        return None

    def _replace_by_name(self, key: str, name: str, payload: Dict):
        items = self.data.get(key, [])
        for index, item in enumerate(items):
            if item.get("name") == name:
                items[index] = payload
                break
        else:
            items.append(payload)
        self.data[key] = items
        self.save()

    def save_local_source(self, config: LocalRasterSourceConfig, original_name: Optional[str] = None):
        if config.builtin:
            default = next((item for item in build_default_local_sources() if item.name == config.name), None)
            if default:
                default.root_dir = config.root_dir
                default.resample_method = config.resample_method
                default.allow_missing_tiles = config.allow_missing_tiles
                default.description = config.description
                default.last_test_point = config.last_test_point
                default.sample_path = config.sample_path
                config = default
        if original_name and original_name != config.name:
            self.delete_local_source(original_name, allow_builtin=True)
        self._replace_by_name("local_sources", config.name, config.to_dict())

    def save_online_source(self, config: OnlineRasterSourceConfig, original_name: Optional[str] = None):
        if config.builtin:
            default = next((item for item in build_default_online_sources() if item.name == config.name), None)
            if default:
                default.api_key = config.api_key
                default.description = config.description
                default.default_dataset = config.default_dataset
                default.extra_params = config.extra_params
                config = default
        if original_name and original_name != config.name:
            self.delete_online_source(original_name, allow_builtin=True)
        self._replace_by_name("online_sources", config.name, config.to_dict())

    def delete_local_source(self, name: str, allow_builtin: bool = False) -> bool:
        sources = self.get_local_sources()
        target = next((item for item in sources if item.name == name), None)
        if not target:
            return False
        if target.builtin and not allow_builtin:
            return False
        self.data["local_sources"] = [item.to_dict() for item in sources if item.name != name]
        self.save()
        return True

    def delete_online_source(self, name: str, allow_builtin: bool = False) -> bool:
        sources = self.get_online_sources()
        target = next((item for item in sources if item.name == name), None)
        if not target:
            return False
        if target.builtin and not allow_builtin:
            return False
        self.data["online_sources"] = [item.to_dict() for item in sources if item.name != name]
        self.save()
        return True

    def duplicate_local_source(self, name: str) -> Optional[LocalRasterSourceConfig]:
        source = self.get_local_source(name)
        if not source:
            return None
        cloned = copy.deepcopy(source)
        cloned.name = self.generate_unique_name(f"{source.name} - 副本", local=True)
        cloned.builtin = False
        return cloned

    def duplicate_online_source(self, name: str) -> Optional[OnlineRasterSourceConfig]:
        source = self.get_online_source(name)
        if not source:
            return None
        cloned = copy.deepcopy(source)
        cloned.name = self.generate_unique_name(f"{source.name} - 副本", local=False)
        cloned.builtin = False
        return cloned

    def generate_unique_name(self, base_name: str, local: bool = True) -> str:
        existing = {item.name for item in (self.get_local_sources() if local else self.get_online_sources())}
        if base_name not in existing:
            return base_name
        index = 2
        while f"{base_name} {index}" in existing:
            index += 1
        return f"{base_name} {index}"

    def get_ui_state(self) -> Dict:
        return dict(self.data.get("ui_state", {}))

    def set_ui_state_value(self, key: str, value):
        self.data.setdefault("ui_state", {})[key] = value
        self.save()
