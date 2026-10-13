'use strict';
const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('musicCore',{
  state:()=>ipcRenderer.invoke('music-core:state'),
  sources:()=>ipcRenderer.invoke('music-core:sources'),
  start:()=>ipcRenderer.invoke('music-core:start'),
  stop:()=>ipcRenderer.invoke('music-core:stop'),
  retry:()=>ipcRenderer.invoke('music-core:retry'),
  gains:value=>ipcRenderer.invoke('music-core:gains',value),
  source:value=>ipcRenderer.invoke('music-core:source',value),
  doctor:()=>ipcRenderer.invoke('music-core:doctor'),
  onState:callback=>{const listener=(_event,state)=>callback(state);ipcRenderer.on('music-core:state',listener);return()=>ipcRenderer.removeListener('music-core:state',listener);}
});
