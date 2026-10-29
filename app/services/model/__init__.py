"""Vendored LLMDet ONNX runtime (copied from D:\\Ideas\\onnx-runtime\\runtime).

Pure ``onnxruntime`` + ``numpy`` + ``pillow`` -- no torch, no transformers, no
tokenizers. ``MarianTranslator`` is imported lazily so the detector still works
when no Marian checkpoint is configured.
"""
from .detector import LLMDet, merge_candidate_labels, plan_size

__all__ = ["LLMDet", "merge_candidate_labels", "plan_size", "MarianTranslator"]


def __getattr__(name):
    """``MarianTranslator`` is imported lazily (needs a Marian ONNX checkpoint)."""
    if name == "MarianTranslator":
        from .translator import MarianTranslator

        return MarianTranslator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | {"MarianTranslator"})
