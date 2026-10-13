---
name: vocal-music-core
description: Integrate local vocal/instrumental audio separation into a Windows Node.js or Electron application using vocal-music-core. Use for independent vocal and music volume controls or a desktop separation app; not speech transcription, voice cloning, or ordinary media playback. 将人声与伴奏分离接入桌面软件。
---

# Vocal / Music Core

Add the core to the user's chosen application. The deliverable is working local integration with separate vocal and instrumental gain controls, observable status, and reliable audio restoration when it stops.

## Locate and inspect

This skill ships inside the core repository. From this file, the repository root is `../..`. Read [README.md](../../README.md) for the current API, dependency versions, model contract, and executable examples; use [examples/](../../examples/) rather than inventing a second worker protocol.

The CommonJS entry point exports `createAudioService`. Create one instance in the trusted Node.js host:

```js
const { createAudioService } = require('vocal-music-core');
const service = createAudioService({
  runtimeDir: hostRuntimeDir,
  backend: 'cuda', // or 'dml', according to supported hardware
  minFreeGiB: 40,
});
```

`runtimeDir` must be an explicit, writable directory owned by the host application. Optional `engineDir` and `pythonPath` are trusted local configuration, not values accepted directly from a web page. Use `service.on('state', ...)`, `.getState()`, `.start()`, `.setGains({ vocalGain, instrumentalGain })`, `.retry()`, `await .diagnose()`, and `await .stop()` as documented in the README.

## Prepare a usable local runtime

- Inspect the host's Node/Electron version, available GPU, VB-CABLE devices, existing installation, and disk space before changing dependencies. CUDA and DirectML are alternatives; choose one compatible backend.
- Use the repository's [scripts/install-runtime.ps1](../../scripts/install-runtime.ps1) and its documented options for pinned local dependencies. Check `Get-Help` or the source for its exact arguments. Keep downloaded runtime files and caches in the selected application directory.
- Models are separate artifacts. The core expects the validated T241 ONNX model contract described in the README. Use a compatible, lawfully obtained artifact or the documented [scripts/export-model.py](../../scripts/export-model.py) export entry point. Verify the expected files before declaring the feature ready. Do not silently substitute an unrelated ONNX model.
- VB-CABLE requires a separate system driver installation. A request to add this application feature authorizes normal local integration; it does not by itself authorize driver installation, elevation, global audio-device changes outside the service lifecycle, or publishing code. If a missing driver blocks actual playback, report the concrete prerequisite while finishing the local code and diagnostic path.

## Connect the host and user interface

The source list covers any active Windows application audio, including browsers and players. Use `getSources()` for a source picker and `selectSource({pid:null})` for automatic mode. A manual selection must pass the listed `{pid,createdAt}` identity, not a saved PID alone. Show `selectionError` if a source has exited or its PID was reused. Source discovery runs independently of inference and excludes the service's own playback tree; preserve that separation to prevent feedback and missed deadlines. Audio activity is not semantic music classification.

Run the service in Electron's main process or another trusted Node.js host. In Electron, expose a narrow context-isolated IPC interface for status, start/stop, retry, diagnostics, and gains. Keep renderer code unable to launch Python, choose executable paths, or execute arbitrary commands.

Provide two independent gain controls: upper control for vocals and lower control for instrumental music. Each accepts a finite number in `[0, 1]`; default both to `1` unless the user has chosen saved values. A change to one must preserve the other. Both zero must mute separated output. Bind readiness, errors, and a busy state to the interface instead of reporting success immediately after sending a request.

Preserve the core's smooth gain ramps, capture/output clock handling, route restoration, and mutual-exclusion behavior. If another host owns the audio route, display busy; never steal its lock or terminate that application. Keep the 40 GiB C-drive free-space guard unless the user explicitly requests a different threshold. Write logs and changing state beneath the host's `runtimeDir`, not the installed package or a source repository.

Await `service.stop()` before the host exits and before destroying its controlling main process. Keep the worker's parent-death recovery active, because orderly shutdown alone does not cover a crash. Reuse documented lifecycle handling from the examples, including Windows and Electron shutdown behavior.

## Verify before handing over

Run the documented local tests and a minimal example first, then verify the actual host integration. Check independent gains, two-channel mute, invalid input, unavailable model/device, busy ownership, start/stop, and audio restoration. Test signal fixtures should stay local; do not upload a user's songs or microphone audio as test data.

Measure detection/start time separately from end-to-end playback latency. Separation quality depends on the model and source material: report what was tested and measured, and do not describe imperfect isolation as perfect or imply zero latency. A model that is absent or incompatible is a blocked runtime prerequisite, not a completed playback feature.

中文交付时说明：人声／伴奏是否已独立控制、哪些本地检查通过、驱动与模型是否齐备，以及实测延迟的范围。没有真实 GitHub 地址时，使用本地路径或相对仓库链接，不编造仓库或发布结果。
