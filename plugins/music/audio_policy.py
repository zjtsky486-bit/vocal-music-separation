"""Per-app Windows routing, adapted from File-New-Project/EarTrumpet (MIT)."""
import ctypes as C
from comtypes import GUID, CoCreateInstance, CLSCTX_ALL
from pycaw.api.policyconfig import IPolicyConfig
from pycaw.constants import CLSID_CPolicyConfigClient
from process_capture import call, checked

PREFIX = '\\\\?\\SWD#MMDEVAPI#'
SUFFIX = '#{e6327cad-dcec-4949-ae8a-991e976a79d2}'

class AudioPolicy:
    def __init__(self):
        self.api = C.WinDLL('combase.dll')
        self.api.WindowsCreateString.argtypes=[C.c_wchar_p,C.c_uint32,C.POINTER(C.c_void_p)]
        self.api.WindowsCreateString.restype=C.c_long
        self.api.WindowsDeleteString.argtypes=[C.c_void_p]
        self.api.WindowsGetStringRawBuffer.argtypes=[C.c_void_p,C.POINTER(C.c_uint32)]
        self.api.WindowsGetStringRawBuffer.restype=C.c_wchar_p
        self.api.RoGetActivationFactory.argtypes=[C.c_void_p,C.POINTER(GUID),C.POINTER(C.c_void_p)]
        self.api.RoGetActivationFactory.restype=C.c_long
        name=self.string('Windows.Media.Internal.AudioPolicyConfig')
        self.factory=C.c_void_p()
        try:
            checked(self.api.RoGetActivationFactory(name,C.byref(GUID('{ab3d4648-e242-459f-b02f-541c70306324}')),C.byref(self.factory)))
        finally:
            self.api.WindowsDeleteString(name)
    def string(self,text):
        value=C.c_void_p()
        checked(self.api.WindowsCreateString(text,len(text),C.byref(value)))
        return value
    def get(self,pid,role):
        value=C.c_void_p()
        result=call(self.factory,26,C.c_long,[C.c_uint32,C.c_int,C.c_int,C.POINTER(C.c_void_p)],pid,0,role,C.byref(value))
        if (result & 0xffffffff) in (0x80070057,0x80070490): return ''
        checked(result)
        try:
            return self.api.WindowsGetStringRawBuffer(value,None) or ''
        finally:
            self.api.WindowsDeleteString(value)
    def set(self,pid,role,device):
        value=self.string(device) if device else C.c_void_p()
        try:
            checked(call(self.factory,25,C.c_long,[C.c_uint32,C.c_int,C.c_int,C.c_void_p],pid,0,role,value))
        finally:
            if value: self.api.WindowsDeleteString(value)
    def route(self,pid,device):
        target=PREFIX+device+SUFFIX if device else ''
        for role in range(3): self.set(pid,role,target)
    def close(self):
        if self.factory:
            call(self.factory,2,C.c_uint32,[])
            self.factory=C.c_void_p()
    @staticmethod
    def visibility(device,visible):
        policy=CoCreateInstance(CLSID_CPolicyConfigClient,IPolicyConfig,CLSCTX_ALL)
        policy.SetEndpointVisibility(device,int(visible))

if __name__=='__main__':
    import os
    policy=AudioPolicy()
    print([policy.get(os.getpid(),r) for r in range(3)])
