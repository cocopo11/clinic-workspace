/* Connected mode: same-origin FastAPI, persisted demo data, actual local model inference. */
(()=>{
'use strict';
let user=null,csrf='',stream=null,selectedPatient=null,aiState=null,editingDoc=null;
let mobileRecorder=null,geoApp=null,staffPortal=null;
const pendingReads=new Map();let geoLoadedFor=null;

const roleNames={admin:'관리자',doctor:'의료진',staff:'CS 직원'};
const loginLayer=document.createElement('div');loginLayer.className='login-layer';loginLayer.innerHTML=`<form id="loginForm" class="card login-card"><div class="label">CLINIC WORKSPACE</div><h1>직원 로그인</h1><p>가상 데이터로 기능을 확인하는 연결형 시연입니다.</p><label for="username">데모 아이디</label><input id="username" autocomplete="username" value=""><label for="password">데모 비밀번호</label><input id="password" type="password" autocomplete="current-password" value=""><button class="primary" type="submit" style="margin-top:18px;width:100%">로그인</button><p id="loginError" role="status" class="small"></p><div class="sep"></div><p class="small">체험 공간 안에서만: admin · doctor · staff / ClinicDemo42!<br>운영자 계정은 별도 비밀번호가 필요합니다.<br>실제 환자·직원 자료를 입력하지 마세요.</p><div class="row"><button type="button" data-login="admin">관리자</button><button type="button" data-login="doctor">의료진</button><button type="button" data-login="staff">CS 직원</button></div></form>`;document.body.append(loginLayer);
const account=document.createElement('div');account.id='accountArea';account.className='row';document.querySelector('.top').append(account);
document.querySelector('.top .pill').textContent='연결형 데모 · 가상 데이터만';
document.querySelector('.aside-note').innerHTML='가상 데이터 시연<br>실제 CRM에는 미연결입니다.';
document.querySelector('#home > .banner').textContent='추천 시연: 가상환자 선택 → 모바일 환자 검색·촬영 → 사진 자동 반영 → 실제 음성 전사 → 관리자 색인 생성 → 의미 검색·RAG 답변. 실제 CRM·콘텐츠 작성는 공개 자료 기반 LLM 원고·검토·발행 준비를 지원합니다. 실제 블로그 게시는 별도입니다.';
document.querySelector('#home .intro p').textContent='사진 전송·음성 전사·직원 권한·문서 검색과 실제 LLM을 연결한 업무 포트폴리오입니다.';
const statusBox=document.createElement('div');statusBox.className='banner';statusBox.id='serverStatus';statusBox.innerHTML='서버 상태 확인 중…';document.querySelector('.top').after(statusBox);
const oldReset=$('reset');oldReset.textContent='화면 새로고침';oldReset.onclick=()=>location.reload();

async function api(path,options={}){
 const method=options.method||'GET';if(method!=='GET')return requestApi(path,options);
 const key=(user?.id||'anonymous')+'|'+csrf+'|'+path;
 if(pendingReads.has(key))return pendingReads.get(key);
 const promise=requestApi(path,options).finally(()=>{if(pendingReads.get(key)===promise)pendingReads.delete(key);});pendingReads.set(key,promise);return promise;
}
async function requestApi(path,{method='GET',body,form}={}){
 const requestCsrf=csrf,requestUser=user?.id;const headers={};if(csrf)headers['X-CSRF-Token']=csrf;if(body!==undefined)headers['Content-Type']='application/json';
 try{const result=await window.ClinicWorkspaceHTTP.requestJSON(path,{method,headers,credentials:'same-origin',cache:'no-store',body:form|| (body===undefined?undefined:JSON.stringify(body))});if((method!=='GET'&&/^\/api\/(leaves|documents|onboarding|notices)(\/|$)/.test(path))||(method==='GET'&&/^\/api\/documents\/[^/?]+$/.test(path)))document.dispatchEvent(new Event('clinicworkspace:staff-data'));return result;}
 catch(e){if(e.status===401&&csrf===requestCsrf&&user?.id===requestUser){loginLayer.style.display='grid';stopConnection();intranetApp.clear();geoApp?.clear();staffPortal?.clear();user=null;csrf='';account.replaceChildren();}throw e;}
}
function err(e){toast(e.message||String(e));}
function stopConnection(){mobileRecorder?.clear();stream?.close();stream=null;selectedPatient=null;}
function clinicalRole(){return user&&['admin','doctor'].includes(user.role);}
function canEdit(){return clinicalRole();}
function applyUser(){
 mobileRecorder?.clear();
 previousVisits.reset();
 ['patientResults','chartList','wikiAnswer','documentsList','usersList'].forEach(id=>$(id)?.replaceChildren());
 ['docTitle','docContent','docUrl'].forEach(id=>{if($(id))$(id).value='';});
 pendingReads.clear();geoLoadedFor=null;lastStaffView=null;intranetApp.setUser();geoApp?.clear();staffPortal?.setUser();patient=null;selectedPatient=null;stream?.close();stream=null;

 account.innerHTML=`<span class="small">${esc(user.name)} · ${roleNames[user.role]}</span><button id="logoutBtn" style="font-size:12px;padding:5px 9px">로그아웃</button>`;
 $('resetClinicalData').classList.toggle('hidden',user.role!=='admin');
 $('logoutBtn').onclick=async()=>{try{await api('/api/logout',{method:'POST',body:{}});stopConnection();intranetApp.clear();geoApp?.clear();staffPortal?.clear();user=null;csrf='';account.replaceChildren();loginLayer.style.display='grid';$('patientWorkspace').classList.add('hidden');}catch(e){err(e);}};
 $$('nav [data-page="clinical"]').forEach(e=>e.classList.toggle('hidden',!clinicalRole()));
 $$('[data-page="clinical"]').filter(e=>!e.closest('nav')).forEach(e=>e.disabled=!clinicalRole());
 $$('[data-sub="charts"]').forEach(e=>e.classList.toggle('hidden',!clinicalRole()));
 $('demoRole').value=user.role==='admin'?'manager':'staff';$('demoRole').disabled=true;
 document.querySelector('#leave .banner')?.remove();
 document.querySelector('#intranet .intro .pill').textContent='데모 계정 · 서버 권한 검사';
 document.querySelector('#clinical .intro .pill').textContent='실제 사진 전송·STT / CRM 모의';
 document.querySelector('#clinical .intro p')?.remove();
 $('recordingIntro').textContent='아이패드에서 녹음하면 전사 후 선택한 모드에 따라 자동 검토·차트 등록 또는 수동 확인·등록으로 이어집니다. 등록된 기록의 검토·수정도 아래에서 진행합니다.';
 const queueNote=document.querySelector('#charts .card p');queueNote.textContent='서버에 저장한 모의 차트 기록입니다.';
 page('home');
 loginLayer.style.display='none';
 $('reindexDocs').classList.toggle('hidden',!canEdit());
}
$$('[data-login]').forEach(b=>b.onclick=()=>{$('username').value=b.dataset.login;$('password').value='ClinicDemo42!';});
$('loginForm').onsubmit=async e=>{e.preventDefault();$('loginError').textContent='로그인 중…';try{const r=await api('/api/login',{method:'POST',body:{username:$('username').value.trim(),password:$('password').value}});user=r.user;csrf=r.csrf;$('loginError').textContent='';applyUser();}catch(e){$('loginError').textContent=e.message;}};
async function loadStatus(){try{aiState=await api('/api/status');$('serverStatus').innerHTML=`<div class="row"><span class="pill">${aiState.cloud?'클라우드 업무 정상':'서버 연결됨'}</span><span>Ollama ${aiState.ollama?'연결':'미실행'} · 임베딩 ${aiState.embedding_ready?'준비됨':'미준비'} · LLM ${aiState.chat_ready?'준비됨':'미준비'} · 색인 ${aiState.indexed_chunks}개</span><button id="refreshStatus" style="font-size:12px;padding:3px 8px">상태 새로고침</button></div><div class="small">전사 ${esc(aiState.whisper_model)} · 검색 ${esc(aiState.embedding_model)} · 답변 ${esc(aiState.chat_model)}. 첫 모델 로딩은 시간이 걸립니다.</div>`;$('refreshStatus').onclick=loadStatus;if($('mobileEntry')){const mobileUrl=(aiState.public_base_url||location.origin)+'/mobile';$('mobileEntry').href=mobileUrl;$('mobileAddress').textContent=mobileUrl;}}catch(e){$('serverStatus').textContent=e.message;}}

const mobileEntry=document.createElement('div');mobileEntry.className='banner';mobileEntry.innerHTML='<h3>아이폰·아이패드에서 직접 사용</h3><p>각 기기에서 아래 주소를 즐겨찾기에 저장하고 직원 로그인 → 환자 이름 검색으로 시작하세요. 매번 QR을 만들 필요가 없습니다.</p><a id="mobileEntry" class="button" href="/mobile" target="_blank" rel="noopener">모바일 진료 열기</a><p id="mobileAddress" class="small"></p>';document.querySelector('#clinical .intro').after(mobileEntry);
api('/api/connection').then(r=>{const url=(r.public_base_url||location.origin)+'/mobile';$('mobileEntry').href=url;$('mobileAddress').textContent=url;}).catch(()=>{});

const clinicalReset=document.createElement('button');clinicalReset.id='resetClinicalData';clinicalReset.className='hidden';clinicalReset.textContent='테스트 진료 자료 초기화';document.querySelector('#clinical .intro').append(clinicalReset);
clinicalReset.onclick=async()=>{
 if(!user||user.role!=='admin')return;
 if(!confirm('모든 가상환자의 테스트 사진·녹음 전사·진료 기록·임시 차트와 등록 모드를 초기화할까요?\n이전 방문 샘플 사진 2장과 가상환자는 유지됩니다. 직원 계정·연차·문서·모델·인증서는 유지됩니다.\n삭제한 테스트 기록은 되돌릴 수 없습니다. 진행 중인 모바일 촬영·녹음은 종료한 뒤 진행하세요.'))return;
 clinicalReset.disabled=true;
 try{await api('/api/admin/reset-clinical',{method:'POST',body:{confirm:'RESET_CLINICAL_DEMO'}});mobileRecorder.clear();stream?.close();stream=null;patient=null;selectedPatient=null;$('patientResults').replaceChildren();$('patientWorkspace').classList.add('hidden');await renderCharts();toast('테스트 진료 자료를 초기화했습니다. 환자를 다시 검색하세요.');}
 catch(e){err(e);}finally{clinicalReset.disabled=false;}
};

// Clinical photo connection, scoped SSE, real upload and private photo endpoints.
const photoStatus=document.createElement('p');photoStatus.id='photoConnection';photoStatus.className='small';photoStatus.textContent='환자를 선택하면 사진 변경을 자동 반영합니다.';document.querySelector('#beforePhoto').closest('.card').append(photoStatus);
document.querySelector('#photoFile').accept='image/jpeg,image/png,image/webp';
function showPreviousPhoto(photo){
 if(photo){$('beforePhoto').innerHTML=`<img src="/api/photos/${photo.id}" alt="선택한 이전 방문 사진"><span class="photo-tag">${esc(photo.visit_date)} · ${esc(photo.site)}</span>`;$('beforeDate').textContent=photo.visit_date;if(patient)patient.prev=photo.visit_date;}
 else{$('beforePhoto').innerHTML='<div class="empty">이전 사진 없음</div>';$('beforeDate').textContent='기록 없음';if(patient)patient.prev=null;}
}
const previousVisits=window.ClinicWorkspacePhotoHistory.create($('previousPhoto'),showPreviousPhoto);
const oldPick=pickPatient;
searchPatient=async function(){try{const q=$('patientSearch').value.trim();if(!q)return toast('이름이나 식별번호를 입력하세요.');const r=await api('/api/patients?q='+encodeURIComponent(q));$('patientResults').innerHTML=r.patients.length?r.patients.map(p=>`<button class="patient-option" data-live-patient="${esc(p.id)}"><strong>${esc(p.name)}</strong> ${esc(p.id)} · 가상환자</button>`).join(''):'검색 결과가 없습니다.';$$('[data-live-patient]').forEach(b=>b.onclick=()=>pickPatient(b.dataset.livePatient));}catch(e){err(e);}};
$('searchPatient').onclick=searchPatient;$('patientSearch').onkeydown=e=>{if(e.key==='Enter')searchPatient();};
pickPatient=async function(id){try{mobileRecorder.clear();stream?.close();previousVisits.reset();oldPick(id);selectedPatient=id;await refreshPhotos(id);await mobileRecorder.select(id);stream=new EventSource('/api/patients/'+encodeURIComponent(id)+'/events');stream.addEventListener('ready',()=>{if(selectedPatient===id){$('photoConnection').textContent='PC 화면: 실시간 변경 알림 연결됨';refreshPhotos(id).catch(err);}});stream.addEventListener('photo',()=>{if(selectedPatient===id){refreshPhotos(id).catch(err);toast('휴대폰/다른 창에서 받은 사진을 자동 반영했습니다.');}});stream.onerror=()=>{if(selectedPatient===id)$('photoConnection').textContent='변경 알림 재연결 중 · 다시 연결되면 서버의 사진을 재조회합니다.';};}catch(e){err(e);}};
async function refreshPhotos(id){const r=await api('/api/patients/'+encodeURIComponent(id)+'/photos');if(selectedPatient!==id)return;$('todayDate').textContent=r.visit_date;showPreviousPhoto(previousVisits.update(r.history||(r.before?[r.before]:[])));if(r.today){$('todayPhoto').innerHTML=`<img src="/api/photos/${r.today.id}" alt="서버에서 받은 오늘 사진"><span class="photo-tag">서버 저장 · 자동 반영</span>`;photoReady=true;}else{$('todayPhoto').innerHTML='<div class="empty">오늘 촬영 대기</div>';photoReady=false;}$('patientBanner').textContent=`${r.patient.name} / ${id} · 가상환자 · ${r.visit_date} 방문`;}
async function sendImage(file){if(!selectedPatient)return toast('환자를 먼저 선택하세요.');const id=selectedPatient,form=new FormData();form.append('file',file);await api('/api/patients/'+encodeURIComponent(id)+'/photos',{method:'POST',form});await refreshPhotos(id);}
$('photoFile').onchange=async e=>{const f=e.target.files[0];if(f)try{await sendImage(f);}catch(x){err(x);}};
$('samplePhoto').onclick=async()=>{if(!selectedPatient)return toast('환자를 선택하세요.');try{const r=await fetch('/api/sample-image',{credentials:'same-origin'});if(!r.ok)throw Error('샘플 이미지를 받지 못했습니다.');await sendImage(new File([await r.blob()],'synthetic.png',{type:'image/png'}));toast('가상 도식을 서버에 저장했습니다.');}catch(e){err(e);}};
const oldPhotoNote=$('photoFile').parentElement.nextElementSibling;oldPhotoNote?.remove();

// Tablet recording owns automatic transcription, registration and review.
mobileRecorder=window.ClinicWorkspaceRecording.create({api,getUser:()=>user,toast,onSaved:()=>renderCharts()});

// Persisted mock CRM notes; still no actual clinic CRM connector.
renderCharts=async function(){const requestingUser=user?.id;if(!clinicalRole()){$('chartCount').textContent='—';$('chartList').textContent='이 계정에는 진료 기록 조회 권한이 없습니다.';return;}try{const r=await api('/api/notes');if(user?.id!==requestingUser)return;$('chartCount').textContent=r.notes.filter(n=>n.status==='전송 성공').length;$('chartList').innerHTML=r.notes.length?r.notes.map(n=>`<details class="source"><summary>REC-${n.id} · ${esc(n.patient_id)} · 모의 ${esc(n.status)}</summary><pre style="white-space:pre-wrap;font:inherit">${esc(n.text)}</pre><span class="small">실제 CRM 미연결 · ${n.attempts}회</span></details>`).join(''):'서버에 저장한 모의 기록이 없습니다.';}catch(e){$('chartList').textContent=e.message;}};

// Persisted leave workflow. Staff cannot grant themselves manager permissions.
async function loadLeave(){const requestingUser=user?.id;try{const r=await api('/api/leaves');if(user?.id!==requestingUser)return;$('balance').textContent=r.balance;$('balanceHome').textContent=r.balance;const names={pending:'승인 대기',approved:'승인',rejected:'반려'};$('leaveList').innerHTML=r.leaves.length?r.leaves.map(l=>`<div class="source"><strong>${esc(l.date)} · ${l.days}일 · ${esc(l.name)}</strong><p class="small">${names[l.status]}${l.reason?' · '+esc(l.reason):''}</p>${user.role==='admin'&&l.status==='pending'?`<button data-server-approve="${l.id}">승인</button> <button data-server-reject="${l.id}">반려</button>`:''}</div>`).join(''):'신청 내역이 없습니다.';$$('[data-server-approve]').forEach(b=>b.onclick=()=>decideLeave(b.dataset.serverApprove,true));$$('[data-server-reject]').forEach(b=>b.onclick=()=>decideLeave(b.dataset.serverReject,false));}catch(e){$('leaveList').textContent=e.message;}}
async function decideLeave(id,approve){try{await api('/api/leaves/'+id+'/decision',{method:'POST',body:{approve}});await loadLeave();toast('서버에서 승인 상태를 처리했습니다.');}catch(e){err(e);}}
$('applyLeave').onclick=async()=>{try{await api('/api/leaves',{method:'POST',body:{date:$('leaveDate').value,days:Number($('leaveType').value),reason:$('leaveReason').value.trim()}});$('leaveReason').value='';await loadLeave();toast('로그인 계정으로 연차를 신청했습니다.');}catch(e){err(e);}};
$('demoRole').onchange=()=>{};

// RAG shares the same approved document library and permission checks.
$('wiki').innerHTML=`<div class="banner amber">실제 임베딩 검색 + 로컬 LLM. 승인된 문서와 유튜브 영상 대본을 검색해 답변합니다. 새 자료는 첫 검색 때 자동으로 검색 준비하며 시간이 걸릴 수 있습니다.</div><div class="grid"><div class="card"><h2>CS 질문 검색</h2><label for="wikiQuestion">고객 질문</label><input id="wikiQuestion" value="예약 날짜를 다른 날로 바꾸려면 어떻게 안내하나요?"><div class="row" style="margin-top:12px"><button id="searchWiki" class="primary">검색</button></div><div id="wikiAnswer" style="margin-top:20px" role="status"></div></div><div class="card"><h2>검색에 사용할 자료</h2><div class="row"><button id="refreshDocs">자료 새로고침</button><button id="reindexDocs" class="primary">승인 자료 색인 생성</button></div><p class="small" id="indexStatus">자료실에서 원문·출처·버전·승인을 관리합니다. 관리자·의료진이 승인 자료의 색인을 만들 수 있습니다.</p><div id="documentsList"></div></div></div>`;
geoApp=window.ClinicWorkspaceGEO.create({api,getUser:()=>user,toast});
const intranetApp=window.ClinicWorkspaceIntranet.create({api,getUser:()=>user,toast,onReindex:loadStatus});
staffPortal=window.ClinicWorkspaceStaffPortal.create({api,getUser:()=>user,toast,openDocument:id=>intranetApp.openDocument(id)});
new MutationObserver(()=>{if(user&&$('geo').classList.contains('active')&&geoLoadedFor!==user.id){geoLoadedFor=user.id;geoApp?.setUser();}}).observe($('geo'),{attributes:true,attributeFilter:['class']});
let lastStaffView=null;new MutationObserver(()=>{if(!user)return;const active=$('intranet').classList.contains('active')?document.querySelector('#intranet .subpage.active')?.id:null;if(active===lastStaffView)return;lastStaffView=active;if(active==='leave')loadLeave();if(active==='charts')renderCharts();if(active==='wiki')loadDocuments();}).observe($('intranet'),{subtree:true,attributes:true,attributeFilter:['class']});
let statusLoaded=0;document.querySelector('main').addEventListener('toggle',event=>{if(event.target.contains?.($('serverStatus'))&&event.target.open&&Date.now()-statusLoaded>10000){statusLoaded=Date.now();loadStatus();}},true);


async function loadDocuments(){try{await intranetApp.refreshDocs();}catch(e){if(!e.stale)err(e);}}
$('refreshDocs').onclick=loadDocuments;$('reindexDocs').onclick=()=>intranetApp.reindex();
function wikiSourceLink(source){try{const url=new URL(source.source_url);if(!['https:','http:'].includes(url.protocol))return '';return `<a href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">${source.kind==='youtube_transcript'?'근거 영상 열기':'출처 열기'}</a>`;}catch{return '';}}
async function queryWiki(){if($('searchWiki').disabled)return;const requestingUser=user?.id;const question=$('wikiQuestion').value.trim();if(!question)return toast('질문을 입력하세요.');$('searchWiki').disabled=true;$('wikiAnswer').textContent='승인된 문서·영상 대본을 검색 준비하고 근거를 바탕으로 답변을 작성하는 중…';try{const r=await api('/api/wiki/ask',{method:'POST',body:{question}});if(user?.id!==requestingUser)return;const sources=(r.sources||[]).map(s=>`<details class="source" open><summary>[${s.source_id}] ${s.kind==='youtube_transcript'?'[유튜브 영상 대본] ':''}${esc(s.title)} · v${s.version}</summary><p style="white-space:pre-wrap">${esc(s.text)}</p>${wikiSourceLink(s)}</details>`).join('');$('wikiAnswer').innerHTML=`<span class="pill">${r.llm_used?'실제 LLM 응답':'답변 유보·담당자 전달'}</span><p class="answer">${esc(r.answer)}</p><p class="small">${esc(r.reason||'')}${r.chat_model?' · '+esc(r.chat_model):''}</p>`+sources+(r.citations||[]).map(c=>`<div class="small">검증된 인용 [${esc(c.source_id)}] “${esc(c.quote)}”</div>`).join('')+'<p class="small">가상 예시 자료입니다. 고객 응대 전 원문·조건·의료 내용을 사람이 검토하세요.</p>';}catch(e){$('wikiAnswer').textContent=e.message;err(e);}finally{$('searchWiki').disabled=false;}}
$('searchWiki').onclick=queryWiki;$('wikiQuestion').onkeydown=e=>{if(e.key==='Enter'&&!e.isComposing&&!$('searchWiki').disabled){e.preventDefault();queryWiki();}};
// The summary explicitly reflects connected and still-mock components.
const ledger=document.querySelector('#home table tbody');if(ledger)ledger.innerHTML=`<tr><td>1</td><td>가상환자 조회, 사진 서버 저장·실시간 반영, 아이패드 녹음·자동 전사·자동 검토/수동 확인·모의 차트 등록, 기록 서식</td><td>실제 CRM 연동, 임상 검증·기관 운영 요건</td></tr><tr><td>2</td><td>직원 계정·권한 관리, 연차 저장·승인, 자료실·버전·승인·보관, 온보딩·접근 이력, 실제 임베딩·LLM RAG</td><td>기관 SSO·실제 인사 규정, 완전한 게시판, 병원 자료·품질 평가</td></tr><tr><td>3</td><td>단일 구간·수동 자막·제목 모션·무음 출력</td><td>원본 음성 포함 출력·다중 클립·자동 자막</td></tr><tr><td>4</td><td>공개 자료·유튜브 대본 기반 LLM 블로그, 검토·파일 출력, 4주 일정, AI 언급·인용 관찰 기록</td><td>실제 블로그 게시 API, 자동 성과 수집, 검색 노출 검증</td></tr>`;
Promise.resolve(window.ClinicWorkspaceDemo?.init()).then(()=>api('/api/me')).then(r=>{user=r.user;csrf=r.csrf;applyUser();}).catch(()=>{loginLayer.style.display='grid';});
})();
