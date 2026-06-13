# -*- coding: utf-8 -*-
"""Subprocess bridge to workers/model_prediction_worker.py."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess  # nosec B404
import threading
import time

import numpy as np

from .model_config import resolve_checkpoint_path, worker_script_basename
from .one_click_log import sanitize_log_line
from .model_venv import get_venv_python, venv_exists
from .subprocess_utils import get_clean_env, popen_hidden

# QgsMessageLog is not thread-safe; stderr drain uses stdlib logging only.
_worker_stderr_log = logging.getLogger("vec_plugin.model_worker")


def build_model_predictor_config(checkpoint: str | None = None) -> dict:
    if not venv_exists():
        raise FileNotFoundError(
            "Model environment not found. Install dependencies from One-Click settings."
        )
    plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    worker_script = os.path.join(plugin_dir, "workers", worker_script_basename())
    venv_python = get_venv_python()
    if not os.path.isfile(venv_python):
        raise FileNotFoundError(f"Virtual environment Python not found: {venv_python}")
    if not os.path.isfile(worker_script):
        raise FileNotFoundError(f"Worker script not found: {worker_script}")
    ckpt = checkpoint or resolve_checkpoint_path()
    if not ckpt:
        raise FileNotFoundError("Segmentation weights not downloaded.")
    return {
        "venv_python": venv_python,
        "worker_script": worker_script,
        "checkpoint": ckpt,
    }


class ModelPredictor:
    _TIMEOUT_INIT = 300
    _TIMEOUT_SET_IMAGE = 180
    _TIMEOUT_PREDICT = 120

    def __init__(self, config: dict) -> None:
        self.venv_python = config["venv_python"]
        self.worker_script = config["worker_script"]
        self.checkpoint = config["checkpoint"]
        self.process = None
        self._cleanup_lock = threading.Lock()
        self._op_lock = threading.RLock()
        self._stderr_stop = threading.Event()
        self._stderr_thread: threading.Thread | None = None
        self.is_image_set = False
        self.original_size = None
        self._low_res_mask = None

    def _read_response(self, timeout: int) -> str:
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Model worker is not running")

        result = [None]
        err = [None]

        def reader():
            try:
                result[0] = self.process.stdout.readline()
            except Exception as exc:
                err[0] = exc

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            self.cleanup()
            raise TimeoutError(f"Model worker timed out after {timeout}s")
        if err[0]:
            raise err[0]
        line = result[0]
        if not line:
            raise RuntimeError("Model worker closed unexpectedly")
        return line

    def _read_json_response(self, timeout: int) -> dict:
        """Read stdout until a valid JSON object line (skip engine log noise)."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.cleanup()
                raise TimeoutError(f"Model worker timed out after {timeout}s")
            line = self._read_response(max(1, int(remaining)))
            stripped = line.strip()
            if not stripped:
                continue
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                # Runs on worker QThreads — QgsMessageLog is not thread-safe here.
                _worker_stderr_log.info(
                    sanitize_log_line(
                        f"Model worker non-JSON stdout (ignored): {stripped[:200]}"
                    )
                )

    def _write_stdin_line(self, line: str, timeout: int = 120) -> None:
        """Write to worker stdin without blocking the caller indefinitely (Windows pipes)."""
        proc = self.process
        if proc is None or proc.stdin is None:
            raise RuntimeError("Model worker not started")
        err: list[Exception | None] = [None]
        done = threading.Event()

        def writer() -> None:
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except Exception as exc:
                err[0] = exc
            finally:
                done.set()

        threading.Thread(target=writer, daemon=True).start()
        if not done.wait(timeout):
            self.cleanup()
            raise TimeoutError(f"Timed out sending to model worker after {timeout}s")
        if err[0] is not None:
            raise err[0]

    def _send(self, payload: dict, timeout: int | None = None) -> dict:
        if self.process is None:
            raise RuntimeError("Model worker not started")
        line = json.dumps(payload) + "\n"
        write_timeout = max(120, min(600, len(line) // 20000 + 60))
        self._write_stdin_line(line, timeout=write_timeout)
        data = self._read_json_response(timeout or self._TIMEOUT_PREDICT)
        if data.get("type") == "error":
            raise RuntimeError(data.get("message", "Model worker error"))
        return data

    def _join_stderr_drain(self, timeout: float = 2.0) -> None:
        self._stderr_stop.set()
        proc = self.process
        if proc is not None and proc.stderr is not None:
            try:
                proc.stderr.close()
            except OSError:
                pass
        thread = self._stderr_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._stderr_thread = None

    def _start_stderr_drain(self) -> None:
        """Drain worker stderr so a full pipe cannot deadlock model load."""
        proc = self.process
        if proc is None or proc.stderr is None:
            return
        self._stderr_stop.clear()

        def drain() -> None:
            try:
                while not self._stderr_stop.is_set():
                    line = proc.stderr.readline()
                    if not line:
                        break
                    text = line.rstrip()
                    if not text:
                        continue
                    _worker_stderr_log.info(sanitize_log_line(text[:4000]))
            except (OSError, ValueError):
                pass

        self._stderr_thread = threading.Thread(target=drain, daemon=True)
        self._stderr_thread.start()

    def start(self):
        with self._cleanup_lock:
            if self.process is not None:
                return
            cmd = [self.venv_python, self.worker_script]
            self.process = popen_hidden(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=get_clean_env(),
            )
            self._start_stderr_drain()
            init = {"action": "init", "checkpoint_path": self.checkpoint}
            self._write_stdin_line(json.dumps(init) + "\n", timeout=30)
            data = self._read_json_response(self._TIMEOUT_INIT)
            if data.get("type") == "error":
                raise RuntimeError(data.get("message", "Init failed"))
            if data.get("type") != "ready":
                raise RuntimeError(f"Unexpected init response: {data}")

    def set_image(self, image: np.ndarray):
        with self._op_lock:
            self.start()
            payload = {
                "action": "set_image",
                "image": base64.b64encode(image.tobytes()).decode("utf-8"),
                "image_shape": list(image.shape),
                "image_dtype": str(image.dtype),
            }
            line = self._send(payload, timeout=self._TIMEOUT_SET_IMAGE)
            if line.get("type") != "image_set":
                raise RuntimeError("Failed to set image on model worker")
            self.is_image_set = True
            self.original_size = tuple(line.get("original_size", image.shape[:2]))

    def predict(
        self,
        point_coords,
        point_labels,
        *,
        mask_input=None,
        multimask_output: bool | None = None,
        pick_at=None,
    ) -> tuple[np.ndarray, float]:
        with self._op_lock:
            return self._predict_unlocked(
                point_coords,
                point_labels,
                mask_input=mask_input,
                multimask_output=multimask_output,
                pick_at=pick_at,
            )

    def _predict_unlocked(
        self,
        point_coords,
        point_labels,
        *,
        mask_input=None,
        multimask_output: bool | None = None,
        pick_at=None,
    ) -> tuple[np.ndarray, float]:
        coords = np.asarray(point_coords, dtype=np.float32)
        if coords.size == 0:
            raise ValueError("No point prompts provided")
        if coords.ndim == 1:
            coords = coords.reshape(-1, 2)
        elif coords.shape[-1] != 2:
            coords = coords.reshape(-1, 2)
        labels = np.asarray(point_labels, dtype=np.int32).reshape(-1)

        payload = {
            "action": "predict",
            "point_coords": coords.tolist(),
            "point_labels": labels.tolist(),
        }
        if mask_input is not None:
            arr = np.asarray(mask_input, dtype=np.float32)
            payload["mask_input"] = base64.b64encode(arr.tobytes()).decode("utf-8")
            payload["mask_input_shape"] = list(arr.shape)
            payload["mask_input_dtype"] = "float32"
        if multimask_output is not None:
            payload["multimask_output"] = multimask_output
        if pick_at is not None:
            pt = np.asarray(pick_at, dtype=np.float32).reshape(-1)
            if pt.size >= 2:
                payload["pick_at"] = [float(pt[0]), float(pt[1])]

        data = self._send(payload)
        if data.get("type") != "predict_result":
            raise RuntimeError("Unexpected predict response")
        mask = np.frombuffer(
            base64.b64decode(data["mask"]),
            dtype=np.uint8,
        ).reshape(data["mask_shape"])
        if data.get("low_res_mask") and data.get("low_res_shape"):
            self._low_res_mask = np.frombuffer(
                base64.b64decode(data["low_res_mask"]),
                dtype=np.float32,
            ).reshape(data["low_res_shape"])
        else:
            self._low_res_mask = None
        return mask, float(data.get("score", 0.0))

    def cleanup(self):
        with self._cleanup_lock:
            if self.process is None:
                self._join_stderr_drain()
                return
            self._join_stderr_drain()
            try:
                if self.process.stdin and self.process.poll() is None:
                    try:
                        self._write_stdin_line(
                            json.dumps({"action": "shutdown"}) + "\n", timeout=5
                        )
                    except (TimeoutError, OSError, RuntimeError):
                        pass
            except OSError:
                pass
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self.process.kill()
                except OSError:
                    pass
            self.process = None
            self.is_image_set = False
            self._low_res_mask = None

    def __del__(self):
        self.cleanup()
