"""Select a real DXGI hardware adapter instead of assuming display GPU zero."""
import ctypes as C
from comtypes import GUID
class Desc(C.Structure):
    _fields_=[('name',C.c_wchar*128),('vendor',C.c_uint32),('device',C.c_uint32),
        ('subsystem',C.c_uint32),('revision',C.c_uint32),('video',C.c_size_t),
        ('system',C.c_size_t),('shared',C.c_size_t),('luid',C.c_uint64),('flags',C.c_uint32)]
def call(p,index,result,types,*args):
    table=C.cast(p,C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result,C.c_void_p,*types)(table[index])(p,*args)
def best_adapter():
    factory=C.c_void_p();iid=GUID('{770aae78-f26f-4dba-a829-253c83d1b387}')
    create=C.WinDLL('dxgi').CreateDXGIFactory1
    create.argtypes=[C.POINTER(GUID),C.POINTER(C.c_void_p)];create.restype=C.c_long
    hr=create(C.byref(iid),C.byref(factory))
    if hr<0:raise OSError('无法枚举图形加速设备。')
    devices=[]
    try:
        for index in range(16):
            p=C.c_void_p()
            if call(factory,12,C.c_long,[C.c_uint32,C.POINTER(C.c_void_p)],index,C.byref(p))<0:break
            try:
                desc=Desc()
                hr=call(p,10,C.c_long,[C.POINTER(Desc)],C.byref(desc))
                if hr>=0 and not desc.flags&2:
                    devices.append({'id':index,'name':desc.name,'videoBytes':desc.video,'vendor':desc.vendor})
            finally:call(p,2,C.c_ulong,[])
    finally:call(factory,2,C.c_ulong,[])
    if not devices:raise OSError('没有可用的硬件图形加速设备。')
    return max(devices,key=lambda d:(d['videoBytes'],d['vendor']==0x10de))

def cuda_adapter():
    # CUDA ordinals differ from DXGI ordinals on laptops with two adapters.
    driver=C.WinDLL('nvcuda')
    def checked(name,*args):
        result=getattr(driver,name)(*args)
        if result:raise OSError(f'CUDA {name} 失败：{result}')
    checked('cuInit',0)
    count=C.c_int();checked('cuDeviceGetCount',C.byref(count));devices=[]
    for index in range(count.value):
        device=C.c_int();checked('cuDeviceGet',C.byref(device),index)
        name=C.create_string_buffer(256);checked('cuDeviceGetName',name,256,device)
        memory=C.c_size_t();checked('cuDeviceTotalMem_v2',C.byref(memory),device)
        devices.append({'id':index,'name':name.value.decode('utf-8'),'videoBytes':memory.value,'vendor':0x10de})
    if not devices:raise OSError('没有可用的 NVIDIA CUDA 显卡。')
    return max(devices,key=lambda d:d['videoBytes'])
