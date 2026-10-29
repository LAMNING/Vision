"""ONNX Runtime inference for LLMDet-L (GroundingDINO / Swin-L).

Dependencies: numpy, pillow, onnxruntime only -- no torch, no transformers, no
tokenizers.

The exported graph has a DYNAMIC image geometry (``pixel_values`` is
``[1, 3, height, width]`` with symbolic ``height``/``width``) and a DYNAMIC text
length, so any resolution can be fed without re-exporting.  Images are resized
the way the Hugging Face processor does it -- shortest edge to 800 px, longest
edge capped at 1333 px -- so no aspect distortion is introduced.

Text is never padded: ``[CLS]``/``[SEP]`` attend to every token, so adding
padding tokens changes the predictions.  Labels are merged into one sentence,
exactly like ``GroundingDinoProcessor`` does:
``["a cat", "a dog"] -> "a cat. a dog."``.

Both shipped graphs are the same network; the ``fp16`` one stores its weights in
float16 plus a Cast-to-float32 per weight (48.9% smaller on disk, identical
boxes, scores within 0.0017).
"""
from __future__ import annotations

import os
import time

import numpy as np
from PIL import Image

from .tokenizer import BertWordPieceTokenizer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# GroundingDinoImageProcessor defaults: shortest_edge / longest_edge.
DEFAULT_SIZE = 800
DEFAULT_MAX_SIZE = 1333

_MODEL_FILES = {
    "fp32": "llmdet_large_dynamic.onnx",
    "fp16": "llmdet_large_dynamic_fp16.onnx",
}


def merge_candidate_labels(labels) -> str:
    """["A cat", "a dog"] -> "a cat. a dog." (same as GroundingDinoProcessor)."""
    if isinstance(labels, str):
        return labels
    labels = [t.strip().lower() for t in labels]
    return ". ".join(labels) + "."


def plan_size(size: tuple[int, int], shortest_edge: int = DEFAULT_SIZE,
              longest_edge: int = DEFAULT_MAX_SIZE) -> tuple[int, int]:
    """(width, height) -> (height, width) of the network input.

    Shortest edge is scaled to ``shortest_edge``; if that makes the longest edge
    exceed ``longest_edge`` the scale is reduced so the longest edge fits.  This
    mirrors ``GroundingDinoImageProcessor`` with ``do_resize=True``.
    """
    width, height = size
    scale = float(shortest_edge) / min(width, height)
    if max(width, height) * scale > longest_edge:
        scale = float(longest_edge) / max(width, height)
    new_width = min(longest_edge, max(32, int(width * scale)))
    new_height = min(longest_edge, max(32, int(height * scale)))
    return new_height, new_width


def preprocess(image: Image.Image, height: int, width: int) -> np.ndarray:
    """RGB -> normalized NCHW float32 tensor resized to (width, height)."""
    image = image.convert("RGB").resize((width, height), Image.BILINEAR)
    x = np.asarray(image, dtype=np.float32) / 255.0
    x = (x - MEAN) / STD
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None])


def _sigmoid(x: np.ndarray) -> np.ndarray:
    out = np.empty_like(x)
    pos = x >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-x[pos]))
    exp_x = np.exp(x[~pos])
    out[~pos] = exp_x / (1.0 + exp_x)
    return out


def _cxcywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    out = np.empty_like(boxes)
    out[:, 0] = boxes[:, 0] - 0.5 * boxes[:, 2]
    out[:, 1] = boxes[:, 1] - 0.5 * boxes[:, 3]
    out[:, 2] = boxes[:, 0] + 0.5 * boxes[:, 2]
    out[:, 3] = boxes[:, 1] + 0.5 * boxes[:, 3]
    return out


class LLMDet:
    """LLMDet-L (Swin-L + GroundingDINO head) zero-shot detector."""

    def __init__(self, onnx_dir: str | None = None, vocab_path: str | None = None,
                 providers: list[str] | None = None, precision: str = "fp32",
                 device: str = "cpu", size: int = DEFAULT_SIZE,
                 max_size: int = DEFAULT_MAX_SIZE, onnx_path: str | None = None):
        if precision not in _MODEL_FILES:
            raise ValueError(f"precision must be one of {sorted(_MODEL_FILES)}, got {precision!r}")
        if device not in ("cpu", "cuda", "auto"):
            raise ValueError(f"device must be 'cpu', 'cuda' or 'auto', got {device!r}")

        # The path fix-ups have to run before onnxruntime is imported: importing
        # the CPU wheel first would shadow the onnxruntime-gpu side install.
        if providers is None and device in ("cuda", "auto"):
            from .cuda_dlls import setup_cuda_paths

            setup_cuda_paths()

        import onnxruntime as ort

        self.precision = precision
        self.device = device
        self.size = size
        self.max_size = max_size
        self.onnx_dir = onnx_dir or os.path.join(ROOT, "onnx")
        self.onnx_path = onnx_path or os.path.join(self.onnx_dir, _MODEL_FILES[precision])
        self.tokenizer = BertWordPieceTokenizer(vocab_path or os.path.join(HERE, "vocab.txt"))
        self.last_latency_ms = 0.0
        self.last_canvas: tuple[int, int] | None = None

        available = ort.get_available_providers()
        if providers is not None:
            self.providers = providers
        elif device in ("cuda", "auto"):
            if "CUDAExecutionProvider" in available:
                self.providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
            elif device == "cuda":
                raise RuntimeError("CUDAExecutionProvider is not available; install onnxruntime-gpu "
                                   "or use device='cpu'")
            else:
                self.providers = ["CPUExecutionProvider"]
        else:
            self.providers = ["CPUExecutionProvider"]

        if not os.path.exists(self.onnx_path):
            raise FileNotFoundError(
                f"ONNX model not found: {self.onnx_path}\n"
                "Set IMAGELABEL_LLMDET_ONNX / IMAGELABEL_LLMDET_DIR (or /admin/model-config) "
                "to a directory containing llmdet_large_dynamic.onnx / "
                "llmdet_large_dynamic_fp16.onnx; the export scripts are not shipped in this repo."
            )
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(self.onnx_path, opts, providers=self.providers)

    # ------------------------------------------------------------------ input
    def plan(self, image: Image.Image) -> tuple[int, int]:
        """(height, width) the image will be resized to before inference."""
        return plan_size(image.size, self.size, self.max_size)

    # --------------------------------------------------------------- predict
    def predict(self, image: Image.Image, labels, threshold: float = 0.3,
                text_threshold: float = 0.25, top_k: int | None = None) -> list[dict]:
        """Detect `labels` in `image`; boxes are in original-image pixels."""
        height, width = self.plan(image)
        self.last_canvas = (height, width)

        text = merge_candidate_labels(labels)
        ids, token_type_ids, attention_mask = self.tokenizer.encode(text)

        feeds = {
            "pixel_values": preprocess(image, height, width),
            "pixel_mask": np.ones((1, height, width), dtype=np.int64),
            "input_ids": np.asarray([ids], dtype=np.int64),
            "token_type_ids": np.asarray([token_type_ids], dtype=np.int64),
            "attention_mask": np.asarray([attention_mask], dtype=np.int64),
        }
        t0 = time.perf_counter()
        logits, pred_boxes = self.session.run(None, feeds)
        self.last_latency_ms = (time.perf_counter() - t0) * 1000.0

        return self.postprocess(logits[0], pred_boxes[0], ids, image.size,
                                threshold, text_threshold, top_k)

    def postprocess(self, logits: np.ndarray, pred_boxes: np.ndarray, ids: list[int],
                    image_size: tuple[int, int], threshold: float, text_threshold: float,
                    top_k: int | None = None) -> list[dict]:
        probs = _sigmoid(logits)                       # (900, 256)
        scores = probs.max(axis=-1)                    # (900,)
        keep = np.flatnonzero(scores > threshold)
        if keep.size == 0:
            return []
        if top_k is not None:
            keep = keep[np.argsort(scores[keep])[::-1][:top_k]]

        # The graph returns cxcywh normalized to the resized canvas; the resize
        # keeps the aspect ratio, so normalized coordinates are image-relative.
        boxes = _cxcywh_to_xyxy(pred_boxes[keep]).astype(np.float32)
        img_w, img_h = image_size
        boxes *= np.array([img_w, img_h, img_w, img_h], dtype=np.float32)

        # Text label of each query: tokens whose probability passes the text
        # threshold, ignoring [CLS] (and the padded tail of the 256 columns).
        posmap = probs[keep] > text_threshold
        posmap[:, 0] = False
        n_text = len(ids)
        if n_text < posmap.shape[1]:
            posmap[:, n_text:] = False

        out = []
        for row, (score, box) in enumerate(zip(scores[keep], boxes)):
            columns = np.flatnonzero(posmap[row]).tolist()
            token_ids = [ids[i] for i in columns]
            out.append({
                "label": self.tokenizer.decode(token_ids),
                "score": float(score),
                "box": [float(box[0]), float(box[1]), float(box[2]), float(box[3])],  # x0 y0 x1 y1
                # Column indices of the positive text tokens. Lets callers map a
                # query back to the prompt (label span) that produced it, instead
                # of matching on the decoded label string (which may mix tokens).
                "token_indices": [int(i) for i in columns],
            })
        out.sort(key=lambda r: r["score"], reverse=True)
        return out
