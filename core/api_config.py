# -*- coding: utf-8 -*-
"""Central API endpoint configuration for the plugin."""

INFERENCE_BASE_URL = "https://inference.usefieldwatch.com/"
UPLOAD_BASE_URL = "https://upload.usefieldwatch.com/"


def _join_url(base: str, *segments: str) -> str:
    return f"{base.rstrip('/')}/{'/'.join(segments)}"


class ApiRoutes:
    """Build full API URLs from base URLs."""

    @staticmethod
    def qgis_feedback(inference_base):
        return _join_url(inference_base, "qgis", "feedback")

    @staticmethod
    def auth_validate(inference_base):
        return _join_url(inference_base, "auth", "validate")

    @staticmethod
    def qgis_trial_state(inference_base):
        return _join_url(inference_base, "qgis", "trial", "state")

    @staticmethod
    def qgis_trial_generate(inference_base):
        return _join_url(inference_base, "qgis", "trial", "generate")

    @staticmethod
    def qgis_trial_usage(inference_base):
        return _join_url(inference_base, "qgis", "trial", "usage")

    @staticmethod
    def upload(upload_base):
        return _join_url(upload_base, "upload")

    @staticmethod
    def qgis_infer(inference_base, file_id):
        return _join_url(inference_base, "qgis", "infer", str(file_id))

    @staticmethod
    def qgis_panel(inference_base, file_id):
        return _join_url(inference_base, "qgis", "panel", str(file_id))

    @staticmethod
    def qgis_status(inference_base, job_id):
        return _join_url(inference_base, "qgis", "status", str(job_id))

    @staticmethod
    def qgis_download_shapefile(inference_base, job_id):
        return _join_url(inference_base, "qgis", "download", "shapefile", str(job_id))

    @staticmethod
    def qgis_download_json(inference_base, job_id):
        return _join_url(inference_base, "qgis", "download", "json", str(job_id))

    @staticmethod
    def qgis_edit(inference_base):
        return _join_url(inference_base, "qgis", "edit")

    @staticmethod
    def csv_download(inference_base, job_id):
        return _join_url(inference_base, "download", "csv", str(job_id))
