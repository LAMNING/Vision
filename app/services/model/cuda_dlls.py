"""Make the CUDA execution provider loadable without a system-wide CUDA toolkit.

`onnxruntime-gpu` needs CUDA 13 + cuDNN 9 shared libraries.  They are usually
available from one of these places, which this helper registers on `PATH`:

* ``$LLMDET_ORT_GPU_DIR`` (default ``<repo>/.cuda``) — a side install created with
  ``pip install --target .cuda onnxruntime-gpu`` (see the README);
* ``site-packages/torch/lib`` — PyTorch wheels ship ``cublasLt64_13.dll``,
  ``cudnn64_9.dll``, ... and are reused here without importing torch;
* ``site-packages/nvidia/*/bin`` — the ``nvidia-*-cu13`` pip packages.

Nothing here is required for CPU inference.
"""
from __future__ import annotations

import glob
import os
import site
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _candidate_dirs() -> list[str]:
    dirs: list[str] = []
    side = os.environ.get("LLMDET_ORT_GPU_DIR", os.path.join(ROOT, ".cuda"))
    if os.path.isdir(side):
        dirs.append(side)
        dirs.extend(os.path.dirname(p) for p in glob.glob(os.path.join(side, "nvidia", "*", "bin")))
    try:
        site_packages = site.getsitepackages()
    except AttributeError:  # pragma: no cover - embedded interpreters
        site_packages = []
    for sp in site_packages:
        torch_lib = os.path.join(sp, "torch", "lib")
        if os.path.isdir(torch_lib):
            dirs.append(torch_lib)
        dirs.extend(os.path.dirname(p) for p in glob.glob(os.path.join(sp, "nvidia", "*", "bin")))
    seen, unique = set(), []
    for d in dirs:
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            unique.append(d)
    return unique


def setup_cuda_paths() -> None:
    """Put the onnxruntime-gpu build and the CUDA/cuDNN DLLs on the search path.

    Must be called *before* ``import onnxruntime``: once the CPU wheel is
    imported, the side build can no longer be selected.
    """
    side = os.environ.get("LLMDET_ORT_GPU_DIR", os.path.join(ROOT, ".cuda"))
    if os.path.isdir(os.path.join(side, "onnxruntime")) and side not in sys.path:
        sys.path.insert(0, side)  # pick up the onnxruntime-gpu build

    dirs = _candidate_dirs()
    if dirs:
        os.environ["PATH"] = ";".join(dirs) + ";" + os.environ.get("PATH", "")
        for d in dirs:
            try:
                os.add_dll_directory(d)
            except (AttributeError, OSError):
                pass


def enable_cuda() -> bool:
    """Register the CUDA DLL directories; True if a CUDA provider is importable."""
    setup_cuda_paths()
    import onnxruntime as ort

    return "CUDAExecutionProvider" in ort.get_available_providers()
