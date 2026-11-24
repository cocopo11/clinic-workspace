import test from 'node:test';
import assert from 'node:assert/strict';
import {pcInference} from '../lib/pc-inference.mjs';

const config={origin:'https://pc.example',token:'TEST-ONLY'};
const input={method:'generate_blog',arguments:{question:'블로그 포스팅'}};
function clock(budgetMs=10000){let time=0;return{now:()=>time,sleep:async ms=>{time+=ms;},budgetMs};}

test('an origin 502 survives a proxy that replaces HTTP error bodies',async()=>{
 let modelCalls=0;
 const response=await pcInference(config,input,async(url,options)=>{
  const b=JSON.parse(options.body);let raw;
  if(b.method==='status')raw=Response.json({rpc_protocol:2,rpc_error_envelope:1});
  else if(b.method==='generate_blog'){modelCalls++;raw=Response.json({job_id:b.request_id},{status:202});}
  else raw=Response.json({_goodmona_rpc_error:1,http_status:502,payload:{detail:'원문 표시 오류',code:'GEO_EVIDENCE_INVALID'}});
  return raw.status===502?new Response('<h1>Bad gateway</h1>',{status:502,headers:{'content-type':'text/html'}}):raw;
 },clock());
 assert.equal(response.status,502);assert.equal((await response.json()).code,'GEO_EVIDENCE_INVALID');assert.equal(modelCalls,1);
});

test('older async PCs get the precise error-transport update message',async()=>{
 let calls=0;
 const response=await pcInference(config,input,async()=>{calls++;return Response.json({rpc_protocol:2});},clock());
 const error=await response.json();assert.equal(error.code,'AI_UPDATE_REQUIRED');assert.match(error.detail,/2\.0\.2/);assert.equal(calls,1);
});

test('long model generation uses short submission and poll requests',async()=>{
 let begins=0,polls=0;const methods=[];
 const response=await pcInference(config,input,async(url,options)=>{
  assert.equal(url,config.origin+'/__local_ai');assert.equal(options.headers['X-Goodmona-Gateway'],config.token);
  const body=JSON.parse(options.body);methods.push(body.method);
  if(body.method==='status')return Response.json({rpc_protocol:2,rpc_error_envelope:1});
  if(body.method==='generate_blog'){begins++;assert.equal(body.async,true);return Response.json({job_id:body.request_id},{status:202});}
  if(body.method==='rpc_poll'){polls++;return polls<3?Response.json({pending:true},{status:202}):Response.json({answer:'완성된 전체 글'});}
  throw Error(body.method);
 },clock());
 assert.equal(response.status,200);assert.equal((await response.json()).answer,'완성된 전체 글');assert.equal(begins,1);assert.equal(polls,3);assert.ok(!methods.includes('rpc_abort'));
});

test('lost submission acknowledgement reuses the same request id',async()=>{
 const ids=[];let executions=0;const accepted=new Set();
 const response=await pcInference(config,input,async(url,options)=>{
  const b=JSON.parse(options.body);
  if(b.method==='status')return Response.json({rpc_protocol:2,rpc_error_envelope:1});
  if(b.method==='generate_blog'){
   ids.push(b.request_id);if(!accepted.has(b.request_id)){accepted.add(b.request_id);executions++;throw Error('ack lost');}
   return Response.json({job_id:b.request_id},{status:202});
  }
  return Response.json({answer:'one result'});
 },clock());
 assert.equal(response.status,200);assert.equal(ids.length,2);assert.equal(ids[0],ids[1]);assert.equal(executions,1);
});

test('a transient tunnel error during polling does not restart inference',async()=>{
 let begins=0,polls=0;
 const response=await pcInference(config,input,async(url,options)=>{
  const b=JSON.parse(options.body);
  if(b.method==='status')return Response.json({rpc_protocol:2,rpc_error_envelope:1});
  if(b.method==='generate_blog'){begins++;return Response.json({job_id:b.request_id},{status:202});}
  if(++polls===1)return new Response('Bad gateway',{status:502,headers:{'content-type':'text/html'}});
  return Response.json({answer:'result'});
 },clock());
 assert.equal(response.status,200);assert.equal(begins,1);assert.equal(polls,2);
});

test('model refusals retain their actual error code instead of becoming offline errors',async()=>{
 const response=await pcInference(config,input,async(url,options)=>{
  const b=JSON.parse(options.body);
  if(b.method==='status')return Response.json({rpc_protocol:2,rpc_error_envelope:1});
  if(b.method==='generate_blog')return Response.json({job_id:b.request_id},{status:202});
  return Response.json({detail:'근거 필요',code:'GEO_INSUFFICIENT_EVIDENCE'},{status:422});
 },clock());
 assert.equal(response.status,422);assert.equal((await response.json()).code,'GEO_INSUFFICIENT_EVIDENCE');
});

test('deadline cancels the PC job and returns an explicit timeout',async()=>{
 let aborted=false;
 const response=await pcInference(config,input,async(url,options)=>{
  const b=JSON.parse(options.body);
  if(b.method==='status')return Response.json({rpc_protocol:2,rpc_error_envelope:1});
  if(b.method==='generate_blog')return Response.json({job_id:b.request_id},{status:202});
  if(b.method==='rpc_abort'){aborted=true;return Response.json({ok:true});}
  return Response.json({pending:true},{status:202});
 },clock(1000));
 assert.equal((await response.json()).code,'AI_TIMEOUT');assert.equal(aborted,true);
});

test('older PCs get the specific update message before starting long blog generation',async()=>{
 let calls=0;
 const response=await pcInference(config,input,async()=>{calls++;return Response.json({chat_ready:true,blog_article_writer:'old'});},clock());
 assert.equal((await response.json()).code,'AI_UPDATE_REQUIRED');assert.equal(calls,1);
});

test('other short operations keep compatibility with an older PC',async()=>{
 const methods=[];
 const response=await pcInference(config,{method:'generate',arguments:{}},async(url,options)=>{
  const b=JSON.parse(options.body);methods.push(b.method);
  return Response.json(b.method==='status'?{}:{answer:'기존 응답'});
 },clock());
 assert.equal((await response.json()).answer,'기존 응답');assert.deepEqual(methods,['status','generate']);
});
