# -*- coding: utf-8 -*-
"""Central API endpoint configuration for the plugin."""

INFERENCE_BASE_URL = "https://inference.usefieldwatch.com/"
UPLOAD_BASE_URL = "https://upload.usefieldwatch.com/"


class ApiRoutes:
    """Build full API URLs from base URLs."""

    @staticmethod
    def auth_validate(inference_base):
        return f"{inference_base.rstrip('/')}/auth/validate"

    @staticmethod
    def upload(upload_base):
        return f"{upload_base.rstrip('/')}/upload"

    @staticmethod
    def qgis_infer(inference_base, file_id):
        return f"{inference_base.rstrip('/')}/qgis/infer/{file_id}"

    @staticmethod
    def qgis_panel(inference_base, file_id):
        return f"{inference_base.rstrip('/')}/qgis/panel/{file_id}"

    @staticmethod
    def qgis_status(inference_base, job_id):
        return f"{inference_base.rstrip('/')}/qgis/status/{job_id}"

    @staticmethod
    def qgis_download_shapefile(inference_base, job_id):
        return f"{inference_base.rstrip('/')}/qgis/download/shapefile/{job_id}"

    @staticmethod
    def qgis_download_json(inference_base, job_id):
        return f"{inference_base.rstrip('/')}/qgis/download/json/{job_id}"

    @staticmethod
    def csv_download(inference_base, job_id):
        return f"{inference_base.rstrip('/')}/download/csv/{job_id}"
