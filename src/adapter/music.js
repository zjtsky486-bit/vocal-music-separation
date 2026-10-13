const { spawn, execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { StringDecoder } = require('node:string_decoder');
class MusicService {
  constructor(root,runtime,publish) { this.root=root;this.runtime=runtime;this.publish=publish;this.enabled=true;this.blend=.5;this.gains={vocalGain:1,instrumentalGain:1};this.state={active:false,ready:false,phase:'idle',enabled:true,...this.gains};this.stopping=false; }
  start() {
    if(this.child) return;
    const python=this.pythonPath||path.join(this.root,'plugins','music','python','python.exe');
    const script=this.workerPath||path.join(this.root,'plugins','music','worker.py');
    if(!fs.existsSync(python)) { this.update({active:false,ready:false,phase:'error',error:'音乐插件运行组件不存在。'});return; }
    this.stopping=false;
    const child=spawn(python,['-u',script,'--parent',String(process.pid),'--journal',path.join(this.runtime,'music-route.json')],{cwd:path.dirname(script),windowsHide:true,env:{...process.env,WORKBENCH_MUSIC_BACKEND:this.backend||'cuda',PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8',PYTHONDONTWRITEBYTECODE:'1'}});
    this.child=child;
    let buffer='';const decoder=new StringDecoder('utf8');
    child.stdin.on('error',()=>{});
    child.stdout.on('data',data=>{
      buffer+=decoder.write(data);
      while(buffer.includes('\n')) {const end=buffer.indexOf('\n');const line=buffer.slice(0,end);buffer=buffer.slice(end+1);try{this.update(JSON.parse(line));}catch{}}
      if(buffer.length>100000) buffer='';
    });
    child.stderr.on('data',data=>{
      try {
        const log=path.join(this.runtime,'music.log');
        if(fs.existsSync(log)&&fs.statSync(log).size>1024*1024) fs.writeFileSync(log,'');
        fs.appendFileSync(log,data);
      } catch {}
    });
    child.on('error',error=>this.update({active:false,ready:false,phase:'error',error:error.message}));
    child.on('exit',()=>{this.child=null;if(!this.stopping){if(this.state.phase==='busy')this.update(this.state);else this.update({active:false,ready:false,phase:'error',error:'音乐服务已停止。'});}});
    this.setGains(this.gains);
  }
  update(state) {
    this.state={...state,enabled:this.enabled,blend:this.blend,...this.gains};
    const now=Date.now();
    if(now-(this.lastStatusWrite||0)>1000||this.lastPhase!==state.phase) {
      this.lastStatusWrite=now;this.lastPhase=state.phase;
      const {active,ready,phase,error,driverAvailable,telemetry,vocals,music}=this.state;
      try {fs.writeFileSync(path.join(this.runtime,'music-state.json'),JSON.stringify({active,ready,phase,error,driverAvailable,telemetry,vocals,music,blend:this.blend,...this.gains,updatedAt:new Date(now).toISOString()},null,2));}catch{}
    }
    this.publish(this.state);
  }
  send(message) {if(this.child?.stdin.writable)this.child.stdin.write(JSON.stringify(message)+'\n');}
  setBlend(value) {if(typeof value!=='number'||!Number.isFinite(value))throw Error('Invalid audio blend');this.blend=Math.max(0,Math.min(1,value));this.gains={vocalGain:Math.min(1,2*(1-this.blend)),instrumentalGain:Math.min(1,2*this.blend)};Object.assign(this.state,this.gains,{blend:this.blend});this.send({action:'blend',value:this.blend});return this.blend;}
  setGains(value) {
    if(!value||['vocalGain','instrumentalGain'].some(key=>typeof value[key]!=='number'||!Number.isFinite(value[key])))throw Error('Invalid stem volume');
    this.gains={vocalGain:Math.max(0,Math.min(1,value.vocalGain)),instrumentalGain:Math.max(0,Math.min(1,value.instrumentalGain))};
    Object.assign(this.state,this.gains);this.send({action:'gains',...this.gains});return {...this.gains};
  }
  retry() {if(!this.child)this.start();else this.send({action:'retry'});}
  stop() {
    if(this.stopPromise) return this.stopPromise;
    this.stopping=true;
    const child=this.child;
    if(!child) return Promise.resolve();
    this.stopPromise=new Promise(resolve=>{
      const timeout=setTimeout(()=>child.kill(),15000);
      child.once('close',()=>{clearTimeout(timeout);resolve();});
      this.send({action:'stop'});child.stdin.end();
    }).then(()=>{
      const journal=path.join(this.runtime,'music-route.json');
      if(!fs.existsSync(journal)) return;
      return new Promise(resolve=>execFile(this.pythonPath||path.join(this.root,'plugins/music/python/python.exe'),
        [this.workerPath||path.join(this.root,'plugins/music/worker.py'),'--parent',String(process.pid),'--journal',journal,'--recover-only'],
        {windowsHide:true,timeout:12000,env:{...process.env,WORKBENCH_MUSIC_BACKEND:this.backend||'cuda',PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'}},
        error=>{if(error)this.update({active:false,ready:false,phase:'error',error:'音频路由恢复未完成，恢复记录已保留。'});resolve();}));
    }).finally(()=>{this.stopPromise=null;});
    return this.stopPromise;
  }
}
module.exports=MusicService;
