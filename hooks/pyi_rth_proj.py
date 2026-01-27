#-----------------------------------------------------------------------------
# Custom runtime hook for setting PROJ_LIB environment variable
# This hook runs before the main script to ensure PROJ can find proj.db
#-----------------------------------------------------------------------------

import os
import sys

if hasattr(sys, "_MEIPASS"):
    base_path = sys._MEIPASS
    
    # Set PROJ_LIB for PROJ library
    proj_candidates = [
        os.path.join(base_path, "proj_data"),
        os.path.join(base_path, "proj_data", "proj"),
        os.path.join(base_path, "proj"),
        os.path.join(base_path, "proj", "proj"),
        os.path.join(base_path, "Library", "share", "proj"),
    ]
    
    for p in proj_candidates:
        proj_db = os.path.join(p, "proj.db")
        if os.path.exists(proj_db):
            os.environ["PROJ_LIB"] = p
            os.environ["PROJ_DATA"] = p  # Some PROJ versions use PROJ_DATA
            break
    
    # Also set GDAL_DATA if not already set
    gdal_candidates = [
        os.path.join(base_path, "gdal_data"),
        os.path.join(base_path, "gdal_data", "gdal"),
        os.path.join(base_path, "data", "gdal"),
        os.path.join(base_path, "Library", "share", "gdal"),
    ]
    
    if "GDAL_DATA" not in os.environ or not os.path.exists(os.environ.get("GDAL_DATA", "")):
        for p in gdal_candidates:
            if os.path.exists(p) and (os.path.exists(os.path.join(p, "gcs.csv")) or os.path.exists(os.path.join(p, "header.dxf"))):
                os.environ["GDAL_DATA"] = p
                break
