'use strict';
window.ClinicWorkspaceRecording={create({api,getUser,toast,onSaved}){
 const $=id=>document.getElementById(id),esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const box=document.createElement('section');box.className='banner';box.id='tabletRecording';
 box.innerHTML=`<h3>가상환자 임시 진료차트 · 모의 CRM</h3><button id="toggleCrmMode">등록 모드 확인 중</button><p class="small">모드는 이 환자의 이후 접수 녹음부터 적용됩니다. 수동 모드는 전사 결과를 확인한 뒤 아래 등록 버튼을 누르세요. 실제 CRM 방화벽 우회 기능은 아닙니다.</p><div id="crmChart"></div><p class="small">아이패드에서 녹음 종료 → 실제 Whisper 전사 → 환자·방문별 모의 CRM에 자동 등록합니다. 자동 모드는 기본 형식·환자/방문 연결을 자동 확인하고 바로 차트에 등록합니다. 수동 모드는 전사 확인 후 등록합니다. 자동 검토는 약명·수치 등 의료 내용의 정확성 확인을 의미하지 않습니다.</p><button id="refreshRecordingJobs">녹음 상태 새로고침</button><p class="small">모바일 진료에서 같은 환자를 선택해 보고·녹음하세요. 등록 상태는 자동으로 갱신됩니다.</p><p id="recordingStatus" role="status" class="small"></p><div id="recordingJobs"></div><div id="recordingReview" class="hidden"><label id="recordingReviewTitle" for="tabletNote">자동 등록된 기록 검토·수정</label><textarea id="tabletNote" style="min-height:240px"></textarea><label class="tick"><input id="tabletReviewed" type="checkbox"> 환자·원문·약명·숫자·부정어를 확인했습니다.</label><button id="reviewTabletNote">확인 후 진료차트 등록·갱신 (모의)</button><p class="small">실제 병원 CRM 전송은 수행하지 않습니다.</p></div>`;
 $('tabletRecordingMount').append(box);
 const rendered=new Map();
 function renderDetails(id,markup){
  if(rendered.get(id)===markup)return;
  const container=$(id),opened=new Set([...container.querySelectorAll('details[data-disclosure]')].filter(d=>d.open).map(d=>d.dataset.disclosure));
  container.innerHTML=markup;
  container.querySelectorAll('details[data-disclosure]').forEach(d=>{d.open=opened.has(d.dataset.disclosure);});
  rendered.set(id,markup);
 }
 let mode="auto",patient=null,generation=0,timer=null,known=new Map(),selected=null,loading=false;
 function clear(){generation++;patient=null;known.clear();selected=null;loading=false;clearTimeout(timer);rendered.clear();$('crmChart').replaceChildren();$('recordingJobs').replaceChildren();$('recordingStatus').textContent='';$('recordingReview').classList.add('hidden');$('tabletNote').value='';$('tabletReviewed').checked=false;}
 const same=(g,p,u)=>g===generation&&p===patient&&u===getUser()?.id;
 function error(e){if(e.stale)return;$('recordingStatus').textContent=e.message;toast(e.message);}
 async function refresh(){
  if(!patient||loading)return;const g=generation,p=patient,u=getUser()?.id;loading=true;clearTimeout(timer);
  try{const chart=await api('/api/patients/'+encodeURIComponent(p)+'/crm-chart');if(!same(g,p,u))return;mode=chart.mode;$('toggleCrmMode').textContent='현재 '+(mode==='auto'?'자동 등록':'확인 후 수동 등록')+' · 눌러 전환';renderDetails('crmChart','<h3>'+esc(p)+' · 임시 진료차트</h3>'+ (chart.entries.map(n=>'<details data-disclosure="chart-'+esc(n.note_id)+'"><summary>REC-'+n.note_id+' · '+esc(n.visit_date)+'</summary><pre style="white-space:pre-wrap">'+esc(n.text)+'</pre></details>').join('')||'<p>등록된 진료차트가 없습니다.</p>'));const r=await api('/api/recording-jobs?patient_id='+encodeURIComponent(p));if(!same(g,p,u))return;
   let changed=false;for(const j of r.jobs){if(known.get(j.id)!=='succeeded'&&j.status==='succeeded')changed=true;known.set(j.id,j.status);}
   renderDetails('recordingJobs',r.jobs.map(j=>`<article class="source"><strong>${esc(j.stage)}</strong><p class="small">${esc(j.patient_id)} · ${esc(j.visit_date)} · ${j.progress}%${j.note_id?' · REC-'+j.note_id:''}</p>${j.error?`<p>${esc(j.error)}</p>`:''}${j.text?`<details data-disclosure="transcript-${esc(j.id)}"><summary>전사 원문</summary><pre style="white-space:pre-wrap">${esc(j.text)}</pre></details><button data-review-recording="${esc(j.id)}">${j.reviewed||j.auto_reviewed?'등록 기록 보기·수정 (선택)':'전사 확인·진료차트 등록'}</button>`:''}</article>`).join('')||'<p class="small">이 환자의 오늘 아이패드 녹음 내역이 없습니다.</p>');
   box.querySelectorAll('[data-review-recording]').forEach(b=>b.onclick=()=>{selected=r.jobs.find(j=>j.id===b.dataset.reviewRecording);$('recordingReviewTitle').textContent='REC-'+selected.note_id+' · 자동 등록 기록 검토·수정';$('tabletNote').value=selected.note_text;$('tabletReviewed').checked=false;$('recordingReview').classList.remove('hidden');});
   if(changed){await onSaved();if(same(g,p,u))toast('녹음 처리가 완료되었습니다. 자동·수동 등록 상태를 확인하세요.');}
   timer=setTimeout(refresh,2000);
  }catch(e){if(same(g,p,u))error(e);}finally{if(same(g,p,u))loading=false;}
 }
 async function select(id){clear();patient=id;await refresh();}
 $('toggleCrmMode').onclick=async()=>{if(!patient)return;try{await api('/api/patients/'+encodeURIComponent(patient)+'/crm-mode',{method:'POST',body:{mode:mode==='auto'?'manual':'auto'}});await refresh();}catch(e){error(e);}};
 $('refreshRecordingJobs').onclick=refresh;
 $('tabletNote').oninput=()=>{$('tabletReviewed').checked=false;};
 $('reviewTabletNote').onclick=async()=>{if(!selected)return;const g=generation,p=patient,u=getUser()?.id;$('reviewTabletNote').disabled=true;
  try{await api('/api/recording-jobs/'+encodeURIComponent(selected.id)+'/review',{method:'POST',body:{text:$('tabletNote').value,reviewed:$('tabletReviewed').checked,expected_version:selected.note_version}});if(!same(g,p,u))return;$('recordingReview').classList.add('hidden');$('recordingStatus').textContent='자동 등록된 REC-'+selected.note_id+' 기록을 검토 완료로 저장했습니다. 실제 CRM 미연결입니다.';selected=null;await refresh();await onSaved();toast('같은 자동 등록 기록을 검토 완료로 저장했습니다. 실제 CRM 미연결입니다.');}
  catch(e){if(same(g,p,u))error(e);}finally{$('reviewTabletNote').disabled=false;}
 };
 return {clear,select};
}};
