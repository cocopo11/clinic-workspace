import {timingSafeEqual,randomBytes} from 'node:crypto';
import {registry} from './registry.mjs';
import {remoteScript} from './remote.mjs';
import {pcInference,slowMethods,rpcResponse} from './pc-inference.mjs';
import {staticAsset} from './static-assets.mjs';
import {publicPage} from './public-pages.mjs';

const headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','X-Robots-Tag':'noindex, nofollow'};
const json=(body,status=200)=>Response.json(body,{status,headers});
const secureEqual=(a,b)=>{if(!a||!b)return false;const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);};
const operations=new Set(['status','embedding_digest','embed','generate','generate_blog','youtube','youtube_status','audio_begin','audio_part','audio_finish','audio_abort','export_manifest','export_table','export_close','export_file']);

function htmlResponse(html,status,outgoing){
 const adapter='<script>'+remoteScript+'</script>';
 // Mobile pages may omit the optional closing head tag.
 if(html.includes('</head>'))html=html.replace('</head>',adapter+'</head>');
 else html=html.replace(/<(?:body|main)(?=[\s>])/i,match=>adapter+match);
 const nonce=randomBytes(18).toString('base64');
 html=html.replace(/<script(?=[\s>])/gi,'<script nonce="'+nonce+'"');
 outgoing.set('Content-Security-Policy',"default-src 'self'; script-src 'self' 'nonce-"+nonce+"'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; form-action 'self'");
 html=html.replace(/<body(?=[\s>])([^>]*)>/i,'<body$1><div class="cloud-banner" style="padding:7px 15px;background:#edf3ff;border-bottom:1px solid #d8e3fc;text-align:right;font:12px system-ui;color:#245fe5">클라우드 업무 서버 · AI만 로컬 PC <a href="/__connect" style="margin-left:12px">AI PC 연결</a></div>');
 return new Response(html,{status,headers:outgoing});
}

export async function cloudHandle(request,env,legacy,fetcher=fetch,connections=registry(env)){
 const url=new URL(request.url);
 if(url.searchParams.has('__gm_route')){url.pathname='/'+url.searchParams.get('__gm_route').replace(/^\/+/, '');url.searchParams.delete('__gm_route');request=new Request(url,request);}
 const asset=await staticAsset(request);if(asset)return asset;
 const page=await publicPage(request);
 if(page){
  const outgoing=new Headers(page.headers);
  for(const [name,value] of Object.entries(headers))outgoing.set(name,value);
  return request.method==='HEAD'?new Response(null,{headers:outgoing}):htmlResponse(await page.text(),200,outgoing);
 }
 if(url.pathname==='/__cloud/infer'){
  if(request.method!=='POST'||!secureEqual(request.headers.get('x-goodmona-backend'),env.CLOUD_BACKEND_KEY))return json({detail:'인증이 필요합니다.'},401);
  try{
   const input=await request.json();if(!operations.has(input.method))return json({detail:'지원하지 않는 AI 작업입니다.'},400);
   const config=await connections.load(url.origin);
   if(!config)return json({detail:'로컬 AI PC를 연결하세요. 일반 업무는 계속 사용할 수 있습니다.',code:'AI_OFFLINE'},503);
   const result=slowMethods.has(input.method)?await pcInference(config,input,fetcher):await rpcResponse(await fetcher(config.origin+'/__local_ai',{method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json','X-Goodmona-Gateway':config.token},body:JSON.stringify(input),redirect:'manual',signal:AbortSignal.timeout(input.method==='status'?5000:240000)}));
   if(!result.ok){
    console.warn('Local inference HTTP',input.method,result.status,result.headers.get('content-type'));
    {
     try{const error=await result.json();console.warn('Local inference response',result.status,error.code||'unknown');if(/^(GEO_[A-Z_]+|AI_[A-Z_]+)$/.test(error.code||'')&&typeof error.detail==='string')return json({detail:error.detail.slice(0,700),code:error.code},result.status);}catch{}
    }
    return json({detail:result.status===404?'PC 연결 프로그램을 최신 파일로 덮어쓴 뒤 다시 실행하세요.':'로컬 AI 응답을 받지 못했습니다. PC 연결 상태를 확인한 뒤 다시 시도하세요.',code:result.status===404?'AI_UPDATE_REQUIRED':'AI_UPSTREAM_ERROR',upstream_status:result.status},503);
   }
   return new Response(result.body,{status:200,headers:{...headers,'Content-Type':'application/json'}});
  }catch{return json({detail:'로컬 AI PC가 꺼져 있거나 연결되지 않았습니다. 일반 업무는 계속 사용할 수 있습니다.',code:'AI_OFFLINE'},503);}
 }
 if(url.pathname==='/__health')return json({ok:true,service:'goodmona-cloud',version:'2.0.0',cloud:true});
 if(url.pathname.startsWith('/__api/')||url.pathname==='/__connect'||url.pathname==='/robots.txt')return legacy(request,env,fetcher,connections);
 if(url.pathname.startsWith('/__runtime')||url.pathname.startsWith('/__cloud/'))return json({detail:'기능을 찾지 못했습니다.'},404);
 if(!env.DATABASE_URL||!env.CLOUD_BACKEND_KEY)return json({detail:'클라우드 데이터베이스 연결을 완료하지 않았습니다.',code:'CLOUD_SETUP_REQUIRED'},503);
 if(!['GET','HEAD','OPTIONS'].includes(request.method)&&request.headers.get('origin')!==url.origin)return json({detail:'이 웹사이트에서 요청하세요.'},403);
 if(url.pathname.endsWith('/events'))return json({detail:'사진 변경을 자동으로 확인합니다.'},409);
 // Keep a nonempty segment for Vercel's wildcard rewrite at the home page.
 const target=new URL('/__runtime'+(url.pathname==='/'?'/__root':url.pathname)+url.search,url.origin);
 const forwarded=new Headers();
 for(const name of ['content-type','cookie','x-csrf-token','accept','range','if-range','origin']){
  const value=request.headers.get(name);if(value)forwarded.set(name,value);
 }
 if(url.pathname==='/__maintenance'&&request.headers.has('authorization'))forwarded.set('authorization',request.headers.get('authorization'));
 forwarded.set('X-Goodmona-Backend',env.CLOUD_BACKEND_KEY);
 // Vercel overwrites this edge header; do not trust browser X-Forwarded-For.
 forwarded.set('X-Goodmona-Client',request.headers.get('x-vercel-forwarded-for')?.split(',')[0]?.trim()||'unknown');
 forwarded.set('X-Goodmona-Route',url.pathname);
 try{
  const started=performance.now();
  const response=await fetcher(target,{method:request.method,headers:forwarded,body:['GET','HEAD'].includes(request.method)?undefined:request.body,duplex:'half',redirect:'manual'});
  const outgoing=new Headers(response.headers);
  outgoing.append('Server-Timing','gateway;dur='+(performance.now()-started).toFixed(1));
  for(const name of ['content-length','content-encoding','transfer-encoding','server'])outgoing.delete(name);
  for(const [name,value] of Object.entries(headers))outgoing.set(name,value);
  if((outgoing.get('content-type')||'').includes('text/html')){
   return htmlResponse(await response.text(),response.status,outgoing);
  }
  if(['/api/status','/api/connection'].includes(url.pathname)&&response.ok){const data=await response.json();data.public_base_url=url.origin;return new Response(JSON.stringify(data),{status:200,headers:outgoing});}
  return new Response(response.body,{status:response.status,headers:outgoing});
 }catch{return json({detail:'클라우드 업무 서버에 일시적으로 연결할 수 없습니다. 잠시 후 다시 시도하세요.'},503);}
}
