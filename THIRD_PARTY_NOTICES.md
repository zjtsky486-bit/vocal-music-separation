# Third-party components

This source repository distributes code and a model contract, not model weights, Python, NVIDIA libraries, Electron or audio-driver installers.

| Component | Source | Distribution / license |
| --- | --- | --- |
| RoFormer NumPy driver and adapted export integration | [Santiago Quiroz Upegui / port-bs-roformer-onnx](https://github.com/santiquiroz/port-bs-roformer-onnx) | MIT; copyright and full license retained in `plugins/music/licenses/RoFormer-port-MIT.txt`. |
| Windows per-app audio policy adaptation | [EarTrumpet](https://github.com/File-New-Project/EarTrumpet) | MIT; retained notice in `plugins/music/licenses/EarTrumpet.txt`. |
| Process loopback capture API adaptation | [Microsoft application loopback sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/) | MIT sample notice retained in `plugins/music/licenses/ProcessAudioCapture.txt`; uses Windows APIs, no copied capture DLL required. |
| Kim Mel-Band RoFormer checkpoint | [KimberleyJSN / melbandroformer](https://huggingface.co/KimberleyJSN/melbandroformer) | Model card declares MIT. Not bundled; source author, checkpoint identity and hash are retained in the model manifest. |
| VB-CABLE | [VB-Audio](https://vb-audio.com/Cable/) | Separate system driver, governed by its own donationware/license terms. Not part of the repository's MIT grant and not bundled or silently installed. |

NumPy, SciPy, psutil, pycaw, comtypes, sounddevice, ONNX Runtime and optional CUDA dependencies are installed from their original package distributions. Their licenses remain in those distributions. GPU driver compatibility is the host system's prerequisite. The export environment installs its own upstream PyTorch/toolkit dependencies and is separate from production.

The repository's MIT license covers its original glue, SDK, source selection, lifecycle handling, tests, documentation and AI skill. Preserve every third-party notice when incorporating the vendored files into a generated application.
