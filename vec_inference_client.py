# -*- coding: utf-8 -*-
"""
/***************************************************************************
 VecInferenceClient
                                 A QGIS plugin
 Client for communicating with VEC inference service
                             -------------------
        begin                : 2025-12-09
        copyright            : (C) 2025 by Anthony_FiledWatch
        email                : kadibiaenu@gmail.com
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
from qgis.core import (
    QgsMessageLog, Qgis, QgsRasterPipe, 
    QgsRasterFileWriter, QgsRasterLayer, QgsGeometry
)


class VecInferenceClient:
    """Client for communicating with VEC inference service."""
    
    def __init__(self, service_url, upload_url=None):
        """
        Initialize the inference client.
        
        :param service_url: URL of the inference service
        :type service_url: str
        :param upload_url: URL of the upload service (if different from inference service)
        :type upload_url: str
        """
        self.service_url = service_url.rstrip('/')
        # If upload_url not provided, assume upload service is at same base URL
        self.upload_url = (upload_url.rstrip('/') if upload_url else service_url.rstrip('/'))
    
    def process_raster_layer(self, raster_layer, progress_callback=None, crop_geometry=None):
        """
        Process a QGIS raster layer through the inference pipeline.
        Uses hardcoded high compression (DEFLATE, predictor 2, zlevel 9).
        
        :param raster_layer: QGIS raster layer to process
        :type raster_layer: QgsRasterLayer
        :param progress_callback: Optional callback function for progress updates
        :type progress_callback: function
        :param crop_geometry: Optional polygon geometry to crop raster
        :type crop_geometry: QgsGeometry
        :returns: Path to shapefile results
        :rtype: str
        """
        try:
            # Crop raster - geometry is now mandatory
            if not crop_geometry or crop_geometry.isEmpty():
                raise Exception("Crop geometry is required. Please draw a polygon before processing.")
            
            if progress_callback:
                progress_callback(5, "Cropping raster to selected area...")
            
            raster_layer = self._crop_raster(raster_layer, crop_geometry)
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
                    "Cropped raster uploaded, now processing...",
                    "VEC Plugin",
                    Qgis.Info
                )
                
                if not file_id:
                    raise Exception("Failed to get file_id from upload service")
                
                # Step 2: Call /infer/{file_id} endpoint
                if progress_callback:
                    progress_callback(25, "Starting inference...")
                
                job_id = self._start_inference(file_id)
                
                if not job_id:
                    raise Exception("Failed to start inference job")
                
                # Step 3: Poll for status until complete
                if progress_callback:
                    progress_callback(35, "Processing inference...")
                
                status_response = self._poll_status(job_id, progress_callback)
                
                QgsMessageLog.logMessage(
                    "Processing complete! Pulling shapefile...",
                    "VEC Plugin",
                    Qgis.Info
                )
                
                # Extract file_id from status response for download
                file_id_for_download = None
                if status_response and 'results' in status_response:
                    file_id_for_download = status_response['results'].get('file_id')
                elif status_response and 'file_id' in status_response:
                    file_id_for_download = status_response.get('file_id')
                
                if not file_id_for_download:
                    file_id_for_download = job_id  # Fallback to job_id
                
                # Step 4: Download shapefile
                if progress_callback:
                    progress_callback(90, "Downloading results...")
                
                shapefile_path = self._download_shapefile(file_id_for_download)
                
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
                f"Error processing raster: {str(e)}",
                "VEC Plugin",
                Qgis.Critical
            )
            raise
    
    def _export_raster_to_temp(self, raster_layer):
        """
        Export raster layer to temporary GeoTIFF file with hardcoded high compression.
        Uses DEFLATE compression with predictor 2 (Horizontal) and zlevel 9.
        
        :param raster_layer: QGIS raster layer
        :type raster_layer: QgsRasterLayer
        :returns: Path to temporary file
        :rtype: str
        """
        # Hardcoded compression settings: DEFLATE, predictor 2, zlevel 9
        compression = 'DEFLATE'
        predictor = '2'  # Horizontal predictor
        zlevel = 9
        
        # Create temporary file
        temp_fd, temp_path = tempfile.mkstemp(suffix='.tif')
        os.close(temp_fd)
        
        # Try using QGIS processing first (more reliable)
        try:
            from qgis.core import QgsProcessing
            from qgis import processing
            
            # Build creation options for compression (hardcoded: DEFLATE, predictor 2, zlevel 9)
            creation_options = [
                f"COMPRESS={compression}",
                f"ZLEVEL={zlevel}",
                f"PREDICTOR={predictor}"
            ]
            
            # Use gdal:translate with compression options
            params = {
                'INPUT': raster_layer,
                'OUTPUT': temp_path,
                'CREATEOPTIONS': '|'.join(creation_options)
            }
            
            result = processing.run("gdal:translate", params)
            return result['OUTPUT']
        except Exception:
            # Fallback to direct export (without compression)
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
            
            return temp_path
    
    def _crop_raster(self, raster_layer, crop_geometry):
        """
        Crop raster layer to the specified polygon geometry.
        
        :param raster_layer: QGIS raster layer to crop
        :type raster_layer: QgsRasterLayer
        :param crop_geometry: Polygon geometry to crop to
        :type crop_geometry: QgsGeometry
        :returns: Cropped raster layer
        :rtype: QgsRasterLayer
        """
        try:
            from qgis import processing
            from qgis.core import QgsVectorLayer, QgsFeature, QgsCoordinateTransform, QgsProject
            
            # Transform geometry to raster CRS if needed
            raster_crs = raster_layer.crs()
            geometry_crs = crop_geometry.crs() if hasattr(crop_geometry, 'crs') else None
            
            if geometry_crs and geometry_crs != raster_crs:
                transform = QgsCoordinateTransform(geometry_crs, raster_crs, QgsProject.instance())
                crop_geometry = QgsGeometry(crop_geometry)
                crop_geometry.transform(transform)
            
            # Get extent of crop geometry
            extent = crop_geometry.boundingBox()
            
            # Use gdal:cliprasterbymasklayer (more accurate than extent-based)
            try:
                # Create a memory layer with the geometry
                mem_layer = QgsVectorLayer(
                    f"Polygon?crs={raster_crs.authid()}",
                    "temp_mask",
                    "memory"
                )
                
                if not mem_layer.isValid():
                    raise Exception("Failed to create memory layer")
                
                # Add feature with crop geometry
                feature = QgsFeature()
                feature.setGeometry(crop_geometry)
                mem_layer.dataProvider().addFeature(feature)
                mem_layer.updateExtents()
                
                # Clip raster by mask
                params = {
                    'INPUT': raster_layer,
                    'MASK': mem_layer,
                    'SOURCE_CRS': raster_crs,
                    'TARGET_CRS': raster_crs,
                    'NODATA': None,
                    'ALPHA_BAND': False,
                    'CROP_TO_CUTLINE': True,
                    'KEEP_RESOLUTION': False,
                    'SET_RESOLUTION': False,
                    'X_RESOLUTION': None,
                    'Y_RESOLUTION': None,
                    'MULTITHREADING': False,
                    'OPTIONS': '',
                    'DATA_TYPE': 0,
                    'EXTRA': '',
                    'OUTPUT': 'TEMPORARY_OUTPUT'
                }
                
                result = processing.run("gdal:cliprasterbymasklayer", params)
                cropped_layer = QgsRasterLayer(result['OUTPUT'], 'cropped_raster')
                
                if cropped_layer.isValid():
                    return cropped_layer
                else:
                    raise Exception("Failed to create cropped raster layer")
                    
            except Exception as e:
                # Fallback to extent-based clipping
                params = {
                    'INPUT': raster_layer,
                    'PROJWIN': extent,
                    'OUTPUT': 'TEMPORARY_OUTPUT'
                }
                
                result = processing.run("gdal:cliprasterbyextent", params)
                cropped_layer = QgsRasterLayer(result['OUTPUT'], 'cropped_raster')
                
                if cropped_layer.isValid():
                    return cropped_layer
                else:
                    raise Exception("Failed to create cropped raster layer")
                    
        except Exception:
            # Return original layer if cropping fails
            return raster_layer
    
    def _upload_to_gcs(self, image_path):
        """
        Upload image to GCS via /upload endpoint and get file_id.
        
        :param image_path: Path to image file
        :type image_path: str
        :returns: file_id from upload service
        :rtype: str
        """
        try:
            upload_endpoint = f"{self.upload_url}/upload"
            
            # Open file and prepare for upload
            file_name = os.path.basename(image_path)
            with open(image_path, 'rb') as f:
                files = {
                    'file': (file_name, f, 'application/octet-stream')
                }
                
                headers = {
                    'User-Agent': 'QGIS-VEC-Plugin/1.0'
                }
                
                try:
                    response = requests.post(
                        upload_endpoint,
                        files=files,
                        headers=headers,
                        timeout=600
                    )
                    
                except requests.exceptions.ConnectionError:
                    raise Exception("Connection error - server unreachable")
                except requests.exceptions.Timeout:
                    raise Exception("Upload timeout after 600 seconds")
                except requests.exceptions.RequestException as e:
                    raise Exception(f"Upload request failed: {str(e)}")
                
                response.raise_for_status()
                result = response.json()
                
                # Extract file_id from response
                file_id = result.get('file_id') or result.get('id') or result.get('fileId')
                
                if not file_id:
                    raise Exception("Upload service did not return file_id")
                
                return file_id
                
        except requests.exceptions.RequestException as e:
            raise Exception("Upload request failed") from e
        except Exception as e:
            raise Exception("Upload error") from e
    
    def _start_inference(self, file_id, prompt="building", confidence=0.3, alpha=0.5, 
                        iou_threshold=0.15, remove_overlaps=True, overlap_method="clip"):
        """
        Start inference job by calling /infer/{file_id} endpoint.
        
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
        inference_endpoint = f"{self.service_url}/infer/{file_id}"
        
        try:
            response = requests.post(
                inference_endpoint,
                params=params,
                timeout=30
            )
            
            response.raise_for_status()
            result = response.json()
            
            # Extract job_id from response
            job_id = result.get('job_id') or result.get('jobId')
            
            if not job_id:
                raise Exception("Inference service did not return job_id")
            
            return job_id
            
        except requests.exceptions.RequestException:
            raise Exception("Inference request failed")
        except Exception:
            raise Exception("Inference error")
    
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
        while True:
            poll_count += 1
            
            try:
                response = requests.get(status_endpoint, timeout=30)
                response.raise_for_status()
                status_data = response.json()
                
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
                    raise Exception(f"Inference job failed: {error_msg}")
                
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed > max_wait_time:
                    raise Exception(f"Inference job timed out after {max_wait_time} seconds")
                
                # Wait before next poll
                time.sleep(poll_interval)
                
            except requests.exceptions.RequestException:
                raise Exception("Status polling failed")
            except Exception as e:
                raise Exception("Status polling error") from e
    
    def _download_shapefile(self, file_id):
        """
        Download shapefile from /download/shapefile/{file_id} endpoint.
        
        :param file_id: File ID from upload service (used for GCS path)
        :type file_id: str
        :returns: Path to downloaded shapefile
        :rtype: str
        """
        download_endpoint = f"{self.service_url}/download/shapefile/{file_id}"
        
        try:
            response = requests.get(download_endpoint, timeout=300, stream=True)
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
                
        except requests.exceptions.RequestException:
            raise Exception("Download request failed")
        except Exception:
            raise Exception("Download error")

