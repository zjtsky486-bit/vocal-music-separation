"""One owner for the machine's music routing, shared by both desktop apps."""
import ctypes
from ctypes import wintypes

class EngineLease:
    def __init__(self):
        self.handle=None
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateMutexW.argtypes=[ctypes.c_void_p,wintypes.BOOL,wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype=wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
        self.kernel.ReleaseMutex.argtypes=[wintypes.HANDLE]
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    def acquire(self):
        if self.handle:return True
        handle=self.kernel.CreateMutexW(None,False,'Local\\PersonalWorkbenchMusicRouting-v1')
        if not handle:raise ctypes.WinError(ctypes.get_last_error())
        result=self.kernel.WaitForSingleObject(handle,0)
        if result in (0,0x80):self.handle=handle;return True
        self.kernel.CloseHandle(handle)
        if result==0x102:return False
        raise ctypes.WinError(ctypes.get_last_error())
    def close(self):
        if self.handle:
            self.kernel.ReleaseMutex(self.handle);self.kernel.CloseHandle(self.handle);self.handle=None
