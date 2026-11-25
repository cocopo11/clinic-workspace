export const remoteScript=`(()=>{
const native=window.fetch.bind(window),CHUNK=1024*1024;
async function large(request){
  const body=await request.clone().blob();
  if(body.size<=2*CHUNK)return native(request);
  const auth={'Content-Type':'application/json'};
  const csrf=request.headers.get('x-csrf-token');if(csrf)auth['X-CSRF-Token']=csrf;
  const u=new URL(request.url),signal=request.signal;
  const r=await native('/__upload/init',{method:'POST',credentials:'same-origin',signal,headers:auth,body:JSON.stringify({path:u.pathname+u.search,method:request.method,content_type:request.headers.get('content-type'),size:body.size})});
  if(!r.ok)return r;
  const {id}=await r.json();let complete=false;
  try{
    for(let offset=0;offset<body.size;offset+=CHUNK){
      const part=await native('/__upload/'+id+'?offset='+offset,{method:'PUT',credentials:'same-origin',signal,headers:{...auth,'Content-Type':'application/octet-stream'},body:body.slice(offset,offset+CHUNK)});
      if(!part.ok)return part;
    }
    const result=await native('/__upload/'+id+'/commit',{method:'POST',credentials:'same-origin',signal,headers:auth});complete=true;return result;
  }finally{if(!complete)native('/__upload/'+id,{method:'DELETE',credentials:'same-origin',headers:auth}).catch(()=>{});}
}
window.fetch=async(input,options={})=>{
  const request=new Request(input instanceof Request?input:new URL(input,location.href),options),u=new URL(request.url);
  if(u.origin!==location.origin)return native(request);
  const local=new Request(request,{credentials:'same-origin'});
  let r=local.body&&!u.pathname.startsWith('/__')?await large(local):await native(local);
  const id=r.headers.get('X-Goodmona-Remote-Job');if(!id)return r;
  for(let i=0;i<600;i++){
    if(request.signal.aborted)throw new DOMException('Aborted','AbortError');
    await new Promise(resolve=>setTimeout(resolve,1500));
    r=await native('/__remote/jobs/'+encodeURIComponent(id),{credentials:'same-origin',cache:'no-store',signal:request.signal});
    if(r.status!==202)return r;
  }
  throw Error('AI 응답 대기 시간이 지났습니다. 저장된 결과 목록을 확인하세요.');
};
class Poll{
  constructor(url){this.url=url.replace(/\\/events$/,'/photos');this.listeners={};this.closed=false;this.signature=null;this.tick();}
  addEventListener(t,f){(this.listeners[t]??=[]).push(f);}emit(t){for(const f of this.listeners[t]||[])f({type:t});}
  async tick(){if(this.closed)return;try{const r=await window.fetch(this.url,{cache:'no-store'});if([401,403,404].includes(r.status)){this.close();this.onerror?.(Error('사진 연결이 만료되었습니다. 다시 로그인하세요.'));return;}if(!r.ok)throw Error('사진 연결 확인');const s=JSON.stringify(await r.json());if(!this.closed){if(this.signature===null)this.emit('ready');else if(s!==this.signature)this.emit('photo');this.signature=s;}}catch(e){if(!this.closed)this.onerror?.(e);}finally{if(!this.closed)this.timer=setTimeout(()=>this.tick(),2000);}}
  close(){this.closed=true;clearTimeout(this.timer);}
}
window.EventSource=Poll;window.ClinicWorkspaceRemoteMode=true;
})();`;
