const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/connected.js'),'utf8');
const helpers=source.slice(source.indexOf('async function api('),source.indexOf('function err('));
(async()=>{
 let reads=0,writes=0;const releases=[];
 const ctx={pendingReads:new Map(),user:{id:'admin'},csrf:'fixture',Event:class{},document:{dispatchEvent(){}},
  window:{ClinicWorkspaceHTTP:{requestJSON:async(path,options)=>{if(options.method==='GET'){reads++;await new Promise(resolve=>releases.push(resolve));}else writes++;return {path};}}}};
 vm.createContext(ctx);vm.runInContext(helpers+';this.api=api;',ctx);
 const a=ctx.api('/api/documents'),b=ctx.api('/api/documents');assert.equal(reads,1);releases.shift()();assert.deepEqual(await a,await b);
 const fresh=ctx.api('/api/documents');assert.equal(reads,2);releases.shift()();await fresh;
 const c=ctx.api('/api/documents');ctx.user={id:'staff'};const d=ctx.api('/api/documents');assert.equal(reads,4);releases.splice(0).forEach(f=>f());await Promise.all([c,d]);
 await Promise.all([ctx.api('/api/leaves',{method:'POST',body:{}}),ctx.api('/api/leaves',{method:'POST',body:{}})]);assert.equal(writes,2);
 console.log('PASS concurrent identical reads share one request; settled reads refresh; accounts stay isolated; writes are never deduplicated or retried');
})().catch(e=>{console.error(e);process.exitCode=1;});
