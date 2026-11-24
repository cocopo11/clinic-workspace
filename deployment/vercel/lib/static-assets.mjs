import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';

// Only public application code is cached. Authenticated APIs and patient files
// continue through the private backend and retain their no-store policy.
const assets=new Map();
export async function staticAsset(request){
 const url=new URL(request.url),match=/^\/static\/([a-zA-Z0-9_-]+\.(js|css))$/.exec(url.pathname);
 if(!match||!['GET','HEAD'].includes(request.method))return null;
 const name=match[1];
 if(!assets.has(name)){
  try{
   const body=await readFile(new URL('../backend/static/'+name,import.meta.url));
   assets.set(name,{body,etag:'"'+createHash('sha256').update(body).digest('hex')+'"'});
  }catch(error){if(error.code==='ENOENT')return null;throw error;}
 }
 const {body,etag}=assets.get(name);
 const headers={'Content-Type':match[2]==='js'?'application/javascript; charset=utf-8':'text/css; charset=utf-8',
  'Cache-Control':url.searchParams.has('v')?'public, max-age=31536000, immutable':'public, max-age=300',
  'ETag':etag,'X-Content-Type-Options':'nosniff','X-Goodmona-Asset':'static','Server-Timing':'asset;dur=0'};
 if(request.headers.get('if-none-match')===etag)return new Response(null,{status:304,headers});
 return new Response(request.method==='HEAD'?null:body,{headers});
}
