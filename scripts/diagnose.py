"""Read-only prerequisites check: no recording, routing or ONNX inference sessions."""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import struct
import sys
import warnings

REPO = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-dir", type=Path, default=REPO / "plugins/music")
    parser.add_argument("--backend", choices=("auto", "cuda", "dml"), default=os.environ.get("WORKBENCH_MUSIC_BACKEND", "auto"))
    parser.add_argument("--inspect-graph", action="store_true", help="Read actual graph shapes with optional onnx; still no inference session.")
    args = parser.parse_args()
    engine = args.engine_dir.resolve()
    checks = []
    def check(name, ok, detail):
        checks.append({"id": name, "ok": bool(ok), "detail": detail})
    windows = os.name == "nt"
    build = sys.getwindowsversion().build if windows else None
    check("windows", windows and build >= 20348, {"platform": platform.system(), "build": build, "requiredBuild": 20348})
    check("python", sys.version_info[:2] == (3, 12) and struct.calcsize("P") == 8,
          {"version": platform.python_version(), "bits": struct.calcsize("P") * 8, "executable": sys.executable})
    c_free = shutil.disk_usage("C:\\").free if windows else None
    check("disk", c_free is not None and c_free > 40 * 1024 ** 3,
          {"cFreeGiB": round(c_free / 1024 ** 3, 2) if c_free is not None else None, "minimumGiB": 40})
    dependencies = {}
    for name in ("numpy", "scipy", "psutil", "pycaw", "comtypes", "sounddevice"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    check("dependencies", all(dependencies.values()), dependencies)
    providers = []
    backend = args.backend
    try:
        with contextlib.redirect_stdout(sys.stderr):
            local_cuda = engine / "cuda-runtime"
            if backend in ("auto", "cuda") and (local_cuda / "onnxruntime/__init__.py").is_file():
                sys.path.insert(0, str(local_cuda))
            import onnxruntime as ort
            if backend == "cuda" or backend == "auto" and "CUDAExecutionProvider" in ort.get_available_providers():
                backend = "cuda"
                ort.preload_dlls(directory="")
            elif backend == "auto":
                backend = "dml"
            providers = ort.get_available_providers()
        expected = "CUDAExecutionProvider" if backend == "cuda" else "DmlExecutionProvider"
        check("ort", expected in providers, {"version": ort.__version__, "backend": backend, "providers": providers,
                                            "note": "Provider registration only; no model session was created."})
    except Exception as error:
        check("ort", False, str(error))
    try:
        if windows:
            sys.path.insert(0, str(engine))
            with contextlib.redirect_stdout(sys.stderr):
                import gpu_device
                gpu = gpu_device.cuda_adapter() if backend == "cuda" else gpu_device.best_adapter()
            check("gpu", True, gpu)
        else:
            check("gpu", False, "Windows DXGI/CUDA GPU discovery is unavailable on this platform.")
    except Exception as error:
        check("gpu", False, str(error))
    try:
        spec = importlib.util.spec_from_file_location("core_model_validation", REPO / "scripts/import-model.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        model = engine / "roformer-port/artifacts-balanced" / module.MODEL
        validated = module.validate_model(model, exported=True, require_graph=args.inspect_graph)
        check("model", True, {k: validated[k] for k in ("model", "bytes", "sha256", "productionDigest")})
        checks[-1]["detail"]["interfaceCheck"] = "actual ONNX shapes" if args.inspect_graph else "manifest contract and file hash"
    except Exception as error:
        check("model", False, str(error))
    try:
        if not windows:
            raise RuntimeError("VB-CABLE enumeration requires Windows.")
        with contextlib.redirect_stdout(sys.stderr), warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            from pycaw.pycaw import AudioUtilities
            cables = [device.FriendlyName for device in AudioUtilities.GetAllDevices()
                      if "CABLE Input" in (device.FriendlyName or "")]
        check("virtualCable", bool(cables), {"devices": cables, "installUrl": "https://vb-audio.com/Cable/",
                                            "note": "Separate system driver; this check does not install or alter it."})
    except Exception as error:
        check("virtualCable", False, str(error))
    missing = [row["id"] for row in checks if not row["ok"]]
    print(json.dumps({"ok": not missing, "checks": checks, "missing": missing, "backend": backend,
                      "engineDir": str(args.engine_dir.resolve()), "audioChanged": False,
                      "inferenceSessionCreated": False}, default=str))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
