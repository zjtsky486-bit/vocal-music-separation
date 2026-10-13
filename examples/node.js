'use strict';
const path=require('node:path');
const {createAudioService}=require('..');
const audio=createAudioService({runtimeDir:path.resolve(process.env.AUDIO_DATA_DIR||'data/runtime'),backend:process.env.AUDIO_BACKEND||'cuda',...(process.env.AUDIO_PYTHON?{pythonPath:process.env.AUDIO_PYTHON}:{})});
audio.on('state',state=>console.log(JSON.stringify(state)));
audio.on('warning',error=>console.error(error.message));
let closing=false;
async function close(){if(closing)return;closing=true;await audio.stop();process.exit(0);}
process.on('SIGINT',close);process.on('SIGTERM',close);
(async()=>{const report=await audio.diagnose();if(!report.ok){console.error(JSON.stringify(report,null,2));process.exitCode=1;return;}audio.setGains({vocalGain:1,instrumentalGain:1});audio.start();})().catch(error=>{console.error(error);process.exitCode=1;});
