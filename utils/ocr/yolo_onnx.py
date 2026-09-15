from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from utils.config import Settings

# COCO 80 classes (YOLOv8 default)
COCO_NAMES: Tuple[str, ...] = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)


class YoloOnnxDetector:
    """Minimal YOLOv8 ONNX inference (CPU or CUDA EP)."""

    _sessions: Dict[str, Any] = {}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.onnx_path = Path(settings.jarvis_ocr_yolo_onnx_path)

    def _get_session(self):
        if not self.onnx_path.is_file():
            raise FileNotFoundError(f"missing_yolo_onnx:{self.onnx_path}")
        key = str(self.onnx_path.resolve())
        if key not in YoloOnnxDetector._sessions:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            providers: List[str] = []
            if self.settings.jarvis_ocr_device.startswith("cuda"):
                providers.append("CUDAExecutionProvider")
            providers.append("CPUExecutionProvider")
            YoloOnnxDetector._sessions[key] = ort.InferenceSession(
                str(self.onnx_path), sess_options=so, providers=providers
            )
        return YoloOnnxDetector._sessions[key]

    def predict(self, image: Image.Image, conf_thres: float = 0.25, iou_thres: float = 0.45) -> List[Dict[str, Any]]:
        session = self._get_session()
        input_name = session.get_inputs()[0].name
        # Letterbox to 640
        im, ratio, pad = self._letterbox(image, 640)
        im = im.astype(np.float32) / 255.0
        im = np.transpose(im, (2, 0, 1))[None, ...]
        out = session.run(None, {input_name: im})[0]
        # (1, 84, 8400) typical YOLOv8 ONNX
        preds = self._postprocess(out, ratio, pad, conf_thres, iou_thres)
        return preds

    @staticmethod
    def _letterbox(img: Image.Image, new: int) -> Tuple[np.ndarray, float, Tuple[float, float]]:
        w, h = img.size
        r = min(new / w, new / h)
        nw, nh = int(round(w * r)), int(round(h * r))
        resized = img.resize((nw, nh), Image.Resampling.BILINEAR)
        pad_w, pad_h = new - nw, new - nh
        dw, dh = pad_w / 2, pad_h / 2
        canvas = Image.new("RGB", (new, new), (114, 114, 114))
        canvas.paste(resized, (int(round(dw - 0.1)), int(round(dh - 0.1))))
        arr = np.asarray(canvas)
        return arr, r, (dw, dh)

    def _postprocess(
        self,
        out: np.ndarray,
        ratio: float,
        pad: Tuple[float, float],
        conf_thres: float,
        iou_thres: float,
    ) -> List[Dict[str, Any]]:
        # out: (1, 84, n) or (1, n, 84)
        x = out[0]
        if x.shape[0] == 84:
            x = x.T
        # x: (n, 84) -> cx,cy,w,h + 80 scores
        boxes = x[:, :4]
        scores = x[:, 4:]
        cls_ids = np.argmax(scores, axis=1)
        cls_scores = np.max(scores, axis=1)
        mask = cls_scores >= conf_thres
        boxes = boxes[mask]
        cls_ids = cls_ids[mask]
        cls_scores = cls_scores[mask]
        if len(boxes) == 0:
            return []
        # xywh -> xyxy in letterbox space
        cx, cy, w, h = boxes.T
        x1 = cx - w / 2
        y1 = cy - h / 2
        x2 = cx + w / 2
        y2 = cy + h / 2
        # map back to original image coords
        dw, dh = pad
        x1 = (x1 - dw) / ratio
        y1 = (y1 - dh) / ratio
        x2 = (x2 - dw) / ratio
        y2 = (y2 - dh) / ratio
        xyxy = np.stack([x1, y1, x2, y2], axis=1)
        keep = self._nms(xyxy, cls_scores, iou_thres)
        results: List[Dict[str, Any]] = []
        for i in keep:
            cid = int(cls_ids[i])
            label = COCO_NAMES[cid] if 0 <= cid < len(COCO_NAMES) else f"class_{cid}"
            bx = [float(xyxy[i, 0]), float(xyxy[i, 1]), float(xyxy[i, 2]), float(xyxy[i, 3])]
            results.append({"label": label, "score": float(cls_scores[i]), "bbox": bx})
        return results

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> List[int]:
        idxs = scores.argsort()[::-1]
        keep: List[int] = []
        while idxs.size > 0:
            i = int(idxs[0])
            keep.append(i)
            if idxs.size == 1:
                break
            rest = idxs[1:]
            ious = YoloOnnxDetector._iou(boxes[i], boxes[rest])
            idxs = rest[ious < iou_thres]
        return keep

    @staticmethod
    def _iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        x1 = np.maximum(a[0], b[:, 0])
        y1 = np.maximum(a[1], b[:, 1])
        x2 = np.minimum(a[2], b[:, 2])
        y2 = np.minimum(a[3], b[:, 3])
        inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        return inter / (area_a + area_b - inter + 1e-6)
