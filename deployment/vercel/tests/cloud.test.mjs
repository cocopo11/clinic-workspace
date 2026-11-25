import test from 'node:test';
import assert from 'node:assert/strict';
import {cloudHandle} from '../lib/cloud.mjs';

const origin='https://goodmona-interview.vercel.app',env={DATABASE_URL:'TEST-ONLY',CLOUD_BACKEND_KEY:'TEST-ONLY-PRIVATE-KEY'};
const noPC={async load(){return null;}},legacy=()=>Response.json({legacy:true});
const req=(path,options={})=>new Request(origin+path,options);

test('ordinary pages and APIs reach the cloud even without a PC configuration',async()=>{
 let called;
 const fetcher=async(url,opts)=>{called={url:String(url),opts};return Response.json({user:'staff'});};
 const response=await cloudHandle(req('/api/portal'),env,legacy,fetcher,noPC);
 assert.equal(response.status,200);assert.equal(called.url,origin+'/__runtime/api/portal');
 assert.equal(called.opts.headers.get('x-goodmona-backend'),env.CLOUD_BACKEND_KEY);
 assert.equal(called.opts.headers.get('x-goodmona-route'),'/api/portal');
 assert.equal(response.headers.get('cache-control'),'no-store');
});
test('HTML keeps mobile polling and bounded upload adapter independently of the PC',async()=>{
 const r=await cloudHandle(req('/'),env,legacy,async(url,options)=>{assert.equal(String(url),origin+'/__runtime/__root');assert.equal(options.headers.get('x-goodmona-route'),'/');return new Response('<head></head><body>workspace</body>',{headers:{'content-type':'text/html'}});},noPC);
 const html=await r.text();assert.match(html,/ClinicWorkspaceRemoteMode/);assert.match(html,/클라우드 업무 서버/);
});
test('offline inference reports 503 while ordinary cloud functions remain available',async()=>{
 const r=await cloudHandle(req('/__cloud/infer',{method:'POST',headers:{'x-goodmona-backend':env.CLOUD_BACKEND_KEY,'Content-Type':'application/json'},body:JSON.stringify({method:'status'})}),env,legacy,()=>{throw Error('must not call PC');},noPC);
 assert.equal(r.status,503);assert.equal((await r.json()).code,'AI_OFFLINE');
});
test('blog output truncation remains an editorial error instead of a false offline error',async()=>{
 const connections={async load(){return{origin:'https://pc.example',token:'TEST-GATEWAY'};}};
 const r=await cloudHandle(req('/__cloud/infer',{method:'POST',headers:{'x-goodmona-backend':env.CLOUD_BACKEND_KEY,'content-type':'application/json'},body:JSON.stringify({method:'generate_blog'})}),env,legacy,async()=>Response.json({code:'GEO_OUTPUT_TRUNCATED',detail:'원고를 소제목별로 나누어 작성하세요.'},{status:502}),connections);
 assert.equal(r.status,502);assert.equal((await r.json()).code,'GEO_OUTPUT_TRUNCATED');
});
test('private bridge, internal routes and cross-origin writes reject public access',async()=>{
 const never=()=>{throw Error('unexpected network');};
 assert.equal((await cloudHandle(req('/__cloud/infer',{method:'POST',body:'{}'}),env,legacy,never,noPC)).status,401);
 assert.equal((await cloudHandle(req('/__cloud/infer',{method:'POST',headers:{'x-goodmona-backend':'é'.repeat(env.CLOUD_BACKEND_KEY.length)},body:'{}'}),env,legacy,never,noPC)).status,401);
 assert.equal((await cloudHandle(req('/__runtime/api/me'),env,legacy,never,noPC)).status,404);
 assert.equal((await cloudHandle(req('/api/notices',{method:'POST',headers:{Origin:'https://other.example'},body:'{}'}),env,legacy,never,noPC)).status,403);
});
test('cloud forwards staff cookies and CSRF, preserves response cookies and uploads',async()=>{
 let seen;
 const r=await cloudHandle(req('/api/notices',{method:'POST',headers:{origin,cookie:'gm_session=test','X-CSRF-Token':'csrf','content-type':'application/json'},body:'{"title":"test"}'}),env,legacy,async(url,opts)=>{seen=opts;return Response.json({ok:true},{headers:{'Set-Cookie':'gm_session=next; Secure; HttpOnly'}});},noPC);
 assert.equal(seen.headers.get('cookie'),'gm_session=test');assert.equal(seen.headers.get('x-csrf-token'),'csrf');assert.match(r.headers.get('set-cookie'),/HttpOnly/);
});
test('PC connection management remains with the original authenticated manager flow',async()=>{
 const r=await cloudHandle(req('/__connect'),env,legacy,fetch,noPC);assert.deepEqual(await r.json(),{legacy:true});
 const health=await cloudHandle(req('/__health'),env,legacy,fetch,noPC);assert.equal((await health.json()).cloud,true);
});

test('CSP uses a fresh nonce for every script and never allows inline scripts globally',async()=>{
 const make=()=>cloudHandle(req('/'),env,legacy,async()=>new Response('<head><script>window.test=1</script></head><body></body>',{headers:{'content-type':'text/html'}}),noPC);
 const a=await make(),b=await make();const policy=a.headers.get('content-security-policy');
 assert.match(policy,/script-src 'self' 'nonce-/);assert.doesNotMatch(policy,/script-src[^;]*unsafe-inline/);
 assert.notEqual(policy,b.headers.get('content-security-policy'));
 const html=await a.text();const scripts=html.match(/<script[^>]*>/g);assert.ok(scripts.every(s=>s.includes('nonce=')));
});
test('browser supplied internal client identity is replaced with edge identity',async()=>{
 let headers;
 await cloudHandle(req('/api/me',{headers:{'x-goodmona-client':'forged','x-forwarded-for':'forged','x-vercel-forwarded-for':'203.0.113.10'}}),env,legacy,async(url,opts)=>{headers=opts.headers;return Response.json({});},noPC);
 assert.equal(headers.get('x-goodmona-client'),'203.0.113.10');assert.equal(headers.get('x-forwarded-for'),null);
});
