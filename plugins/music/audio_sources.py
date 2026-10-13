"""Read-only Windows session discovery and stable automatic source selection.

Only ``scan`` calls COM. ``select`` validates the last snapshot, making it safe
to accept controls from another thread without touching Windows audio there.
Injected scans and a clock allow the selector to run without Windows or audio.
"""
import math
import os
import threading
import time


def windows_processes():
    import psutil
    return list(psutil.process_iter(['pid', 'ppid', 'name', 'exe', 'create_time']))


def windows_sessions():
    """Read each active render endpoint, including a routed virtual cable."""
    import ctypes
    from pycaw.pycaw import AudioUtilities
    from pycaw.api.audiopolicy import IAudioSessionControl2
    from pycaw.api.endpointvolume import IAudioMeterInformation

    ole = ctypes.WinDLL('ole32')
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 0)
    # An already initialized STA is also usable for a synchronous session read.
    if initialized not in (0, 1, -2147417850):
        raise OSError(f'COM initialization failed: {initialized}')
    result = []
    try:
        for device in AudioUtilities.GetAllDevices():
            if not device.id.startswith('{0.0.0.') or device.state.value != 1:
                continue
            try:
                sessions = device.AudioSessionManager.GetSessionEnumerator()
                for index in range(sessions.GetCount()):
                    try:
                        session = sessions.GetSession(index)
                        control = session.QueryInterface(IAudioSessionControl2)
                        pid, state = control.GetProcessId(), control.GetState()
                        if pid <= 0 or state != 1:
                            continue
                        meter = session.QueryInterface(IAudioMeterInformation)
                        result.append({'pid': pid, 'peak': meter.GetPeakValue(), 'active': True})
                    except Exception:
                        # Sessions may disappear while enumerating an endpoint.
                        continue
            except Exception:
                continue
        return result
    finally:
        if initialized in (0, 1):
            ole.CoUninitialize()


def _identity(source):
    return source['pid'], source['createdAt']


def _capture_root(process, processes):
    """Only identical executables may be merged into one capture tree."""
    root, seen = process, {process['pid']}
    executable = os.path.normcase(process['exe'])
    while executable:
        parent = processes.get(root['ppid'])
        if not parent or parent['pid'] in seen or os.path.normcase(parent['exe']) != executable:
            break
        seen.add(parent['pid'])
        root = parent
    return root


class SourceCatalog:
    def __init__(self, parent_pid=None, self_pid=None, *, process_scan=None,
                 session_scan=None, clock=None, peak_threshold=0.0005,
                 silence_hold=2.0, candidate_hold=0.3):
        self._seeds = {int(pid) for pid in (parent_pid, self_pid or os.getpid()) if pid}
        self._process_scan = process_scan or windows_processes
        self._session_scan = session_scan or windows_sessions
        self._clock = clock or time.monotonic
        self.peak_threshold = peak_threshold
        self.silence_hold = silence_hold
        self.candidate_hold = candidate_hold
        self._lock = threading.RLock()
        self._processes, self._objects = {}, {}
        self._sources, self._active, self._audible_since = {}, set(), {}
        self._current = None
        self._silent_since = None
        self._selection = {'pid': None, 'createdAt': None}

    @property
    def current(self):
        with self._lock:
            return dict(self._current) if self._current else None

    @property
    def selection(self):
        with self._lock:
            return dict(self._selection)

    def scan(self):
        """Return sounding session PIDs; keep silent live processes privately."""
        processes, objects = {}, {}
        for process in self._process_scan():
            info = process if isinstance(process, dict) else process.info
            try:
                pid, created = int(info['pid']), float(info['create_time'])
                if pid <= 0 or not math.isfinite(created):
                    continue
                processes[pid] = {
                    'pid': pid, 'createdAt': created, 'ppid': int(info.get('ppid') or 0),
                    'name': info.get('name') or str(pid), 'exe': info.get('exe') or '',
                }
                objects[pid] = process
            except (KeyError, TypeError, ValueError):
                continue

        # Exclude UI/service descendants, and the ancestors themselves. Do not
        # exclude siblings of Explorer merely because it is our UI's ancestor.
        excluded = set(self._seeds)
        for seed in self._seeds:
            ancestor = processes.get(seed)
            seen = {seed}
            while ancestor and ancestor['ppid'] not in seen:
                parent_pid = ancestor['ppid']
                seen.add(parent_pid)
                excluded.add(parent_pid)
                ancestor = processes.get(parent_pid)
        descendants = set(self._seeds)
        while True:
            children = {pid for pid, p in processes.items() if p['ppid'] in descendants}
            updated = descendants | children
            if updated == descendants:
                break
            descendants = updated
        excluded.update(descendants)
        peaks, active = {}, set()
        for session in self._session_scan():
            try:
                pid, peak = int(session['pid']), float(session['peak'])
                if (pid in excluded or pid not in processes or not session.get('active')
                        or not math.isfinite(peak)):
                    continue
                active.add(pid)
                peaks[pid] = max(peaks.get(pid, 0.0), max(0.0, min(1.0, peak)))
            except (KeyError, TypeError, ValueError):
                continue
        sources = {}
        for pid, peak in peaks.items():
            if peak <= self.peak_threshold:
                continue
            process = processes[pid]
            capture = _capture_root(process, processes)
            source = {key: process[key] for key in ('pid', 'createdAt', 'name', 'exe')}
            source.update(peak=peak, active=True, capturePid=capture['pid'],
                          captureCreatedAt=capture['createdAt'])
            sources[_identity(source)] = source
        now = self._clock()
        with self._lock:
            self._processes, self._objects = processes, objects
            self._sources, self._active = sources, active
            self._audible_since = {key: self._audible_since.get(key, now) for key in sources}
            return [dict(source) for source in sorted(sources.values(), key=lambda s: (-s['peak'], s['pid']))]

    def select(self, pid, created_at=None):
        """Select only a PID/creation identity present in the sounding snapshot."""
        with self._lock:
            if pid is None:
                self._selection = {'pid': None, 'createdAt': None}
                return dict(self._selection)
            if type(pid) is not int or pid <= 0:
                raise ValueError('Invalid audio source PID')
            matches = [source for source in self._sources.values() if source['pid'] == pid]
            if created_at is not None:
                if isinstance(created_at, bool) or not isinstance(created_at, (int, float)) or not math.isfinite(created_at):
                    raise ValueError('Invalid audio source creation time')
                matches = [source for source in matches if source['createdAt'] == created_at]
            if len(matches) != 1:
                raise ValueError('Audio source is no longer in the current list')
            source = matches[0]
            self._selection = {'pid': pid, 'createdAt': source['createdAt']}
            self._current = dict(source)
            self._silent_since = None
            return dict(self._selection)

    def choose(self):
        """Prefer a live current source over transient louder sessions."""
        now = self._clock()
        with self._lock:
            current = self._current
            process = self._processes.get(current['pid']) if current else None
            if not process or process['createdAt'] != current['createdAt']:
                current = self._current = None
                self._silent_since = None
                # An exited manual source cannot pin a recycled PID.
                self._selection = {'pid': None, 'createdAt': None}
            if current:
                sounding = self._sources.get(_identity(current))
                if sounding:
                    self._current = dict(sounding)
                    self._silent_since = None
                    return dict(self._current)
                self._current = {**current, 'peak': 0.0, 'active': current['pid'] in self._active}
                if self._selection['pid'] is not None:
                    return dict(self._current)
                if self._silent_since is None:
                    self._silent_since = now
                if now - self._silent_since < self.silence_hold:
                    return dict(self._current)
            candidates = list(self._sources.values())
            if current:
                candidates = [source for source in candidates
                              if now - self._audible_since[_identity(source)] >= self.candidate_hold]
            if candidates:
                self._current = dict(max(candidates, key=lambda source: (source['peak'], -source['pid'])))
                self._silent_since = None
            return dict(self._current) if self._current else None

    def members(self, source):
        """Return active same-executable session processes in this capture tree."""
        if not source:
            return []
        with self._lock:
            selected = self._processes.get(source['pid'])
            if not selected or selected['createdAt'] != source['createdAt']:
                return []
            root = _capture_root(selected, self._processes)
            result = []
            for pid in self._active | {source['pid']}:
                process = self._processes.get(pid)
                if not process:
                    continue
                capture = _capture_root(process, self._processes)
                if _identity(capture) == _identity(root):
                    result.append(self._objects[pid])
            return result


class SourceMonitor:
    """Keep slow endpoint enumeration off the playback/control thread."""
    def __init__(self, catalog, interval=0.25):
        self.catalog = catalog
        self.interval = max(0.01, float(interval))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._rows = []
        self._last_ms = self._max_ms = 0.0
        self._error = None

    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name='AudioSourceMonitor', daemon=True)
            self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                rows = self.catalog.scan()
                error = None
            except Exception as exception:
                rows = []
                error = f'{type(exception).__name__}: {exception}'[:500]
            elapsed = time.perf_counter() - started
            with self._lock:
                self._rows, self._error = rows, error
                self._last_ms = elapsed * 1000
                self._max_ms = max(self._max_ms, self._last_ms)
            self._stop.wait(max(0.01, self.interval - elapsed))

    def snapshot(self):
        with self._lock:
            return {'sources': [dict(source) for source in self._rows],
                    'lastScanMs': round(self._last_ms, 3),
                    'maxScanMs': round(self._max_ms, 3),
                    'sourceScanError': self._error}

    def stop(self, timeout=5.0):
        self._stop.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        return not thread or not thread.is_alive()
