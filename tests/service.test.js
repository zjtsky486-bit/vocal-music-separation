'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {EventEmitter}=require('node:events'),{PassThrough,Writable}=require('node:stream');
const childProcess=require('node:child_process');
const runtime=path.resolve(__dirname,'../data/test-runtime');fs.mkdirSync(runtime,{recursive:true});
const fakePython=path.join(runtime,'python.exe');fs.writeFileSync(fakePython,'test fixture, never executed');
const originalSpawn=childProcess.spawn;let child,launch,commands=[],spawnCount=0;
childProcess.spawn=(command,args,options)=>{
  spawnCount++;
  launch={command,args,options};child=new EventEmitter();child.stdout=new PassThrough();child.stderr=new PassThrough();
  child.stdin=new Writable({write(data,_encoding,done){commands.push(JSON.parse(data.toString()));done();},final(done){done();setImmediate(()=>{child.emit('exit',0);child.emit('close',0);});}});
  child.kill=()=>{child.emit('exit',1);child.emit('close',1);};return child;
};
const {createAudioService}=require('..');
test.after(()=>{childProcess.spawn=originalSpawn;});

// Exercise the production guard with controlled free space, regardless of CI's C drive size.
function mockStorage(t){
  let freeGiB=100;
  t.mock.method(fs,'statfsSync',drive=>{
    assert.equal(drive,'C:/');
    return {bsize:4096,bavail:freeGiB*1024**3/4096};
  });
  return value=>{freeGiB=value;};
}

test('SDK validates boundaries, persists isolated gains, forwards source identity and restores on stop',async(t)=>{
  mockStorage(t);
  for(const value of [{},{runtimeDir:'relative'},{runtimeDir:runtime,backend:'cpu'},{runtimeDir:runtime,minFreeGiB:39}])assert.throws(()=>createAudioService(value));
  const audio=createAudioService({runtimeDir:runtime,pythonPath:fakePython,backend:'dml'});let states=[];audio.on('state',state=>states.push(state));
  for(const value of [null,{}, {vocalGain:NaN,instrumentalGain:1},{vocalGain:2,instrumentalGain:1},{vocalGain:1,instrumentalGain:'1'}])assert.throws(()=>audio.setGains(value));
  audio.setGains({vocalGain:.25,instrumentalGain:.8});audio.start();
  assert.equal(launch.command,fakePython);assert.equal(launch.options.env.WORKBENCH_MUSIC_BACKEND,'dml');assert.equal(launch.options.windowsHide,true);
  assert(launch.args.includes(String(process.pid)));assert(launch.args.includes(path.join(runtime,'music-route.json')));
  assert.deepEqual(commands.at(-1),{action:'gains',vocalGain:.25,instrumentalGain:.8});
  const message=Buffer.from(JSON.stringify({active:true,ready:true,phase:'playing',error:undefined,sources:[{pid:123,createdAt:45,name:'任意音乐应用.exe'}]})+'\n');
  const split=message.indexOf(Buffer.from('任意'))+1;child.stdout.write(message.subarray(0,split));child.stdout.write(message.subarray(split));
  assert.equal(audio.getSources()[0].name,'任意音乐应用.exe');assert.equal(states.at(-1).ready,true);
  const copy=audio.getState();copy.sources.length=0;assert.equal(audio.getSources().length,1);
  assert.throws(()=>audio.selectSource({pid:123}));
  audio.selectSource({pid:123,createdAt:45});assert.deepEqual(commands.at(-1),{action:'source',pid:123,createdAt:45});
  audio.selectSource({pid:null});assert.deepEqual(commands.at(-1),{action:'source',pid:null});
  const first=audio.stop();assert.equal(audio.stop(),first);assert.throws(()=>audio.start(),/restoration/);await first;
  assert.equal(audio.getState().phase,'stopped');assert.deepEqual(commands.at(-1),{action:'stop'});
  const restored=createAudioService({runtimeDir:runtime,pythonPath:fakePython});assert.equal(restored.getState().vocalGain,.25);assert.equal(restored.getState().instrumentalGain,.8);
});

test('busy state survives worker exit and the 40 GiB guard prevents capture and writes',async(t)=>{
  const setFreeGiB=mockStorage(t);
  const audio=createAudioService({runtimeDir:runtime,pythonPath:fakePython});audio.start();
  child.stdout.write(JSON.stringify({active:false,ready:false,phase:'busy',error:'Another audio service owns the route'})+'\n');child.emit('exit',2);child.emit('close',2);
  assert.equal(audio.getState().phase,'busy');await audio.stop();
  const guarded=createAudioService({runtimeDir:runtime,pythonPath:fakePython});
  const launches=spawnCount,preferences=fs.readFileSync(path.join(runtime,'gains.json'),'utf8');
  setFreeGiB(41);assert.doesNotThrow(()=>guarded.checkStorage());
  for(const freeGiB of [40,39]){
    setFreeGiB(freeGiB);
    assert.throws(()=>guarded.start(),/40 GiB free-space/);
    assert.throws(()=>guarded.setGains({vocalGain:1,instrumentalGain:0}),/40 GiB free-space/);
    assert.equal(spawnCount,launches);
    assert.equal(fs.readFileSync(path.join(runtime,'gains.json'),'utf8'),preferences);
  }
  await guarded.stop();
});
