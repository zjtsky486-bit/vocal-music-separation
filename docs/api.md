# Core API

`require('vocal-music-core')` exports `createAudioService(options)` and `AudioService`. The SDK uses only Node built-ins; it launches a local Python worker. It is intended for a trusted Windows Node/Electron main process.

`options.runtimeDir` is required and absolute. Status, gain preferences, bounded logs and route journals are stored there. `engineDir` defaults to this package's `plugins/music`; `pythonPath` defaults to its installed Python/venv. `backend` is `cuda` (default) or `dml`. `minFreeGiB` defaults to `40` and can be raised.

| Method | Result / behavior |
| --- | --- |
| `start()` | Starts once and returns current state. Loading continues asynchronously. Check `ready`, not merely the method return. |
| `on('state', listener)` | Receives status, independent gains, actual vocal/music spectra, source list and telemetry. |
| `getState()` | Returns a detached snapshot; editing it cannot modify the service. |
| `getSources()` | Returns currently detected active application audio sources. |
| `selectSource({pid:null})` | Automatic source selection. |
| `selectSource({pid,createdAt})` | Select the exact live source identity returned by `getSources()`; a reused PID must not select a different application. |
| `setGains({vocalGain,instrumentalGain})` | Two finite values between `0` and `1`. Saves preferences and sends both controls together. |
| `retry()` | Retries a failed/busy connection or starts a stopped worker. |
| `await stop()` | Stops capture/playback, restores application output preferences and waits for worker cleanup. Repeated concurrent calls share the same promise. |
| `await diagnose()` | Read-only prerequisite report. Does not capture, record, change routing or run a separation session. |

Common `phase` values are `loading`, `idle`, `connecting`, `playing`, `paused`, `monitoring`, `busy`, `error`, `storage` and `stopped`. Display `state.error` and source-switch `state.selectionError` when present; selecting a stale identity is a failed request, not a successful switch. `ready` enables live gain controls; `active` reflects input activity. Sources and frequency bands come from actual audio activity, not animation-generated random values.

The same source model supports vocals and accompaniment, not voice cloning or speech transcription. Automatic source detection chooses an application with audio; it is not a semantic classifier distinguishing songs from notifications, movie dialogue or game sound effects. If several applications play, provide a source selector using the listed identities. Never capture the separator's own output process tree.

Both gains `1` retain normal mixed audio detail; `{1,0}` selects cleaned vocals, `{0,1}` cleaned music and `{0,0}` mutes. Solo endpoints retain their own cleanup. Do not write `vocalGain = 1 - instrumentalGain`; this API represents independent volume controls.

Always await stop before app exit. The worker also tracks parent death and owns a named exclusive routing lease. An unexpected process termination can require recovery from the retained journal; do not delete journals to hide an unfinished recovery. A second host reports busy and must not kill or steal the other host's route.
