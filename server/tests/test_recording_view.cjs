/* Regression: polling must preserve disclosure and draft state. Uses a minimal DOM double. */
const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
class Element {
 constructor(){this.value='';this.checked=false;this.details=[];this.writes=0;this.classList={add(){},remove(){}};}
 set innerHTML(value){this.markup=value;this.writes++;this.details=[...value.matchAll(/<details data-disclosure="([^"]+)"/g)].map(m=>({dataset:{disclosure:m[1]},open:false}));}
 get innerHTML(){return this.markup;}
 querySelectorAll(selector){return selector==='details[data-disclosure]'?this.details:[];}
 replaceChildren(){this.innerHTML='';} append(){}
}
(async()=>{
 const elements=new Map(),get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
 let chart={mode:'auto',entries:[{note_id:1,visit_date:'2026-11-15',text:'가상 차트'}]},jobs=[{id:'job-1',stage:'완료',status:'succeeded',patient_id:'DEMO-101',visit_date:'2026-11-15',progress:100,note_id:1,text:'가상 원문'}];
 const ctx={window:{},document:{getElementById:get,createElement:()=>new Element()},setTimeout:()=>1,clearTimeout(){}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../static/mobile-recording.js'),'utf8'),ctx);
 const app=ctx.window.ClinicWorkspaceRecording.create({api:async url=>url.includes('crm-chart')?chart:{jobs},getUser:()=>({id:'doctor'}),toast(){},onSaved:async()=>{}});
 await app.select('DEMO-101');get('crmChart').details[0].open=true;get('recordingJobs').details[0].open=true;
 get('tabletNote').value='수정 중인 가상 기록';get('tabletReviewed').checked=true;
 const chartNode=get('crmChart').details[0],transcriptNode=get('recordingJobs').details[0],writes=get('crmChart').writes;
 for(let i=0;i<5;i++)await get('refreshRecordingJobs').onclick();
 assert.equal(get('crmChart').writes,writes);assert.equal(get('crmChart').details[0],chartNode);assert.equal(get('recordingJobs').details[0],transcriptNode);
 chart.entries.unshift({note_id:2,visit_date:'2026-11-15',text:'추가 차트'});jobs[0].text='갱신 원문';await get('refreshRecordingJobs').onclick();
 assert.equal(get('crmChart').details[0].open,false);assert.equal(get('crmChart').details[1].open,true);assert.equal(get('recordingJobs').details[0].open,true);
 assert.equal(get('tabletNote').value,'수정 중인 가상 기록');assert.equal(get('tabletReviewed').checked,true);
 get('crmChart').details[1].open=false;get('recordingJobs').details[0].open=false;chart.entries[1].text='수정 차트';jobs[0].progress=99;await get('refreshRecordingJobs').onclick();
 assert.equal(get('crmChart').details[1].open,false);assert.equal(get('recordingJobs').details[0].open,false);
 jobs[0].auto_reviewed=true;await get('refreshRecordingJobs').onclick();assert.match(get('recordingJobs').innerHTML,/등록 기록 보기·수정 \(선택\)/);assert(!get('recordingJobs').innerHTML.includes('전사 확인·진료차트 등록'));
 jobs[0].auto_reviewed=false;await get('refreshRecordingJobs').onclick();assert.match(get('recordingJobs').innerHTML,/전사 확인·진료차트 등록/);
 get('crmChart').details[1].open=true;await app.select('DEMO-102');assert(get('crmChart').details.every(d=>!d.open));assert.equal(get('tabletNote').value,'');assert.equal(get('tabletReviewed').checked,false);
 console.log('PASS repeated polling retains nodes; changed data preserves open/closed state; new entries closed; edits intact; patient switch resets state');
})().catch(e=>{console.error(e);process.exitCode=1;});
