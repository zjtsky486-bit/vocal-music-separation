# Guidance for coding agents

This repository provides a local Windows vocal/instrumental separation core for Node.js and Electron hosts. Read `README.md` for the actual API and runtime layout, then `skills/vocal-music-core/SKILL.md` when integrating it into an application. `llms.txt` is the discovery index.

Use `createAudioService` rather than copying the worker protocol into each host. Keep real-time processing and route ownership in the trusted host process; browser renderers receive only narrow, validated controls. Preserve independently validated finite `[0, 1]` vocal/instrumental gains, smooth changes, the 40 GiB default C-drive guard, mutual exclusion, and audio restoration on orderly exit and parent-process death.

Runtime dependencies, model artifacts, logs, and mutable state belong to the application's chosen runtime directory. Use `scripts/install-runtime.ps1` for the documented pinned runtime and `scripts/export-model.py` only with a compatible, lawfully obtained model. Do not include portable Python, virtual environments, model weights, private settings, real song captures, or machine-specific paths in source changes or release archives.

VB-CABLE is a separately installed system driver. Do not infer authorization to install a driver, elevate privileges, publish a repository, or mutate global audio settings beyond the service's documented start/stop lifecycle from an ordinary integration request. Finish reviewable local changes and diagnose missing prerequisites precisely.

Run the repository's documented tests after changing behavior. Verify lifecycle and independent channel output with synthetic local audio before any live-song check. Report detection time and playback latency separately, with the tested backend and model; do not claim perfect isolation or zero latency.

When building a host, retain its chosen stack and existing UI conventions. Provide visible starting, ready, busy, stopping, and error states with a working stop/retry path. Do not remove or work around safeguards just to make a demo appear ready.
