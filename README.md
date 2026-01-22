# Toolbox - 遥感工具箱

一个基于PySide6开发的遥感数据处理与分析工具箱，提供便捷的栅格数据处理、时序分析和可视化功能。

## 项目简介

本项目旨在打造一个功能丰富、易用的遥感数据处理工具箱，整合常用的遥感数据分析功能，提供友好的图形界面，降低遥感数据处理的门槛。

## 已完成功能

<p align="center">
  <img src="imgs/main.png" alt="主界面">
</p>

### 1. TIFF边界转矢量

根据导入的TIFF数据，将其矩形边界转成矢量数据

- 支持导出多种矢量格式
- 支持坐标系统设置

<p align="center">
  <img src="imgs/boundary.png" alt="TIFF边界转矢量">
</p>

### 2. 像素时序查看器

导入时序数据，查看每个像素的时序曲线

- 支持TIFF/GeoTIFF时序影像和MintPy h5时序形变数据
- 双图像窗口同步显示

<p align="center">
  <img src="imgs/sereis.png" alt="像素时序查看器">
</p>

#### 使用说明

1. 点击主界面"像素时序查看器"按钮
2. 选择数据源：
   - **TIFF序列**：点击"打开图像文件夹"，选择包含时序TIFF文件的文件夹
   - **h5数据**：点击"打开h5时序数据"，选择mintpy格式的h5文件
3. 在图像窗口中点击像素查看时序曲线
4. 使用滑块或按钮切换不同时相
5. 可手动设置Nodata值、更改colormap

### 3. 图像局部查看器

导入数据，绘制矩形查看局部区域的图像直方图，绘制折线查看沿线像素变化曲线图

- 支持TIFF/GeoTIFF、PNG、JPG等常见格式和h5数据文件

<p align="center">
  <img src="imgs/local.png" alt="图像局部查看器">
</p>

#### 使用说明

1. 点击主界面"图像局部查看器"按钮
2. 点击"打开图像"选择TIFF/PNG/JPG文件，或"打开h5数据"选择h5文件
3. 选择绘制模式：
   - **矩形模式**：拖动绘制矩形，查看区域直方图
   - **折线模式**：点击绘制折线，双击完成，查看剖面数据
4. 鼠标移动查看像素坐标和值
5. 可手动设置Nodata值、更改colormap

## 环境要求

- Python 3.12+
- 主要依赖包：
  - PySide6
  - numpy
  - GDAL
  - matplotlib
  - Pillow
  - h5py

## 安装与使用

### 使用可执行文件（推荐）

直接运行打包好的`Toolbox.exe`即可，无需安装Python环境。

### 从源码运行

1. 克隆项目

```bash
git clone <repository-url>
cd Toolbox
```

2. 创建conda环境并安装依赖

```bash
conda create -n toolbox python=3.12
conda activate toolbox
pip install -r requirements.txt
```

3. 运行程序

```bash
python main.py
```

## 项目打包

### 打包为单文件exe

1. 确保已安装pyinstaller

```bash
pip install pyinstaller
```

2. 激活toolbox环境并执行打包

```bash
conda activate toolbox
pyinstaller Toolbox.spec
```

3. 打包完成后，可执行文件位于`dist/Toolbox.exe`

## 待完成功能

- [ ] **DEM数据获取工具**
  - 根据TIFF数据的地理范围自动获取对应区域的DEM
  - 支持多种DEM数据源（SRTM、ASTER等）
  - 自动裁剪到研究区范围
  - 重采样到与输入数据相同的分辨率

## 许可证

Copyright (c) 2026 by Yibo Yuan, All Rights Reserved.
