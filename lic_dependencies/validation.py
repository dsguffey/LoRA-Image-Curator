"""Separate metadata/CPU evidence from actual NVIDIA graph execution evidence."""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib
import importlib.metadata as metadata
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .profile import PROFILE, conflicts, inventory


def package_files() -> dict:
    """Find shared owned paths and verify selected native-provider RECORD hashes."""
    owners: dict[str, list[str]] = {}
    bad = []
    hashed = 0
    for dist in metadata.distributions():
        name = dist.metadata["Name"].lower().replace("_", "-")
        for member in dist.files or []:
            if not member.hash:
                continue
            path = Path(dist.locate_file(member))
            key = os.path.normcase(os.path.abspath(path))
            owners.setdefault(key, []).append(name)
            if name not in {"onnxruntime-gpu", "opencv-contrib-python"}:
                continue
            if not path.is_file():
                bad.append(f"Missing: {path}")
                continue
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, member.hash.mode).digest()
            actual = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            hashed += 1
            if actual != member.hash.value:
                bad.append(f"Hash mismatch: {path}")
    duplicates = {p: names for p, names in owners.items() if len(names) > 1}
    return {"duplicate_owned_paths": duplicates, "hash_errors": bad, "hashes_checked": hashed}


def tiny_graph() -> bytes:
    """Make a fixed, broadly supported MatMul graph entirely in memory."""
    import onnx
    from onnx import TensorProto, helper
    node = helper.make_node("MatMul", ["x", "y"], ["z"])
    graph = helper.make_graph([node], "lic-cuda-proof",
        [helper.make_tensor_value_info(n, TensorProto.FLOAT, [64, 64]) for n in ("x", "y")],
        [helper.make_tensor_value_info("z", TensorProto.FLOAT, [64, 64])])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    return model.SerializeToString()


def graph_execution(*, gpu: bool) -> dict:
    """Run real ONNX work and inspect profiled node execution, not advertised EPs."""
    import numpy as np
    import onnxruntime as ort
    requested = "CUDAExecutionProvider" if gpu else "CPUExecutionProvider"
    with tempfile.TemporaryDirectory(prefix="lic-ort-proof-") as temporary:
        options = ort.SessionOptions()
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(temporary) / "profile")
        if gpu:
            options.add_session_config_entry("session.disable_cpu_ep_fallback", "1")
        session = ort.InferenceSession(tiny_graph(), sess_options=options, providers=[requested])
        x = np.ones((64, 64), dtype=np.float32)
        value = session.run(None, {"x": x, "y": x})[0]
        events = json.loads(Path(session.end_profiling()).read_text())
        providers = sorted({e.get("args", {}).get("provider") for e in events
                            if e.get("args", {}).get("provider")})
        ok = bool(np.allclose(value, 64)) and requested in providers
        return {"ok": ok, "requested": requested, "session_providers": session.get_providers(),
                "executed_providers": providers}


def gpu_evidence(face_model_root: Path | None = None, image: Path | None = None) -> dict:
    """Require Torch and ONNX GPU work; optionally verify copied local face weights."""
    import torch
    import onnxruntime as ort
    result: dict = {"ok": False, "face_validated": False}
    if not torch.cuda.is_available():
        raise RuntimeError("Torch CUDA is unavailable")
    value = torch.ones((64, 64), device="cuda") @ torch.ones((64, 64), device="cuda")
    # Exercise cuDNN as well as a CUDA tensor kernel.
    conv = torch.nn.functional.conv2d(torch.ones((1, 3, 16, 16), device="cuda"),
                                     torch.ones((8, 3, 3, 3), device="cuda"))
    torch.cuda.synchronize()
    result.update(torch=str(torch.__version__), cuda=torch.version.cuda,
                  cudnn=torch.backends.cudnn.version(), device=torch.cuda.get_device_name(0),
                  torch_ok=bool(torch.all(value == 64).item() and torch.all(conv == 27).item()))
    if (str(torch.__version__) != PROFILE["torch"] or torch.version.cuda != "13.0"
            or not result["cudnn"] or not str(result["cudnn"]).startswith("9")):
        raise RuntimeError(f"Unexpected CUDA/cuDNN runtime: {result}")
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        ort.preload_dlls()
    result["preload_diagnostics"] = capture.getvalue()
    result["onnx"] = graph_execution(gpu=True)
    if face_model_root is not None and image is not None:
        import shutil
        import cv2
        from insightface.app import FaceAnalysis
        source = face_model_root / "models" / "buffalo_l"
        if not (source / "det_10g.onnx").is_file() or not (source / "w600k_r50.onnx").is_file():
            raise RuntimeError("Complete existing buffalo_l detection/recognition weights required; no download allowed")
        with tempfile.TemporaryDirectory(prefix="lic-face-proof-") as temporary:
            target = Path(temporary) / "models" / "buffalo_l"
            target.mkdir(parents=True)
            for name in ("det_10g.onnx", "w600k_r50.onnx"):
                shutil.copyfile(source / name, target / name)
            app = FaceAnalysis(name="buffalo_l", root=temporary,
                               allowed_modules=["detection", "recognition"],
                               providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            # InsightFace 1.0.1's get_model drops sess_options. For diagnostics
            # only, recreate each identical session with profiling enabled;
            # the upstream detection/embedding methods still run unchanged.
            for name, model in app.models.items():
                opts = ort.SessionOptions()
                opts.enable_profiling = True
                opts.profile_file_prefix = str(Path(temporary) / name)
                model.session = ort.InferenceSession(model.model_file, sess_options=opts,
                    providers=model.session.get_providers(),
                    provider_options=[model.session.get_provider_options()[p]
                                      for p in model.session.get_providers()])
            app.prepare(ctx_id=0, det_size=(640, 640))
            decoded = cv2.imread(str(image))
            if decoded is None:
                raise RuntimeError("Face fixture could not be decoded")
            faces = app.get(decoded)
            sessions = {}
            for name, model in app.models.items():
                events = json.loads(Path(model.session.end_profiling()).read_text())
                used = sorted({e.get("args", {}).get("provider") for e in events if e.get("args", {}).get("provider")})
                sessions[name] = {"advertised": model.session.get_providers(), "executed": used}
            import numpy as np
            result["face_sessions"] = sessions
            result["face_count"] = len(faces)
            result["face_validated"] = set(sessions) == {"detection", "recognition"} and bool(faces) and all(
                f.embedding is not None and f.embedding.shape == (512,) and np.isfinite(f.embedding).all() for f in faces
            ) and all("CUDAExecutionProvider" in s["executed"] for s in sessions.values())
    result["ok"] = result["torch_ok"] and result["onnx"]["ok"]
    return result


def run_validation(*, gpu: bool = False, face_model_root: Path | None = None,
                   image: Path | None = None) -> dict:
    """Produce independent test stages without ever asserting human approval."""
    installed = inventory()
    report: dict = {"schema_version": 1, "inventory": installed, "errors": [],
                    "cpu_metadata_test_valid": False, "gpu_validated": False,
                    "approved_for_lic": False}
    try:
        face_dist = metadata.distribution("insightface")
        direct = json.loads(face_dist.read_text("direct_url.json") or "{}")
        report["insightface_artifact"] = {
            "version": face_dist.version,
            "wheel_sha256": direct.get("archive_info", {}).get("hashes", {}).get("sha256"),
            "provenance": json.loads(face_dist.read_text("LIC_PROVENANCE.json") or "null"),
        }
    except metadata.PackageNotFoundError:
        report["errors"].append("InsightFace distribution is missing")
    report["errors"].extend(conflicts(installed, nvidia=True))
    for name in ("onnxruntime-gpu", "opencv-contrib-python", "mediapipe"):
        if installed.get(name) != PROFILE[name]:
            report["errors"].append(f"Expected {name}=={PROFILE[name]}")
    check = subprocess.run([sys.executable, "-m", "pip", "check"], text=True, capture_output=True)
    report["pip_check"] = {"exit_code": check.returncode, "output": check.stdout + check.stderr}
    report["files"] = package_files()
    report["imports"] = {}
    with tempfile.TemporaryDirectory(prefix="lic-import-cache-") as cache:
        previous = os.environ.get("MPLCONFIGDIR")
        os.environ["MPLCONFIGDIR"] = cache
        try:
            for name in ("numpy", "cv2", "onnxruntime", "insightface", "mediapipe"):
                try:
                    module = importlib.import_module(name)
                    report["imports"][name] = getattr(module, "__version__", "imported")
                except Exception as error:
                    report["errors"].append(f"{name}: {type(error).__name__}: {error}")
            try:
                report["cpu_graph"] = graph_execution(gpu=False)
            except Exception as error:
                report["errors"].append(f"CPU graph: {error}")
        finally:
            if previous is None:
                os.environ.pop("MPLCONFIGDIR", None)
            else:
                os.environ["MPLCONFIGDIR"] = previous
    report["cpu_metadata_test_valid"] = not (report["errors"] or check.returncode or
        report["files"]["duplicate_owned_paths"] or report["files"]["hash_errors"]) and report.get("cpu_graph", {}).get("ok", False)
    if gpu:
        try:
            report["gpu"] = gpu_evidence(face_model_root, image)
            # A full GPU gate requires actual InsightFace inference as well.
            report["gpu_validated"] = bool(report["gpu"]["ok"] and report["gpu"]["face_validated"])
        except Exception as error:
            report["errors"].append(f"GPU: {type(error).__name__}: {error}")
    return report


def main() -> int:
    """Write a report to an explicit diagnostic destination, never user catalogs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--gpu", action="store_true")
    parser.add_argument("--face-model-root", type=Path)
    parser.add_argument("--image", type=Path)
    args = parser.parse_args()
    report = run_validation(gpu=args.gpu, face_model_root=args.face_model_root, image=args.image)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"CPU/metadata valid: {report['cpu_metadata_test_valid']}; GPU validated: {report['gpu_validated']}")
    print(f"Report: {args.output}")
    return 0 if report["cpu_metadata_test_valid"] and (not args.gpu or report["gpu_validated"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
