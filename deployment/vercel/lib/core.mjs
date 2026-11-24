import {page} from './page.mjs';
import {remoteScript} from './remote.mjs';
import {parseCode,load} from './session.mjs';
import {registry} from './registry.mjs';
import {timingSafeEqual,randomBytes,createHash} from 'node:crypto';

const common={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','X-Robots-Tag':'noindex, nofollow'};
const json=(body,status=200,extra={})=>new Response(JSON.stringify(body),{status,headers:{...common,'Content-Type':'application/json; charset=utf-8',...extra}});
const html=()=>new Response(page,{headers:{...common,'Content-Type':'text/html; charset=utf-8'}});
const MANAGER='__Host-gm_manager',AGE=30*24*3600;
const managerValue=request=>(request.headers.get('cookie')||'').split(';').map(s=>s.trim()).find(s=>s.startsWith(MANAGER+'='))?.slice(MANAGER.length+1)||'';
const hash=value=>createHash('sha256').update(value).digest('hex');
const managerCookie=value=>`${MANAGER}=${value}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=${AGE}`;
function manages(request,config){const value=managerValue(request);return !!(config&&config.managerExpires>Date.now()&&/^[a-f0-9]{64}$/.test(value)&&/^[a-f0-9]{64}$/.test(config.managerHash||'')&&timingSafeEqual(Buffer.from(hash(value)),Buffer.from(config.managerHash)));}
async function probe(config,fetcher,site){
  const r=await fetcher(config.origin+'/__gateway/health',{headers:{'X-Goodmona-Gateway':config.token},redirect:'manual',signal:AbortSignal.timeout(15000)});
  if(!r.ok)throw Error('Offline');const data=await r.json();
  if(data.service!=='goodmona-interview-gateway'||data.site_origin!==site)throw Error('Wrong gateway');
  return data;
}
export async function handle(request,env,fetcher=fetch,connections=registry(env)){
 const u=new URL(request.url);
 // The query capture ensures the original route survives Vercel's rewrite.
 if(u.searchParams.has('__gm_route')){u.pathname='/'+u.searchParams.get('__gm_route').replace(/^\/+/, '');u.searchParams.delete('__gm_route');request=new Request(u,request);}
 try{
  if(u.pathname==='/__health')return json({ok:true,service:'goodmona-vercel-relay',version:'1.4.1'});
  if(u.pathname==='/robots.txt')return new Response('User-agent: *\nDisallow: /\n',{headers:common});
  if(u.pathname==='/__connect')return html();
  if(!['GET','HEAD','OPTIONS'].includes(request.method)&&request.headers.get('origin')!==u.origin)return json({detail:'이 웹사이트에서 요청하세요.'},403);
  const config=await connections.load(u.origin);
  if(u.pathname==='/__api/adopt'&&request.method==='POST'){
   if(config)return json({ok:true,configured:true});
   // Previously paired browser authority is required, even with a fresh code.
   const legacy=await load(request,env.CONFIG_KEY);
   if(!legacy)return json({detail:'기존에 PC를 연결했던 브라우저에서 새로고침하세요.'},403);
   const input=await request.json();let candidate=legacy;
   if(input.code){try{candidate=parseCode(input.code,u.origin);}catch{return json({detail:'PC 연결 정보를 확인하세요.'},400);}}
   const state=await probe(candidate,fetcher,u.origin);
   if(!state.chunked_upload)return json({detail:'PC 연결 창을 확인하세요.'},409);
   const manager=randomBytes(32).toString('hex');
   try{await connections.claim(u.origin,{...candidate,managerHash:hash(manager),managerExpires:Date.now()+AGE*1000});}
   catch{return json({detail:'다른 창에서 연결을 이관하고 있습니다. 잠시 후 새로고침하세요.'},409);}
   return json({ok:true},200,{'Set-Cookie':managerCookie(manager)});
  }
  if(u.pathname==='/__api/connect'){
   if(request.method!=='POST')return json({detail:'요청 방식을 확인하세요.'},405);
   if(!manages(request,config))return json({detail:'처음 PC를 연결한 브라우저에서 실행하세요. 아이폰·아이패드는 같은 웹주소에서 직원 로그인만 하면 됩니다.'},403);
   let next;try{next=parseCode((await request.json()).code,u.origin);}catch{return json({detail:'새 웹주소용 PC 연결 코드인지 확인하세요. v1.3 덮어쓰기 파일을 적용한 뒤 실행하세요.'},400);}
   const state=await probe(next,fetcher,u.origin);
   if(!state.chunked_upload)return json({detail:'PC 연결 프로그램 v1.3을 덮어쓴 뒤 다시 실행하세요.'},409);
   await connections.save(u.origin,{...next,managerHash:config.managerHash,managerExpires:Date.now()+AGE*1000});
   return json({ok:true,app_version:state.app_version},200,{'Set-Cookie':managerCookie(managerValue(request))});
  }
  if(u.pathname==='/__api/status'){
   if(!config)return json({configured:false,connected:false,can_migrate:!!(await load(request,env.CONFIG_KEY))});
   try{const s=await probe(config,fetcher,u.origin);return json({configured:true,connected:true,app_version:s.app_version,cloud_rpc:!!s.cloud_rpc,connector_version:s.connector_version||'1.x'});}catch{return json({configured:true,connected:false});}
  }
  if(u.pathname.startsWith('/__api/'))return json({detail:'기능을 찾지 못했습니다.'},404);
  if(!config)return request.method==='GET'&&!u.pathname.startsWith('/api/')?html():json({detail:'PC에서 START_INTERVIEW_Windows.bat을 실행하세요. 휴대기기에서는 코드 입력이 필요 없습니다.'},503);
  if(u.pathname.endsWith('/events'))return json({detail:'외부 시연에서는 사진 자동 확인을 사용합니다.'},409);
  const headers=new Headers();
  for(const n of ['content-type','x-csrf-token','accept','range','if-range']){const value=request.headers.get(n);if(value)headers.set(n,value);}
  const session=(request.headers.get('cookie')||'').split(';').map(x=>x.trim()).filter(x=>/^gm_session=/.test(x)).join('; ');
  if(session)headers.set('cookie',session);headers.set('X-Goodmona-Gateway',config.token);
  const response=await fetcher(config.origin+u.pathname+u.search,{method:request.method,headers,body:['GET','HEAD'].includes(request.method)?undefined:request.body,duplex:'half',redirect:'manual'});
  if(response.status>=300&&response.status<400)return json({detail:'PC 주소가 바뀌었습니다. 새 연결 코드를 등록하세요.'},502);
  const rh=new Headers(response.headers);
  for(const n of ['content-length','content-encoding','transfer-encoding','server','set-cookie'])rh.delete(n);
  for(const [name,value] of Object.entries(common))rh.set(name,value);
  for(const c of response.headers.getSetCookie?.()||[response.headers.get('set-cookie')].filter(Boolean))if(/^gm_session=/.test(c))rh.append('set-cookie',c+(/;\s*secure(?:;|$)/i.test(c)?'':'; Secure'));
  if((rh.get('content-type')||'').includes('text/html')){
   let content=await response.text();content=content.replace('</head>','<script>'+remoteScript+'</script></head>');
   content=content.replace('<body>','<body><div style="padding:7px 15px;background:#edf3ff;border-bottom:1px solid #d8e3fc;text-align:right;font:12px system-ui;color:#245fe5">포트폴리오 시연 · 로컬 PC 연결 <a href="/__connect" style="color:#245fe5;margin-left:12px">PC 연결 관리</a></div>');
   return new Response(content,{status:response.status,headers:rh});
  }
  if(['/api/status','/api/connection'].includes(u.pathname)&&response.ok){const data=await response.json();data.public_base_url=u.origin;return new Response(JSON.stringify(data),{status:response.status,headers:rh});}
  return new Response(response.body,{status:response.status,headers:rh});
 }catch{return json({detail:'PC 연결을 처리하지 못했습니다. 연결 창과 PC 연결 상태를 확인하세요.'},502);}
}
