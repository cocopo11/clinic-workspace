const encoder=new TextEncoder(),decoder=new TextDecoder();
export const COOKIE='__Host-gm_pc';
export function parseCode(code,site){
  if(typeof code!=='string'||!code.startsWith('GM1.')||code.length>4096)throw Error('Invalid code');
  const c=JSON.parse(Buffer.from(code.slice(4),'base64url').toString('utf8'));
  const u=new URL(c.origin);
  if(u.protocol!=='https:'||!/^[-a-z0-9]+\.trycloudflare\.com$/.test(u.hostname)||u.port||u.username||u.password||u.search||u.hash||u.pathname!=='/'||!/^[a-f0-9]{64}$/.test(c.token||'')||c.site!==site)throw Error('Invalid connection');
  return {origin:u.origin,token:c.token};
}
async function key(secret){
  if(!/^[a-f0-9]{64}$/.test(secret||''))throw Error('Runtime key unavailable');
  return crypto.subtle.importKey('raw',Buffer.from(secret,'hex'),'AES-GCM',false,['encrypt','decrypt']);
}
export async function seal(config,secret,site,ttl=12*3600*1000){
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const data=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode(site)},await key(secret),encoder.encode(JSON.stringify({...config,expires:ttl===null?null:Date.now()+ttl})));
  return Buffer.concat([iv,new Uint8Array(data)]).toString('base64url');
}
export async function load(request,secret){
  const value=(request.headers.get('cookie')||'').split(';').map(s=>s.trim()).find(s=>s.startsWith(COOKIE+'='))?.slice(COOKIE.length+1);
  if(!value||value.length>4096)return null;
  return open(value,secret,new URL(request.url).origin);
}
export async function open(value,secret,site,persistent=false){
  try{
    const bytes=Buffer.from(value,'base64url');
    const config=JSON.parse(decoder.decode(await crypto.subtle.decrypt({name:'AES-GCM',iv:bytes.subarray(0,12),additionalData:encoder.encode(site)},await key(secret),bytes.subarray(12))));
    if(!(persistent&&config.expires===null)&&(!Number.isFinite(config.expires)||config.expires<Date.now()))return null;
    const host=site.replace(/^shared:/,''),parsed=parseCode('GM1.'+Buffer.from(JSON.stringify({...config,site:host})).toString('base64url'),host);
    return persistent?{...parsed,managerHash:config.managerHash,managerExpires:config.managerExpires}:parsed;
  }catch{return null;}
}
export const cookie=(value,age=43200)=>`${COOKIE}=${value}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=${age}`;
