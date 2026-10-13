import {EventEmitter} from 'node:events';
export type StemGains={vocalGain:number;instrumentalGain:number};
export type AudioSource={pid:number;createdAt:number;name:string;[key:string]:unknown};
export type AudioState=StemGains & {active:boolean;ready:boolean;phase:string;error?:string;selectionError?:string;sources?:AudioSource[];currentSource?:AudioSource|null;telemetry?:Record<string,unknown>;[key:string]:unknown};
export type AudioOptions={runtimeDir:string;engineDir?:string;pythonPath?:string;backend?:'cuda'|'dml';minFreeGiB?:number};
export class AudioService extends EventEmitter {
  constructor(options:AudioOptions);
  start():AudioState;
  getState():AudioState;
  getSources():AudioSource[];
  setGains(value:StemGains):StemGains;
  selectSource(value:{pid:null}|{pid:number;createdAt:number}):void;
  retry():void;
  stop():Promise<AudioState>;
  diagnose():Promise<{ok:boolean;[key:string]:unknown}>;
  on(event:'state',listener:(state:AudioState)=>void):this;
  on(event:'warning',listener:(error:Error)=>void):this;
}
export function createAudioService(options:AudioOptions):AudioService;
