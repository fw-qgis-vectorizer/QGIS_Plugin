# -*- coding: utf-8 -*-
"""
GDAL environment bootstrap for QGIS plugins.

This module ensures GDAL_DATA and PROJ_LIB environment variables are set
correctly for GDAL subprocess calls within QGIS plugins.
"""

import os
import sys


def ensure_gdal_environment():
    """
    Ensure GDAL_DATA and PROJ_LIB environment variables are set.
    
    This function finds the GDAL_DATA and PROJ_LIB directories from QGIS
    installation and returns their paths. It also sets them as environment
    variables if they're not already set.
    
    :returns: Tuple of (gdal_data_path, proj_lib_path)
    :rtype: tuple
    """
    gdal_data_path = None
    proj_lib_path = None
    
    # Try to get from environment first
    if 'GDAL_DATA' in os.environ:
        gdal_data_path = os.environ['GDAL_DATA']
    if 'PROJ_LIB' in os.environ:
        proj_lib_path = os.environ['PROJ_LIB']
    
    # If not in environment, try to find from QGIS installation
    if not gdal_data_path or not proj_lib_path:
        # Common QGIS installation paths
        possible_paths = []
        
        # Windows paths
        if sys.platform == 'win32':
            # QGIS installed via OSGeo4W
            possible_paths.extend([
                r'C:\OSGeo4W64\share\gdal',
                r'C:\OSGeo4W\share\gdal',
                r'C:\Program Files\QGIS 3.x\share\gdal',
                r'C:\Program Files (x86)\QGIS 3.x\share\gdal',
            ])
            # PROJ paths
            proj_paths = [
                r'C:\OSGeo4W64\share\proj',
                r'C:\OSGeo4W\share\proj',
                r'C:\Program Files\QGIS 3.x\share\proj',
                r'C:\Program Files (x86)\QGIS 3.x\share\proj',
            ]
        else:
            # Linux/Mac paths
            possible_paths.extend([
                '/usr/share/gdal',
                '/usr/local/share/gdal',
                '/opt/QGIS/share/gdal',
            ])
            proj_paths = [
                '/usr/share/proj',
                '/usr/local/share/proj',
                '/opt/QGIS/share/proj',
            ]
        
        # Try to find GDAL_DATA
        if not gdal_data_path:
            for path in possible_paths:
                # Check for gcs.csv file which indicates valid GDAL_DATA
                gcs_file = os.path.join(path, 'gcs.csv')
                if os.path.exists(gcs_file):
                    gdal_data_path = path
                    break
        
        # Try to find PROJ_LIB
        if not proj_lib_path:
            for path in proj_paths:
                # Check for proj.db or epsg file
                if os.path.exists(path) and (
                    os.path.exists(os.path.join(path, 'proj.db')) or
                    os.path.exists(os.path.join(path, 'epsg'))
                ):
                    proj_lib_path = path
                    break
    
    # Set environment variables if we found paths
    if gdal_data_path and not os.environ.get('GDAL_DATA'):
        os.environ['GDAL_DATA'] = gdal_data_path
    
    if proj_lib_path and not os.environ.get('PROJ_LIB'):
        os.environ['PROJ_LIB'] = proj_lib_path
    
    return gdal_data_path, proj_lib_path
