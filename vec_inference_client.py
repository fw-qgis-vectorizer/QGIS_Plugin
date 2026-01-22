# -*- coding: utf-8 -*-
"""
/***************************************************************************
VecInferenceClient
                                A QGIS plugin
Client for communicating with VEC inference service
                            -------------------
       begin                : 2025-12-09
       copyright            : (C) 2025 FieldWatch AI
***************************************************************************/


/***************************************************************************
*                                                                         *
*   This program is free software; you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 2 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************/
"""
import requests
import tempfile
import os
import time
import zipfile
import re
import logging
from qgis import processing
from qgis.core import (
   QgsMessageLog, Qgis, QgsRasterPipe,
   QgsRasterFileWriter, QgsRasterLayer, QgsGeometry,
   QgsVectorLayer, QgsFeature, QgsCoordinateTransform, QgsProject, QgsField, QgsApplication
)
from qgis.PyQt.QtCore import QVariant
from .gdal_bootstrap import ensure_gdal_environment


# Setup logging for raster cropping operations
logger = logging.getLogger('RasterCropLogger')
if not logger.hasHandlers():
   logger.setLevel(logging.INFO)
   ch = logging.StreamHandler()
   ch.setLevel(logging.INFO)
   formatter = logging.Formatter('%(levelname)s: %(message)s')
   ch.setFormatter(formatter)
   logger.addHandler(ch)




class VecInferenceClient:
    """Client for communicating with VEC inference service."""
  
    @staticmethod
    def _sanitize_urls(text):
        """
        Remove URLs from text to prevent exposing internal endpoints in logs.
        Handles HTTP/HTTPS URLs, storage URLs (GCS, S3, Azure), and file paths.
      
        :param text: Text that may contain URLs
        :type text: str
        :returns: Text with URLs removed
        :rtype: str
        """
        if not text:
            return text
      
        text_str = str(text)
      
        # Pattern to match HTTP/HTTPS URLs
        http_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        text_str = re.sub(http_pattern, '', text_str)
      
        # Pattern to match storage URLs (gs://, s3://, azure://, etc.)
        storage_pattern = r'(gs|s3|azure|gcs)://[^\s<>"{}|\\^`\[\]]+'
        text_str = re.sub(storage_pattern, '', text_str, flags=re.IGNORECASE)
      
        # Clean up extra spaces and colons left behind
        text_str = re.sub(r'\s+', ' ', text_str)  # Multiple spaces to single space
        text_str = re.sub(r'\s*:\s*', ': ', text_str)  # Clean up colons
        text_str = text_str.strip()
      
        return text_str
  
    def __init__(self, service_url, upload_url=None, jwt_token=None):
        """
        Initialize the inference client.
      
        :param service_url: URL of the inference service
        :type service_url: str
        :param upload_url: URL of the upload service (if different from inference service)
        :type upload_url: str
        :param jwt_token: JWT token for authentication
        :type jwt_token: str
        """
        self.service_url = service_url.rstrip('/')
        # If upload_url not provided, assume upload service is at same base URL
        self.upload_url = (upload_url.rstrip('/') if upload_url else service_url.rstrip('/'))
        self.jwt_token = jwt_token
  
    def validate_license_key(self, license_key):
        """
        Validate license key and get JWT token.
      
        :param license_key: License key to validate
        :type license_key: str
        :returns: Tuple of (jwt_token, expiry_timestamp) or (None, None) if invalid
        :rtype: tuple
        """
        auth_endpoint = f"{self.service_url}/auth/validate"
      
        try:
            response = requests.post(
                auth_endpoint,
                json={"license_key": license_key},
                timeout=10
            )
          
            response.raise_for_status()
            result = response.json()
          
            if result.get('valid') and result.get('token'):
                token = result['token']
                expiry = result.get('expires_at')
                self.jwt_token = token  # Store token in client
                return token, expiry
          
            return None, None
          
        except requests.exceptions.RequestException as e:
            QgsMessageLog.logMessage(
                "License validation failed",
                "VEC Plugin",
                Qgis.Warning
            )
            return None, None
  
    def _get_auth_headers(self):
        """Get headers with JWT token if available."""
        headers = {
            'User-Agent': 'QGIS-VEC-Plugin/1.0'
        }
        if self.jwt_token:
            headers['Authorization'] = f'Bearer {self.jwt_token}'
        return headers
  
    def process_raster_layer(self, raster_layer, progress_callback=None, crop_geometry=None, crop_geometry_crs=None):
        """
        Process a QGIS raster layer through the inference pipeline.
        Uses hardcoded compression settings.
      
        :param raster_layer: QGIS raster layer to process
        :type raster_layer: QgsRasterLayer
        :param progress_callback: Optional callback function for progress updates
        :type progress_callback: function
        :param crop_geometry: Optional polygon geometry to crop raster
        :type crop_geometry: QgsGeometry
        :param crop_geometry_crs: CRS of the canvas where geometry was drawn
        :type crop_geometry_crs: QgsCoordinateReferenceSystem
        :returns: Path to shapefile results
        :rtype: str
        """
        try:
            # Crop raster - geometry is now mandatory
            if not crop_geometry or crop_geometry.isEmpty():
                raise Exception("Crop geometry is required. Please draw a polygon before processing.")
          
            if progress_callback:
                progress_callback(5, "Cropping raster to selected area...")
          
            # Convert crop_geometry to a temporary vector layer for _crop_raster
            raster_crs = raster_layer.crs()
            logger.info(f"Original raster CRS: {raster_crs.authid()}")
            logger.info(f"Crop geometry CRS: {crop_geometry_crs.authid() if crop_geometry_crs and crop_geometry_crs.isValid() else 'None'}")
          
            # Transform geometry to raster CRS if needed
            if crop_geometry_crs and crop_geometry_crs.isValid() and crop_geometry_crs != raster_crs:
                transform = QgsCoordinateTransform(crop_geometry_crs, raster_crs, QgsProject.instance())
                crop_geometry = QgsGeometry(crop_geometry)
                crop_geometry.transform(transform)
                logger.info("Crop geometry transformed to raster CRS")
            else:
                crop_geometry = QgsGeometry(crop_geometry)
          
            # Create a memory layer with the crop geometry
            temp_vector_layer = QgsVectorLayer(f"Polygon?crs={raster_crs.authid()}", "temp_crop", "memory")
            if not temp_vector_layer.isValid():
                raise Exception("Failed to create temporary vector layer for cropping")
          
            # Add WKT field first
            temp_vector_layer.dataProvider().addAttributes([QgsField('wkt_geom', QVariant.String)])
            temp_vector_layer.updateFields()
          
            # Add geometry as feature with WKT
            feature = QgsFeature(temp_vector_layer.fields())
            feature.setGeometry(crop_geometry)
            feature['wkt_geom'] = crop_geometry.asWkt()
            temp_vector_layer.dataProvider().addFeature(feature)
            temp_vector_layer.updateExtents()
            # Explicitly set CRS to match raster (geometries are already transformed)
            temp_vector_layer.setCrs(raster_crs)
            logger.info(f"Temporary vector layer created with crop geometry. Features: {temp_vector_layer.featureCount()}, CRS: {temp_vector_layer.crs().authid()}")
          
            # Store original extent for validation
            original_extent = raster_layer.extent()
            original_width = raster_layer.width()
            original_height = raster_layer.height()
          
            # Crop the raster - pass None for vector_crs since geometries are already in raster CRS
            cropped_raster_layer = self._crop_raster(raster_layer, temp_vector_layer, 'wkt_geom', None)
          
            # Validate that we got a valid cropped layer
            if not cropped_raster_layer or not cropped_raster_layer.isValid():
                raise Exception("Failed to create cropped raster layer. Cannot proceed with original raster.")
          
            # Validate that the cropped layer is actually different from the original
            cropped_extent = cropped_raster_layer.extent()
            cropped_width = cropped_raster_layer.width()
            cropped_height = cropped_raster_layer.height()
          
            logger.info(f"Original raster extent: {original_extent.toString()}, size: {original_width}x{original_height}")
            logger.info(f"Cropped raster extent: {cropped_extent.toString()}, size: {cropped_width}x{cropped_height}")
          
            # Use the cropped layer for export
            raster_layer = cropped_raster_layer
            logger.info(f"Using cropped raster layer for export. CRS: {raster_layer.crs().authid()}")
            QgsMessageLog.logMessage(
                "Raster cropped, compressing for upload...",
                "VEC Plugin",
                Qgis.Info
            )
          
            # Export raster to temporary file with hardcoded compression (first compression)
            if progress_callback:
                progress_callback(10, "Exporting raster with compression...")
          
            temp_file = self._export_raster_to_temp(raster_layer)
          
            # Verify temp file was created
            if not os.path.exists(temp_file):
                raise Exception("Temporary file was not created")
          
            QgsMessageLog.logMessage(
                "Raster compressed and uploading for processing...",
                "VEC Plugin",
                Qgis.Info
            )
          
            try:
                # Step 1: Upload to GCS via /upload endpoint
                if progress_callback:
                    progress_callback(15, "Uploading raster for processing...")
              
                file_id = self._upload_to_gcs(temp_file)
                QgsMessageLog.logMessage(
                    f"Upload completed. File ID: {file_id}",
                    "VEC Plugin",
                    Qgis.Info
                )
              
                if not file_id:
                    raise Exception("Failed to get file_id from upload service")
              
                # Step 2: Call /infer/qgis/{file_id} endpoint
                if progress_callback:
                    progress_callback(25, "Starting inference...")
              
                QgsMessageLog.logMessage(
                    f"=== STARTING INFERENCE ===",
                    "VEC Plugin",
                    Qgis.Info
                )
              
                job_id = self._start_inference(file_id)
              
                if not job_id:
                    raise Exception("Failed to start inference job")
              
                # Step 3: Download shapefile (using file_id from upload)
                if progress_callback:
                    progress_callback(50, "Downloading results...")
              
                QgsMessageLog.logMessage(
                    "Pulling shapefile...",
                    "VEC Plugin",
                    Qgis.Info
                )
              
                shapefile_path = self._download_shapefile(file_id)
              
                QgsMessageLog.logMessage(
                    "Shapefile downloaded successfully",
                    "VEC Plugin",
                    Qgis.Info
                )
              
                if progress_callback:
                    progress_callback(100, "Complete!")
              
                return shapefile_path
              
            finally:
                # Clean up temp raster file
                if os.path.exists(temp_file):
                    os.remove(temp_file)
          
        except Exception as e:
            QgsMessageLog.logMessage(
                f"Error processing raster: {self._sanitize_urls(str(e))}",
                "VEC Plugin",
                Qgis.Critical
            )
            raise
  
    def _ensure_gdal_environment(self):
        """
        Ensure GDAL_DATA and PROJ_LIB environment variables are set for subprocess calls.
        This must be called before each processing.run() call to ensure subprocesses inherit the vars.
        Uses shared function from gdal_bootstrap module.
        """
        gdal_data_path, proj_lib_path = ensure_gdal_environment()
      
        if gdal_data_path:
            logger.info(f"Set GDAL_DATA={gdal_data_path} (gcs.csv exists: {os.path.exists(os.path.join(gdal_data_path, 'gcs.csv'))})")
        else:
            logger.warning("GDAL_DATA not found - EPSG support may be limited")
      
        if proj_lib_path:
            logger.info(f"Set PROJ_LIB={proj_lib_path}")
        else:
            logger.warning("PROJ_LIB not found")
  
    def _export_raster_to_temp(self, raster_layer):
        """
        Export raster layer to temporary GeoTIFF file with maximum compression.
        Uses aggressive compression settings to minimize file size for upload.
      
        :param raster_layer: QGIS raster layer
        :type raster_layer: QgsRasterLayer
        :returns: Path to temporary file
        :rtype: str
        """
        # Ensure GDAL environment is set before processing
        self._ensure_gdal_environment()
      
        # Get GDAL paths for passing to subprocess via EXTRA
        gdal_data_path, proj_lib_path = ensure_gdal_environment()
      
        # Get raster info for logging
        provider = raster_layer.dataProvider()
        width = provider.xSize()
        height = provider.ySize()
        band_count = raster_layer.bandCount()
      
        # Log original raster size info
        QgsMessageLog.logMessage(
            f"Exporting raster: {width}x{height}, {band_count} band(s)",
            "VEC Plugin",
            Qgis.Info
        )
      
        # Maximum compression settings:
        # - DEFLATE with zlevel 9 (maximum)
        # - Predictor 2 for better compression of imagery
        # - TILED=YES for better compression and faster processing
        # - BLOCKXSIZE and BLOCKYSIZE for tiling
        compression = 'DEFLATE'
        predictor = '2'  # Horizontal predictor (best for imagery)
        zlevel = 9  # Maximum compression level (1-9)
      
        # Create temporary file
        temp_fd, temp_path = tempfile.mkstemp(suffix='.tif')
        os.close(temp_fd)
      
        # Try using QGIS processing first (more reliable)
        try:
            from qgis.core import QgsProcessing
            from qgis import processing
          
            # Build creation options for maximum compression
            # TILED=YES significantly improves compression ratios
            creation_options = [
                f"COMPRESS={compression}",
                f"ZLEVEL={zlevel}",
                f"PREDICTOR={predictor}",
                "TILED=YES",
                "BLOCKXSIZE=512",
                "BLOCKYSIZE=512"
            ]
          
            # Build EXTRA parameter with GDAL config options to pass GDAL_DATA and PROJ_LIB
            # GDAL command-line tools accept --config GDAL_DATA <path> and --config PROJ_LIB <path>
            extra_options = []
            if gdal_data_path:
                extra_options.append(f'--config GDAL_DATA "{gdal_data_path}"')
            if proj_lib_path:
                extra_options.append(f'--config PROJ_LIB "{proj_lib_path}"')
            extra_string = ' '.join(extra_options) if extra_options else ''
          
            # Use gdal:translate with compression options
            params = {
                'INPUT': raster_layer,
                'OUTPUT': temp_path,
                'CREATEOPTIONS': '|'.join(creation_options),
                'EXTRA': extra_string
            }
          
            result = processing.run("gdal:translate", params)
            output_path = result['OUTPUT']
          
            # Log file size after compression
            if os.path.exists(output_path):
                file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
                QgsMessageLog.logMessage(
                    f"Compressed raster size: {file_size_mb:.2f} MB",
                    "VEC Plugin",
                    Qgis.Info
                )
              
                # If file is still very large (>400MB), try resampling to reduce size
                # This helps prevent 413 errors from upload service
                max_file_size_mb = 400  # Threshold for resampling (conservative)
                if file_size_mb > max_file_size_mb:
                    QgsMessageLog.logMessage(
                        f"File size ({file_size_mb:.2f} MB) exceeds threshold ({max_file_size_mb} MB). "
                        f"Attempting to resample to reduce size...",
                        "VEC Plugin",
                        Qgis.Warning
                    )
                  
                    # Calculate resampling factor to get file under threshold
                    # Target ~350MB to leave buffer
                    target_size_mb = 350
                    resample_factor = (target_size_mb / file_size_mb) ** 0.5  # Square root since we scale X and Y
                    new_width = max(512, int(width * resample_factor))  # Minimum 512px
                    new_height = max(512, int(height * resample_factor))
                  
                    QgsMessageLog.logMessage(
                        f"Resampling from {width}x{height} to {new_width}x{new_height} "
                        f"(factor: {resample_factor:.2f})",
                        "VEC Plugin",
                        Qgis.Info
                    )
                  
                    # Create resampled version using gdal:translate with size reduction
                    resampled_fd, resampled_path = tempfile.mkstemp(suffix='.tif')
                    os.close(resampled_fd)
                  
                    resampled_layer = QgsRasterLayer(output_path, 'temp_resampled')
                    if resampled_layer.isValid():
                        resample_options = [
                           f"COMPRESS={compression}",
                           f"ZLEVEL={zlevel}",
                           f"PREDICTOR={predictor}",
                           "TILED=YES",
                           "BLOCKXSIZE=512",
                           "BLOCKYSIZE=512"
                        ]
                      
                        extra_options = []
                        if gdal_data_path:
                           extra_options.append(f'--config GDAL_DATA "{gdal_data_path}"')
                        if proj_lib_path:
                           extra_options.append(f'--config PROJ_LIB "{proj_lib_path}"')
                        extra_string = ' '.join(extra_options) if extra_options else ''
                      
                        resample_params = {
                           'INPUT': resampled_layer,
                           'OUTPUT': resampled_path,
                           'WIDTH': new_width,
                           'HEIGHT': new_height,
                           'CREATEOPTIONS': '|'.join(resample_options),
                           'EXTRA': extra_string
                        }
                      
                        try:
                           resample_result = processing.run("gdal:translate", resample_params)
                           resampled_output = resample_result['OUTPUT']
                          
                           # Clean up original large file and use resampled version
                           if os.path.exists(resampled_output):
                               resampled_size_mb = os.path.getsize(resampled_output) / (1024 * 1024)
                               QgsMessageLog.logMessage(
                                   f"Resampled raster size: {resampled_size_mb:.2f} MB "
                                   f"(reduced from {file_size_mb:.2f} MB)",
                                   "VEC Plugin",
                                   Qgis.Info
                               )
                              
                               # Delete original large file
                               try:
                                   os.remove(output_path)
                               except:
                                   pass
                              
                               output_path = resampled_output
                           else:
                               QgsMessageLog.logMessage(
                                   "Resampling failed, using original compressed file",
                                   "VEC Plugin",
                                   Qgis.Warning
                               )
                        except Exception as resample_error:
                           QgsMessageLog.logMessage(
                               f"Resampling failed: {str(resample_error)}. Using original file.",
                               "VEC Plugin",
                               Qgis.Warning
                           )
                           # Continue with original file
                else:
                    # File size is acceptable, continue
                    pass
          
            return output_path
        except Exception as e:
            # Log the exception but try fallback
            QgsMessageLog.logMessage(
                f"GDAL translate failed, trying fallback method: {str(e)}",
                "VEC Plugin",
                Qgis.Warning
            )
          
            # Fallback to direct export (without compression)
            # This is less optimal but better than failing completely
            provider = raster_layer.dataProvider()
            pipe = QgsRasterPipe()
          
            if not pipe.set(provider.clone()):
                raise Exception("Failed to create raster pipe")
          
            file_writer = QgsRasterFileWriter(temp_path)
            error = file_writer.writeRaster(
                pipe,
                provider.xSize(),
                provider.ySize(),
                provider.extent(),
                provider.crs()
            )
          
            if error != QgsRasterFileWriter.NoError:
                raise Exception(f"Failed to write raster: {error}")
          
            # Log fallback file size
            if os.path.exists(temp_path):
                file_size_mb = os.path.getsize(temp_path) / (1024 * 1024)
                QgsMessageLog.logMessage(
                    f"Fallback export (no compression) size: {file_size_mb:.2f} MB",
                    "VEC Plugin",
                    Qgis.Warning
                )
          
            return temp_path
  
    def _crop_raster(self, raster_layer, vector_layer, wkt_field='wkt_geom', vector_crs=None):
        """
        Crop a raster layer using polygons from a vector layer (with WKT geometries).


        :param raster_layer: QGIS raster layer to crop
        :type raster_layer: QgsRasterLayer
        :param vector_layer: Vector layer containing polygons in WKT
        :type vector_layer: QgsVectorLayer
        :param wkt_field: Field name containing WKT geometry
        :type wkt_field: str
        :param vector_crs: CRS of the vector layer (if different from raster CRS)
        :type vector_crs: QgsCoordinateReferenceSystem
        :returns: Cropped raster layer
        :rtype: QgsRasterLayer
        """
        try:
            raster_crs = raster_layer.crs()
            logger.info(f"Raster CRS: {raster_crs.authid()}")


            # Determine vector CRS
            geometry_crs = vector_crs if vector_crs else vector_layer.crs()
            logger.info(f"Vector CRS: {geometry_crs.authid() if geometry_crs.isValid() else 'None'}")


            # Prepare in-memory polygon layer for mask
            # Use authid() to get the CRS identifier, or use toWkt() if authid is not available
            try:
                crs_authid = raster_crs.authid()
                if not crs_authid or crs_authid == '':
                    # Fall back to EPSG code or WKT if authid is not available
                    epsg_code = raster_crs.postgisSrid()
                    if epsg_code and epsg_code > 0:
                        crs_authid = f"EPSG:{epsg_code}"
                    else:
                        crs_authid = raster_crs.toWkt()
            except:
                crs_authid = raster_crs.toWkt()
          
            mem_layer = QgsVectorLayer(f"Polygon?crs={crs_authid}", "mask", "memory")
            provider = mem_layer.dataProvider()
            if not mem_layer.isValid():
                raise Exception("Failed to create memory mask layer")
          
            # Explicitly set CRS to ensure it matches raster
            mem_layer.setCrs(raster_crs)
            logger.info(f"Memory mask layer CRS set to: {mem_layer.crs().authid() if mem_layer.crs().authid() else 'WKT'}")


            # Raster extent for filtering
            raster_extent = raster_layer.extent()
            logger.info(f"Raster extent: {raster_extent.toString()}")

            # Prepare CRS transform if needed
            transform = None
            if geometry_crs.isValid() and raster_crs != geometry_crs:
                transform = QgsCoordinateTransform(geometry_crs, raster_crs, QgsProject.instance())
                logger.info("CRS transform prepared for vector geometries.")


            # Add polygons from WKT
            features_to_add = []
            skipped_count = 0
            for feat in vector_layer.getFeatures():
                wkt = feat[wkt_field]
                if not wkt:
                    skipped_count += 1
                    logger.warning("Skipped feature: empty WKT")
                    continue

                geom = QgsGeometry.fromWkt(wkt)
                if geom.isEmpty():
                    skipped_count += 1
                    logger.warning("Skipped feature: empty geometry")
                    continue

                # Transform geometry if needed
                if transform:
                    try:
                        geom.transform(transform)
                        logger.info("Geometry transformed to raster CRS")
                    except Exception as transform_err:
                        logger.error(f"CRS transformation failed: {transform_err}")
                        skipped_count += 1
                        continue

                # Check if polygon intersects raster
                geom_bbox = geom.boundingBox()
                if not geom_bbox.intersects(raster_extent):
                    skipped_count += 1
                    logger.warning(f"Polygon outside raster extent. Polygon: {geom_bbox.toString()}, Raster: {raster_extent.toString()}")
                    continue

                f = QgsFeature()
                f.setGeometry(geom)
                features_to_add.append(f)

            # CRITICAL CHECK: Ensure we have at least one polygon
            if len(features_to_add) == 0:
                error_msg = (
                    f"No valid polygons found for cropping. "
                    f"Skipped {skipped_count} features. "
                    f"Make sure your polygon intersects the raster layer."
                )
                logger.error(error_msg)
                QgsMessageLog.logMessage(error_msg, "VEC Plugin", Qgis.Critical)
                raise Exception(error_msg)

            # Add all features at once
            if not provider.addFeatures(features_to_add):
                raise Exception(f"Failed to add {len(features_to_add)} features to mask layer")
            
            mem_layer.updateExtents()
            logger.info(f"Memory mask layer created with {len(features_to_add)} polygon(s)")


            # Ensure GDAL environment is set right before GDAL subprocess call
            # QGIS processing framework spawns GDAL as subprocess, so env vars must be set now
            self._ensure_gdal_environment()
          
            # Get GDAL paths for passing to subprocess via EXTRA
            # GDAL subprocesses don't inherit Python environment variables, so we pass via --config
            gdal_data_path, proj_lib_path = ensure_gdal_environment()
          
            # Build EXTRA parameter with GDAL config options
            # GDAL command-line tools accept --config GDAL_DATA <path> and --config PROJ_LIB <path>
            extra_options = []
            if gdal_data_path:
                extra_options.append(f'--config GDAL_DATA "{gdal_data_path}"')
            if proj_lib_path:
                extra_options.append(f'--config PROJ_LIB "{proj_lib_path}"')
            extra_string = ' '.join(extra_options) if extra_options else ''


            # Crop raster using GDAL clip tool
            # Don't set SOURCE_CRS/TARGET_CRS explicitly - let GDAL infer from layers
            # This avoids transformation issues with complex compound CRS
            params = {
                'INPUT': raster_layer,
                'MASK': mem_layer,
                'NODATA': None,
                'ALPHA_BAND': False,
                'CROP_TO_CUTLINE': True,
                'KEEP_RESOLUTION': True,
                'SET_RESOLUTION': False,
                'X_RESOLUTION': None,
                'Y_RESOLUTION': None,
                'MULTITHREADING': True,  # Enable multithreading
                'OPTIONS': '',
                'DATA_TYPE': 0,
                'EXTRA': extra_string,  # Pass GDAL_DATA and PROJ_LIB via --config
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }


            logger.info("Running GDAL cliprasterbymasklayer...")
            result = processing.run("gdal:cliprasterbymasklayer", params)
            
            # Validate processing result
            if not result or 'OUTPUT' not in result:
                raise Exception("GDAL processing did not return OUTPUT path")
            
            output_path = result['OUTPUT']
            logger.info(f"GDAL processing completed. Output: {output_path}")
            
            # Check if output file exists
            if not os.path.exists(output_path):
                raise Exception(f"GDAL output file does not exist: {output_path}")
            
            # Check file size
            file_size = os.path.getsize(output_path)
            if file_size == 0:
                raise Exception(f"GDAL output file is empty (0 bytes): {output_path}")
            
            logger.info(f"Output file exists: {output_path}, size: {file_size} bytes")
            
            # Try to load the raster layer
            cropped_layer = QgsRasterLayer(output_path, 'cropped_raster')
            
            # Get detailed error if layer is invalid
            if not cropped_layer.isValid():
                error_details = cropped_layer.error().message() if cropped_layer.error().isValid() else "Unknown error"
                raise Exception(
                    f"Failed to create valid cropped raster layer. "
                    f"File exists: {os.path.exists(output_path)}, "
                    f"Size: {file_size} bytes, "
                    f"Error: {error_details}"
                )
            
            # Validate layer has dimensions
            if cropped_layer.width() == 0 or cropped_layer.height() == 0:
                raise Exception(f"Cropped raster has zero dimensions: {cropped_layer.width()}x{cropped_layer.height()}")
            
            logger.info(f"Cropped raster created successfully. CRS: {cropped_layer.crs().authid()}, "
                       f"Size: {cropped_layer.width()}x{cropped_layer.height()}")
            return cropped_layer


        except Exception as e:
            logger.critical(f"Raster cropping failed: {e}")
            QgsMessageLog.logMessage(
                f"Raster cropping error details: {str(e)}",
                "VEC Plugin",
                Qgis.Critical
            )
            raise Exception(f"Raster cropping failed: {e}. Cannot proceed with original raster.") from e


    def _upload_to_gcs(self, image_path):
        """
        Upload image to GCS via two-step process:
        1. Get signed URL from Cloud Run (query params only, no file upload)
        2. Upload directly to GCS using signed URL (bypasses Cloud Run 32MB limit)
      
        :param image_path: Path to image file
        :type image_path: str
        :returns: file_id from upload service
        :rtype: str
        """
        # Validate file exists and is readable
        if not os.path.exists(image_path):
            raise Exception(f"File not found: {image_path}")
      
        if not os.access(image_path, os.R_OK):
            raise Exception(f"File is not readable: {image_path}")
      
        file_size = os.path.getsize(image_path)
        if file_size == 0:
            raise Exception(f"File is empty: {image_path}")
      
        file_size_mb = file_size / (1024 * 1024)
        QgsMessageLog.logMessage(
            f"Uploading file: {os.path.basename(image_path)} ({file_size_mb:.2f} MB)",
            "VEC Plugin",
            Qgis.Info
        )
      
        try:
            upload_endpoint = f"{self.upload_url}/upload"
            file_name = os.path.basename(image_path)
            content_type = "image/tiff"  # More accurate than application/octet-stream
            
            # Calculate expiration time based on file size
            # Small files (<100MB): 1 hour
            # Large files (100MB+): 4 hours
            # Very large files (500MB+): 8 hours
            # Huge files (1GB+): 24 hours
            if file_size_mb < 100:
                expiration_seconds = 3600  # 1 hour
            elif file_size_mb < 500:
                expiration_seconds = 14400  # 4 hours
            elif file_size_mb < 1024:
                expiration_seconds = 28800  # 8 hours
            else:
                expiration_seconds = 86400  # 24 hours
            
            # Step 1: Get signed URL from Cloud Run
            try:
                response = requests.post(
                    upload_endpoint,
                    params={
                        "filename": file_name,
                        "content_type": content_type,
                        "expiration_seconds": expiration_seconds
                    },
                    headers=self._get_auth_headers(),  # JWT auth required for this step
                    timeout=30  # Small request, should be fast
                )
            
            except requests.exceptions.ConnectionError as e:
                error_msg = self._sanitize_urls(str(e))
                QgsMessageLog.logMessage(
                    f"Upload connection error: {error_msg}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception("Connection error - server unreachable. Check your internet connection and try again.")
            
            except requests.exceptions.Timeout as e:
                error_msg = self._sanitize_urls(str(e))
                QgsMessageLog.logMessage(
                    f"Upload timeout (getting signed URL): {error_msg}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception("Timeout getting signed URL. Please try again.")
            
            except requests.exceptions.RequestException as e:
                error_msg = self._sanitize_urls(str(e))
                QgsMessageLog.logMessage(
                    f"Upload request exception (step 1): {error_msg}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception(f"Failed to get signed URL: {type(e).__name__}")
            
            # Check HTTP status code for step 1
            if response.status_code >= 400:
                error_detail = ""
                try:
                    error_detail = response.text[:500]
                except:
                    pass
              
                sanitized_error = self._sanitize_urls(error_detail)
                QgsMessageLog.logMessage(
                    f"Upload HTTP error {response.status_code} (getting signed URL): {sanitized_error}",
                    "VEC Plugin",
                    Qgis.Warning
                )
              
                if response.status_code == 401:
                    raise Exception("Authentication failed (401). JWT token may be invalid or expired. Please re-validate your license key.")
                elif response.status_code == 403:
                    raise Exception("Access forbidden (403). Your license may not have permission for this operation.")
                elif response.status_code == 429:
                    raise Exception("Rate limit exceeded (429). Please wait a moment and try again.")
                elif response.status_code >= 500:
                    raise Exception(f"Server error ({response.status_code}). The upload service encountered an internal error. Please try again later.")
                else:
                    raise Exception(f"Failed to get signed URL (HTTP {response.status_code}). Please check your connection and try again.")
            
            # Parse response JSON from step 1
            try:
                result = response.json()
            except ValueError as e:
                QgsMessageLog.logMessage(
                    f"Failed to parse signed URL response JSON: {e}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception("Upload service returned invalid response. Please try again.")
            
            # Extract signed_url and file_id from response
            signed_url = result.get('signed_url') or result.get('signedUrl') or result.get('url')
            file_id = result.get('file_id') or result.get('id') or result.get('fileId')
            
            if not signed_url:
                QgsMessageLog.logMessage(
                    f"Upload response missing signed_url. Response: {self._sanitize_urls(str(result))}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception("Upload service did not return signed_url in response")
            
            if not file_id:
                QgsMessageLog.logMessage(
                    f"Upload response missing file_id. Response: {self._sanitize_urls(str(result))}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception("Upload service did not return file_id in response")
            
            # Step 2: Upload directly to GCS using signed URL with progress tracking
            try:
                uploaded_bytes = 0
                chunk_size = 8192  # 8KB chunks for progress tracking
                last_logged_percent = -1
                
                def read_file_with_progress():
                    """Generator that reads file in chunks and logs progress"""
                    nonlocal uploaded_bytes, last_logged_percent
                    with open(image_path, 'rb') as f:
                        while True:
                            chunk = f.read(chunk_size)
                            if not chunk:
                                break
                            uploaded_bytes += len(chunk)
                            percent = int((uploaded_bytes / file_size) * 100)
                            uploaded_mb = uploaded_bytes / (1024 * 1024)
                            
                            # Log every 5% progress
                            if percent >= last_logged_percent + 5:
                                last_logged_percent = percent
                                QgsMessageLog.logMessage(
                                    f"Upload progress: {percent}% ({uploaded_mb:.2f} MB / {file_size_mb:.2f} MB)",
                                    "VEC Plugin",
                                    Qgis.Info
                                )
                            yield chunk
                
                upload_response = requests.put(
                    signed_url,  # Signed URL already contains authentication
                    data=read_file_with_progress(),
                    headers={"Content-Type": content_type},  # Must match content_type used in step 1
                    timeout=600  # Large files need longer timeout
                )
                
                # Log 100% completion if not already logged
                if uploaded_bytes >= file_size:
                    QgsMessageLog.logMessage(
                        f"Upload progress: 100% ({file_size_mb:.2f} MB / {file_size_mb:.2f} MB)",
                        "VEC Plugin",
                        Qgis.Info
                    )
            
            except requests.exceptions.ConnectionError as e:
                error_msg = self._sanitize_urls(str(e))
                QgsMessageLog.logMessage(
                    f"GCS upload connection error: {error_msg}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception("Connection error uploading to GCS. Check your internet connection and try again.")
            
            except requests.exceptions.Timeout as e:
                error_msg = self._sanitize_urls(str(e))
                QgsMessageLog.logMessage(
                    f"GCS upload timeout: {error_msg}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception(f"Upload timeout after 600 seconds. File ({file_size_mb:.2f} MB) may be too large or connection too slow. Try again or use a faster connection.")
            
            except requests.exceptions.RequestException as e:
                error_msg = self._sanitize_urls(str(e))
                QgsMessageLog.logMessage(
                    f"GCS upload request exception: {error_msg}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception(f"GCS upload failed: {type(e).__name__}")
            
            # Check HTTP status code for step 2 (GCS upload)
            if upload_response.status_code >= 400:
                error_detail = ""
                try:
                    error_detail = upload_response.text[:500]
                except:
                    pass
              
                sanitized_error = self._sanitize_urls(error_detail)
                QgsMessageLog.logMessage(
                    f"GCS upload HTTP error {upload_response.status_code}: {sanitized_error}",
                    "VEC Plugin",
                    Qgis.Warning
                )
              
                if upload_response.status_code == 403:
                    raise Exception("GCS upload forbidden (403). Signed URL may have expired or be invalid. Please try again.")
                elif upload_response.status_code == 413:
                    raise Exception("File too large (413). GCS rejected the file size.")
                elif upload_response.status_code >= 500:
                    raise Exception(f"GCS server error ({upload_response.status_code}). Please try again later.")
                else:
                    raise Exception(f"GCS upload failed with HTTP {upload_response.status_code}. Please try again.")
            
            # Upload successful
            return file_id
          
        except requests.exceptions.RequestException as e:
            # Fallback error handling
            error_msg = self._sanitize_urls(str(e))
            QgsMessageLog.logMessage(
                f"Upload request exception (outer catch): {error_msg}",
                "VEC Plugin",
                Qgis.Warning
            )
            raise Exception(f"Upload failed: {type(e).__name__}")
          
        except Exception as e:
            # Re-raise if already formatted, otherwise wrap
            if "Authentication failed" in str(e) or "Connection error" in str(e) or "timeout" in str(e).lower():
                raise
            error_msg = self._sanitize_urls(str(e))
            QgsMessageLog.logMessage(
                f"Upload error: {error_msg}",
                "VEC Plugin",
                Qgis.Warning
            )
            raise Exception(f"Upload error: {error_msg}")
  
    def _start_inference(self, file_id, prompt="building", confidence=0.3, alpha=0.5,
                        iou_threshold=0.15, remove_overlaps=True, overlap_method="clip"):
        """
        Start inference job by calling /infer/qgis/{file_id} endpoint.
      
        :param file_id: File ID from upload service
        :type file_id: str
        :param prompt: Text prompt for segmentation (default: "building")
        :param confidence: Confidence threshold (default: 0.3)
        :param alpha: Overlay transparency (default: 0.5)
        :param iou_threshold: IoU threshold for merging (default: 0.15)
        :param remove_overlaps: Remove overlaps (default: True)
        :param overlap_method: Overlap method: "clip" or "merge" (default: "clip")
        :returns: job_id from inference service
        :rtype: str
        """
        # Build query parameters
        params = {
            'prompt': prompt,
            'confidence': confidence,
            'alpha': alpha,
            'iou_threshold': iou_threshold,
            'remove_overlaps': remove_overlaps,
            'overlap_method': overlap_method
        }
      
        # Call inference endpoint
        inference_endpoint = f"{self.service_url}/infer/qgis/{file_id}"
        
        try:
            headers = self._get_auth_headers()
            
            try:
                response = requests.post(
                    inference_endpoint,
                    params=params,
                    headers=headers,
                    timeout=30
                )
                QgsMessageLog.logMessage(
                    f"HTTP request completed. Status code: {response.status_code}",
                    "VEC Plugin",
                    Qgis.Info
                )
            except requests.exceptions.Timeout as timeout_err:
                QgsMessageLog.logMessage(
                    f"Request timed out after 30 seconds: {self._sanitize_urls(str(timeout_err))}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise
            except requests.exceptions.ConnectionError as conn_err:
                QgsMessageLog.logMessage(
                    f"Connection error during request: {self._sanitize_urls(str(conn_err))}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise
            
            QgsMessageLog.logMessage(
                f"Response received. Status code: {response.status_code}",
                "VEC Plugin",
                Qgis.Info
            )
            
            # Log response details before raising for status
            if response.status_code >= 400:
                try:
                    error_detail = response.text[:500]
                    QgsMessageLog.logMessage(
                        f"Inference endpoint error response: {self._sanitize_urls(error_detail)}",
                        "VEC Plugin",
                        Qgis.Warning
                    )
                except:
                    QgsMessageLog.logMessage(
                        f"Inference endpoint returned error status {response.status_code} but no readable error body",
                        "VEC Plugin",
                        Qgis.Warning
                    )
            
            response.raise_for_status()
            
            try:
                result = response.json()
            except ValueError as json_err:
                QgsMessageLog.logMessage(
                    f"Failed to parse inference response as JSON: {json_err}. Response text: {response.text[:200]}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception(f"Inference service returned invalid JSON: {json_err}")
            
            # Extract job_id from response
            job_id = result.get('job_id') or result.get('jobId') or result.get('jobID')
            
            if not job_id:
                QgsMessageLog.logMessage(
                    f"Inference response missing job_id. Response keys: {list(result.keys())}, "
                    f"Response: {self._sanitize_urls(str(result))}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception("Inference service did not return job_id")
            
            QgsMessageLog.logMessage(
                f"Inference job started successfully. Job ID: {job_id}",
                "VEC Plugin",
                Qgis.Info
            )
          
            return job_id
          
        except requests.exceptions.ConnectionError as e:
            error_msg = self._sanitize_urls(str(e))
            QgsMessageLog.logMessage(
                f"Inference connection error: {error_msg}",
                "VEC Plugin",
                Qgis.Warning
            )
            raise Exception(f"Inference connection failed: {error_msg}")
        
        except requests.exceptions.Timeout as e:
            error_msg = self._sanitize_urls(str(e))
            QgsMessageLog.logMessage(
                f"Inference timeout error: {error_msg}",
                "VEC Plugin",
                Qgis.Warning
            )
            raise Exception(f"Inference request timed out after 30 seconds: {error_msg}")
        
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else None
            error_detail = ""
            try:
                if e.response:
                    error_detail = e.response.text[:500]
            except:
                pass
            
            sanitized_error = self._sanitize_urls(error_detail)
            QgsMessageLog.logMessage(
                f"Inference HTTP error {status_code}: {sanitized_error}",
                "VEC Plugin",
                Qgis.Warning
            )
            
            if status_code == 401:
                raise Exception("Authentication failed (401). JWT token may be invalid or expired. Please re-validate your license key.")
            elif status_code == 403:
                raise Exception("Access forbidden (403). Your license may not have permission for this operation.")
            elif status_code == 404:
                raise Exception(f"File not found (404). File ID {file_id} may not exist or may not be ready yet.")
            elif status_code == 429:
                raise Exception("Rate limit exceeded (429). Please wait a moment and try again.")
            elif status_code >= 500:
                raise Exception(f"Server error ({status_code}). The inference service encountered an internal error. Please try again later.")
            else:
                raise Exception(f"Inference request failed with HTTP {status_code}: {sanitized_error}")
        
        except requests.exceptions.RequestException as e:
            error_msg = self._sanitize_urls(str(e))
            QgsMessageLog.logMessage(
                f"Inference request exception: {type(e).__name__}: {error_msg}",
                "VEC Plugin",
                Qgis.Warning
            )
            raise Exception(f"Inference request failed: {type(e).__name__} - {error_msg}")
        
        except Exception as e:
            error_msg = self._sanitize_urls(str(e))
            QgsMessageLog.logMessage(
                f"Inference unexpected error: {type(e).__name__}: {error_msg}",
                "VEC Plugin",
                Qgis.Warning
            )
            raise Exception(f"Inference error: {error_msg}")
  
    def _poll_status(self, job_id, progress_callback=None, poll_interval=5, max_wait_time=3600):
        """
        Poll status endpoint until job is complete.
      
        :param job_id: Job ID from inference service
        :type job_id: str
        :param progress_callback: Optional callback function for progress updates
        :type progress_callback: function
        :param poll_interval: Seconds between polls (default: 5)
        :param max_wait_time: Maximum time to wait in seconds (default: 3600 = 1 hour)
        :returns: Final status response
        :rtype: dict
        """
        status_endpoint = f"{self.service_url}/status/{job_id}"
        start_time = time.time()
      
        poll_count = 0
        consecutive_errors = 0
        max_consecutive_errors = 3  # Allow up to 3 consecutive errors before giving up
      
        while True:
            poll_count += 1
          
            try:
                headers = self._get_auth_headers()
              
                response = requests.get(status_endpoint, headers=headers, timeout=120)
                response.raise_for_status()
                status_data = response.json()
              
                # Reset error counter on successful request
                consecutive_errors = 0
              
                status = status_data.get('status', 'unknown')
                progress = status_data.get('progress', 0)
                message = status_data.get('message', 'Processing...')
              
                # Update progress callback
                if progress_callback:
                    # Map service progress (0-100) to our range (40-90)
                    mapped_progress = 40 + int(progress * 0.5)  # 40-90 range
                    progress_callback(mapped_progress, message)
              
                if status == 'completed':
                    return status_data
                elif status == 'failed':
                    error_msg = status_data.get('message', 'Processing failed')
                    error_details = status_data.get('error', '')
                  
                    # Provide more user-friendly error messages for common server-side issues
                    if 'No such file or directory' in error_msg or '[Errno 2]' in error_msg:
                        # Server-side file access issue
                        user_friendly_msg = (
                           "The inference service encountered an internal error while creating output files. "
                           "This is a server-side issue. Please try again, or contact support if the problem persists."
                        )
                        # Log the technical details for debugging
                        QgsMessageLog.logMessage(
                           f"Inference job failed - Server file error: {error_msg}",
                           "VEC Plugin",
                           Qgis.Warning
                        )
                        raise Exception(f"Inference job failed: {user_friendly_msg}")
                    else:
                        # Generic error - pass through server message but sanitize paths
                        sanitized_error = self._sanitize_urls(error_msg)
                        raise Exception(f"Inference job failed: {sanitized_error}")
              
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > max_wait_time:
                    raise Exception(f"Inference job timed out after {max_wait_time} seconds")
              
                # Wait before next poll
                time.sleep(poll_interval)
              
            except requests.exceptions.HTTPError as e:
                # Handle HTTP errors (401, 403, 404, 500, etc.)
                status_code = e.response.status_code if e.response else None
                error_detail = ""
                try:
                    if e.response:
                        error_detail = e.response.text[:200]  # First 200 chars
                except:
                    pass
              
                # Log the error for debugging (sanitize URLs)
                QgsMessageLog.logMessage(
                    f"Status polling HTTP error ({status_code}) on attempt {poll_count}: {self._sanitize_urls(str(e))}. Details: {self._sanitize_urls(error_detail)}",
                    "VEC Plugin",
                    Qgis.Warning
                )
              
                # Non-retryable errors (auth, not found, forbidden)
                if status_code in (401, 403, 404):
                    if status_code == 401:
                        raise Exception(f"Authentication failed (401). JWT token may be invalid or expired.")
                    elif status_code == 404:
                        raise Exception(f"Job not found (404). Job ID: {job_id}.")
                    elif status_code == 403:
                        raise Exception(f"Access forbidden (403). License may be invalid.")
              
                # Retryable HTTP errors (500, 502, 503, 504)
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise Exception(f"Status polling HTTP error ({status_code}) after {max_consecutive_errors} attempts.")
              
                # Exponential backoff: wait 2^consecutive_errors seconds
                wait_time = min(2 ** consecutive_errors, 30)  # Cap at 30 seconds
                QgsMessageLog.logMessage(
                    f"Retrying after {wait_time} seconds (error {consecutive_errors}/{max_consecutive_errors})",
                    "VEC Plugin",
                    Qgis.Info
                )
                time.sleep(wait_time)
                continue
              
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                # Retryable network errors
                consecutive_errors += 1
                error_type = "timeout" if isinstance(e, requests.exceptions.Timeout) else "connection"
              
                QgsMessageLog.logMessage(
                    f"Status polling {error_type} error on attempt {poll_count} (error {consecutive_errors}/{max_consecutive_errors}): {self._sanitize_urls(str(e))}",
                    "VEC Plugin",
                    Qgis.Warning
                )
              
                if consecutive_errors >= max_consecutive_errors:
                    if isinstance(e, requests.exceptions.Timeout):
                        raise Exception(f"Status polling timeout after {max_consecutive_errors} attempts.")
                    else:
                        raise Exception(f"Status polling connection error after {max_consecutive_errors} attempts - server unreachable.")
              
                # Exponential backoff: wait 2^consecutive_errors seconds
                wait_time = min(2 ** consecutive_errors, 30)  # Cap at 30 seconds
                QgsMessageLog.logMessage(
                    f"Retrying after {wait_time} seconds (error {consecutive_errors}/{max_consecutive_errors})",
                    "VEC Plugin",
                    Qgis.Info
                )
                time.sleep(wait_time)
                continue
              
            except requests.exceptions.RequestException as e:
                # Other request exceptions - retry a few times
                consecutive_errors += 1
                QgsMessageLog.logMessage(
                    f"Status polling request error on attempt {poll_count} (error {consecutive_errors}/{max_consecutive_errors}): {self._sanitize_urls(str(e))}",
                    "VEC Plugin",
                    Qgis.Warning
                )
              
                if consecutive_errors >= max_consecutive_errors:
                    raise Exception(f"Status polling request failed after {max_consecutive_errors} attempts.")
              
                # Exponential backoff
                wait_time = min(2 ** consecutive_errors, 30)
                time.sleep(wait_time)
                continue
              
            except Exception as e:
                # Unexpected errors - don't retry
                QgsMessageLog.logMessage(
                    f"Status polling unexpected error on attempt {poll_count}: {self._sanitize_urls(str(e))}",
                    "VEC Plugin",
                    Qgis.Warning
                )
                raise Exception(f"Status polling error: {self._sanitize_urls(str(e))}") from e
  
    def _download_shapefile(self, file_id):
        """
        Download shapefile from /download/shapefile/{file_id} endpoint.
        Waits for inference to complete by retrying until file is ready.
      
        :param file_id: File ID from upload service (used for GCS path)
        :type file_id: str
        :returns: Path to downloaded shapefile
        :rtype: str
        """
        download_endpoint = f"{self.service_url}/download/shapefile/{file_id}"
      
        max_retries = 120  # Allow up to 120 retries for very large files (20-60 minutes)
        base_delay = 10  # Start with 10 seconds between retries
        max_delay = 60  # Cap delay at 60 seconds
        
        for attempt in range(max_retries):
            try:
                headers = self._get_auth_headers()
                response = requests.get(download_endpoint, headers=headers, timeout=300, stream=True)
                response.raise_for_status()
              
                # Create temporary directory for shapefile
                temp_dir = tempfile.mkdtemp()
                zip_path = os.path.join(temp_dir, 'result.zip')
              
                # Download ZIP file
                with open(zip_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
              
                # Extract ZIP file
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
              
                # Find .shp file in extracted contents
                shp_files = [f for f in os.listdir(temp_dir) if f.endswith('.shp')]
              
                if shp_files:
                    shp_path = os.path.join(temp_dir, shp_files[0])
                    return shp_path
                else:
                    raise Exception("No shapefile (.shp) found in downloaded ZIP")
            
            except requests.exceptions.HTTPError as e:
                # If 404 or 503, inference may still be processing - retry
                if e.response.status_code in (404, 503) and attempt < max_retries - 1:
                    # Gradually increase delay: 10s for first 20, then 15s, 20s, 25s, 30s, then cap at 30s
                    if attempt < 20:
                        delay = 10
                    elif attempt < 40:
                        delay = 15
                    elif attempt < 60:
                        delay = 20
                    elif attempt < 80:
                        delay = 25
                    elif attempt < 100:
                        delay = 30
                    else:
                        delay = max_delay  # 60 seconds for final attempts
                    QgsMessageLog.logMessage(
                        f"Waiting for inference to complete (attempt {attempt + 1}/{max_retries}). Retrying in {delay} seconds...",
                        "VEC Plugin",
                        Qgis.Info
                    )
                    time.sleep(delay)
                    continue
                else:
                    # Other HTTP errors or max retries reached
                    raise Exception(f"Download failed: HTTP {e.response.status_code}")
            
            except requests.exceptions.RequestException as e:
                # Network errors - retry with backoff
                if attempt < max_retries - 1:
                    # Same delay progression as HTTP errors
                    if attempt < 20:
                        delay = 10
                    elif attempt < 40:
                        delay = 15
                    elif attempt < 60:
                        delay = 20
                    elif attempt < 80:
                        delay = 25
                    elif attempt < 100:
                        delay = 30
                    else:
                        delay = max_delay  # 60 seconds for final attempts
                    error_msg = self._sanitize_urls(str(e))
                    QgsMessageLog.logMessage(
                        f"Download connection error (attempt {attempt + 1}/{max_retries}). Retrying in {delay} seconds...",
                        "VEC Plugin",
                        Qgis.Warning
                    )
                    time.sleep(delay)
                    continue
                else:
                    raise Exception(f"Download request failed after {max_retries} attempts: {type(e).__name__}")
            
            except Exception as e:
                # Other errors (file extraction, etc.) - don't retry
                raise Exception(f"Download error: {str(e)}")
        
        # Should never reach here, but just in case
        raise Exception(f"Download failed after {max_retries} attempts")