"""Short-context RoFormer with the existing bounded streaming overlap interface.

Model conversion/STFT implementation: roformer-port (MIT). No user audio files.
"""
from pathlib import Path
import sys,json,hashlib,time
import numpy as np
from audio_runtime import ort,BACKEND
from gpu_device import best_adapter,cuda_adapter
from scipy.ndimage import gaussian_filter

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE/'roformer-port'))
from driver.pipeline import RoformerDriver,RoformerSpec
from driver.stft import stft_bands_last,istft_bands_last

def vocal_gain(voice_power,music_power,purity,tail_seconds,frame_seconds):
    confidence=voice_power/(voice_power+music_power+1e-12)
    if tail_seconds>0:
        # Keep the decay of an already confident vocal in the same frequency
        # band. A quiet unrelated synth cannot open this envelope by itself.
        held=np.zeros(confidence.shape[0],np.float32)
        evidence=np.zeros_like(held)
        decay=np.exp(-frame_seconds/tail_seconds)
        for frame in range(confidence.shape[1]):
            power=voice_power[:,frame]
            seed=confidence[:,frame]>=.45
            held=np.maximum(held*decay,np.where(seed,power,0))
            evidence=np.maximum(evidence*decay,np.where(seed,confidence[:,frame],0))
            support=np.minimum(1,power/(held*.12+1e-12))
            confidence[:,frame]=np.maximum(confidence[:,frame],np.minimum(purity,evidence)*support)
    return np.sqrt(np.clip(confidence/purity,.02,1)).astype(np.float32)

class VocalSeparator:
    name='Mel-Band RoFormer · DirectML · Recent context'
    def __init__(self,path=None,*,clean=False,purity=.15,recent=False,lookahead=.2,tail_seconds=0,recovery=0,cuda_graph=False,music_purity=0):
        folder=Path(path).parent if path else BASE/'roformer-port/artifacts'
        info=json.loads((folder/'manifest.json').read_text())['models']['mel_band_roformer_kim']
        path=Path(path) if path else folder/info['file']
        with path.open('rb') as model_file:
            digest=hashlib.file_digest(model_file,'sha256').hexdigest()
        if digest!=info['onnx_sha256']:raise RuntimeError('RoFormer 模型校验失败。')
        provider='CUDAExecutionProvider' if BACKEND=='cuda' else 'DmlExecutionProvider'
        if provider not in ort.get_available_providers():
            raise RuntimeError('RoFormer 图形加速组件不可用。')
        options=ort.SessionOptions();options.enable_mem_pattern=False
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        options.intra_op_num_threads=2
        options.add_session_config_entry('session.intra_op.allow_spinning','0')
        self.gpu=cuda_adapter() if BACKEND=='cuda' else best_adapter()
        config={'device_id':str(self.gpu['id'])}
        if BACKEND=='cuda':
            # Retain FP32 multiplications: TF32 can alter quiet effect details.
            config.update(use_tf32='0',arena_extend_strategy='kSameAsRequested',gpu_mem_limit=str(5*1024**3))
            if cuda_graph:config['enable_cuda_graph']='1'
        self.session=ort.InferenceSession(str(path),sess_options=options,
            providers=[(provider,config),'CPUExecutionProvider'])
        if provider not in self.session.get_providers():
            raise RuntimeError('模型未在指定显卡通道上运行。')
        self.name=f'Mel-Band RoFormer · {"CUDA FP32" if BACKEND=="cuda" else "DirectML"} · Recent context'
        self.length=info['chunk_size'];self.margin=self.length//10
        self.step=(self.length-2*self.margin)//2
        self.right_margin=self.margin
        if recent:
            # Most context is history, not additional lookahead. Keep the same
            # two-hop cosine overlap but emit the recent part of the prediction.
            self.step=28224;self.right_margin=round(44100*lookahead)
            self.margin=self.length-2*self.step-self.right_margin
            if self.margin<0:raise ValueError('Recent-context window too short.')
        session=self.session
        self.run_graph=lambda spec:session.run(None,{'spec':spec})[0]
        if BACKEND=='cuda' and cuda_graph:
            self.graph_input=ort.OrtValue.ortvalue_from_numpy(np.zeros(self.session.get_inputs()[0].shape,np.float32),'cuda',self.gpu['id'])
            self.graph_output=ort.OrtValue.ortvalue_from_numpy(np.zeros(self.session.get_outputs()[0].shape,np.float32),'cuda',self.gpu['id'])
            self.binding=self.session.io_binding()
            self.binding.bind_ortvalue_input(self.session.get_inputs()[0].name,self.graph_input)
            self.binding.bind_ortvalue_output(self.session.get_outputs()[0].name,self.graph_output)
            graph_input,graph_output,binding=self.graph_input,self.graph_output,self.binding
            def run_graph(spec):
                graph_input.update_inplace(spec)
                session.run_with_iobinding(binding)
                return graph_output.numpy()
            self.run_graph=run_graph
        self.driver=RoformerDriver(self.run_graph,
            RoformerSpec(chunk_size=self.length,n_fft=info['n_fft'],hop_length=info['hop_length']))
        self.fade=(.5-.5*np.cos(np.pi*(np.arange(self.step)+.5)/self.step)).astype(np.float32)
        self.clean=clean;self.purity=purity;self.tail_seconds=tail_seconds;self.recovery=recovery;self.music_purity=music_purity;self.last_ms=0;self.max_ms=0
        self.warmed=False;self.inference_count=0
        self.reset();self.predict(np.zeros((2,self.length),np.float32))
        self.warmed=True
        self.reset();self.last_ms=0;self.max_ms=0;self.inference_count=0
    def reset(self):
        self.pending=np.zeros((2,self.margin),np.float32)
        self.previous=None
    def push(self,audio):
        if audio.ndim!=2 or audio.shape[0]!=2 or not np.isfinite(audio).all():
            raise ValueError('Invalid captured audio.')
        if self.pending.shape[1]+audio.shape[1]>44100*20:
            raise RuntimeError('分离处理积压超过 20 秒，已恢复原声。')
        self.pending=np.concatenate((self.pending,audio),axis=1)
    def available(self):return self.pending.shape[1]>=self.length
    def extract(self,audio):
        gain=min(64,.95/max(float(np.max(np.abs(audio))),1e-6))
        return np.ascontiguousarray(self.driver.separate_chunk(audio*gain)[0]/gain,dtype=np.float32)
    def predict(self,audio):
        start=time.perf_counter()
        if self.warmed and np.max(np.abs(audio))<1e-8:
            self.music_prediction=np.zeros_like(audio)
            self.last_ms=(time.perf_counter()-start)*1000
            return np.zeros_like(audio)
        self.inference_count+=1
        vocal=self.extract(audio)
        music=audio-vocal
        if self.clean:
            remaining_voice=self.extract(music)
            music-=remaining_voice
            # Voice evidence is frequency-local: suppress weak synth leakage
            # smoothly while retaining voice-dominant bins, including quiet vocals.
            s=self.driver.spec
            spec=stft_bands_last(vocal,s.n_fft,s.hop_length)
            residual=stft_bands_last(audio-vocal,s.n_fft,s.hop_length)
            shape=(s.n_fft//2+1,2,s.frames,2)
            vp=gaussian_filter(np.mean(spec[0].reshape(shape)**2,axis=(1,3)),(1,1.5))
            mp=gaussian_filter(np.mean(residual[0].reshape(shape)**2,axis=(1,3)),(1,1.5))
            if self.recovery:
                # Return residual voice only where the first pass already has
                # vocal evidence. Full residual recovery also recovers synths.
                support=np.clip((vp/(vp+mp+1e-12)-.12)/.33,0,1)
                extra=stft_bands_last(remaining_voice,s.n_fft,s.hop_length)
                extra=extra[0].reshape(shape)*support[:,None,:,None]*self.recovery
                spec=spec+extra.reshape(spec.shape)
                residual=residual-extra.reshape(residual.shape)
                vp=gaussian_filter(np.mean(spec[0].reshape(shape)**2,axis=(1,3)),(1,1.5))
                mp=gaussian_filter(np.mean(residual[0].reshape(shape)**2,axis=(1,3)),(1,1.5))
            gain=vocal_gain(vp,mp,self.purity,self.tail_seconds,s.hop_length/44100)
            masked=spec[0].reshape(shape)*gain[:,None,:,None]
            vocal=istft_bands_last(masked.reshape(spec.shape[1:]),2,s.n_fft,s.hop_length,self.length).astype(np.float32)
            if self.music_purity:
                # A stereo-shared soft mask removes quiet remaining voice in
                # voice-dominant bins without changing strong accompaniment.
                music_spec=stft_bands_last(music,s.n_fft,s.hop_length)
                voice_power=gaussian_filter(np.mean(masked**2,axis=(1,3)),(1,1.5))
                music_power=gaussian_filter(np.mean(music_spec[0].reshape(shape)**2,axis=(1,3)),(1,1.5))
                music_gain=vocal_gain(music_power,voice_power,self.music_purity,0,s.hop_length/44100)
                music_masked=music_spec[0].reshape(shape)*music_gain[:,None,:,None]
                music=istft_bands_last(music_masked.reshape(spec.shape[1:]),2,s.n_fft,s.hop_length,self.length).astype(np.float32)
        if not np.isfinite(vocal).all() or not np.isfinite(music).all():
            raise RuntimeError('RoFormer 分离输出异常。')
        self.music_prediction=music
        self.last_ms=(time.perf_counter()-start)*1000;self.max_ms=max(self.max_ms,self.last_ms)
        return vocal
    def next(self):
        original=self.pending[:,:self.length]
        stop=self.length-self.right_margin
        vocal=self.predict(original)[:,self.margin:stop]
        music=self.music_prediction[:,self.margin:stop]
        mix=original[:,self.margin:stop]
        first=vocal[:,:self.step];second=music[:,:self.step]
        if self.previous is not None:
            first=self.previous[0]*(1-self.fade)+first*self.fade
            second=self.previous[1]*(1-self.fade)+second*self.fade
        self.previous=(vocal[:,self.step:].copy(),music[:,self.step:].copy())
        self.original=np.ascontiguousarray(mix[:,:self.step])
        self.pending=self.pending[:,self.step:]
        return np.ascontiguousarray(first),np.ascontiguousarray(second)
