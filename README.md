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

**使用方法：**

1. 点击主界面"TIFF边界转矢量"按钮
2. 选择要处理的TIFF文件
3. 在设置对话框中配置坐标系统和输出格式（支持Shapefile、KML等）
4. 点击"确定"生成矢量边界文件

<p align="center">
  <img src="imgs/boundary.png" alt="TIFF边界转矢量">
</p>

### 2. 像素时序查看器

导入时序数据，查看每个像素的时序曲线

- 支持 TIFF/GeoTIFF 时序影像、 MintPy h5 时序形变数据以及GAMMA二进制时序数据
- 双图像窗口同步显示

**使用方法：**

1. 点击主界面"像素时序查看器"按钮
2. 选择数据类型：
   - **TIFF时序**：点击"打开TIFF文件夹"，选择包含时序TIFF文件的文件夹
   - **h5文件**：点击"打开h5文件"，选择MintPy生成的h5文件
   - **GAMMA时序**：点击"打开GAMMA时序数据"，选择包含GAMMA二进制文件的文件夹（自动检测PAR文件）
3. 使用滑块切换不同时间点的影像
4. 在图像上点击像素，右侧图表显示该像素的时序曲线
5. 可调整colormap、缩放图像、导出数据、将GAMMA强度数据转为dB形式

<p align="center">
  <img src="imgs/sereis.png" alt="像素时序查看器">
</p>

### 3. 图像局部查看器

绘制矩形查看局部区域直方图，绘制折线查看沿线像素变化曲线

- 支持 TIFF/GeoTIFF、PNG、JPG 等常见格式、MintPy h5 数据文件以及GAMMA二进制文件
  **使用方法：**

1. 点击主界面"图像局部查看器"按钮
2. 打开图像文件：
   - **常规图像**：点击"打开图像"选择TIFF/PNG/JPG等文件
   - **h5文件**：点击"打开h5文件"，选择数据集
   - **GAMMA文件**：点击"打开GAMMA文件"，选择二进制文件（自动或手动选择PAR参数文件）
3. 使用绘图工具：
   - **矩形工具**：点击"绘制矩形"，在图像上拖动绘制矩形，查看区域直方图
   - **折线工具**：点击"绘制折线"，在图像上依次点击绘制折线，右键完成，查看沿线像素值变化
4. 鼠标悬停在折线上可查看对应位置的像素值
5. 可调整colormap、缩放图像、导出区域数据、将GAMMA强度数据转为dB形式
<p align="center">
  <img src="imgs/local.png" alt="图像局部查看器">
</p>

### 4. DEM 数据获取（已完成）

可根据研究区范围从本地或者从 OpenTopography 获取 DEM 数据

**使用方法：**

1. 点击主界面"DEM数据获取"按钮
2. 选择数据获取方式：
   - **在线获取**：输入OpenTopography API Key，绘制或输入研究区范围，选择DEM产品（如COP30），点击下载
   - **本地获取**：选择本地DEM文件，绘制或输入研究区范围，裁剪得到目标区域DEM
3. 设置输出路径和文件名
4. 点击"开始处理"获取DEM数据

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
uv sync --active
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

本项目在仓库中包含了用于打包的脚本和配置，支持两种常见目标：Windows 可执行文件（使用 PyInstaller）和 Linux AppImage（基于 PyInstaller 的目录模式 + appimagetool）。

- Windows (PyInstaller):
  - 使用 `build_win.ps1` 自动调用 PyInstaller（在 Windows PowerShell 下运行）。
  - 可选择目录模式（Directory）或单文件模式（OneFile），通过环境变量 `ONEFILE` 控制：`ONEFILE=0` 目录模式，`ONEFILE=1` 单文件模式。示例（PowerShell）：

    ```powershell
    # 自动打包（Windows）
    ./build_win.ps1
    ```

- Linux (PyInstaller + AppImage):
  - 仓库提供 `build_linux.sh`，默认使用 PyInstaller 的目录模式生成一个 AppDir（可通过设置 `ONEFILE` 切换为单文件，但 AppImage 通常基于目录模式更可靠）。
  - `build/` 目录下包含 `appimagetool`，脚本会尝试生成 `.AppImage` 文件。
  - 生成后的 AppImage 可用 `install_appimage.sh` 安装到用户目录并创建桌面条目。示例：

    ```bash
    sudo chmod +x build_linux.sh
    bash build_linux.sh

    # 生成的 AppImage 后，安装到本用户：
    chmod +x install_appimage.sh
    ./install_appimage.sh path/to/Toolbox-*.AppImage
    ```

注意：不同系统和发行版环境对打包依赖（尤其 GDAL、Qt 等本地库）要求不同。建议在目标平台的干净环境或对应的构建容器中执行打包脚本以减少兼容性问题。

## 许可证

Copyright (c) 2026 by Yibo Yuan, All Rights Reserved.
