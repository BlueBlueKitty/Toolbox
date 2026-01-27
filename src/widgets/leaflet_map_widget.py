'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-26
Description: Leaflet 地图组件
    基于 QWebEngineView 和 Leaflet.js 的交互式地图组件
    支持绘制矩形、显示边界、底图切换等功能

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
from typing import Optional, Callable
from PySide6.QtCore import QObject, Signal, Slot, Qt, QUrl
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPushButton, QHBoxLayout, QMessageBox

# 尝试导入 WebEngine
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebChannel import QWebChannel
    WEBENGINE_AVAILABLE = True
except ImportError:
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView
        from PyQt6.QtWebEngineCore import QWebEngineSettings
        from PyQt6.QtWebChannel import QWebChannel
        WEBENGINE_AVAILABLE = True
    except ImportError:
        WEBENGINE_AVAILABLE = False
        QWebEngineView = None


# Leaflet 地图 HTML 模板
LEAFLET_MAP_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Leaflet Map</title>
    <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
    <link rel="stylesheet" href="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css"/>
    <style>
        html, body, #map { 
            height: 100%; 
            width: 100%;
            margin: 0; 
            padding: 0; 
        }
        .info-box {
            padding: 6px 8px;
            background: white;
            background: rgba(255,255,255,0.9);
            box-shadow: 0 0 15px rgba(0,0,0,0.2);
            border-radius: 5px;
            font-size: 12px;
            max-width: 200px;
        }
    </style>
</head>
<body>
    <div id="map"></div>
    <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    <script src="https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js"></script>
    <script>
        // QWebChannel 简化版内联实现
        var QWebChannel = function(transport, initCallback) {
            var channel = this;
            this.transport = transport;
            this.objects = {};
            this.execId = 0;
            this.execCallbacks = {};
            
            this.send = function(data) {
                if (typeof data !== "string") data = JSON.stringify(data);
                channel.transport.send(data);
            };
            
            this.transport.onmessage = function(message) {
                var data = message.data;
                if (typeof data === "string") data = JSON.parse(data);
                
                if (data.id !== undefined && channel.execCallbacks[data.id]) {
                    channel.execCallbacks[data.id](data.data);
                    delete channel.execCallbacks[data.id];
                } else if (data.object && data.signal) {
                    var object = channel.objects[data.object];
                    if (object) object[data.signal].forEach(function(cb) { cb.apply(cb, data.args); });
                }
            };
            
            this.exec = function(data, callback) {
                data.id = channel.execId++;
                if (callback) channel.execCallbacks[data.id] = callback;
                channel.send(data);
            };
            
            channel.exec({type: 6}, function(payload) {
                for (var name in payload) {
                    var obj = payload[name];
                    channel.objects[name] = {};
                    for (var method in obj.methods) {
                        (function(methodName) {
                            channel.objects[name][methodName] = function() {
                                var args = Array.prototype.slice.call(arguments);
                                channel.send({type: 2, object: name, method: methodName, args: args});
                            };
                        })(obj.methods[method]);
                    }
                    for (var signal in obj.signals) {
                        channel.objects[name][obj.signals[signal]] = [];
                    }
                }
                if (initCallback) initCallback(channel);
            });
        };
    </script>
    <script>
        // 初始化地图
        var map = L.map('map').setView([35, 105], 4);
        
        // 底图图层
        var cartoLight = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 19,
            attribution: '© CARTO © OpenStreetMap'
        });
        
        var cartoDark = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
            maxZoom: 19,
            attribution: '© CARTO'
        });
        
        var osmLayer = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap contributors'
        });
        
        // 高德地图（国内访问更快）
        var gdLayer = L.tileLayer('http://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
            subdomains: ['1', '2', '3', '4'],
            maxZoom: 18,
            attribution: '© 高德地图'
        });
        
        // 默认使用 CARTO Light
        cartoLight.addTo(map);
        
        // 图层控制
        var baseMaps = {
            "CARTO Light": cartoLight,
            "CARTO Dark": cartoDark,
            "OpenStreetMap": osmLayer,
            "高德地图": gdLayer
        };
        L.control.layers(baseMaps).addTo(map);
        
        // 绘制图层
        var drawnItems = new L.FeatureGroup();
        map.addLayer(drawnItems);
        
        // 绘制控件
        var drawControl = new L.Control.Draw({
            draw: {
                polygon: false,
                polyline: false,
                circle: false,
                circlemarker: false,
                marker: false,
                rectangle: {
                    shapeOptions: {
                        color: '#3388ff',
                        weight: 2,
                        fillOpacity: 0.2
                    }
                }
            },
            edit: {
                featureGroup: drawnItems,
                remove: true
            }
        });
        map.addControl(drawControl);
        
        // 信息显示控件
        var info = L.control({position: 'bottomleft'});
        info.onAdd = function(map) {
            this._div = L.DomUtil.create('div', 'info-box');
            this.update();
            return this._div;
        };
        info.update = function(bounds) {
            if (bounds) {
                this._div.innerHTML = '<b>选择区域</b><br>' +
                    '南: ' + bounds.south.toFixed(6) + '°<br>' +
                    '北: ' + bounds.north.toFixed(6) + '°<br>' +
                    '西: ' + bounds.west.toFixed(6) + '°<br>' +
                    '东: ' + bounds.east.toFixed(6) + '°';
            } else {
                this._div.innerHTML = '在地图上绘制矩形选择区域';
            }
        };
        info.addTo(map);
        
        // Python 通信桥接
        var pyBridge = null;
        
        new QWebChannel(qt.webChannelTransport, function(channel) {
            pyBridge = channel.objects.bridge;
            console.log('QWebChannel initialized');
        });
        
        // 绘制完成事件
        map.on(L.Draw.Event.CREATED, function(e) {
            drawnItems.clearLayers();
            drawnItems.addLayer(e.layer);
            
            var bounds = e.layer.getBounds();
            var boundsData = {
                south: bounds.getSouth(),
                north: bounds.getNorth(),
                west: bounds.getWest(),
                east: bounds.getEast()
            };
            
            info.update(boundsData);
            
            if (pyBridge) {
                pyBridge.onBoundsDrawn(JSON.stringify(boundsData));
            }
        });
        
        // 删除事件
        map.on(L.Draw.Event.DELETED, function(e) {
            info.update();
            if (pyBridge) {
                pyBridge.onBoundsCleared();
            }
        });
        
        // 从 Python 调用的函数
        function showBounds(south, north, west, east) {
            drawnItems.clearLayers();
            
            if (south !== null && north !== null && west !== null && east !== null) {
                var bounds = [[south, west], [north, east]];
                var rect = L.rectangle(bounds, {
                    color: '#e74c3c',
                    weight: 2,
                    fillOpacity: 0.2
                });
                drawnItems.addLayer(rect);
                map.fitBounds(bounds, {padding: [50, 50]});
                
                info.update({south: south, north: north, west: west, east: east});
            } else {
                info.update();
            }
        }
        
        // 显示多个边界区域，用不同颜色区分
        function showMultipleBounds(boundsArray) {
            drawnItems.clearLayers();
            
            var allBounds = [];
            for (var i = 0; i < boundsArray.length; i++) {
                var item = boundsArray[i];
                var bounds = [[item.south, item.west], [item.north, item.east]];
                var rect = L.rectangle(bounds, {
                    color: item.color || '#3388ff',
                    weight: 2,
                    fillOpacity: 0.2
                });
                
                if (item.label) {
                    rect.bindTooltip(item.label, {permanent: true, direction: 'center'});
                }
                
                drawnItems.addLayer(rect);
                allBounds.push(bounds);
            }
            
            if (allBounds.length > 0) {
                var group = L.featureGroup(allBounds.map(function(b) {
                    return L.rectangle(b);
                }));
                map.fitBounds(group.getBounds(), {padding: [50, 50]});
            }
        }
        
        function clearBounds() {
            drawnItems.clearLayers();
            info.update();
        }
        
        function setView(lat, lng, zoom) {
            map.setView([lat, lng], zoom);
        }
        
        console.log('Leaflet map initialized');
    </script>
</body>
</html>
'''


class MapBridge(QObject):
    """Python 和 JavaScript 之间的通信桥接"""
    
    boundsDrawn = Signal(str)  # 边界绘制完成信号，传递 JSON 字符串
    boundsCleared = Signal()   # 边界清除信号
    
    def __init__(self, parent=None):
        super().__init__(parent)
    
    @Slot(str)
    def onBoundsDrawn(self, bounds_json: str):
        """接收从地图绘制的边界"""
        self.boundsDrawn.emit(bounds_json)
    
    @Slot()
    def onBoundsCleared(self):
        """边界被清除"""
        print("MapBridge: 收到清除信号")
        self.boundsCleared.emit()


class LeafletMapWidget(QWidget):
    """Leaflet 地图组件"""
    
    # 信号
    boundsDrawn = Signal(float, float, float, float)  # south, north, west, east
    boundsCleared = Signal()
    
    def __init__(self, parent=None, 
                 center_lat: float = 35.0, 
                 center_lng: float = 105.0, 
                 zoom: int = 4):
        """
        初始化地图组件
        
        Args:
            parent: 父组件
            center_lat: 地图中心纬度
            center_lng: 地图中心经度
            zoom: 地图缩放级别
        """
        super().__init__(parent)
        
        self.center_lat = center_lat
        self.center_lng = center_lng
        self.zoom = zoom
        
        self._setup_ui()
    
    def _setup_ui(self):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        if not WEBENGINE_AVAILABLE:
            from PySide6.QtWidgets import QLabel
            label = QLabel(
                "地图功能需要安装 PyQt6-WebEngine 或 PySide6-WebEngine\n\n"
                "请运行: pip install PyQt6-WebEngine\n\n"
                "安装后重启程序即可使用地图功能"
            )
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("color: #7f8c8d; font-size: 12px;")
            layout.addWidget(label)
            self.web_view = None
            return
        
        # 创建 WebEngine 视图
        self.web_view = QWebEngineView()
        
        # 允许缓存以提高性能
        try:
            self.web_view.page().profile().setHttpCacheType(self.web_view.page().profile().HttpCacheType.DiskHttpCache)
        except:
            pass
            
        # 忽略SSL错误(解决 handshake failed 问题)
        self.web_view.page().certificateError.connect(self._on_certificate_error)
        
        # 启用必要的设置和调试
        settings = self.web_view.page().settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        
        # 启用开发者工具（调试用）
        try:
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        except:
            pass
        
        # 捕获控制台消息
        self.web_view.page().javaScriptConsoleMessage = self._on_console_message
        
        # 设置 WebChannel
        self.map_channel = QWebChannel()
        self.map_bridge = MapBridge()
        self.map_channel.registerObject("bridge", self.map_bridge)
        self.web_view.page().setWebChannel(self.map_channel)
        
        # 连接信号
        self.map_bridge.boundsDrawn.connect(self._on_bounds_drawn)
        self.map_bridge.boundsCleared.connect(self._on_bounds_cleared)
        
        # 加载 HTML（使用 setHtml 确保 WebChannel 正常工作）
        html_path = self._get_html_path()
        if html_path and os.path.exists(html_path):
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            # 使用 setHtml 并设置 baseUrl，这样 WebChannel 才能正常工作
            base_url = QUrl.fromLocalFile(os.path.dirname(html_path) + '/')
            self.web_view.setHtml(html_content, base_url)
        else:
            print("使用内嵌HTML")
            self.web_view.setHtml(LEAFLET_MAP_HTML)
        
        layout.addWidget(self.web_view)
    
    def _on_certificate_error(self, error):
        """处理证书错误，返回True表示忽略错误"""
        return True
    
    def _get_html_path(self) -> Optional[str]:
        """获取HTML文件路径"""
        # 尝试多个可能的路径
        possible_paths = [
            os.path.join(os.path.dirname(__file__), '..', '..', 'resources', 'leaflet_map.html'),
            os.path.join(os.getcwd(), 'resources', 'leaflet_map.html'),
        ]
        
        for path in possible_paths:
            abs_path = os.path.abspath(path)
            if os.path.exists(abs_path):
                return abs_path
        
        return None
    
    def _on_console_message(self, level, message, line, source):
        """捕获JavaScript控制台消息"""
        print(f"[JS Console] {message} (line {line})")
    
    def _on_bounds_drawn(self, bounds_json: str):
        """处理边界绘制"""
        import json
        try:
            bounds = json.loads(bounds_json)
            self.boundsDrawn.emit(
                bounds['south'],
                bounds['north'],
                bounds['west'],
                bounds['east']
            )
        except Exception as e:
            print(f"解析边界数据失败: {e}")
    
    def _on_bounds_cleared(self):
        """处理边界清除"""
        self.boundsCleared.emit()
    
    def show_bounds(self, south: float, north: float, west: float, east: float):
        """
        在地图上显示边界
        
        Args:
            south: 南纬度
            north: 北纬度
            west: 西经度
            east: 东经度
        """
        if self.web_view:
            self.web_view.page().runJavaScript(
                f"showBounds({south}, {north}, {west}, {east});"
            )
    
    def show_multiple_bounds(self, bounds_list):
        """
        在地图上显示多个边界区域，用不同颜色区分
        
        Args:
            bounds_list: 边界列表，每个元素为字典，包含:
                - south, north, west, east: 边界坐标
                - color: 颜色 (可选，默认#3388ff)
                - label: 标签文本 (可选)
        
        Example:
            bounds_list = [
                {'south': 30, 'north': 40, 'west': 100, 'east': 110, 
                 'color': '#e74c3c', 'label': '选择区域'},
                {'south': 31, 'north': 39, 'west': 101, 'east': 109, 
                 'color': '#27ae60', 'label': 'DEM范围'}
            ]
        """
        if self.web_view:
            import json
            bounds_json = json.dumps(bounds_list)
            self.web_view.page().runJavaScript(
                f"showMultipleBoundsNoLabel({bounds_json});"
            )
    
    def show_bounds_with_legend(self, bounds_list):
        """
        在地图上显示多个边界区域，并在右下角显示图例
        
        Args:
            bounds_list: 边界列表，每个元素为字典，包含:
                - south, north, west, east: 边界坐标
                - color: 颜色
                - name: 图例名称
        """
        if self.web_view:
            import json
            bounds_json = json.dumps(bounds_list)
            self.web_view.page().runJavaScript(
                f"showBoundsWithLegend({bounds_json});"
            )
    
    def clear_bounds(self):
        """清除地图上的边界"""
        if self.web_view:
            self.web_view.page().runJavaScript("clearBounds();")
    
    def set_view(self, lat: float, lng: float, zoom: int):
        """
        设置地图视图
        
        Args:
            lat: 纬度
            lng: 经度
            zoom: 缩放级别
        """
        if self.web_view:
            self.web_view.page().runJavaScript(
                f"setView({lat}, {lng}, {zoom});"
            )
    
    def is_available(self) -> bool:
        """检查地图功能是否可用"""
        return WEBENGINE_AVAILABLE and self.web_view is not None
    
    def add_dem_layer(self, dem_path: str, opacity: float = 0.7):
        """
        添加DEM图层到地图
        
        Args:
            dem_path: DEM TIF文件路径
            opacity: 透明度 (0-1)
        """
        if not self.web_view or not os.path.exists(dem_path):
            print(f"无法添加DEM图层: 地图不可用或文件不存在")
            return
        
        try:
            # 将DEM转换为PNG并获取边界
            png_path, bounds = self._convert_dem_to_png(dem_path)
            if not png_path or not bounds:
                print("DEM转换失败")
                return
            
            # 读取PNG并转为base64
            import base64
            with open(png_path, 'rb') as f:
                png_data = base64.b64encode(f.read()).decode('utf-8')
            
            south, north, west, east = bounds
            
            # 在地图上添加图层
            js_code = f"""
                (function() {{
                    var imageUrl = 'data:image/png;base64,{png_data}';
                    var imageBounds = [[{south}, {west}], [{north}, {east}]];
                    
                    // 移除旧的DEM图层
                    if (window.demLayer) {{
                        map.removeLayer(window.demLayer);
                    }}
                    
                    // 添加新图层
                    window.demLayer = L.imageOverlay(imageUrl, imageBounds, {{
                        opacity: {opacity}
                    }}).addTo(map);
                    
                    // 适配视图
                    map.fitBounds(imageBounds, {{padding: [50, 50]}});
                    
                    console.log('DEM图层已添加');
                }})();
            """
            
            self.web_view.page().runJavaScript(js_code)
            print(f"DEM图层已添加: {os.path.basename(dem_path)}")
            
            # 清理临时PNG文件
            try:
                if os.path.exists(png_path):
                    os.remove(png_path)
            except:
                pass
                
        except Exception as e:
            print(f"添加DEM图层失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _convert_dem_to_png(self, dem_path: str):
        """
        将DEM TIF转换为PNG用于地图显示
        
        Returns:
            (png_path, bounds) 或 (None, None)
        """
        try:
            from osgeo import gdal
            import numpy as np
            from PIL import Image
            import tempfile
            
            # 读取DEM
            ds = gdal.Open(dem_path)
            if not ds:
                return None, None
            
            # 读取数据
            band = ds.GetRasterBand(1)
            data = band.ReadAsArray()
            
            # 获取nodata值
            nodata = band.GetNoDataValue()
            
            # 获取地理边界
            gt = ds.GetGeoTransform()
            width = ds.RasterXSize
            height = ds.RasterYSize
            
            west = gt[0]
            north = gt[3]
            east = gt[0] + width * gt[1]
            south = gt[3] + height * gt[5]
            
            # 如果是地理坐标系，检查是否需要重投影
            # 这里简化处理，假设已经是WGS84
            
            # 数据归一化
            if nodata is not None:
                mask = data != nodata
                if mask.any():
                    vmin = data[mask].min()
                    vmax = data[mask].max()
                else:
                    vmin, vmax = 0, 1
            else:
                vmin = data.min()
                vmax = data.max()
            
            # 归一化到0-255
            if vmax > vmin:
                normalized = (data - vmin) / (vmax - vmin) * 255
            else:
                normalized = np.zeros_like(data)
            
            normalized = normalized.astype(np.uint8)
            
            # 创建彩色地图（使用terrain色标）
            # 这里使用简单的灰度图，可以后续改进为彩色
            img = Image.fromarray(normalized, mode='L')
            
            # 如果有nodata值，设置透明度
            if nodata is not None:
                # 转换为RGBA
                img = img.convert('RGBA')
                datas = img.getdata()
                
                newData = []
                for i, item in enumerate(datas):
                    y = i // width
                    x = i % width
                    if data[y, x] == nodata:
                        newData.append((255, 255, 255, 0))  # 透明
                    else:
                        # 应用terrain色标
                        val = normalized[y, x]
                        # 简单的蓝-绿-黄-红渐变
                        if val < 64:
                            r, g, b = 0, val*2, 128
                        elif val < 128:
                            r, g, b = 0, 128+(val-64), 128-(val-64)*2
                        elif val < 192:
                            r, g, b = (val-128)*2, 128, 0
                        else:
                            r, g, b = 128+(val-192), 128-(val-192)*2, 0
                        newData.append((int(r), int(g), int(b), 255))
                
                img.putdata(newData)
            else:
                # 转换为彩色
                img = img.convert('RGB')
                pixels = img.load()
                for y in range(height):
                    for x in range(width):
                        val = normalized[y, x]
                        if val < 64:
                            r, g, b = 0, val*2, 128
                        elif val < 128:
                            r, g, b = 0, 128+(val-64), 128-(val-64)*2
                        elif val < 192:
                            r, g, b = (val-128)*2, 128, 0
                        else:
                            r, g, b = 128+(val-192), 128-(val-192)*2, 0
                        pixels[x, y] = (int(r), int(g), int(b))
            
            # 保存为临时PNG
            temp_png = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
            img.save(temp_png.name, 'PNG')
            temp_png.close()
            
            ds = None  # 关闭数据集
            
            return temp_png.name, (south, north, west, east)
            
        except Exception as e:
            print(f"DEM转PNG失败: {e}")
            import traceback
            traceback.print_exc()
            return None, None


# 测试代码
if __name__ == "__main__":
    import sys
    # Fix for QtWebEngine crash
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--single-process"
    from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QHBoxLayout
    
    class TestWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Leaflet Map Widget 测试")
            self.resize(900, 600)
            
            central = QWidget()
            self.setCentralWidget(central)
            layout = QVBoxLayout(central)
            
            # 先创建地图
            self.map = LeafletMapWidget(center_lat=35, center_lng=105, zoom=4)
            self.map.boundsDrawn.connect(self.on_bounds_drawn)
            self.map.boundsCleared.connect(self.on_bounds_cleared)
            
            # 按钮
            btn_layout = QHBoxLayout()
            
            beijing_btn = QPushButton("显示北京")
            beijing_btn.clicked.connect(lambda: self.map.show_bounds(39.4, 41.1, 115.4, 117.5))
            btn_layout.addWidget(beijing_btn)
            
            shanghai_btn = QPushButton("显示上海")
            shanghai_btn.clicked.connect(lambda: self.map.show_bounds(30.7, 31.5, 121.2, 121.9))
            btn_layout.addWidget(shanghai_btn)
            
            clear_btn = QPushButton("清除")
            clear_btn.clicked.connect(self.map.clear_bounds)
            btn_layout.addWidget(clear_btn)
            
            btn_layout.addStretch()
            layout.addLayout(btn_layout)
            
            # 添加地图到布局
            layout.addWidget(self.map)
        
        def on_bounds_drawn(self, south, north, west, east):
            print(f"绘制区域: 南={south:.6f}, 北={north:.6f}, 西={west:.6f}, 东={east:.6f}")
        
        def on_bounds_cleared(self):
            print("区域已清除")
    
    app = QApplication(sys.argv)
    window = TestWindow()
    window.show()
    sys.exit(app.exec())
