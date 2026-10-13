'use strict';
// Import this adapter from the host's real Electron main process. The host
// supplies its BrowserWindow and decides the writable app-local runtime path.
const path=require('node:path');
const {app,ipcMain}=require('electron');
const {createAudioService}=require('vocal-music-core');
module.exports=function attachAudio(window,runtimeDir){
  const entry=window.webContents.getURL();
  const audio=createAudioService({runtimeDir:path.resolve(runtimeDir)});
  function trusted(event){if(event.sender!==window.webContents||event.senderFrame!==window.webContents.mainFrame||event.senderFrame.url!==entry)throw Error('Untrusted audio request');}
  audio.on('state',state=>{if(!window.isDestroyed())window.webContents.send('music-core:state',state);});
  for(const [channel,operation] of Object.entries({state:()=>audio.getState(),sources:()=>audio.getSources(),start:()=>audio.start(),stop:()=>audio.stop(),retry:()=>audio.retry(),gains:value=>audio.setGains(value),source:value=>audio.selectSource(value),doctor:()=>audio.diagnose()})){
    ipcMain.handle(`music-core:${channel}`,(event,value)=>{trusted(event);return operation(value);});
  }
  let stopped=false,closing=false;
  async function close(){if(closing)return;closing=true;await audio.stop();stopped=true;app.quit();}
  window.on('close',event=>{if(!stopped){event.preventDefault();close().catch(error=>{closing=false;console.error(error);});}});
  app.on('before-quit',event=>{if(!stopped){event.preventDefault();close().catch(error=>{closing=false;console.error(error);});}});
  return audio;
};
