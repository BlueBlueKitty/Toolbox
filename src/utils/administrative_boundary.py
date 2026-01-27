'''
Author: Yibo Yuan 2633669459@qq.com
Date: 2026-01-26
Description: 行政区划选择器
    提供按省份和城市选择行政区划边界的功能

Copyright (c) 2026 by Yibo Yuan 2633669459@qq.com, All Rights Reserved. 
'''

import os
import sqlite3
from typing import List, Optional, Tuple, Dict, Any

# 数据库路径
DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'DEM_downloader', 'src', 'data', 'administrative_boundaries.db'
)


class AdministrativeBoundarySelector:
    """行政区划边界选择器"""
    
    def __init__(self, db_path: Optional[str] = None):
        """
        初始化选择器
        
        Args:
            db_path: SQLite数据库路径，默认为内置数据库
        """
        self.db_path = db_path or DEFAULT_DB_PATH
        self._connection = None
    
    @property
    def connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        if self._connection is None:
            if not os.path.exists(self.db_path):
                raise FileNotFoundError(f"找不到行政区划数据库: {self.db_path}")
            self._connection = sqlite3.connect(self.db_path)
        return self._connection
    
    def close(self):
        """关闭数据库连接"""
        if self._connection:
            self._connection.close()
            self._connection = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def get_provinces(self) -> List[str]:
        """获取所有省份名称"""
        try:
            cursor = self.connection.cursor()
            cursor.execute("""
                SELECT DISTINCT name 
                FROM boundaries 
                WHERE level = 'province'
                ORDER BY name
            """)
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"获取省份列表失败: {e}")
            return []
    
    def get_cities(self, province: str) -> List[str]:
        """
        获取指定省份的城市列表
        
        Args:
            province: 省份名称
            
        Returns:
            城市名称列表
        """
        try:
            cursor = self.connection.cursor()
            # 先获取省份的code
            cursor.execute("""
                SELECT code FROM boundaries 
                WHERE name = ? AND level = 'province'
            """, (province,))
            row = cursor.fetchone()
            if not row:
                return []
            
            province_code = row[0]
            # 省份code格式: 156410000 (中国代码156 + 省份代码41 + 0000)
            # 城市code格式: 156410100 (中国代码156 + 省份代码41 + 城市代码01 + 00)
            province_prefix = province_code[:5]  # 156 + 省份代码2位
            
            cursor.execute("""
                SELECT DISTINCT name 
                FROM boundaries 
                WHERE level = 'city' AND code LIKE ?
                ORDER BY name
            """, (province_prefix + '%',))
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"获取城市列表失败: {e}")
            return []
    
    def get_districts(self, province: str, city: str) -> List[str]:
        """
        获取指定城市的区县列表
        
        Args:
            province: 省份名称
            city: 城市名称
            
        Returns:
            区县名称列表
        """
        try:
            cursor = self.connection.cursor()
            # 先获取城市的code
            cursor.execute("""
                SELECT code FROM boundaries 
                WHERE name = ? AND level = 'city'
            """, (city,))
            row = cursor.fetchone()
            if not row:
                return []
            
            city_code = row[0]
            # 城市code格式: 156410100
            # 区县code格式: 156410101
            city_prefix = city_code[:7]  # 156 + 省份代码 + 城市代码
            
            cursor.execute("""
                SELECT DISTINCT name 
                FROM boundaries 
                WHERE level = 'district' AND code LIKE ?
                ORDER BY name
            """, (city_prefix + '%',))
            return [row[0] for row in cursor.fetchall()]
        except sqlite3.Error as e:
            print(f"获取区县列表失败: {e}")
            return []
    
    def get_boundary(
        self, 
        province: str, 
        city: Optional[str] = None, 
        district: Optional[str] = None
    ) -> Optional[Tuple[float, float, float, float]]:
        """
        获取行政区划的边界框
        
        Args:
            province: 省份名称
            city: 城市名称（可选）
            district: 区县名称（可选）
            
        Returns:
            (west, south, east, north) 或 None
        """
        try:
            cursor = self.connection.cursor()
            
            # 根据提供的参数确定查询目标
            if district:
                name = district
                level = 'district'
            elif city:
                name = city
                level = 'city'
            else:
                name = province
                level = 'province'
            
            cursor.execute("""
                SELECT west, south, east, north 
                FROM boundaries 
                WHERE name = ? AND level = ?
                LIMIT 1
            """, (name, level))
            
            row = cursor.fetchone()
            if row:
                return (row[0], row[1], row[2], row[3])
            return None
            
        except sqlite3.Error as e:
            print(f"获取边界失败: {e}")
            return None
    
    def search_by_name(self, name: str) -> List[Dict[str, Any]]:
        """
        按名称搜索行政区划
        
        Args:
            name: 搜索关键词
            
        Returns:
            匹配的行政区划列表
        """
        try:
            cursor = self.connection.cursor()
            pattern = f"%{name}%"
            
            cursor.execute("""
                SELECT name, code, level, west, south, east, north 
                FROM boundaries 
                WHERE name LIKE ?
                ORDER BY level, name
                LIMIT 100
            """, (pattern,))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    'name': row[0],
                    'code': row[1],
                    'level': row[2],
                    'bounds': (row[3], row[4], row[5], row[6]) if row[3] is not None else None
                })
            return results
            
        except sqlite3.Error as e:
            print(f"搜索失败: {e}")
            return []
    
    def is_available(self) -> bool:
        """检查数据库是否可用"""
        try:
            if not os.path.exists(self.db_path):
                return False
            # 尝试查询
            cursor = self.connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM boundaries WHERE level = 'province'")
            count = cursor.fetchone()[0]
            return count > 0
        except Exception as e:
            print(f"数据库检查失败: {e}")
            return False
