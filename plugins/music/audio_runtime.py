"""Select the separately installed, workspace-local GPU runtime before import.

DirectML remains the default for reference tests. The live worker explicitly
selects its validated engine; two ONNX Runtime builds never share one process.
"""
from pathlib import Path
import os,sys

BACKEND=os.environ.get('WORKBENCH_MUSIC_BACKEND','dml')
if BACKEND=='cuda':
    folder=Path(__file__).resolve().parent/'cuda-runtime'
    if 'onnxruntime' in sys.modules:
        raise RuntimeError('CUDA 音频运行时必须在加载 ONNX Runtime 前选择。')
    if (folder/'onnxruntime/__init__.py').exists():
        sys.path.insert(0,str(folder))
elif BACKEND!='dml':
    raise RuntimeError('未知音频推理通道。')

import onnxruntime as ort
if BACKEND=='cuda':
    ort.preload_dlls(directory='')
