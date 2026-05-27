#!/usr/bin/env python3
"""Subprocess worker for SAM 2.1 interactive point segmentation (fast path)."""

import base64
import json
import os
import sys

_plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)

from core.model_engine_patch import resolve_torch_device  # noqa: E402

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

try:
    from sam2.build_sam import build_sam2
    from sam2.sam2_image_predictor import SAM2ImagePredictor
except ImportError as exc:
    sys.stdout = _real_stdout
    print(
        json.dumps(
            {
                "type": "error",
                "message": f"Segmentation engine not installed: {exc}. "
                "Use Install dependencies in One-Click settings.",
            }
        )
    )
    sys.exit(1)

_predictor = None
_device = None

sys.stdout = _real_stdout
MAX_LINE_LENGTH = 50 * 1024 * 1024
SAM2_MODEL_CFG = "configs/sam2.1/sam2.1_hiera_b+.yaml"


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


def _configure_cpu_threads():
    if resolve_torch_device() != "cpu":
        return
    num_cores = os.cpu_count() or 4
    torch.set_num_threads(num_cores)
    if hasattr(torch, "set_num_interop_threads"):
        try:
            torch.set_num_interop_threads(max(2, num_cores // 2))
        except RuntimeError:
            pass


def build_predictor(checkpoint_path: str):
    global _predictor, _device
    _device = torch.device(resolve_torch_device())
    _configure_cpu_threads()
    model = build_sam2(SAM2_MODEL_CFG, checkpoint_path, device=str(_device), mode="eval")
    _predictor = SAM2ImagePredictor(model)


def _safe_readline():
    line = sys.stdin.readline()
    if len(line) > MAX_LINE_LENGTH:
        raise ValueError("Input line too large")
    return line


def _prepare_mask_input(mask_input):
    """SAM2 expects mask_input shape (1, 1, H, W), not (H, W)."""
    arr = np.asarray(mask_input, dtype=np.float32)
    if arr.ndim == 4:
        return arr
    if arr.ndim == 3:
        if arr.shape[0] == 1:
            return arr[np.newaxis, :, :, :]
        return arr[np.newaxis, np.newaxis, :, :]
    if arr.ndim == 2:
        return arr[np.newaxis, np.newaxis, :, :]
    raise ValueError(f"Unsupported mask_input shape: {arr.shape}")


def _pick_best_mask(masks, scores):
    if masks is None or len(masks) == 0:
        return None, None, None
    if len(masks) == 1:
        return masks[0], scores[0] if scores is not None and len(scores) else 0.0, 0
    total = masks[0].shape[0] * masks[0].shape[1]
    areas = [int(m.sum()) for m in masks]
    small = [i for i, a in enumerate(areas) if a < 0.8 * total]
    idx = max(small, key=lambda i: float(scores[i])) if small else int(np.argmax(scores))
    return masks[idx], scores[idx], idx


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
        {
            "device": str(_device),
            "device_type": resolve_torch_device(),
            "backend": "interactive",
        },
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
                if image.ndim == 2:
                    image = np.stack([image, image, image], axis=-1)
                image = np.ascontiguousarray(image.astype(np.uint8, copy=False))
                with torch.inference_mode():
                    _predictor.set_image(image)
                send_response("image_set", {"original_size": list(image.shape[:2])})

            elif action == "predict":
                coords = np.array(req.get("point_coords") or [], dtype=np.float32)
                if coords.ndim == 1 and coords.size >= 2:
                    coords = coords.reshape(-1, 2)
                elif coords.ndim == 2 and coords.shape[-1] != 2:
                    coords = coords.reshape(-1, 2)
                labels = np.array(req.get("point_labels") or [], dtype=np.int32).reshape(-1)
                if coords.size == 0:
                    send_error("No point prompts provided")
                    continue

                mask_input = None
                if req.get("mask_input"):
                    mask_input = decode_array(
                        req["mask_input"],
                        req["mask_input_shape"],
                        req.get("mask_input_dtype", "float32"),
                    )

                multimask = bool(req.get("multimask_output", False))
                if mask_input is None and labels.size and (labels == 1).sum() == 1:
                    if (labels == 0).sum() == 0:
                        multimask = True

                predict_kwargs = {
                    "point_coords": coords,
                    "point_labels": labels,
                    "multimask_output": multimask,
                    "normalize_coords": True,
                }
                if mask_input is not None:
                    predict_kwargs["mask_input"] = _prepare_mask_input(mask_input)

                with torch.inference_mode():
                    masks, scores, low_res = _predictor.predict(**predict_kwargs)

                if masks is None or len(masks) == 0:
                    send_error("Model returned no mask")
                    continue

                if multimask and len(masks) > 1:
                    mask, score, idx = _pick_best_mask(masks, scores)
                    if hasattr(low_res, "__len__") and len(low_res) > idx:
                        lr = low_res[idx]
                    else:
                        lr = low_res[0] if len(low_res) else None
                else:
                    mask = masks[0]
                    score = float(scores[0]) if scores is not None and len(scores) else 0.0
                    lr = low_res[0] if low_res is not None and len(low_res) else None

                if hasattr(mask, "cpu"):
                    mask = mask.cpu().numpy()
                mask = (mask > 0).astype(np.uint8)
                try:
                    from scipy import ndimage

                    labeled, count = ndimage.label(mask)
                    if count > 1:
                        sizes = ndimage.sum(mask, labeled, range(1, count + 1))
                        keep = int(np.argmax(sizes)) + 1
                        mask = (labeled == keep).astype(np.uint8)
                except Exception:
                    pass

                payload = {
                    "mask": encode_array(mask),
                    "mask_shape": list(mask.shape),
                    "mask_dtype": "uint8",
                    "score": float(score),
                }
                if lr is not None:
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
