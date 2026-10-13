"""Offline catalog checks: no COM, capture, routing, GPU, or real audio."""
import json
import threading
import time

import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"plugins/music"))
from audio_sources import SourceCatalog, SourceMonitor


class Process:
    def __init__(self, pid, name, ppid=1, created=100.0):
        self.pid = pid
        self.info = {'pid': pid, 'ppid': ppid, 'name': name,
                     'exe': 'C:/Applications/' + name, 'create_time': created}


class Fixture:
    def __init__(self):
        self.now, self.scans = 0.0, 0
        self.processes = [Process(1, 'explorer.exe', 0),
                          Process(20, 'electron.exe'), Process(21, 'python.exe', 20),
                          Process(22, 'electron.exe', 20), Process(23, 'helper.exe', 21),
                          Process(30, 'spotify.exe'), Process(40, 'firefox.exe')]
        self.sessions = []
        self.catalog = SourceCatalog(parent_pid=20, self_pid=21,
                                     process_scan=lambda: self.processes,
                                     session_scan=self.scan_sessions, clock=lambda: self.now)

    def scan_sessions(self):
        self.scans += 1
        return self.sessions

    def sound(self, *pairs):
        self.sessions = [{'pid': pid, 'peak': peak, 'active': True} for pid, peak in pairs]

    def tick(self, seconds=0):
        self.now += seconds
        sources = self.catalog.scan()
        return sources, self.catalog.choose()


def run_tests():
    checks = []
    fixture = Fixture()
    fixture.sound((1, .9), (20, .9), (21, .9), (22, .9), (23, .9), (30, .12), (40, .02))
    fixture.sessions += [{'pid': 0, 'peak': 1, 'active': True},
                         {'pid': 999, 'peak': 1, 'active': True},
                         {'pid': 40, 'peak': float('nan'), 'active': True},
                         {'pid': 40, 'peak': .8, 'active': False},
                         {'pid': 30, 'peak': .3, 'active': True}]
    sources, current = fixture.tick()
    assert {source['pid'] for source in sources} == {30, 40}
    assert current['pid'] == 30 and current['peak'] == .3
    assert all(set(('pid', 'createdAt', 'name', 'exe', 'peak', 'active',
                    'capturePid', 'captureCreatedAt')) <= source.keys() for source in sources)
    assert fixture.now == 0  # Initial recognition has no confirmation delay.
    checks.append('all sounding applications; excluded own tree and ancestors; immediate first recognition')

    fixture.sound((30, .03), (40, .95))
    _, current = fixture.tick(4)
    assert current['pid'] == 30  # A loud notification never steals a sounding source.
    fixture.sound((30, .00001), (40, .6))
    _, current = fixture.tick()
    assert current['pid'] == 30 and current['peak'] == 0
    _, current = fixture.tick(1.99)
    assert current['pid'] == 30
    _, current = fixture.tick(.02)
    assert current['pid'] == 40
    fixture.sound((30, .1))
    fixture.tick()
    fixture.tick(2.1)
    # Reset the candidate's continuous sound, then ensure a new short blip cannot win.
    fixture.sound()
    fixture.tick()
    fixture.tick(2.1)
    fixture.sound((40, .2))
    _, current = fixture.tick()
    assert current['pid'] == 30
    _, current = fixture.tick(.29)
    assert current['pid'] == 30
    _, current = fixture.tick(.02)
    assert current['pid'] == 40
    checks.append('2-second silence hold and 0.3-second continuous candidate hysteresis')

    fixture = Fixture()
    fixture.sound((30, .3), (40, .1))
    fixture.tick()
    fixture.sound((40, .1))
    fixture.processes = [process for process in fixture.processes if process.pid != 30]
    _, current = fixture.tick()
    assert current['pid'] == 40  # Exit bypasses both confirmation delays.
    fixture.processes += [Process(40, 'unrelated.exe', created=900)]
    fixture.sound((40, .1))
    _, current = fixture.tick()
    assert current['createdAt'] == 900 and current['name'] == 'unrelated.exe'
    try:
        fixture.catalog.select(40, 100)
        assert False, 'PID reuse accepted an obsolete creation time'
    except ValueError:
        pass
    checks.append('immediate exited-source switch and PID/creation identity protection')

    fixture = Fixture()
    fixture.sound((30, .1), (40, .2))
    fixture.tick()
    scans = fixture.scans
    errors = []

    def choose_from_control_thread():
        try:
            assert fixture.catalog.select(30, 100) == {'pid': 30, 'createdAt': 100}
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=choose_from_control_thread)
    thread.start()
    thread.join()
    assert not errors and fixture.scans == scans
    fixture.sound((40, .5))
    _, current = fixture.tick(20)
    assert current['pid'] == 30 and fixture.catalog.selection['pid'] == 30
    fixture.catalog.select(None)
    fixture.tick()
    _, current = fixture.tick(2.01)
    assert current['pid'] == 40
    for pid, created in ((True, 100), ('30', 100), (0, 100), (999, 100),
                         (40, float('nan')), (40, True), (40, '100')):
        try:
            fixture.catalog.select(pid, created)
            assert False, (pid, created)
        except ValueError:
            pass
    selection = fixture.catalog.selection
    selection['pid'] = 999
    assert fixture.catalog.selection['pid'] is None
    checks.append('manual snapshot validation and control-thread selection without COM; auto release')

    fixture = Fixture()
    fixture.processes += [Process(50, 'chrome.exe'), Process(51, 'chrome.exe', 50),
                          Process(52, 'chrome.exe', 50), Process(53, 'chrome.exe', 51),
                          Process(54, 'chrome.exe'), Process(55, 'helper.exe', 50)]
    fixture.sound((51, .3), (52, .02), (53, .0), (54, .02), (55, .02))
    sources, current = fixture.tick()
    assert current['pid'] == 51 and current['capturePid'] == 50
    assert current['captureCreatedAt'] == 100
    assert {process.pid for process in fixture.catalog.members(current)} == {51, 52, 53}
    assert next(source for source in sources if source['pid'] == 54)['capturePid'] == 54
    assert next(source for source in sources if source['pid'] == 55)['capturePid'] == 55
    fixture.processes = [process for process in fixture.processes if process.pid != 51]
    assert fixture.catalog.members(current)  # The cache changes only at the next scan.
    fixture.tick()
    assert fixture.catalog.members(current) == []
    checks.append('safe same-executable capture root and exact active browser members, no unrelated tree')

    fixture = Fixture()
    fixture.sound((30, .2))
    sources, current = fixture.tick()
    sources[0]['name'] = 'tampered'
    current['createdAt'] = -1
    assert fixture.catalog.current['name'] == 'spotify.exe'
    assert fixture.catalog.current['createdAt'] == 100
    fixture.sound()
    _, current = fixture.tick(20)
    assert current['pid'] == 30 and current['peak'] == 0 and not current['active']
    fixture.processes = []
    sources, current = fixture.tick()
    assert sources == [] and current is None
    checks.append('copied snapshots; quiet retained source is marked silent; no capture after all processes exit')

    fixture = Fixture()
    fixture.sound((30, .2))
    entered, release = threading.Event(), threading.Event()
    fail, recovered = threading.Event(), threading.Event()
    scanning_threads, calls = set(), []

    def slow_sessions():
        scanning_threads.add(threading.get_ident())
        calls.append(len(calls) + 1)
        if len(calls) == 2:
            entered.set()
            assert release.wait(2), 'The test did not release the slow scanner'
        if len(calls) == 3:
            raise RuntimeError('temporary session failure')
        if len(calls) == 4:
            fail.set()
            assert recovered.wait(2), 'The test did not release the recovery scan'
        return fixture.sessions

    fixture.catalog._session_scan = slow_sessions
    monitor = SourceMonitor(fixture.catalog, interval=.01).start()
    try:
        assert entered.wait(2)
        # Reading a snapshot and changing selection still work during a blocked
        # COM scan. The scanner never holds the monitor/catalog locks over IO.
        snapshot = monitor.snapshot()
        assert snapshot['sources'][0]['pid'] == 30
        assert snapshot['lastScanMs'] >= 0 and snapshot['sourceScanError'] is None
        snapshot['sources'][0]['name'] = 'tampered'
        assert monitor.snapshot()['sources'][0]['name'] == 'spotify.exe'
        assert fixture.catalog.choose()['pid'] == 30
        fixture.catalog.select(30, 100)
        assert len(calls) == 2
        assert threading.get_ident() not in scanning_threads
        release.set()
        assert fail.wait(2)
        snapshot = monitor.snapshot()
        assert snapshot['sources'] == []
        assert snapshot['sourceScanError'] == 'RuntimeError: temporary session failure'
        assert snapshot['maxScanMs'] >= snapshot['lastScanMs']
        recovered.set()
        deadline = time.monotonic() + 2
        while monitor.snapshot()['sourceScanError'] and time.monotonic() < deadline:
            time.sleep(.005)
        assert monitor.snapshot()['sources'][0]['pid'] == 30
        assert monitor.snapshot()['sourceScanError'] is None
    finally:
        release.set()
        recovered.set()
        assert monitor.stop(2), 'Scanner did not join'
    count = len(calls)
    time.sleep(.025)
    assert len(calls) == count
    checks.append('background scan isolation, non-blocking copied cache, timings, error recovery and joined stop')
    return checks


if __name__ == '__main__':
    print(json.dumps({'passed': True, 'checks': run_tests()}, ensure_ascii=False))
