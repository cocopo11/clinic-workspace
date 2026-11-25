import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {handle} from '../lib/core.mjs';
import {COOKIE,load,seal} from '../lib/session.mjs';
import {createRegistry} from '../lib/registry.mjs';
import {remoteScript} from '../lib/remote.mjs';
const origin='https://goodmona.example',tunnel='https://test-pc.trycloudflare.com',token='ab'.repeat(32),env={CONFIG_KEY:'cd'.repeat(32)};
const code=(v={})=>'GM1.'+Buffer.from(JSON.stringify({origin:tunnel,token,site:origin,...v})).toString('base64url');
const req=(path,method='GET',data,session='')=>new Request(origin+path,{method,headers:{origin,cookie:session,...(data?{'content-type':'application/json'}:{})},...(data?{body:JSON.stringify(data)}:{})});
const probe=async()=>Response.json({service:'goodmona-interview-gateway',site_origin:origin,app_version:'FIX3',chunked_upload:true});
function fixture(){const objects=new Map(),options=[];const blob={async get(path,opts){options.push(opts);const v=objects.get(path);return v?{statusCode:200,stream:new Response(v).body}:null;},async put(path,v,opts){options.push(opts);if(objects.has(path)&&!opts.allowOverwrite)throw Error('exists');objects.set(path,v);}};return {objects,options,blob,store:createRegistry(env,blob)};}
async function legacy(){return COOKIE+'='+await seal({origin:tunnel,token},env.CONFIG_KEY,origin);}
async function migrate(f){const r=await handle(req('/__api/adopt','POST',{},await legacy()),env,probe,f.store);assert.equal(r.status,200,await r.clone().text());return r.headers.get('set-cookie').split(';')[0];}

test('mobile start screen has no pairing input and its script parses',async()=>{const f=fixture(),r=await handle(req('/mobile'),env,probe,f.store),html=await r.text();assert.match(html,/코드를 입력할 필요가 없습니다/);assert(!html.includes('<textarea'));for(const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);assert.equal((await handle(req('/api/me'),env,probe,f.store)).status,503);});
test('one paired PC enables an iPhone and iPad with no pairing cookies',async()=>{const f=fixture();await migrate(f);let seen;const r=await handle(req('/api/me'),env,async(url,opts)=>{seen={url,opts};return Response.json({detail:'로그인이 필요합니다.'},{status:401});},f.store);assert.equal(r.status,401);assert.equal(seen.url,tunnel+'/api/me');assert.equal(seen.opts.headers.get('X-Goodmona-Gateway'),token);const tablet=await handle(req('/record'),env,async()=>new Response('<head></head><body>record</body>',{headers:{'content-type':'text/html'}}),f.store);assert.match(await tablet.text(),/ClinicWorkspaceRemoteMode/);});
test('restarted PC can migrate using its fresh tunnel without probing the dead old address',async()=>{const f=fixture(),next='https://new-pc.trycloudflare.com',seen=[];const r=await handle(req('/__api/adopt','POST',{code:code({origin:next})},await legacy()),env,async(url)=>{seen.push(url);assert(url.startsWith(next));return probe();},f.store);assert.equal(r.status,200,await r.clone().text());assert.equal((await createRegistry(env,f.blob).load(origin)).origin,next);assert.equal(seen.length,1);});
test('a fresh code without an authenticated PC cookie cannot claim the site',async()=>{const f=fixture();let called=false;const r=await handle(req('/__api/adopt','POST',{code:code()}),env,async()=>{called=true;return probe();},f.store);assert.equal(r.status,403);assert(!called);assert.equal(f.objects.size,0);});
test('metadata remains encrypted and loads in a fresh function instance',async()=>{const f=fixture();await migrate(f);const raw=[...f.objects.values()][0];assert(!raw.includes(token));assert(!raw.includes(tunnel));assert(f.options.every(o=>o.access==='private'));assert(f.options.some(o=>o.useCache===false));const c=await createRegistry(env,f.blob).load(origin);assert.equal(c.token,token);assert.match(c.managerHash,/^[a-f0-9]{64}$/);});
test('anonymous browsers cannot replace or delete the shared connection',async()=>{const f=fixture();await migrate(f);const old=[...f.objects.values()][0];assert.equal((await handle(req('/__api/connect','POST',{code:code()}),env,probe,f.store)).status,403);assert.equal((await handle(req('/__api/connect','DELETE',{}),env,probe,f.store)).status,405);assert.equal([...f.objects.values()][0],old);});
test('only the established manager can update a restarted PC tunnel',async()=>{const f=fixture(),manager=await migrate(f),next='https://new-pc.trycloudflare.com';const r=await handle(req('/__api/connect','POST',{code:code({origin:next})},manager),env,probe,f.store);assert.equal(r.status,200);assert.equal((await createRegistry(env,f.blob).load(origin)).origin,next);assert.match(r.headers.get('set-cookie'),/Secure; HttpOnly; SameSite=Strict/);});
test('unsafe destinations and cross-origin writes are refused',async()=>{const f=fixture(),manager=await migrate(f);for(const target of ['http://127.0.0.1:11434','https://bad.example','https://test.trycloudflare.com/x','https://test.trycloudflare.com@bad.example']){let called=false;const r=await handle(req('/__api/connect','POST',{code:code({origin:target})},manager),env,async()=>{called=true;return probe();},f.store);assert.equal(r.status,400);assert(!called);}const request=req('/__api/connect','POST',{code:code()},manager);request.headers.set('origin','https://bad.example');assert.equal((await handle(request,env,probe,f.store)).status,403);});
test('initial claim cannot overwrite another manager in a race',async()=>{const f=fixture(),c={origin:tunnel,token,managerHash:'f'.repeat(64),managerExpires:Date.now()+10000};await f.store.claim(origin,c);await assert.rejects(f.store.claim(origin,{...c,managerHash:'a'.repeat(64)}));assert.equal((await createRegistry(env,f.blob).load(origin)).managerHash,c.managerHash);});
test('tampered, expired and copied storage ciphertext cannot grant management',async()=>{const f=fixture();assert.equal((await handle(req('/__api/adopt','POST',{},COOKIE+'=forged'),env,probe,f.store)).status,403);const expired=await seal({origin:tunnel,token},env.CONFIG_KEY,origin,-1000);assert.equal((await handle(req('/__api/adopt','POST',{},COOKIE+'='+expired),env,probe,f.store)).status,403);await migrate(f);assert.equal(await load(req('/','GET',null,COOKIE+'='+[...f.objects.values()][0]),env.CONFIG_KEY),null);});
test('staff sessions and CSRF survive the relay while manager cookies stay private',async()=>{const f=fixture(),manager=await migrate(f);let seen;const r=await handle(req('/api/me','GET',null,manager+'; gm_session=staff; platform_cookie=private'),env,async(url,opts)=>{seen={url,opts};return new Response('{}',{headers:{'content-type':'application/json','set-cookie':'gm_session=new; HttpOnly; SameSite=Strict'}});},f.store);assert.equal(seen.opts.headers.get('cookie'),'gm_session=staff');assert.match(r.headers.get('set-cookie'),/Secure/);});
test('mobile origin and existing upload/polling adapters are retained',async()=>{const f=fixture();await migrate(f);let r=await handle(req('/api/status'),env,async()=>Response.json({public_base_url:'https://192.168.1.242:8443'}),f.store);assert.equal((await r.json()).public_base_url,origin);r=await handle(req('/record'),env,async()=>new Response('<head></head><body>test</body>',{headers:{'content-type':'text/html'}}),f.store);const html=await r.text();assert.match(html,/__upload\/init/);assert.match(html,/X-Goodmona-Remote-Job/);for(const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g))new vm.Script(m[1]);});
test('offline PC and unavailable metadata have honest errors',async()=>{const f=fixture();await migrate(f);const r=await handle(req('/__api/status'),env,async()=>{throw Error('offline');},f.store);assert.deepEqual(await r.json(),{configured:true,connected:false});assert.equal((await handle(req('/api/me'),env,probe,{async load(){throw Error('unavailable');}})).status,502);});
test('browser adapter splits a real 6 MB multipart request and forwards its exact bytes',async()=>{
 const calls=[];let expected;const encoded=[];
 const native=async(input,opts)=>{
  const request=input instanceof Request?input:new Request(new URL(input,origin),opts);calls.push(request.url);
  if(request.url.endsWith('/__upload/init')){const spec=await request.json();assert(spec.size>6*1024*1024);assert.match(spec.content_type,/boundary=/);return Response.json({id:'fixture'});}
  if(request.method==='PUT'){encoded.push(Buffer.from(await request.arrayBuffer()));return Response.json({ok:true});}
  if(request.url.endsWith('/commit'))return Response.json({uploaded:true});
  throw Error('Unexpected request '+request.url);
 };
 const window={fetch:native};vm.runInNewContext(remoteScript,{window,Request,Response,URL,location:{href:origin+'/',origin},setTimeout,clearTimeout,DOMException});
 const form=new FormData();form.append('file',new Blob([new Uint8Array(6*1024*1024).fill(7)]),'photo.jpg');
 const request=new Request(origin+'/api/patients/DEMO-101/photos',{method:'POST',headers:{'X-CSRF-Token':'csrf'},body:form});
 expected=Buffer.from(await request.clone().arrayBuffer());const r=await window.fetch(request);assert.equal((await r.json()).uploaded,true);
 assert.deepEqual(Buffer.concat(encoded),expected);assert(encoded.every(p=>p.length<=1024*1024));assert(!calls.some(c=>c.includes('/api/patients/')));
});


test('a new public domain uses the established encrypted PC connection binding',async()=>{
 const f=fixture();await migrate(f);
 const site='https://clinic-workspace-demo.vercel.app';
 const mappedEnv={...env,CONNECTION_SITE_ORIGIN:origin};
 const connections=createRegistry(mappedEnv,f.blob);
 const config=await connections.load(site);
 assert.equal(config.token,token);
 assert.equal(config.origin,tunnel);
 const result=await handle(new Request(site+'/__api/status'),mappedEnv,probe,connections);
 assert.equal(result.status,200);
 assert.equal((await result.json()).connected,true);
 const encoded=[...f.objects.values()][0];
 assert(!encoded.includes(token));
});
