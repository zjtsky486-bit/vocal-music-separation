# Models, runtime installation, and reproducibility

The plugin separates music played by a selected Windows application into vocals
and accompaniment. Source selection belongs to the host/service; the neural
model is independent of the player's brand. Automatic source selection can
choose a currently audible application. This does not make the model perfect,
and detection time is separate from buffered playback latency.

## Install a local runtime

Use **CPython 3.12 x64** already installed by the user. The validated original
desktop deployment used Python 3.12.10. NumPy 2.5.3 and SciPy 1.18.1 require
Python 3.12 or later; copying the upstream toolkit's Python 3.11 setup verbatim
would conflict with these runtime pins.

From the repository root, run one backend installation:

```powershell
pwsh -File scripts/install-runtime.ps1 -Backend cuda -PythonPath "C:/path/to/Python312/python.exe"
# Or DirectML for a suitable Windows DX12 GPU:
pwsh -File scripts/install-runtime.ps1 -Backend dml -PythonPath "C:/path/to/Python312/python.exe"
```

Without `-PythonPath`, the script tries the already-installed `py -3.12` launcher.
It never downloads or globally installs Python, never elevates, and never
installs VB-CABLE. Its default venv is `plugins/music/python/`; the interpreter
is `plugins/music/python/Scripts/python.exe`. `-DryRun` validates the installed
interpreter and prints the planned paths without creating or installing files.

The two ORT builds must **not** be installed into the same venv. The installer
refuses a backend change or a conflicting ORT package in an existing environment.
Use a different project-local `-RuntimeDir` for another backend, and configure
the SDK's Python executable accordingly. The CUDA build uses official NVIDIA
runtime wheels; no full CUDA toolkit or production PyTorch install is needed.
The system still needs a compatible NVIDIA display driver. Provider registration
alone does not establish that a particular GPU can run this graph in real time.

Pip cache, temporary files, logs, and the optional export environment stay in
the repository's `.cache/`. Before a writing step, scripts require more than
41 GiB free on C:. They stop ongoing managed installation/export subprocesses
if C: reaches 40 GiB free. The PowerShell installer stops only its own spawned
processes and verified descendants; the Python exporter owns a native Windows
job for its own subprocess tree. No unrelated music application is terminated.

Runtime dependencies are pinned in `requirements/runtime.txt` and the chosen
`requirements/cuda.txt` or `requirements/dml.txt`. Export dependencies belong
to the separate `requirements/export.txt`; they are not needed at inference time.
Use `python -m pip freeze` after installation if a host needs to lock all
transitive package versions as well.

## Import the existing validated graph

Stop the host's separation service before replacing model files. To install the
already validated T241 model, supply the ONNX path with its adjacent manifest:

```powershell
pwsh -File scripts/install-runtime.ps1 -Backend cuda -ModelPath "C:/path/to/mel_band_roformer_kim_T241.onnx"
```

To check the model without copying it or installing anything:

```powershell
plugins/music/python/Scripts/python.exe scripts/import-model.py --model "C:/path/to/mel_band_roformer_kim_T241.onnx" --validate-only
```

`--manifest` can explicitly point to a manifest elsewhere. The ordinary import
route checks the exact production hash, file size, source checkpoint hash,
attribution, license, interface, and graph parity metadata. Models are placed
under `plugins/music/roformer-port/artifacts-balanced/`. The source archive
does not contain these large ONNX/checkpoint files or the runtime venv.

| Field | Validated production value |
| --- | --- |
| File | `mel_band_roformer_kim_T241.onnx` |
| ONNX SHA-256 | `7d3d1291a047e6baa7e35618230ebab2a1961d207ab70598677e159237bdab75` |
| ONNX bytes | `921863688` |
| Input | `spec`, FP32 `[1, 2050, 241, 2]` |
| Output | `mask`, FP32 `[1, 1, 2050, 241, 2]` |
| Audio | 44,100 Hz stereo; 105,840 samples per inference window |
| STFT | `n_fft=2048`, `hop_length=441` |
| Source checkpoint SHA-256 | `87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e` |

The weights author is **KimberleyJensen (KimberleyJSN)**. The author's
[Hugging Face model card](https://huggingface.co/KimberleyJSN/melbandroformer)
declares MIT. The source checkpoint is
[MelBandRoformer.ckpt](https://huggingface.co/KimberleyJSN/melbandroformer/resolve/main/MelBandRoformer.ckpt).
Downloads are verified against the checkpoint hash above; a mutable `main` URL
alone is not treated as identity.

There is **no verified public T241 ONNX download URL** in the upstream release.
The [official `models-v1.0` release](https://github.com/santiquiroz/port-bs-roformer-onnx/releases/tag/models-v1.0)
publishes T801 and its manifest. T801 uses a different fixed graph length and is
not a drop-in replacement for the bounded T241 streaming implementation.

## Build T241 explicitly from source

This is an optional, substantial setup/export step. It downloads the licensed
checkpoint, installs export-only dependencies when explicitly requested, and
uses the CPU PyTorch exporter. It does not execute during normal app startup.

```powershell
# Create a separate local export venv and install the pinned export dependencies:
python scripts/export-model.py --python "C:/path/to/Python312/python.exe" --prepare

# Or use an export environment whose dependencies are already installed:
python scripts/export-model.py --python "C:/path/to/export-venv/Scripts/python.exe"

# Show all source pins and destinations without writing or downloading:
python scripts/export-model.py --python "C:/path/to/Python312/python.exe" --prepare --dry-run
```

The script pins the [ONNX port](https://github.com/santiquiroz/port-bs-roformer-onnx)
at `3f727cde9710cd0237130a96155a70e2c850d553` and
[Music-Source-Separation-Training](https://github.com/ZFTurbo/Music-Source-Separation-Training)
at `e247dfe4abc1f17c69dff719207fe045dc04413a`. It verifies checkout origins and
refuses tracked source modifications. Only those official repositories and the
author's checkpoint URL are used. Export invokes the pinned upstream command:

```text
python toolkit/export_roformer.py mel_band_roformer_kim --chunk 105840
```

The upstream exporter checks checkpoint identity and a **PyTorch vs ORT CPU
graph parity maximum absolute error below `1e-4`**, before writing its manifest.
The wrapper then checks the actual ONNX FP32 input/output shapes, self-contained
weights, source commit, model hash and model metadata, and installs both the new
graph and its matching generated manifest.

An export made with another compatible exporter environment can have different
ONNX bytes and SHA-256 while representing the same validated architecture and
weights. **Do not overwrite the generated `onnx_sha256` with the old production
hash.** The worker validates the actual graph against its accompanying manifest.
`productionDigest` distinguishes an exact original production artifact from a
new pinned-source export. `integration_provenance` preserves that distinction.
The importer's `--accept-export` flag is for a trusted explicit source export,
requires the optional `onnx` package to inspect actual graph shapes, and is not
an instruction to accept arbitrary internet models or falsified manifests.

Keep the port author's MIT copyright/license with vendored `driver/` code and
keep the weights attribution and model card link. This repository includes the
relevant notices; a format conversion does not remove the original authorship.

## Read-only diagnosis

```powershell
plugins/music/python/Scripts/python.exe scripts/diagnose.py --engine-dir "C:/path/to/core/plugins/music" --backend cuda
```

The script emits one JSON object containing `ok`, `checks`, and `missing`, with
warnings on stderr. It checks Windows build 20348+, Python 3.12 x64, C: space,
installed runtime dependencies, the requested ORT provider and GPU, model
manifest/hash, and the existing VB-CABLE input device. It respects
`WORKBENCH_MUSIC_BACKEND` when `--backend` is omitted and supports the legacy
separate `cuda-runtime/` layout. It neither records nor changes routing, and
creates **no ONNX inference session**. Its ordinary model interface check is
the manifest contract plus the graph's hash; `--inspect-graph` additionally reads
actual ONNX shapes using optional `onnx`, which can require significant memory.
Running a successful real-time inference and synthetic audio lifecycle test is
still necessary before claiming that a new device is suitable for live playback.

[VB-CABLE](https://vb-audio.com/Cable/) is a separate Windows driver. Install it
from its official site using the user's own system administration flow. Its
[licensing rules](https://vb-audio.com/Services/licensing.htm) differ from MIT:
the standard cable is donationware; A+B and C+D cannot be bundled with another
product. No VB-CABLE installer, personal download link, or driver binary is
included here.

Sources: [Python venv](https://docs.python.org/3.12/library/venv.html),
[ORT CUDA runtime/DLL loading](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html),
[ORT DirectML requirements](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html),
[Microsoft application loopback API requirements](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/).
