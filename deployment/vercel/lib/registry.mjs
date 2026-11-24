import {get,put} from '@vercel/blob';
import {seal,open} from './session.mjs';
const PATH='connection/config.enc',registries=new WeakMap();
export function createRegistry(env,blob={get,put},clock=Date.now){
 const cache=new Map();
 async function write(site,config,overwrite){
  const encoded=await seal(config,env.CONFIG_KEY,'shared:'+site,null);
  await blob.put(PATH,encoded,{access:'private',addRandomSuffix:false,allowOverwrite:overwrite,contentType:'application/octet-stream',cacheControlMaxAge:60,token:env.BLOB_READ_WRITE_TOKEN});
  cache.set(site,{value:config,until:clock()+5000});
 }
 return {
  async load(site){
   const cached=cache.get(site);if(cached&&cached.until>clock())return cached.value;
   const result=await blob.get(PATH,{access:'private',useCache:false,token:env.BLOB_READ_WRITE_TOKEN});let value=null;
   if(result){if(result.statusCode!==200)throw Error('Connection unavailable');value=await open(await new Response(result.stream).text(),env.CONFIG_KEY,'shared:'+site,true);if(!value)throw Error('Connection damaged');}
   cache.set(site,{value,until:clock()+5000});return value;
  },
  save:(site,config)=>write(site,config,true),
  claim:(site,config)=>write(site,config,false)
 };
}
export function registry(env){if(!registries.has(env))registries.set(env,createRegistry(env));return registries.get(env);}
