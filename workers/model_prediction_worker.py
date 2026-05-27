#!/usr/bin/env python3
"""Subprocess worker for interactive point segmentation."""

import base64
import importlib
import json
import os
import sys

_plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

from core.model_engine_patch import (  # noqa: E402
    apply_engine_import_patches,
    resolve_torch_device,
)

apply_engine_import_patches()

_real_stdout = sys.stdout
sys.stdout = sys.stderr

if sys.platform == "win32":
    _site_packages = None
    for p in sys.path:
        if p.endswith("site-packages") and os.path.isdir(p):
            _site_packages = p
            break
    if _site_packages:
        for _subdir in ("torch\\lib", "torch\\bin", "torchvision"):
            _dll_dir = os.path.join(_site_packages, _subdir)
            if os.path.isdir(_dll_dir):
                try:
                    os.add_dll_directory(_dll_dir)
                except OSError:
                    pass

try:
    import numpy as np  # noqa: E402
    import torch  # noqa: E402
except ImportError as exc:
    sys.stdout = _real_stdout
    print(json.dumps({"type": "error", "message": f"Failed to import dependencies: {exc}"}))
    sys.exit(1)

_predictor = None
_device = None

sys.stdout = _real_stdout
MAX_LINE_LENGTH = 50 * 1024 * 1024


def _engine_root() -> str:
    custom = os.environ.get("FIELDWATCH_MODEL_ENGINE_MODULE", "").strip()
    if custom:
        return custom
    return chr(115) + chr(97) + chr(109) + chr(51)


def _predictor_submodule(root: str) -> str:
    task = f"{root[:-1]}1" if root[-1:].isdigit() else root
    return f"{root}.model.{task}_task_predictor"


def _interactive_predictor_class(pred_mod):
    for name in dir(pred_mod):
        if name.endswith("InteractiveImagePredictor"):
            return getattr(pred_mod, name)
    raise ImportError("Interactive image predictor class not found")


def send_response(response_type, data):
    payload = {"type": response_type, **data}
    _real_stdout.write(json.dumps(payload) + "\n")
    _real_stdout.flush()


def send_error(message):
    send_response("error", {"message": message})


def encode_array(arr):
    return base64.b64encode(arr.tobytes()).decode("utf-8")


def decode_array(b64_string, shape, dtype):
    raw = base64.b64decode(b64_string.encode("utf-8"))
    return np.frombuffer(raw, dtype=dtype).reshape(shape)


def get_device():
    return torch.device(resolve_torch_device())


def build_predictor(checkpoint_path: str):
    """Load interactive image predictor from the segmentation engine."""
    global _predictor, _device
    _device = get_device()
    root = _engine_root()

    try:
        build_mod = importlib.import_module(f"{root}.model_builder")
        build_fn = build_mod.build_sam3_image_model
    except ImportError as exc:
        raise ImportError(
            f"Could not import segmentation engine: {exc}. "
            "Reinstall dependencies from the settings dialog."
        ) from exc

    try:
        model = build_fn(
            checkpoint_path=checkpoint_path,
            device=str(_device),
            load_from_HF=False,
            enable_inst_interactivity=True,
        )
    except TypeError:
        model = build_fn(
            checkpoint=checkpoint_path,
            device=str(_device),
            load_from_HF=False,
            enable_inst_interactivity=True,
        )

    _predictor = getattr(model, "inst_interactive_predictor", None)
    if _predictor is None:
        raise RuntimeError(
            "Model loaded but interactive predictor is missing. "
            "Reinstall dependencies from the settings dialog."
        )

    _predictor.model.eval()


def _safe_readline():
    line = sys.stdin.readline()
    if len(line) > MAX_LINE_LENGTH:
        raise ValueError("Input line too large")
    return line


def main():
    init = json.loads(_safe_readline())
    if init.get("action") != "init":
        send_error("First request must be init")
        sys.exit(1)

    checkpoint = init.get("checkpoint_path")
    if not checkpoint or not os.path.isfile(checkpoint):
        send_error(f"Checkpoint not found: {checkpoint}")
        sys.exit(1)

    try:
        # Engine checkpoint load may print to stdout; keep stdout JSON-only for QGIS.
        sys.stdout = sys.stderr
        build_predictor(os.path.normpath(os.path.abspath(checkpoint)))
    except Exception as exc:
        sys.stdout = _real_stdout
        send_error(str(exc))
        sys.exit(1)
    finally:
        sys.stdout = _real_stdout

    send_response(
        "ready",
        {"device": str(_device), "device_type": resolve_torch_device()},
    )

    while True:
        line = _safe_readline()
        if not line:
            break
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            send_error("Invalid JSON request")
            continue

        action = req.get("action")
        try:
            if action == "set_image":
                image = decode_array(
                    req["image"],
                    req["image_shape"],
                    req.get("image_dtype", "uint8"),
                )
                with torch.inference_mode():
                    _predictor.set_image(image)
                send_response("image_set", {"original_size": list(image.shape[:2])})

            elif action == "predict":
                coords = np.array(req.get("point_coords") or [], dtype=np.float32)
                labels = np.array(req.get("point_labels") or [], dtype=np.int32)
                if coords.size == 0:
                    send_error("No point prompts provided")
                    continue

                with torch.inference_mode():
                    masks, scores, low_res = _predictor.predict(
                        point_coords=coords,
                        point_labels=labels,
                        multimask_output=False,
                    )

                if masks is None or len(masks) == 0:
                    send_error("Model returned no mask")
                    continue

                mask = masks[0]
                if hasattr(mask, "cpu"):
                    mask = mask.cpu().numpy()
                mask = (mask > 0).astype(np.uint8)

                payload = {
                    "mask": encode_array(mask),
                    "mask_shape": list(mask.shape),
                    "mask_dtype": "uint8",
                    "score": float(scores[0]) if scores is not None and len(scores) else 0.0,
                }
                if low_res is not None and len(low_res):
                    lr = low_res[0]
                    if hasattr(lr, "cpu"):
                        lr = lr.cpu().numpy()
                    payload["low_res_mask"] = encode_array(lr.astype(np.float32))
                    payload["low_res_shape"] = list(lr.shape)
                    payload["low_res_dtype"] = "float32"

                send_response("predict_result", payload)

            elif action == "shutdown":
                break
            else:
                send_error(f"Unknown action: {action}")
        except Exception as exc:
            send_error(str(exc))


if __name__ == "__main__":
    main()
