/* Recorder event flow without hardware or a browser; server responses are injected. */
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../static/record.js'),'utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture({secure=true,lost=false,offline=false,denied=false,microphone=true,recording=true,expired=false}={}){
 const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,{value:id==='language'?'ko':'',disabled:false,checked:false,hidden:false,textContent:''});return elements.get(id);};
 let record,accepted=false,postCount=0,micCount=0,stopped=0;const uploads=[];let clock=0,tick;
 const job={id:'TEST-AUDIO',patient_id:'DEMO-101',visit_date:'2026-11-15',status:'queued',stage:'전사 대기',progress:0};
 const handlers={},document={getElementById:get,addEventListener:(name,fn)=>handlers[name]=fn,hidden:false};
 class Recorder{
  static isTypeSupported(t){return t==='audio/mp4';}
  constructor(media,opts){this.mimeType=opts.mimeType;this.state='inactive';record=this;}
  start(){this.state='recording';}
  stop(){this.state='inactive';this.ondataavailable({data:new Blob(['test audio bytes'],{type:'audio/mp4'})});this.onstop();}
 }
 const request=async(url,opts)=>{
  if(opts.method==='POST'){
   postCount++;uploads.push(opts);if(offline)throw Error('network offline');accepted=true;if(lost)throw Error('response lost');return {job};
  }
  if(expired)throw Object.assign(Error('녹음 연결이 만료됐습니다.'),{status:410});
  return {patient_id:'DEMO-101',visit_date:'2026-11-15',job:accepted?job:null};
 };
 const context={document,window:{isSecureContext:secure,MediaRecorder:recording?Recorder:undefined,ClinicWorkspaceHTTP:{requestJSON:request},addEventListener:()=>{}},
  MediaRecorder:Recorder,location:{search:'?token=test-only-token'},navigator:{mediaDevices:{getUserMedia:async()=>{micCount++;if(denied)throw Object.assign(Error('permission denied'),{name:'NotAllowedError'});return {getTracks:()=>[{stop:()=>stopped++}]};}}},
  URL:{createObjectURL:()=> 'blob:test-only',revokeObjectURL:()=>{}},URLSearchParams,FormData,Blob,Date:{now:()=>clock},
  setTimeout:()=>1,clearTimeout:()=>{},setInterval:fn=>{tick=fn;return 1;},clearInterval:()=>{}};
 if(!microphone)context.navigator.mediaDevices=undefined;
 vm.runInNewContext(script,context);await settle();
 assert.equal(get('start').disabled,true,'patient confirmation required');
 get('confirmPatient').checked=true;get('confirmPatient').onchange();
 return {get,uploads,handlers,job,async advance(seconds){clock+=seconds*1000;tick?.();await settle();await settle();},async start(){await get('start').onclick();await settle();},async stop(){get('stop').onclick();await settle();await settle();},stats:()=>({postCount,micCount,stopped,accepted}),record:()=>record,online(){offline=false;},renew(){expired=false;}};
}
(async()=>{
 let f=await fixture();assert.equal(f.get('start').disabled,false);await f.start();assert.equal(f.record().state,'recording');await f.stop();
 assert.equal(f.stats().postCount,1);assert.equal(f.uploads[0].body.get('language'),'ko');assert.equal(f.uploads[0].body.get('file').name,'tablet-recording.m4a');assert.equal(f.stats().stopped,1);assert.equal(f.get('retryUpload').hidden,true);
 console.log('PASS recording stop automatically posts audio once, without file selection');
 f=await fixture({lost:true});await f.start();await f.stop();assert.equal(f.stats().postCount,1);assert.equal(f.get('retryUpload').hidden,true);assert.match(f.get('status').textContent,/전사 대기/);
 console.log('PASS lost upload response resolves original accepted job without duplicate POST');
 f=await fixture({offline:true});await f.start();await f.stop();assert.equal(f.get('retryUpload').hidden,false);assert.equal(f.get('saveAudio').hidden,false);const size=f.uploads[0].body.get('file').size;f.online();await f.get('retryUpload').onclick();assert.equal(f.stats().postCount,2);assert.equal(f.uploads[1].body.get('file').size,size);
 console.log('PASS network failure retains recording for explicit retry and local save');
 f=await fixture({secure:false});assert.equal(f.get('confirmPatient').disabled,false);assert.equal(f.get('start').disabled,true);await f.start();assert.equal(f.stats().micCount,0);assert.match(f.get('status').textContent,/HTTPS/);
 console.log('PASS HTTP allows patient confirmation and explains why recording stays blocked');
 for(const options of [{microphone:false},{recording:false}]){
  f=await fixture(options);assert.equal(f.get('confirmPatient').disabled,false);assert.equal(f.get('start').disabled,true);await f.start();assert.equal(f.stats().micCount,0);assert.match(f.get('status').textContent,/Safari/);
 }
 console.log('PASS unavailable microphone/recorder never locks patient confirmation or requests capture');
 f=await fixture({expired:true});assert.equal(f.get('confirmPatient').disabled,true);assert.match(f.get('status').textContent,/만료/);f.renew();await f.get('refreshConnection').onclick();await settle();assert.equal(f.get('confirmPatient').disabled,false);assert.equal(f.get('start').disabled,false);
 console.log('PASS expired link stays blocked and can recheck connection state');
 f=await fixture({denied:true});await f.start();assert.equal(f.stats().postCount,0);assert.match(f.get('status').textContent,/마이크 사용을 허용/);
 console.log('PASS microphone denial never creates an upload');
 f=await fixture();await Promise.all([f.start(),f.start()]);assert.equal(f.stats().micCount,1);await f.stop();assert.equal(f.stats().postCount,1);
 console.log('PASS repeated start actions create only one microphone request and recording');
 f=await fixture();await f.start();await f.advance(301);assert.equal(f.record().state,'recording');assert.equal(f.stats().postCount,0);await f.advance(1494);assert.equal(f.stats().postCount,1);
 console.log('PASS recording continues past five minutes and automatically sends at the thirty-minute boundary');
})().catch(e=>{console.error(e);process.exitCode=1;});
