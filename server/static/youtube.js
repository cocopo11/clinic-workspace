/* Link ingestion UI. Jobs are stored on the server; successful drafts stay unapproved. */
'use strict';
window.ClinicWorkspaceYouTube={create({api,getUser,openDocument,deleteDocument,onSaved,toast}){
 const $=id=>document.getElementById(id);
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const labels={queued:'처리 대기',running:'처리 중',succeeded:'저장 완료',failed:'실패',cancelled:'취소'};
 const box=document.createElement('section');box.id='youtubeImporter';box.className='card hidden';
 box.innerHTML=`<h2>유튜브 영상 → 직원 CS 위키</h2><p class="small">병원에서 사용할 수 있는 공개 영상의 말을 대본으로 옮깁니다. 자막을 우선 가져오고, 자막이 없으면 영상 음성을 Whisper로 전사합니다. 저장한 대본을 관리자가 검토하고 검색 사용 승인하면, 직원은 CS 위키에서 질문만 입력해 영상 내용에 근거한 답변을 받을 수 있습니다. 검색 색인은 첫 검색 때 자동 준비합니다.</p>
 <form id="youtubeForm"><label for="youtubeUrl">유튜브 영상 링크</label><input id="youtubeUrl" type="url" required placeholder="https://www.youtube.com/watch?v=... 또는 Shorts 링크"><div class="grid"><div><label for="youtubeMode">대본 생성 방식</label><select id="youtubeMode"><option value="auto">자동 · 자막 우선, 없으면 음성 전사</option><option value="captions">제공 자막만 가져오기</option><option value="audio">영상 음성을 직접 전사 · 제공 자막 오류 시</option></select></div><div><label for="youtubeLanguage">우선 언어</label><select id="youtubeLanguage"><option value="ko">한국어 우선</option><option value="en">영어 우선</option><option value="auto">자동</option></select></div></div>
 <label for="youtubeTitle">저장할 제목 (선택)</label><input id="youtubeTitle" maxlength="120" placeholder="비워두면 영상 제목으로 저장합니다."><h3 style="margin-top:15px">승인 후 열람 역할</h3><div class="row"><label class="tick"><input id="youtubeAdmin" type="checkbox" checked> 관리자</label><label class="tick"><input id="youtubeDoctor" type="checkbox" checked> 의료진</label><label class="tick"><input id="youtubeStaff" type="checkbox" checked> CS 직원</label></div>
 <div class="row intranet-actions"><button id="startYoutube" type="submit" class="primary">대본 자동 생성·저장</button><button id="refreshYoutube" type="button">작업 상태 새로고침</button></div><p id="youtubeTools" class="small"></p><p id="youtubeStatus" class="resource-status" role="status"></p></form><div id="youtubeJobs"></div><p class="small">최대 30분·음성 120MB, 영상 한 개씩 처리합니다. 동일 링크를 다시 넣으면 기존 작업·자료를 열 수 있습니다. 공개 접근이 제한되거나 유튜브가 요청을 차단하면 실패 상태로 표시합니다.</p>`;
 $('resources').insertBefore(box,$('resources').children[1]);
 let generation=0,timer=null,known=new Map(),loading=false;
 const allowed=()=>['admin','doctor'].includes(getUser()?.role);
 function clear(){generation++;clearTimeout(timer);timer=null;known=new Map();loading=false;$('youtubeForm').reset();$('youtubeJobs').replaceChildren();$('youtubeStatus').textContent='';$('youtubeTools').textContent='';box.classList.add('hidden');}
 function error(e){if(e.stale)return;$('youtubeStatus').textContent=e.message;toast(e.message);}
 async function loadJobs(){
  if(!allowed()||loading)return;loading=true;const mine=generation;clearTimeout(timer);
  try{
   const r=await api('/api/youtube/jobs');if(mine!==generation)return;
   let changed=false;
   for(const j of r.jobs){if(known.has(j.id)&&known.get(j.id)!=='succeeded'&&j.status==='succeeded')changed=true;known.set(j.id,j.status);}
   $('youtubeJobs').innerHTML=r.jobs.map(j=>`<article class="source"><div class="resource-heading"><strong>${esc(j.title||j.video_id)}</strong><span class="pill ${j.status==='failed'?'warn':''}">${labels[j.status]||j.status}</span></div><p class="small">${esc(j.stage)} · ${j.progress}%</p><progress max="100" value="${j.progress}" aria-label="${esc(j.title||j.video_id)} 처리 진행률"></progress>${j.error_message?`<p>${esc(j.error_message)}</p>`:''}<div class="row">${j.doc_id?`<button type="button" data-youtube-document="${esc(j.doc_id)}">대본 검토·위키 승인</button><button type="button" class="danger" data-delete-youtube-document="${esc(j.doc_id)}">자료 삭제</button>`:''}${['queued','running'].includes(j.status)?`<button type="button" data-cancel-youtube="${esc(j.id)}" ${j.cancel_requested?'disabled':''}>${j.cancel_requested?'취소 요청됨':'작업 취소'}</button>`:''}${['failed','cancelled'].includes(j.status)?`<button type="button" data-reuse-youtube="${esc(j.id)}">링크 다시 입력</button>`:''}</div><div class="small">${esc(j.url)}</div></article>`).join('')||'<p class="small">등록한 영상 작업이 없습니다.</p>';
   document.querySelectorAll('[data-youtube-document]').forEach(b=>b.onclick=()=>openDocument(b.dataset.youtubeDocument));
   document.querySelectorAll('[data-delete-youtube-document]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{await deleteDocument(b.dataset.deleteYoutubeDocument);}catch(e){error(e);}finally{b.disabled=false;}});
   document.querySelectorAll('[data-cancel-youtube]').forEach(b=>b.onclick=async()=>{try{await api(`/api/youtube/jobs/${b.dataset.cancelYoutube}/cancel`,{method:'POST',body:{}});await loadJobs();}catch(e){error(e);}});
   document.querySelectorAll('[data-reuse-youtube]').forEach(b=>b.onclick=()=>{const j=r.jobs.find(j=>j.id===b.dataset.reuseYoutube);$('youtubeUrl').value=j.url;$('youtubeTitle').value=j.title;$('youtubeMode').value=j.mode;$('youtubeLanguage').value=j.language;$('youtubeStatus').textContent='링크를 확인하고 대본 생성 버튼을 누르세요.';});
   if(changed){await onSaved();toast('영상 대본을 저장했습니다. 대본 검토·위키 승인 후 직원 검색에 사용할 수 있습니다.');}
   if(r.jobs.some(j=>['queued','running'].includes(j.status)))timer=setTimeout(loadJobs,2000);
  }catch(e){error(e);}finally{if(mine===generation)loading=false;}
 }
 async function setUser(){clear();if(!allowed())return;box.classList.remove('hidden');try{const r=await api('/api/youtube/status');$('youtubeTools').textContent=r.captions&&r.audio?'유튜브 처리 도구 준비됨'+(r.runtime?' · 음성 수집 실행 도구 '+r.runtime:' · 음성 수집에는 Node.js 22+ 또는 Deno 2.3+가 추가로 필요할 수 있습니다.'):'추가 도구 설치 필요 · python -m pip install -r server/requirements-ai.txt 실행 후 서버를 다시 시작하세요.';await loadJobs();}catch(e){error(e);}}
 $('youtubeForm').onsubmit=async e=>{e.preventDefault();$('startYoutube').disabled=true;$('youtubeStatus').textContent='영상 작업을 등록하는 중…';try{const roles=[['youtubeAdmin','admin'],['youtubeDoctor','doctor'],['youtubeStaff','staff']].filter(([id])=>$(id).checked).map(x=>x[1]);const r=await api('/api/youtube/jobs',{method:'POST',body:{url:$('youtubeUrl').value.trim(),title:$('youtubeTitle').value.trim(),mode:$('youtubeMode').value,language:$('youtubeLanguage').value,roles}});known.set(r.job.id,r.job.status);$('youtubeStatus').textContent=r.reused?'동일 링크의 기존 작업·자료를 표시합니다.':'작업을 등록했습니다. 처리 상태는 아래에서 확인할 수 있습니다.';await loadJobs();}catch(e){error(e);}finally{$('startYoutube').disabled=false;}};
 $('refreshYoutube').onclick=loadJobs;
 return {clear,setUser,refresh:loadJobs};
}};
