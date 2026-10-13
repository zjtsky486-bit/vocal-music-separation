'use strict';
const {EventEmitter}=require('node:events');
const fs=require('node:fs'),path=require('node:path'),{randomUUID}=require('node:crypto');
const {execFile}=require('node:child_process');
const MusicService=require('./adapter/music');
const PACKAGE_ROOT=path.resolve(__dirname,'..');

function validateGains(value){
  if(!value||['vocalGain','instrumentalGain'].some(key=>typeof value[key]!=='number'||!Number.isFinite(value[key])||value[key]<0||value[key]>1))throw new TypeError('Stem gains must be finite numbers between 0 and 1');
  return {vocalGain:value.vocalGain,instrumentalGain:value.instrumentalGain};
}
function absolute(value,name){if(typeof value!=='string'||!path.isAbsolute(value))throw new TypeError(`${name} must be an absolute path`);return path.resolve(value);}
function atomic(file,value){const temporary=`${file}.${randomUUID()}.tmp`;try{fs.writeFileSync(temporary,JSON.stringify(value,null,2),{flag:'wx'});fs.renameSync(temporary,file);}finally{fs.rmSync(temporary,{force:true});}}

class AudioService extends EventEmitter {
  constructor(options={}){
    super();
    this.runtimeDir=absolute(options.runtimeDir,'runtimeDir');
    this.engineDir=absolute(options.engineDir||path.join(PACKAGE_ROOT,'plugins/music'),'engineDir');
    if(!['cuda','dml'].includes(options.backend||'cuda'))throw new TypeError('backend must be cuda or dml');
    this.backend=options.backend||'cuda';
    this.minFreeGiB=options.minFreeGiB??40;
    if(!Number.isFinite(this.minFreeGiB)||this.minFreeGiB<40)throw new TypeError('minFreeGiB must be at least 40');
    this.pythonPath=absolute(options.pythonPath||[path.join(this.engineDir,'python/python.exe'),path.join(this.engineDir,'python/Scripts/python.exe')].find(fs.existsSync)||path.join(this.engineDir,'python/Scripts/python.exe'),'pythonPath');
    fs.mkdirSync(this.runtimeDir,{recursive:true});
    this.preferences=path.join(this.runtimeDir,'gains.json');
    this.service=new MusicService(path.resolve(this.engineDir,'../..'),this.runtimeDir,state=>this.emit('state',structuredClone(state)));
    this.service.pythonPath=this.pythonPath;
    this.service.workerPath=path.join(this.engineDir,'worker.py');
    this.service.backend=this.backend;
    try{this.service.setGains(validateGains(JSON.parse(fs.readFileSync(this.preferences,'utf8'))));}catch{}
    this.guard=null;this.stopping=null;
  }
  getState(){return structuredClone(this.service.state);}
  getSources(){return structuredClone(this.service.state.sources||[]);}
  checkStorage(){const stat=fs.statfsSync('C:/');if(stat.bavail*stat.bsize<=this.minFreeGiB*1024**3)throw new Error(`C drive has reached the ${this.minFreeGiB} GiB free-space limit`);}
  start(){
    if(process.platform!=='win32')throw new Error('Live application capture requires Windows');
    if(this.stopping)throw new Error('Audio route restoration is still in progress');
    this.checkStorage();
    for(const file of [this.pythonPath,this.service.workerPath])if(!fs.existsSync(file))throw new Error(`Missing runtime file: ${file}. Run the installer and doctor first.`);
    this.service.start();
    if(!this.guard){this.guard=setInterval(()=>{try{this.checkStorage();}catch(error){this.stop().then(()=>this.service.update({active:false,ready:false,phase:'storage',error:error.message})).catch(failure=>this.emit('warning',failure));}},2000);this.guard.unref();}
    return this.getState();
  }
  setGains(value){const gains=validateGains(value);this.checkStorage();atomic(this.preferences,gains);return this.service.setGains(gains);}
  selectSource(value={pid:null}){
    if(!value||!(value.pid===null||(Number.isInteger(value.pid)&&value.pid>0&&Number.isFinite(value.createdAt)&&value.createdAt>0)))throw new TypeError('Source must be {pid:null} or a listed {pid,createdAt} identity');
    if(!this.service.child)throw new Error('Start the service before selecting a source');
    this.service.send({action:'source',pid:value.pid,createdAt:value.pid===null?undefined:value.createdAt});
  }
  retry(){if(this.stopping)throw new Error('Audio route restoration is still in progress');this.checkStorage();if(this.service.child)this.service.retry();else this.start();}
  stop(){
    if(this.stopping)return this.stopping;
    if(this.guard){clearInterval(this.guard);this.guard=null;}
    this.stopping=this.service.stop().then(()=>{
      if(!this.service.state.error?.includes('恢复未完成'))this.service.update({active:false,ready:false,phase:'stopped'});
      return this.getState();
    }).finally(()=>{this.stopping=null;});
    return this.stopping;
  }
  diagnose(){
    return new Promise((resolve,reject)=>execFile(this.pythonPath,[path.join(PACKAGE_ROOT,'scripts/diagnose.py'),'--engine-dir',this.engineDir],{windowsHide:true,timeout:120000,maxBuffer:1024*1024,env:{...process.env,WORKBENCH_MUSIC_BACKEND:this.backend,PYTHONUTF8:'1',PYTHONDONTWRITEBYTECODE:'1'}},(error,stdout,stderr)=>{
      try{const lines=stdout.trim().split(/\r?\n/);resolve(JSON.parse(lines.at(-1)));}catch{reject(new Error(error?.message||stderr||'No JSON diagnostic report was returned'));}
    }));
  }
}
module.exports={createAudioService:options=>new AudioService(options),AudioService};
