"""Explicit ORT execution diagnostics; failures do not disable LIC CPU fallback."""
from __future__ import annotations

import contextlib
import io
import json


def inspect_runtime() -> dict:
    """Retain DLL diagnostics and distinguish advertised, session and executed EPs."""
    result = {"advertised_providers": [], "gpu_execution_ok": False, "errors": []}
    capture = io.StringIO()
    try:
        import onnxruntime as ort
        result["advertised_providers"] = ort.get_available_providers()
        if "CUDAExecutionProvider" not in result["advertised_providers"]:
            result["errors"].append("CUDA EP is not advertised; CPU face fallback remains available")
            return result
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            import torch  # loads the selected PyTorch runtime DLLs
            result["torch_cuda"] = torch.version.cuda
            result["cudnn"] = torch.backends.cudnn.version()
            ort.preload_dlls()
        from .validation import graph_execution
        result["graph"] = graph_execution(gpu=True)
        result["gpu_execution_ok"] = result["graph"]["ok"]
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        result["preload_diagnostics"] = capture.getvalue()
    return result


if __name__ == "__main__":
    print(json.dumps(inspect_runtime()))
