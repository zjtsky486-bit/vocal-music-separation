"""Real stem mixing and log-frequency analysis; NumPy FFT, no synthetic meters."""
import numpy as np

RATE=44100

def mix_stem_gains(vocals,music,gains,previous=None,original=None):
    """Independent stem volumes, ramped over one device callback to avoid clicks."""
    frames=vocals.shape[1]
    target=np.clip(np.asarray(gains,dtype=np.float32),0,1)
    start=target if previous is None else np.clip(np.asarray(previous,dtype=np.float32),0,1)
    vocal_gain=np.linspace(start[0],target[0],frames,dtype=np.float32)
    music_gain=np.linspace(start[1],target[1],frames,dtype=np.float32)
    if original is not None and target[0]==target[1] and start[0]==start[1]:
        return original*vocal_gain
    result=vocals*vocal_gain+music*music_gain
    if original is not None:
        # Recover discarded detail only in the shared mix. Solo/mute endpoints
        # retain the cleaned stem, and equal gains retain the original waveform.
        result+=(original-vocals-music)*np.minimum(vocal_gain,music_gain)
    return result
def mix_stems(vocals,music,blend,original=None):
    blend=float(np.clip(blend,0,1))
    if original is None:original=vocals+music
    return vocals*max(0,1-2*blend)+music*max(0,2*blend-1)+original*(1-abs(2*blend-1))

class Spectrum:
    def __init__(self):
        self.audio=np.zeros((2,2048),np.float32)
        self.window=np.hanning(2048).astype(np.float32)
        self.frequency=np.fft.rfftfreq(2048,1/RATE)
        self.edges=np.geomspace(40,16000,37)
        self.masks=[(self.frequency>=a)&(self.frequency<b) for a,b in zip(self.edges[:-1],self.edges[1:])]
    def analyze(self,stereo):
        if stereo.shape[1]>=2048:self.audio[:]=stereo[:,-2048:]
        else:self.audio=np.concatenate([self.audio[:,stereo.shape[1]:],stereo],axis=1)
        # Average channel power, not waveforms: stereo widening/reverb can put
        # the channels out of phase without making their audible energy vanish.
        spectra=np.abs(np.fft.rfft(self.audio*self.window,axis=1))*2/max(1,float(self.window.sum()))
        magnitude=np.sqrt(np.mean(spectra**2,axis=0))
        bands=[float(np.max(magnitude[mask])) if mask.any() else float(magnitude[np.argmin(abs(self.frequency-np.sqrt(a*b)))]) for mask,a,b in zip(self.masks,self.edges[:-1],self.edges[1:])]
        def level(x): return float(np.clip((20*np.log10(max(x,1e-8))+65)/65,0,1))
        return {'bands':[level(x) for x in bands],'bass':level(float(np.max(magnitude[self.frequency<250]))),'treble':level(float(np.max(magnitude[self.frequency>2500]))),'rms':float(np.sqrt(np.mean(stereo**2)))}
