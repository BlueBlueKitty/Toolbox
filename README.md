# Toolbox - 遥感工具箱

一个基于PySide6开发的遥感数据处理与分析工具箱，提供便捷的栅格数据处理、时序分析和可视化功能。

## 项目简介

本项目旨在打造一个功能丰富、易用的遥感数据处理工具箱，整合常用的遥感数据分析功能，提供友好的图形界面，降低遥感数据处理的门槛。

# Toolbox - 遥感工具箱

一个基于 PySide6 的遥感数据处理与分析工具箱，提供便捷的栅格数据处理、时序分析和可视化功能。

## 项目简介

本项目旨在打造一个功能丰富、易用的遥感数据处理工具箱，整合常用的遥感数据分析功能，提供友好的图形界面，降低遥感数据处理门槛。

## 已完成功能

<p align="center">
  <img src="imgs/main.png" alt="主界面">
</p>

### 1. TIFF 边界转矢量

根据导入的 TIFF 数据，将其矩形边界转成矢量数据

- 支持导出多种矢量格式
- 支持坐标系统设置

<p align="center">
  <img src="imgs/boundary.png" alt="TIFF边界转矢量">
</p>

### 2. 像素时序查看器

导入时序数据，查看每个像素的时序曲线

- 支持 TIFF/GeoTIFF 时序影像和 MintPy h5 时序形变数据
- 双图像窗口同步显示

<p align="center">
  <img src="imgs/sereis.png" alt="像素时序查看器">
</p>

### 3. 图像局部查看器

绘制矩形查看局部区域直方图，绘制折线查看沿线像素变化曲线

- 支持 TIFF/GeoTIFF、PNG、JPG 等常见格式和 h5 数据文件

<p align="center">
  <img src="imgs/local.png" alt="图像局部查看器">
</p>

### 4. DEM 数据获取（已完成）

可根据研究区范围从本地或者从 OpenTopography 获取 DEM 数据：

<p align="center">
  <img src="imgs/dem.png" alt="DEM 数据获取">
</p>

## 环境要求

- Python 3.12+
- 主要依赖包：PySide6、numpy、GDAL、matplotlib、Pillow、h5py、pyinstaller

## 安装与使用

### 使用可执行文件（推荐）

运行打包好的 `Toolbox.exe` 即可，无需安装 Python 环境。

### 从源码运行（推荐使用 `uv` 管理依赖）

1. 克隆项目并进入目录：

```bash
git clone <repository-url>
cd Toolbox
```

2. 使用 `uv` 同步环境（`uv` 会读取 `pyproject.toml`）：

```bash
uv sync
```

注意：`pyproject.toml` 中的 `tool.uv.sources` 里通常需要指定 GDAL 的 Windows wheel 路径。请在运行 `uv sync` 前，手动编辑 `pyproject.toml` 中 `gdal` 的路径为你本地 Windows wheel 文件的绝对或相对路径，例如：

```toml
[tool.uv.sources]
gdal = { path = "C:/path/to/gdal‑3.11.4‑cp312‑cp312‑win_amd64.whl" }
```

3. 运行程序：

```bash
python main.py
```

## 项目打包

使用 `PyInstaller` 打包：

```bash
pip install pyinstaller
# 单次构建（单文件）：
$env:ONEFILE="1"; python -m PyInstaller Toolbox.spec --clean --noconfirm; Remove-Item Env:\ONEFILE
# 目录模式：
python -m PyInstaller Toolbox.spec --clean --noconfirm
```

## 许可证

Copyright (c) 2026 by Yibo Yuan, All Rights Reserved.
