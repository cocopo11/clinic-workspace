import {randomUUID} from 'node:crypto';

export const slowMethods=new Set(['generate_blog','generate','embed','audio_finish','youtube']);
const fail=(detail,code,status=503)=>Response.json({detail,code},{status});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const interrupted=r=>[502,503,504].includes(r.status)&&!(r.headers.get('content-type')||'').includes('application/json');

export async function rpcResponse(response){
 if(response.status!==200||!(response.headers.get('content-type')||'').includes('json'))return response;
 let data;try{data=await response.clone().json();}catch{return response;}
 if(data?._goodmona_rpc_error!==1)return response;
 if(!Number.isInteger(data.http_status)||data.http_status<500||data.http_status>599||!data.payload||typeof data.payload!=='object')return fail('PC 오류 응답의 형식이 올바르지 않습니다.','AI_PROTOCOL_ERROR');
 return Response.json(data.payload,{status:data.http_status});
}

// The PC owns model execution. Every tunnel request only submits or polls a job.
// Retrying a lost acknowledgement uses the SAME id, so it cannot run inference twice.
export async function pcInference(config,input,fetcher=fetch,{sleep=delay,now=Date.now,budgetMs=220000}={}){
 const deadline=now()+budgetMs;
 const send=async(body,timeout=10000)=>{
  const started=now();
  try{
   const transport=await fetcher(config.origin+'/__local_ai',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-Goodmona-Gateway':config.token},body:JSON.stringify(body),redirect:'manual',signal:AbortSignal.timeout(timeout)});
   const response=await rpcResponse(transport);
   if(!response.ok){
    let title='',edge='',problem;
    if((response.headers.get('content-type')||'').includes('json')){
     try{const data=await response.clone().json();problem={code:data.code,type:data.type,error_category:data.error_category,errors:data.errors?.map(x=>({code:x.code,message:x.message})).slice(0,2)};}catch{}
    }
    if((response.headers.get('content-type')||'').includes('text/html')){
     const html=await response.clone().text();
     title=(html.match(/<title>([\s\S]*?)<\/title>/i)?.[1]||'').replace(/[a-z0-9-]+(?:\.[a-z0-9-]+)+/gi,'[host]').slice(0,100);
     edge=html.replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>/gi,'').replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').replace(/[a-z0-9-]+(?:\.[a-z0-9-]+)+/gi,'[host]').replace(/(?:\d{1,3}\.){3}\d{1,3}/g,'[ip]').trim().slice(0,700);
    }
    console.warn('PC RPC HTTP',JSON.stringify({method:body.method,transport_status:transport.status,status:response.status,type:response.headers.get('content-type'),elapsed:now()-started,title,edge,problem,server:response.headers.get('server'),cache:response.headers.get('cf-cache-status')}));
   }
   return response;
  }catch(error){console.warn('PC RPC network',JSON.stringify({method:body.method,name:error.name,code:error.cause?.code||error.code,elapsed:now()-started}));throw error;}
 };
 const state=await send({method:'status'},5000);
 if(!state.ok)return state;
 let capability;
 try{capability=await state.json();}catch{return fail('PC 연결 프로그램의 상태 응답 형식이 올바르지 않습니다.','AI_PROTOCOL_ERROR');}
 if(capability.rpc_protocol!==2){
  if(input.method==='generate_blog')return fail('원고 오류 전달을 수정한 PC 연결 프로그램 v2.0.2를 적용하고 다시 실행하세요.','AI_UPDATE_REQUIRED');
  return send(input,240000); // Existing short operations on an older connector.
 }
 if(input.method==='generate_blog'&&capability.rpc_error_envelope!==1)return fail('원고 오류 전달을 수정한 PC 연결 프로그램 v2.0.2를 적용하고 다시 실행하세요.','AI_UPDATE_REQUIRED');
 const id=randomUUID().replaceAll('-','');let completed=false;
 const submission={...input,async:true,request_id:id};
 try{
  let result;
  for(let attempt=0;attempt<2;attempt++){
   try{result=await send(submission);if(!interrupted(result)||attempt)break;}
   catch(error){if(attempt)throw error;}
  }
  if(result.status!==202)return result;
  const receipt=await result.json();
  if(receipt.job_id!==id)return fail('PC가 다른 AI 작업 번호를 반환했습니다. 연결 프로그램을 다시 실행하세요.','AI_PROTOCOL_ERROR');
  let failures=0;
  while(now()<deadline){
   await sleep(700);
   if(now()>=deadline)break;
   try{
    result=await send({method:'rpc_poll',arguments:{id}},Math.max(1,Math.min(10000,deadline-now())));
    if(interrupted(result))throw Error('tunnel interrupted');
    if(result.status===202){await result.json();failures=0;continue;}
    failures=0;
    completed=true;return result;
   }catch{
    if(failures===0){
     try{const health=await send({method:'status'},5000);const data=health.ok?await health.json():{};console.warn('PC RPC health during job',JSON.stringify({status:health.status,protocol:data.rpc_protocol,chat_ready:data.chat_ready}));}catch{}
    }
    if(++failures>=3)return fail('AI 작업 상태 연결이 끊겼습니다. PC 연결 창이 실행 중인지 확인하세요.','AI_CONNECTION_INTERRUPTED');
   }
  }
  return fail('AI 작업 시간이 초과되었습니다. 선택 자료의 범위를 줄여 다시 시도하세요.','AI_TIMEOUT');
 }finally{
  if(!completed){try{await send({method:'rpc_abort',arguments:{id}},3000);}catch{}}
 }
}
