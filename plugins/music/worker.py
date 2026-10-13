"""Local application audio service. Capture -> GPU RoFormer -> stems -> output.
All models, runtime, journals and logs stay under this application directory.
"""
import sys
sys.coinit_flags=0
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import os,json,time,threading,warnings,collections,shutil,argparse,queue
os.environ.setdefault('WORKBENCH_MUSIC_BACKEND','cuda')
if __name__=='__main__':
    # Claim the route before GPU initialization or recovery of an old journal.
    import atexit
    from service_lock import EngineLease
    lease=EngineLease()
    if not lease.acquire():
        print(json.dumps({'active':False,'ready':False,'phase':'busy','error':'音频分离正在另一份软件中运行，请先停止另一份软件的分离，再重试。'},ensure_ascii=False),flush=True)
        raise SystemExit(2)
    atexit.register(lease.close)
from pathlib import Path
import psutil,numpy as np
from pycaw.pycaw import AudioUtilities
from pycaw.api.audiopolicy import IAudioSessionControl2
from process_capture import ProcessCapture
from audio_policy import AudioPolicy
from dsp import Spectrum,RATE,mix_stem_gains
from roformer_live import VocalSeparator
from audio_sources import SourceCatalog,SourceMonitor

warnings.filterwarnings('ignore',category=UserWarning,module='pycaw')
BASE=Path(__file__).resolve().parent
STOP=threading.Event()
RETRY=threading.Event()
SOURCE_CHANGED=threading.Event()
SOURCE_COMMANDS=queue.SimpleQueue()
SOURCE_ROWS=[]
CURRENT_SOURCE=None
SOURCE_MODE={'pid':None}
SOURCE_SCAN_STATS={}
blend=.5
stem_gains=None
write_lock=threading.Lock()
DRIVER_AVAILABLE=any('CABLE Input' in (d.FriendlyName or '') for d in AudioUtilities.GetAllDevices())
device_cache=[];device_cache_time=0
try:psutil.Process().nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
except Exception:pass
def render_devices():
    global device_cache,device_cache_time
    now=time.monotonic()
    if not device_cache or now-device_cache_time>5:
        device_cache=[d for d in AudioUtilities.GetAllDevices() if d.id.startswith('{0.0.0.')]
        device_cache_time=now
    return device_cache
def emit(**state):
    state['driverAvailable']=DRIVER_AVAILABLE
    state.update(sources=SOURCE_ROWS,currentSource=CURRENT_SOURCE,source=SOURCE_MODE)
    state['telemetry']={**state.get('telemetry',{}),**SOURCE_SCAN_STATS}
    with write_lock:
        print(json.dumps(state,ensure_ascii=False),flush=True)
def controls():
    global blend,stem_gains
    try:
        for line in sys.stdin:
            message=json.loads(line)
            if message.get('action')=='stop': break
            if message.get('action')=='retry': RETRY.set()
            if message.get('action')=='source':
                SOURCE_COMMANDS.put({'pid':message.get('pid'),'createdAt':message.get('createdAt')})
            if message.get('action')=='blend':
                blend=float(np.clip(message['value'],0,1));stem_gains=None
            if message.get('action')=='gains':
                gains=(float(message['vocalGain']),float(message['instrumentalGain']))
                if all(np.isfinite(gains)):stem_gains=tuple(float(np.clip(value,0,1)) for value in gains)
    except Exception: pass
    STOP.set()

def all_processes():
    """Recovery must search every executable, independent of the selected source."""
    found=[]
    for process in psutil.process_iter(['pid','name','ppid','exe','create_time']):
        try:
            if process.info.get('exe'):found.append(process)
        except (psutil.NoSuchProcess,psutil.AccessDenied):pass
    return found

def source_identity(source):
    return (source.get('capturePid',source['pid']),source.get('captureCreatedAt',source['createdAt'])) if source else None

def valid_source(source):
    if not source:return False
    pid,created=source_identity(source)
    try:return abs(psutil.Process(pid).create_time()-created)<.01
    except (psutil.NoSuchProcess,psutil.AccessDenied):return False

def refresh_sources():
    global SOURCE_ROWS,CURRENT_SOURCE,SOURCE_MODE,SOURCE_SCAN_STATS
    if globals().get('TEST_PID'):
        try:
            if CURRENT_SOURCE and CURRENT_SOURCE['pid']==TEST_PID:
                item=CURRENT_SOURCE
                if not valid_source(item):raise psutil.NoSuchProcess(TEST_PID)
            else:
                process=psutil.Process(TEST_PID)
                item={'pid':process.pid,'createdAt':process.create_time(),'name':process.name(),'exe':process.exe(),'active':True,'peak':1}
            SOURCE_ROWS=[item];CURRENT_SOURCE=item
        except (psutil.NoSuchProcess,psutil.AccessDenied):SOURCE_ROWS=[];CURRENT_SOURCE=None
        while not SOURCE_COMMANDS.empty():
            message=SOURCE_COMMANDS.get()
            if message['pid'] is None:SOURCE_MODE={'pid':None}
            elif CURRENT_SOURCE and message['pid']==CURRENT_SOURCE['pid'] and isinstance(message.get('createdAt'),(int,float)) and abs(message['createdAt']-CURRENT_SOURCE['createdAt'])<.01:
                SOURCE_MODE={'pid':CURRENT_SOURCE['pid'],'createdAt':CURRENT_SOURCE['createdAt']}
            else:emit(active=False,ready=False,phase='idle',selectionError='音源已退出或身份发生变化，请刷新音源。');continue
            SOURCE_CHANGED.set()
        return CURRENT_SOURCE
    snapshot=SOURCE_MONITOR.snapshot()
    SOURCE_ROWS=snapshot['sources'];SOURCE_SCAN_STATS={key:value for key,value in snapshot.items() if key!='sources'}
    while not SOURCE_COMMANDS.empty():
        message=SOURCE_COMMANDS.get()
        try:
            SOURCE_CATALOG.select(message['pid'],message.get('createdAt'));SOURCE_CHANGED.set()
        except (ValueError,TypeError) as error:
            emit(active=False,ready=False,phase='idle',selectionError=str(error))
    CURRENT_SOURCE=SOURCE_CATALOG.choose() if not snapshot.get('sourceScanError') else SOURCE_CATALOG.current
    SOURCE_MODE=dict(SOURCE_CATALOG.selection)
    return CURRENT_SOURCE

def source_members(source):
    if not valid_source(source):return []
    if globals().get('TEST_PID'):
        process=psutil.Process(TEST_PID);process.info={'pid':process.pid,'name':source['name'],'exe':source['exe'],'create_time':source['createdAt']};return [process]
    return SOURCE_CATALOG.members(source)

def handoff_ready(hop_times,step):
    """Do not mute the original app until recent inference meets realtime."""
    budget=step/RATE*1000*.9
    return len(hop_times)>=2 and all(duration<budget for duration in list(hop_times)[-2:])

def device_sessions(device):
    sessions=[]
    manager=device.AudioSessionManager.GetSessionEnumerator()
    for i in range(manager.GetCount()):
        control=manager.GetSession(i).QueryInterface(IAudioSessionControl2)
        sessions.append((control.GetProcessId(),control.GetState()))
    return sessions

def audio_process_ids():
    ids=set()
    for device in render_devices():
        if not device.id.startswith('{0.0.0.') or device.state.value!=1: continue
        try: ids.update(pid for pid,state in device_sessions(device) if state==1)
        except Exception: continue
    return ids

class Route:
    def __init__(self,journal):
        self.journal=journal
        self.policy=AudioPolicy()
        devices=AudioUtilities.GetAllDevices()
        self.device_ids={d.id for d in devices}
        self.sink=next((d for d in devices if 'CABLE Input' in (d.FriendlyName or '') and d.id.startswith('{0.0.0.')),None)
        if self.sink is None:
            self.policy.close()
            raise RuntimeError('需要 VB-CABLE 独立音频通道才能调节人声与伴奏。')
        self.was_enabled=self.sink.state.value==1
        self.entries=[]
        self.restore_pending()
    def save(self):
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.journal.with_suffix('.tmp')
        temporary.write_text(json.dumps({'sink':self.sink.id,'wasEnabled':self.was_enabled,'entries':self.entries},ensure_ascii=False),encoding='utf-8')
        temporary.replace(self.journal)
    def restore_pending(self):
        if not self.journal.exists(): return
        saved=json.loads(self.journal.read_text(encoding='utf-8'))
        if saved.get('sink')==self.sink.id:
            self.was_enabled=saved.get('wasEnabled',True)
        found=all_processes()
        live=audio_process_ids()
        for entry in saved.get('entries',[]):
            candidates=[p for p in found if (p.info['exe'] or '').lower()==entry['exe'].lower()]
            candidates.sort(key=lambda p:(p.pid not in live,p.pid!=entry['pid']))
            target=next((p for p in candidates if p.pid in live or p.pid==entry['pid']),None)
            if target:
                for role,original in enumerate(entry['roles']):
                    if self.policy.get(target.pid,role)!=original:self.policy.set(target.pid,role,original)
            else:
                # Keep original preferences until this executable has a live audio session.
                self.entries.append({**entry,'pending':True})
        if not saved.get('wasEnabled',True) and saved.get('sink') in self.device_ids:
            self.policy.visibility(saved['sink'],False)
    def add(self,found):
        live=audio_process_ids()
        for p in found:
            if p.pid not in live: continue
            created=p.info.get('create_time')
            try:
                if created is None or abs(p.create_time()-created)>.01:continue
            except (psutil.NoSuchProcess,psutil.AccessDenied):continue
            if any(e['pid']==p.pid and e.get('createdAt')==created and not e.get('pending') for e in self.entries): continue
            original=[self.policy.get(p.pid,r) for r in range(3)]
            # Every process of the same executable shares persisted preferences.
            old=next((e for e in self.entries if e['exe'].lower()==(p.info['exe'] or '').lower()),None)
            if old: original=old['roles']
            self.entries.append({'pid':p.pid,'createdAt':created,'exe':p.info['exe'] or '', 'roles':original})
            self.save()
            self.policy.visibility(self.sink.id,True)
            self.policy.route(p.pid,self.sink.id)
    def connected(self,ids):
        return any(pid in ids and state==1 for pid,state in device_sessions(self.sink))
    def restore(self):
        found=all_processes()
        live=audio_process_ids()
        failed=[]
        for entry in self.entries:
            candidates=[p for p in found if (p.info['exe'] or '').lower()==entry['exe'].lower()]
            candidates.sort(key=lambda p:(p.pid not in live,p.pid!=entry['pid']))
            p=next((p for p in candidates if p.pid==entry['pid'] or p.pid in live),None)
            if p:
                try:
                    for role,original in enumerate(entry['roles']):
                        if self.policy.get(p.pid,role)!=original:self.policy.set(p.pid,role,original)
                except Exception: failed.append(entry)
            else: failed.append(entry)
        if not self.was_enabled:
            try: self.policy.visibility(self.sink.id,False)
            except Exception: pass
        self.entries=failed
        if failed: self.save()
        elif self.journal.exists(): self.journal.unlink()
        self.policy.close()

class CaptureReader:
    """Drain WASAPI on its own COM thread, independent of GPU and device queries."""
    def __init__(self,pid):
        self.blocks=collections.deque();self.lock=threading.Lock();self.stop=threading.Event()
        self.error=None;self.last_sound=time.monotonic()-20;self.frames=0;self.total_frames=0
        self.thread=threading.Thread(target=self.read,args=(pid,),daemon=True);self.thread.start()
    def read(self,pid):
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None,0)
        capture=None
        try:
            capture=ProcessCapture(pid)
            while not self.stop.is_set():
                block=capture.read()
                if block is None:self.stop.wait(.002);continue
                if np.max(np.abs(block))>1e-5:self.last_sound=time.monotonic()
                with self.lock:
                    self.blocks.append(block);self.frames+=len(block);self.total_frames+=len(block)
                    if self.frames>RATE*20:raise RuntimeError('分离处理积压超过 20 秒，已恢复原声。')
        except Exception as error:self.error=error
        finally:
            if capture:capture.close()
            ctypes.windll.ole32.CoUninitialize()
    def take(self):
        if self.error:raise self.error
        with self.lock:
            if not self.blocks:return None
            result=np.concatenate(self.blocks).T.copy();self.blocks.clear();self.frames=0
            return result
    def close(self):self.stop.set();self.thread.join(timeout=5)

class Playback:
    def __init__(self,sink,step=52992):
        import sounddevice as sd
        speaker=AudioUtilities.GetSpeakers()
        if speaker.id==sink: raise RuntimeError('默认输出不能是独立音频通道，请选择耳机或扬声器。')
        devices=sd.query_devices()
        output=next((i for i,d in enumerate(devices) if d['name']==speaker.FriendlyName and d['max_output_channels']>=2 and sd.query_hostapis(d['hostapi'])['name']=='Windows WASAPI'),None)
        if output is None: raise RuntimeError('未找到默认耳机或扬声器。')
        self.blocks=collections.deque();self.offset=0;self.lock=threading.Lock();self.ready=False
        self.underruns=0;self.host_underruns=0;self.missing_frames=0;self.queued=0;self.rendered=0
        # One full inference hop plus a quarter hop of jitter protection. Keep this
        # reserve intact after handoff; only unheard startup backlog is trimmed.
        self.prefill=round(step*(1.25 if step<RATE*.8 else 1));self.start_guard=.12;self.primed_at=None
        self.target=step*.625+RATE*self.start_guard;self.current_blend=blend;self.fraction=0;self.source_frames=0;self.capture_origin=0
        self.current_gains=stem_gains or (min(1,2*(1-blend)),min(1,2*blend))
        self.latest=(np.zeros((2,512),np.float32),np.zeros((2,512),np.float32))
        self.started=False;self.min_buffer=None;self.output_peak=0;self.startup_skipped=0
        self.stream=sd.OutputStream(device=output,samplerate=RATE,channels=2,dtype='float32',blocksize=512,latency=.06,extra_settings=sd.WasapiSettings(auto_convert=True),callback=self.callback)
        # Warm the physical output while the selected app still plays on its original route.
        # Silent callbacks run now; the prepared stems are enabled at handoff.
        self.stream.start()
    def callback(self,out,frames,when,status):
        out.fill(0)
        with self.lock:
            if not self.ready:return
            if status.output_underflow:self.host_underruns+=1
            # Correct the small capture/render clock mismatch without dropping blocks.
            ratio=1+float(np.clip((self.queued-self.target)/RATE*.0005,-.001,.001))
            exact=frames*ratio+self.fraction;needed=int(exact);self.fraction=exact-needed
            raw=np.zeros((6,needed),np.float32);written=0
            while written<needed and self.blocks:
                block=self.blocks[0];count=min(block.shape[1]-self.offset,needed-written)
                raw[:,written:written+count]=block[:,self.offset:self.offset+count]
                written+=count;self.offset+=count;self.queued-=count
                if self.offset==block.shape[1]:self.blocks.popleft();self.offset=0
            if written<needed:self.underruns+=1;self.missing_frames+=needed-written
            self.min_buffer=self.queued if self.min_buffer is None else min(self.min_buffer,self.queued)
        if needed!=frames:
            x=np.linspace(0,needed-1,frames);raw=np.array([np.interp(x,np.arange(needed),r) for r in raw],np.float32)
        vocals,music,original=raw[:2],raw[2:4],raw[4:]
        # Ramp the gain over a callback instead of clicking or mixing old queued settings.
        gains=stem_gains
        if gains is None:
            values=np.linspace(self.current_blend,blend,frames,dtype=np.float32)
            self.current_blend=blend
            self.current_gains=(min(1,2*(1-blend)),min(1,2*blend))
            out[:]=(vocals*np.maximum(0,1-2*values)+music*np.maximum(0,2*values-1)+original*(1-np.abs(2*values-1))).T
        else:
            out[:]=mix_stem_gains(vocals,music,gains,self.current_gains,original).T
            self.current_gains=gains
        # Start with a short equal-power fade to avoid a sudden hard audio edge.
        if self.rendered<RATE*.12:
            progress=np.minimum(1,(self.rendered+np.arange(frames))/(RATE*.12))
            out*=np.sin(progress*np.pi/2)[:,None]**2
        np.clip(out,-1,1,out=out)
        self.latest=(vocals,music);self.rendered+=frames;self.source_frames+=needed;self.output_peak=float(np.max(np.abs(out)))
    def add(self,vocals,music,original=None):
        if original is None:original=vocals+music
        block=np.concatenate((vocals,music,original),axis=0)
        with self.lock:
            if self.queued>RATE*15: raise RuntimeError('音频缓冲积压过大，已恢复原声。')
            self.blocks.append(block);self.queued+=block.shape[1]
            if self.primed_at is None and self.queued>=self.prefill:self.primed_at=time.monotonic()
    def start(self):
        with self.lock:
            if self.started:return
            # Device enumeration/routing can accumulate extra, unheard startup
            # blocks. Begin with the latest guarded reservoir, not that backlog.
            # Never discard samples once audible playback has started.
            discard=max(0,self.queued-self.prefill)
            self.startup_skipped=discard;self.source_frames+=discard
            while discard and self.blocks:
                block=self.blocks[0];count=min(discard,block.shape[1]-self.offset)
                self.offset+=count;self.queued-=count;discard-=count
                if self.offset==block.shape[1]:self.blocks.popleft();self.offset=0
            self.ready=True;self.started=True
    def stats(self):
        return {'bufferMs':round(self.queued/RATE*1000),'minBufferMs':round((self.min_buffer or 0)/RATE*1000),
            'underruns':self.underruns,'hostUnderruns':self.host_underruns,'missingFrames':self.missing_frames,
            'renderedFrames':self.rendered,'startupSkippedFrames':self.startup_skipped,'outputPeak':self.output_peak,'appliedBlend':self.current_blend,
            'appliedVocalGain':self.current_gains[0],'appliedInstrumentalGain':self.current_gains[1],
            'outputLatencyMs':round(self.stream.latency*1000),'startupReserveMs':round(self.prefill/RATE*1000)}
    def close(self): self.stream.stop();self.stream.close()

def run(parent,journal):
    separator=None
    while not STOP.is_set() and psutil.pid_exists(parent):
        if shutil.disk_usage('C:\\').free<=40*1024**3:break
        if separator is None:
            emit(active=False,ready=False,phase='loading')
            try:
                separator=VocalSeparator(BASE/'roformer-port/artifacts-balanced/mel_band_roformer_kim_T241.onnx',clean=True,recent=True,
                    purity=.2,tail_seconds=.18,recovery=1,music_purity=.01,cuda_graph=os.environ['WORKBENCH_MUSIC_BACKEND']=='cuda')
            except Exception as error:
                emit(active=False,ready=False,phase='error',error=str(error))
                while not STOP.is_set() and psutil.pid_exists(parent) and not RETRY.is_set():STOP.wait(.2)
                RETRY.clear();continue
        selected=refresh_sources();SOURCE_CHANGED.clear()
        if not valid_source(selected):
            emit(active=False,ready=False,phase='idle',modelReady=True);STOP.wait(.2);continue
        route=capture=playback=None;failed=False
        try:
            emit(active=False,ready=False,phase='loading')
            separator.reset()
            capture=CaptureReader(source_identity(selected)[0])
            vocal_spectrum,music_spectrum=Spectrum(),Spectrum()
            last_check=last_emit=time.monotonic();last_route_update=0;route_attempted=False;route_error=None
            hop_times=collections.deque(maxlen=2);speed_error=None
            silence_since=None;last_packet=time.monotonic();last_padding=0
            while not STOP.is_set() and not RETRY.is_set() and psutil.pid_exists(parent):
                now=time.monotonic();active=now-capture.last_sound<2.5
                block=capture.take()
                if block is not None:last_packet=now
                if playback is not None and block is not None:separator.push(block)
                elif not active and (separator.pending.shape[1]!=separator.margin or separator.previous is not None):separator.reset()
                if playback is None and active:
                    emit(active=True,ready=False,phase='connecting',modelReady=True)
                    # Build a reservoir before moving the selected app away from its speaker.
                    separator.reset()
                    if block is not None:separator.push(block)
                    capture_origin=capture.total_frames-(block.shape[1] if block is not None else 0)
                    playback=Playback('',separator.step)
                    playback.capture_origin=capture_origin
                if playback and separator.available():
                    began=time.perf_counter()
                    vocals,music=separator.next()
                    hop_times.append((time.perf_counter()-began)*1000)
                    if playback:playback.add(vocals,music,separator.original)
                    speed_error=None if handoff_ready(hop_times,separator.step) else '分离处理速度暂未达到实时要求，原声继续播放；可稍后重试。' if len(hop_times)>=2 else None
                # Inference can take hundreds of milliseconds. Routing and
                # telemetry must use its completion time, not the stale start.
                now=time.monotonic();active=now-capture.last_sound<2.5
                # Route as soon as the guarded reservoir is ready; the previous
                # 800 ms polling interval could add an entire inference cycle.
                check_interval=.1 if playback and not playback.started else .4
                if now-last_check>check_interval:
                    next_source=refresh_sources();last_check=now
                    if source_identity(next_source)!=source_identity(selected) or not valid_source(selected):break
                    selected=next_source;SOURCE_CHANGED.clear()
                    found=source_members(selected)
                    if shutil.disk_usage('C:\\').free<=40*1024**3:STOP.set();break
                    if playback and playback.primed_at is not None and now-playback.primed_at>=playback.start_guard and not route_attempted and handoff_ready(hop_times,separator.step):
                        route_attempted=True
                        try:route=Route(journal)
                        except Exception as error:
                            route_error=str(error)
                            if route:route.restore();route=None
                    if route and (not playback.started or now-last_route_update>5):
                        route.add(found);last_route_update=now
                    if route and not playback.started and route.connected({p.pid for p in found}):playback.start()
                    if playback and playback.started and active and (playback.underruns>1 or playback.host_underruns>8):
                        raise RuntimeError('检测到音频断流，已恢复应用原声；请重试接入。')
                if now-last_emit>.08:
                    last_emit=now
                    ready=bool(playback and playback.started and playback.rendered>0)
                    vocals,music=playback.latest if playback else (np.zeros((2,1024),np.float32),np.zeros((2,1024),np.float32))
                    telemetry=playback.stats() if playback else {}
                    telemetry.update(model=separator.name,inferenceMs=round(separator.last_ms),maxInferenceMs=round(separator.max_ms),
                        inferenceCalls=separator.inference_count,
                        recentHopMs=[round(value) for value in hop_times],handoffReady=handoff_ready(hop_times,separator.step),
                        gpu=separator.gpu['name'],
                        inferenceBudgetMs=round(separator.step/RATE*1000),inputBufferMs=round(separator.pending.shape[1]/RATE*1000),
                        delaySeconds=round(max(0,(capture.total_frames-playback.capture_origin-playback.source_frames)/RATE+playback.stream.latency),2) if ready and active else None,
                        preparingSeconds=round((separator.length-separator.margin)/RATE+.7,1))
                    emit(active=active,ready=ready and active,phase='playing' if ready and active else 'paused' if not active else 'monitoring' if route_error or speed_error else 'connecting',
                        error=route_error or speed_error,vocals=vocal_spectrum.analyze(vocals),music=music_spectrum.analyze(music),blend=blend,telemetry=telemetry)
                if not active:
                    if silence_since is None:silence_since=now
                    # A stopped player has no further WASAPI packets: finish the delayed tail with silence.
                    if playback and playback.started and playback.queued<separator.step*1.5 and now-last_packet>separator.step/RATE and now-last_padding>separator.step/RATE and capture.frames==0:
                        separator.push(np.zeros((2,separator.step),np.float32))
                        playback.capture_origin-=separator.step;last_padding=now
                    if now-silence_since>12:
                        if playback:playback.close();playback=None
                        if route:route.restore();route=None
                        route_attempted=False;separator.reset();hop_times.clear();speed_error=None;silence_since=now
                else:silence_since=None
                if not separator.available():STOP.wait(.003 if playback else .025)
        except Exception as error:
            import traceback
            traceback.print_exc(file=sys.stderr);failed=True
            telemetry=playback.stats() if playback else {}
            telemetry.update(model=separator.name,gpu=separator.gpu['name'],inferenceMs=round(separator.last_ms),maxInferenceMs=round(separator.max_ms),inputBufferMs=round(separator.pending.shape[1]/RATE*1000))
            emit(active=True,ready=False,phase='error',error=str(error),telemetry=telemetry)
        finally:
            try:
                if playback:playback.close()
            finally:
                try:
                    if capture:capture.close()
                finally:
                    if route:route.restore()
            RETRY.clear()
            SOURCE_CHANGED.clear()
        if failed:
            while not STOP.is_set() and psutil.pid_exists(parent):
                current=refresh_sources()
                if source_identity(current)!=source_identity(selected) or RETRY.is_set() or SOURCE_CHANGED.is_set():break
                STOP.wait(.2)
        if not STOP.is_set():STOP.wait(.2)
    emit(active=False,ready=False,phase='idle')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--parent',type=int,required=True);parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--test-pid',type=int)
    parser.add_argument('--recover-only',action='store_true')
    args=parser.parse_args()
    TEST_PID=args.test_pid
    SOURCE_CATALOG=SourceCatalog(args.parent,os.getpid())
    if args.journal.exists():Route(args.journal).restore()
    if not args.recover_only:
        SOURCE_MONITOR=SourceMonitor(SOURCE_CATALOG).start() if not TEST_PID else None
        threading.Thread(target=controls,daemon=True).start()
        try:run(args.parent,args.journal)
        finally:
            if SOURCE_MONITOR:SOURCE_MONITOR.stop()
