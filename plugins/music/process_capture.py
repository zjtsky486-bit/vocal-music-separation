"""Memory-only Windows process loopback. Based on Microsoft's ApplicationLoopback API."""
import sys
sys.coinit_flags = 0
import ctypes as C
import threading
import time
from comtypes import GUID, IUnknown, COMObject, COMMETHOD, HRESULT
import numpy as np

def call(pointer, index, result, types, *args):
    table = C.cast(pointer, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *types)(table[index])(pointer, *args)

def checked(value):
    if value < 0:
        raise OSError(f"Windows audio error 0x{value & 0xffffffff:08x}")

class Agile(IUnknown):
    _iid_ = GUID('{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}')
    _methods_ = []

class Completion(IUnknown):
    _iid_ = GUID('{41D949AB-9862-444A-80F6-C261334DA5EB}')
    _methods_ = [COMMETHOD([], HRESULT, 'ActivateCompleted', (['in'], C.c_void_p, 'operation'))]

class Callback(COMObject):
    _com_interfaces_ = [Completion, Agile]
    def __init__(self):
        super().__init__()
        self.event = threading.Event()
        self.client = C.c_void_p()
        self.error = 0
    def ActivateCompleted(self, operation):
        code = C.c_long()
        self.error = call(operation, 3, C.c_long, [C.POINTER(C.c_long), C.POINTER(C.c_void_p)], C.byref(code), C.byref(self.client))
        if not self.error:
            self.error = code.value
        self.event.set()
        return 0

class Activation(C.Structure):
    _fields_ = [('kind', C.c_uint32), ('pid', C.c_uint32), ('mode', C.c_uint32)]
class Variant(C.Structure):
    _fields_ = [('vt', C.c_uint16), ('reserved', C.c_uint16 * 3), ('size', C.c_uint32), ('padding', C.c_uint32), ('data', C.c_void_p)]
class WaveFormat(C.Structure):
    _pack_ = 2
    _fields_ = [('tag', C.c_uint16), ('channels', C.c_uint16), ('rate', C.c_uint32), ('bytes', C.c_uint32), ('align', C.c_uint16), ('bits', C.c_uint16), ('extra', C.c_uint16)]

class ProcessCapture:
    def __init__(self, pid, rate=44100):
        self.client = self.capture = None
        self.callback = Callback()
        activation = Activation(1, pid, 0)
        variant = Variant(65, (C.c_uint16 * 3)(), C.sizeof(activation), 0, C.cast(C.byref(activation), C.c_void_p))
        operation = C.c_void_p()
        iid = GUID('{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}')
        api = C.WinDLL('Mmdevapi.dll').ActivateAudioInterfaceAsync
        api.restype = C.c_long
        api.argtypes = [C.c_wchar_p, C.POINTER(GUID), C.POINTER(Variant), C.c_void_p, C.POINTER(C.c_void_p)]
        checked(api('VAD\\Process_Loopback', C.byref(iid), C.byref(variant), C.cast(self.callback.QueryInterface(Completion), C.c_void_p), C.byref(operation)))
        try:
            if not self.callback.event.wait(8):
                raise TimeoutError('Process audio activation timed out')
            checked(self.callback.error)
            self.client = self.callback.client
            fmt = WaveFormat(3, 2, rate, rate * 8, 8, 32, 0)
            checked(call(self.client, 3, C.c_long, [C.c_int, C.c_uint32, C.c_int64, C.c_int64, C.POINTER(WaveFormat), C.c_void_p], 0, 0x20000, 0, 0, C.byref(fmt), None))
            capture = C.c_void_p()
            capture_iid = GUID('{C8ADBD64-E71E-48A0-A4DE-185C395CD317}')
            checked(call(self.client, 14, C.c_long, [C.POINTER(GUID), C.POINTER(C.c_void_p)], C.byref(capture_iid), C.byref(capture)))
            self.capture = capture
            checked(call(self.client, 10, C.c_long, []))
        except Exception:
            self.close()
            raise
        finally:
            if operation:
                call(operation, 2, C.c_ulong, [])
    def read(self):
        size = C.c_uint32()
        checked(call(self.capture, 5, C.c_long, [C.POINTER(C.c_uint32)], C.byref(size)))
        if not size.value:
            return None
        pointer = C.c_void_p()
        count, flags = C.c_uint32(), C.c_uint32()
        checked(call(self.capture, 3, C.c_long, [C.POINTER(C.c_void_p), C.POINTER(C.c_uint32), C.POINTER(C.c_uint32), C.c_void_p, C.c_void_p], C.byref(pointer), C.byref(count), C.byref(flags), None, None))
        try:
            if flags.value & 2:
                return np.zeros((count.value, 2), np.float32)
            return np.frombuffer(C.string_at(pointer, count.value * 8), dtype=np.float32).reshape(-1, 2).copy()
        finally:
            checked(call(self.capture, 4, C.c_long, [C.c_uint32], count.value))
    def close(self):
        if self.client:
            call(self.client, 11, C.c_long, [])
        if self.capture:
            call(self.capture, 2, C.c_ulong, [])
            self.capture = None
        if self.client:
            call(self.client, 2, C.c_ulong, [])
            self.client = None

if __name__ == '__main__':
    capture = ProcessCapture(int(sys.argv[1]))
    until = time.monotonic() + 2
    frames = peak = 0
    try:
        while time.monotonic() < until:
            block = capture.read()
            if block is not None:
                frames += len(block)
                peak = max(peak, float(np.max(np.abs(block))))
            else:
                time.sleep(.003)
    finally:
        capture.close()
    print({'frames': frames, 'peak': peak})
