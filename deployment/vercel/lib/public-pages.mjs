import {readFile} from 'node:fs/promises';

// These HTML shells contain only application code and synthetic examples.
// Sessions, patient files and all API responses still use the private backend.
const pages=new Map([
 ['/', 'index.html'], ['/mobile','mobile.html'], ['/capture','capture.html'],
 ['/record','record.html'], ['/ipad-check','ipad-check.html']
]);
const cache=new Map();
export async function publicPage(request){
 const name=pages.get(new URL(request.url).pathname);
 if(!name||!['GET','HEAD'].includes(request.method))return null;
 if(!cache.has(name))cache.set(name,await readFile(new URL('../backend/static/'+name,import.meta.url),'utf8'));
 return new Response(request.method==='HEAD'?null:cache.get(name),{headers:{
  'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store',
  'X-ClinicWorkspace-Page':'public-shell','Server-Timing':'shell;dur=0'
 }});
}
