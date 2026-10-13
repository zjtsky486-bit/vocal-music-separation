"""Explicitly build T241 from pinned, licensed upstream sources (not runtime startup)."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

REPO = Path(__file__).resolve().parents[1]
UPSTREAM = "https://github.com/santiquiroz/port-bs-roformer-onnx.git"
UPSTREAM_PIN = "3f727cde9710cd0237130a96155a70e2c850d553"
MSST = "https://github.com/ZFTurbo/Music-Source-Separation-Training.git"
MSST_PIN = "e247dfe4abc1f17c69dff719207fe045dc04413a"
CHECKPOINT_URL = "https://huggingface.co/KimberleyJSN/melbandroformer/resolve/main/MelBandRoformer.ckpt"
CHECKPOINT_SHA = "87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e"
MODEL_SHA = "7d3d1291a047e6baa7e35618230ebab2a1961d207ab70598677e159237bdab75"


def guard_space(min_gib=40):
    import shutil
    if os.name == "nt" and shutil.disk_usage("C:\\").free <= min_gib * 1024 ** 3:
        raise RuntimeError(f"C: has at most {min_gib:g} GiB free; export stopped.")


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def windows_job(process):
    """Own only this subprocess tree; closing the job cancels its descendants."""
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64), ("Flags", w.DWORD),
                    ("MinWS", ctypes.c_size_t), ("MaxWS", ctypes.c_size_t), ("Active", w.DWORD),
                    ("Affinity", ctypes.c_size_t), ("Priority", w.DWORD), ("Scheduling", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("IO", IO), ("ProcessMemory", ctypes.c_size_t),
                    ("JobMemory", ctypes.c_size_t), ("PeakProcess", ctypes.c_size_t), ("PeakJob", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        process.terminate()
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        error = ctypes.get_last_error()
        if process.poll() is not None:
            kernel.CloseHandle(handle)
            return lambda: None
        process.terminate()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    return lambda: kernel.CloseHandle(handle)


def run(command, *, cache, env, cwd=None):
    guard_space(41)
    with (cache / "export.log").open("ab") as log:
        process = subprocess.Popen([str(v) for v in command], cwd=cwd, env=env,
                                   stdout=log, stderr=log,
                                   creationflags=0x08000000 if os.name == "nt" else 0)
        close_job = windows_job(process) if os.name == "nt" else lambda: None
        try:
            while process.poll() is None:
                guard_space()
                time.sleep(.5)
            if process.returncode:
                raise RuntimeError(f"Command exited {process.returncode}; see {cache / 'export.log'}")
        finally:
            close_job()
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def checkout(folder, url, pin, cache, env):
    if not (folder / ".git").exists():
        folder.mkdir(parents=True, exist_ok=True)
        run(["git", "init", folder], cache=cache, env=env)
        run(["git", "-C", folder, "remote", "add", "origin", url], cache=cache, env=env)
    actual = subprocess.check_output(["git", "-C", str(folder), "remote", "get-url", "origin"], text=True).strip()
    if actual != url:
        raise ValueError("Refusing to use a source checkout with an unexpected origin.")
    run(["git", "-C", folder, "fetch", "--depth", "1", "origin", pin], cache=cache, env=env)
    run(["git", "-C", folder, "checkout", "--detach", pin], cache=cache, env=env)
    actual = subprocess.check_output(["git", "-C", str(folder), "rev-parse", "HEAD"], text=True).strip()
    if actual != pin:
        raise ValueError("Source checkout does not match its pinned commit.")
    if subprocess.check_output(["git", "-C", str(folder), "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        raise ValueError("Pinned source contains tracked local modifications; export stopped without discarding them.")


def checkpoint(path):
    if path.exists():
        if file_sha(path) != CHECKPOINT_SHA:
            raise ValueError("Existing checkpoint hash is invalid; it was not overwritten.")
        return
    guard_space(41)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        request = urllib.request.Request(CHECKPOINT_URL, headers={"User-Agent": "vocal-music-core/1.0"})
        hasher = hashlib.sha256()
        with urllib.request.urlopen(request, timeout=60) as response, pending.open("xb") as stream:
            while block := response.read(128 * 1024):
                guard_space()
                stream.write(block)
                hasher.update(block)
        if hasher.hexdigest() != CHECKPOINT_SHA:
            raise ValueError("Downloaded official checkpoint hash is invalid.")
        guard_space()
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True, help="Python 3.12 export environment, or base Python when using --prepare.")
    parser.add_argument("--prepare", action="store_true", help="Explicitly create/install the project-local export-only venv.")
    parser.add_argument("--engine-dir", type=Path, default=REPO / "plugins/music")
    parser.add_argument("--dry-run", action="store_true", help="Print the plan without downloads, installations or writes.")
    args = parser.parse_args()
    cache = REPO / ".cache/export"
    upstream = cache / "upstream"
    model = upstream / "artifacts/mel_band_roformer_kim_T241.onnx"
    plan = {"upstream": UPSTREAM, "upstreamPin": UPSTREAM_PIN, "msst": MSST, "msstPin": MSST_PIN,
            "checkpointUrl": CHECKPOINT_URL, "checkpointSha256": CHECKPOINT_SHA,
            "samples": 105840, "frames": 241, "prepare": args.prepare,
            "engineDir": str(args.engine_dir.resolve()), "modelDownloadUrl": None}
    try:
        if args.dry_run:
            print(json.dumps({"ok": True, "dryRun": True, **plan}))
            return 0
        version = json.loads(subprocess.check_output([str(args.python.resolve(strict=True)), "-B", "-c", "import json,sys,struct;print(json.dumps([*sys.version_info[:2],struct.calcsize('P')*8]))"], text=True))
        if version != [3, 12, 64]:
            raise ValueError("Export requires CPython 3.12 x64.")
        guard_space(41)
        cache.mkdir(parents=True, exist_ok=True)
        temp = cache / "tmp"
        temp.mkdir(exist_ok=True)
        env = {**os.environ, "PIP_CACHE_DIR": str(REPO / ".cache/pip"), "TEMP": str(temp), "TMP": str(temp),
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
               "TORCH_HOME": str(cache / "torch"), "HF_HOME": str(cache / "huggingface")}
        python = args.python.resolve()
        if args.prepare:
            python = cache / "venv/Scripts/python.exe"
            if not python.exists():
                run([args.python, "-m", "venv", python.parent.parent], cache=cache, env=env)
            run([python, "-m", "pip", "--isolated", "install", "--index-url", "https://pypi.org/simple",
                 "--cache-dir", REPO / ".cache/pip", "--disable-pip-version-check", "--only-binary=:all:",
                 "--requirement", REPO / "requirements/export.txt"], cache=cache, env=env)
        else:
            # The script never installs packages into the supplied interpreter implicitly.
            run([python, "-c", "import torch,onnx,onnxruntime,einops,rotary_embedding_torch,beartype,librosa,ml_collections,yaml"], cache=cache, env=env)
        checkout(upstream, UPSTREAM, UPSTREAM_PIN, cache, env)
        checkout(upstream / "msst", MSST, MSST_PIN, cache, env)
        checkpoint(upstream / "checkpoints/MelBandRoformer.ckpt")
        run([python, upstream / "toolkit/export_roformer.py", "mel_band_roformer_kim", "--chunk", "105840"], cache=cache, env=env, cwd=upstream)
        manifest = json.loads((model.parent / "manifest.json").read_text())
        if manifest.get("msst_commit") != MSST_PIN:
            raise ValueError("Export manifest does not report the pinned MSST source.")
        run([python, REPO / "scripts/import-model.py", "--model", model, "--accept-export", "--require-graph",
             "--engine-dir", args.engine_dir], cache=cache, env=env)
        found = file_sha(model)
        print(json.dumps({"ok": True, **plan, "sha256": found, "productionDigest": found == MODEL_SHA,
                          "model": str(args.engine_dir.resolve() / "roformer-port/artifacts-balanced" / model.name)}))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
