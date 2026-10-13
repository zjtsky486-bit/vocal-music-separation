'use strict';
const path=require('node:path');
const {createAudioService}=require('..');
createAudioService({runtimeDir:path.resolve('data/doctor'),backend:process.env.AUDIO_BACKEND||'cuda',...(process.env.AUDIO_PYTHON?{pythonPath:process.env.AUDIO_PYTHON}:{})}).diagnose().then(report=>{console.log(JSON.stringify(report,null,2));process.exitCode=report.ok?0:1;}).catch(error=>{console.error(error.message);process.exitCode=1;});
