"""Validate/import the licensed T241 graph; no model download or inference."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

REPO = Path(__file__).resolve().parents[1]
MODEL = "mel_band_roformer_kim_T241.onnx"
MODEL_SHA = "7d3d1291a047e6baa7e35618230ebab2a1961d207ab70598677e159237bdab75"
MODEL_BYTES = 921863688
CHECKPOINT_SHA = "87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e"
CHECKPOINT_URL = "https://huggingface.co/KimberleyJSN/melbandroformer/resolve/main/MelBandRoformer.ckpt"
INPUT = [1, 2050, 241, 2]
OUTPUT = [1, 1, 2050, 241, 2]


def guard_space(min_gib: float = 40) -> None:
    if os.name == "nt" and shutil.disk_usage("C:\\").free <= min_gib * 1024 ** 3:
        raise RuntimeError(f"C: has at most {min_gib:g} GiB free; stopped before writing.")


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 ** 2), b""):
            hasher.update(block)
    return hasher.hexdigest()


def inspect_graph(path: Path) -> dict:
    import onnx
    graph = onnx.load(str(path), load_external_data=False)
    if any(t.data_location == onnx.TensorProto.EXTERNAL for t in graph.graph.initializer):
        raise ValueError("The supported T241 graph must be self-contained.")
    inputs = [(v.name, [d.dim_value for d in v.type.tensor_type.shape.dim]) for v in graph.graph.input]
    outputs = [(v.name, [d.dim_value for d in v.type.tensor_type.shape.dim]) for v in graph.graph.output]
    if inputs != [("spec", INPUT)] or outputs != [("mask", OUTPUT)]:
        raise ValueError("ONNX inputs/outputs do not match the fixed T241 contract.")
    if any(v.type.tensor_type.elem_type != onnx.TensorProto.FLOAT for v in [*graph.graph.input, *graph.graph.output]):
        raise ValueError("The T241 graph must have FP32 inputs and outputs.")
    return {"input": {"spec": INPUT}, "output": {"mask": OUTPUT}}


def validate_model(model: Path, manifest: Path | None = None, *, exported: bool = False,
                   require_graph: bool = False) -> dict:
    model = model.resolve(strict=True)
    manifest = manifest or model.parent / "manifest.json"
    if not model.is_file():
        raise ValueError("Model path must be a regular file.")
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    info = data["models"]["mel_band_roformer_kim"]
    expected = {
        "file": MODEL, "architecture": "mel_band_roformer", "stems": ["vocals"],
        "chunk_size": 105840, "frames": 241, "sample_rate": 44100,
        "n_fft": 2048, "hop_length": 441, "num_overlap": 2,
        "source_checkpoint": "MelBandRoformer.ckpt",
        "source_checkpoint_sha256": CHECKPOINT_SHA,
        "source_checkpoint_url": CHECKPOINT_URL,
        "graph_io": {"input": {"spec": INPUT}, "output": {"mask": OUTPUT}},
    }
    for key, value in expected.items():
        if info.get(key) != value:
            raise ValueError(f"Unexpected model manifest field: {key}")
    if info.get("redistributable") is not True or not str(info.get("weights_license", "")).startswith("MIT"):
        raise ValueError("The manifest must preserve the original explicit MIT weights license.")
    if info.get("weights_author") != "KimberleyJensen (KimberleyJSN)":
        raise ValueError("The model author's attribution is missing or changed.")
    parity = info.get("net_parity_cpu_max_abs")
    if not isinstance(parity, (int, float)) or isinstance(parity, bool) or not 0 <= parity < 1e-4:
        raise ValueError("The export must carry a passing torch/ORT CPU graph parity result.")
    found = digest(model)
    if found != info.get("onnx_sha256") or model.stat().st_size != info.get("onnx_bytes"):
        raise ValueError("ONNX model hash/size does not match its manifest.")
    if not exported and (found != MODEL_SHA or model.stat().st_size != MODEL_BYTES):
        raise ValueError("This is not the previously validated production T241 graph. Use the documented export route for new exports.")
    if exported and found != MODEL_SHA and data.get("msst_commit") != "e247dfe4abc1f17c69dff719207fe045dc04413a":
        raise ValueError("A new export must preserve the verified pinned MSST source commit.")
    if require_graph:
        inspect_graph(model)
    return {"model": str(model), "sha256": found, "bytes": model.stat().st_size,
            "productionDigest": found == MODEL_SHA, "info": info,
            "manifest": data}


def install_model(validated: dict, engine: Path) -> dict:
    guard_space(41)
    source = Path(validated["model"])
    folder = engine.resolve() / "roformer-port" / "artifacts-balanced"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MODEL
    temporary = folder / (MODEL + "." + uuid.uuid4().hex + ".tmp")
    try:
        if source != target.resolve():
            with source.open("rb") as incoming, temporary.open("xb") as outgoing:
                while block := incoming.read(4 * 1024 ** 2):
                    guard_space()
                    outgoing.write(block)
            if digest(temporary) != validated["sha256"]:
                raise RuntimeError("Model changed during copying; existing installation was preserved.")
            guard_space()
            os.replace(temporary, target)
        manifest = dict(validated["manifest"])
        manifest["models"] = {"mel_band_roformer_kim": validated["info"]}
        manifest["integration_provenance"] = {
            "production_digest_match": validated["productionDigest"],
            "validation": "known production digest" if validated["productionDigest"] else "explicit pinned-source export with graph parity",
        }
        pending = folder / ("manifest." + uuid.uuid4().hex + ".tmp")
        try:
            pending.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            guard_space()
            os.replace(pending, folder / "manifest.json")
        finally:
            pending.unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)
    return {"ok": True, "model": str(target), "sha256": validated["sha256"],
            "productionDigest": validated["productionDigest"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--engine-dir", type=Path, default=REPO / "plugins/music")
    parser.add_argument("--validate-only", action="store_true", help="Read/hash only, without copying.")
    parser.add_argument("--accept-export", action="store_true", help="Trust a new explicit pinned-source export, not an arbitrary downloaded graph.")
    parser.add_argument("--require-graph", action="store_true", help="Inspect actual ONNX FP32 shapes; requires onnx in the export environment.")
    args = parser.parse_args()
    try:
        validated = validate_model(args.model, args.manifest, exported=args.accept_export,
                                   require_graph=args.require_graph or args.accept_export)
        result = {"ok": True, "validateOnly": True, **{k: validated[k] for k in ("model", "sha256", "bytes", "productionDigest")}} if args.validate_only else install_model(validated, args.engine_dir)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
