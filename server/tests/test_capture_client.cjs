const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const html=fs.readFileSync(path.join(__dirname,'../static/capture.html'),'utf8');
const source=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const settle=()=>new Promise(resolve=>setImmediate(resolve));
async function fixture({offline=false,expired=false}={}){
 const nodes=new Map(),get=id=>{if(!nodes.has(id))nodes.set(id,{disabled:true,hidden:true,value:'',files:[],textContent:'',clicks:0,click(){this.clicks++;}});return nodes.get(id);};
 const posts=[];const context={URLSearchParams,location:{search:'?token=fixture'},document:{getElementById:get},
  window:{addEventListener(){}},URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},FormData,
  fetch:async(url,options={})=>{
   if(options.method==='POST'){posts.push(options.body);if(offline)throw Error('fixture network failure');return {ok:true,json:async()=>({patient_id:'DEMO-101'})};}
   return {ok:!expired,status:expired?410:200,json:async()=>expired?{detail:'촬영 연결이 만료됐습니다.'}:{patient_id:'DEMO-101',visit_date:'2026-11-18',site:'정수리'}};
  }};
 vm.runInNewContext(source,context);await settle();return {get,posts};
}
(async()=>{
 assert(!/startCamera|liveVideo|getUserMedia|id="shutter"/.test(html));
 assert.match(html,/<input id="camera"[^>]*capture="environment"/);
 assert(!/<input id="album"[^>]*capture=/.test(html));
 for(const [button,input] of [['chooseCamera','camera'],['chooseAlbum','album']]){
  const f=await fixture();assert.equal(f.get(button).disabled,false);f.get(button).onclick();assert.equal(f.get(input).clicks,1);
  f.get(input).onchange();await settle();assert.equal(f.posts.length,0,'cancel picker must not upload');
  f.get(input).files=[new File(['fixture photo'],'fixture.jpg',{type:'image/jpeg'})];f.get(input).onchange();await settle();await settle();
  assert.equal(f.posts.length,1);assert.equal(f.posts[0].get('file').name,'fixture.jpg');assert.match(f.get('status').textContent,/전송 완료/);
  assert.equal(f.get('chooseCamera').disabled,true);assert.equal(f.get('chooseAlbum').disabled,true);
 }
 let f=await fixture({offline:true});f.get('album').files=[new File(['fixture'],'fixture.jpg',{type:'image/jpeg'})];f.get('album').onchange();await settle();await settle();
 assert.equal(f.get('chooseCamera').disabled,false);assert.equal(f.get('chooseAlbum').disabled,false);assert.match(f.get('status').textContent,/전송 실패/);
 f=await fixture({expired:true});assert.equal(f.get('chooseCamera').disabled,true);assert.equal(f.get('chooseAlbum').disabled,true);
 console.log('PASS native camera and gallery, picker cancellation, exactly one upload, retry controls, expiry and removal of live camera dependencies');
})().catch(e=>{console.error(e);process.exitCode=1;});
