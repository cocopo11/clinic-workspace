'use strict';
/* Persistent library, versioned onboarding and demo administration. */
window.ClinicWorkspaceIntranet = {
create({api, getUser, toast, onReindex}) {
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const roles={admin:'관리자',doctor:'의료진',staff:'CS 직원'};
const kinds={manual:'매뉴얼',onboarding:'온보딩',youtube_transcript:'유튜브 대본'};
const actions={create_notice:'공지 작성',update_notice:'공지 수정',archive_notice:'공지 보관 변경',view_notice:'공지 열람',read_notice:'공지 확인',wiki_auto_index:'위키 검색 자동 준비',recording_link:'아이패드 녹음 연결',recording_uploaded:'아이패드 녹음 접수',recording_registered:'녹음 자동 등록',recording_failed:'녹음 등록 실패',recording_reviewed:'자동 등록 기록 검토',youtube_queued:'유튜브 수집 등록',youtube_imported:'유튜브 대본 저장',youtube_failed:'유튜브 수집 실패',youtube_cancelled:'유튜브 수집 취소',youtube_cancel_requested:'유튜브 취소 요청',create_capture:'촬영 연결',transcribe:'음성 전사',login:'로그인',logout:'로그아웃',login_failed:'로그인 실패',access_denied:'접근 거절',list_documents:'자료 목록 조회',read_document:'자료 열람',download_document:'원문 다운로드',import_document:'원문 파일 불러오기',add_document:'자료 등록',update_document:'자료 수정',approve_document:'검색 승인 변경',archive_document:'자료 보관 변경',delete_document:'자료 삭제',restore_document:'이전 버전 복원',view_versions:'버전 이력 조회',reindex:'검색 색인 생성',semantic_search:'의미 검색',rag_answer:'RAG 답변',rag_handoff:'의료진 전달',rag_insufficient:'근거 없음',view_onboarding:'온보딩 조회',complete_onboarding:'온보딩 읽음 확인',undo_onboarding:'읽음 확인 취소',create_course:'과정 등록',update_course:'과정 변경',create_user:'직원 계정 생성',update_user:'계정·권한 변경',view_audit:'접근 이력 조회'};
let importingDoc=false;
let youtubeFor=null,documentSeq=0;
let epoch=0,allDocs=[],selectedDoc=null,editingDoc=null,courses=[],adminCourses=[],editingCourse=null,users=[],editingUser=null,auditOffset=0,auditTotal=0;
const isAdmin=()=>getUser()?.role==='admin';
const canEdit=()=>['admin','doctor'].includes(getUser()?.role);
function auditInfo(info){
 const parts=[];
 if(info.version)parts.push('자료 v'+info.version);
 if(info.count!==undefined)parts.push('자료 '+info.count+'개');
 if(info.role)parts.push('역할 '+(roles[info.role]||info.role));
 if(info.active!==undefined)parts.push(info.active?'활성':'비활성');
 if(info.sessions_revoked)parts.push('기존 로그인 만료');
 if(info.course_id)parts.push('과정 '+info.course_id);
 if(info.format)parts.push('파일 '+info.format);
 if(info.status)parts.push('요청 거절 '+info.status);
 return parts.join(' · ')||'—';
}
const when=stamp=>stamp?new Date(stamp*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false}):'—';
async function call(path,options){const started=epoch;const value=await api(path,options);if(started!==epoch)throw {stale:true};return value;}
function run(fn,target){return async(...args)=>{try{return await fn(...args);}catch(error){if(error.stale)return;if(target&&$(target))$(target).textContent=error.message;toast(error.message||'요청을 처리하지 못했습니다.');}};}
function toggle(id,show){$(id)?.classList.toggle('hidden',!show);}
function activate(id){
 if(id==='administration'&&!isAdmin())return;
 document.querySelectorAll('#intranet [data-sub]').forEach(b=>b.classList.toggle('active',b.dataset.sub===id));
 document.querySelectorAll('#intranet .subpage').forEach(s=>s.classList.toggle('active',s.id===id));
}
function roleChecks(prefix){return Object.entries(roles).map(([key,name])=>`<label class="tick"><input id="${prefix}${key}" type="checkbox" checked> ${name}</label>`).join('');}
function selectedRoles(prefix){return Object.keys(roles).filter(key=>$(prefix+key).checked);}
function setRoles(prefix,values){Object.keys(roles).forEach(key=>$(prefix+key).checked=values.includes(key));}
const style=document.createElement('style');style.textContent=`
.library-layout{display:grid;grid-template-columns:minmax(260px,1fr) minmax(300px,1.25fr);gap:18px}.library-list{max-height:630px;overflow:auto}.resource-item{padding:15px 0;border-bottom:1px solid var(--line)}.resource-item h3{margin:0 0 6px}.resource-body{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto;padding:16px;background:#f5f7f1;border-radius:10px}.resource-tags{display:flex;gap:5px;flex-wrap:wrap;margin:8px 0}.resource-status{font-size:12px;min-height:24px;color:var(--green);white-space:pre-wrap}.intranet-actions{margin-top:14px}.course-card{border:1px solid var(--line);border-radius:14px;padding:18px;margin:14px 0}.course-step{padding:12px 0;border-top:1px solid var(--line)}progress{width:100%;height:12px;accent-color:var(--green)}.admin-controls{display:grid;grid-template-columns:repeat(3,minmax(120px,1fr));gap:12px}.admin-controls label{margin-top:0}.table-scroll{overflow:auto}.audit-table{min-width:700px}.empty-panel{color:var(--muted);padding:18px 0;font-size:13px}.course-doc-select{min-height:180px}.resource-heading{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}@media(max-width:1000px){.library-layout{grid-template-columns:1fr}.admin-controls{grid-template-columns:1fr 1fr}}@media(max-width:600px){.admin-controls{grid-template-columns:1fr}}
`;document.head.append(style);
const tabs=document.querySelector('#intranet .tabs');
for(const [id,label] of [['resources','자료실·문서 관리'],['onboarding','온보딩'],['administration','관리자']]){
 const button=document.createElement('button');button.dataset.sub=id;button.textContent=label;button.id='tab-'+id;tabs.append(button);
 button.onclick=run(async()=>{activate(id);if(id==='resources')await refreshDocs();if(id==='onboarding'){await loadCourses();if(isAdmin()){await refreshDocs();await loadAdminCourses();}}if(id==='administration')await loadAdmin();});
}
const resource=document.createElement('div');resource.className='subpage';resource.id='resources';resource.innerHTML=`
<div class="banner">유튜브 대본·매뉴얼·온보딩 자료를 버전별로 보관합니다. CS 직원은 승인된 열람 범위의 자료만 볼 수 있습니다.</div>
<div class="card"><div class="resource-heading"><h2>자료실·문서 관리</h2><div class="row"><button id="newDocument" class="primary">새 자료 등록</button><button id="libraryRefresh">목록 새로고침</button><button id="libraryReindex">승인 자료 색인 생성</button></div></div>
<div class="admin-controls"><div><label for="libraryQuery">자료 검색</label><input id="libraryQuery" placeholder="제목·태그·요약·작성자"></div><div><label for="libraryKind">자료 종류</label><select id="libraryKind"><option value="">전체 종류</option value="manual">매뉴얼</option><option value="onboarding">온보딩</option><option value="youtube_transcript">유튜브 대본</option></select></div><div><label for="libraryStatus">자료 상태</label><select id="libraryStatus"><option value="active">전체 사용 자료</option value="approved">승인됨</option><option value="draft">미승인</option><option value="archived">보관함</option></select></div></div>
<div class="row intranet-actions"><button id="librarySearch">자료 검색</button><span id="libraryCount" class="small"></span></div><p id="libraryStatusText" class="resource-status" role="status"></p></div>
<div class="library-layout"><div class="card"><h3>자료 목록</h3><div id="libraryList" class="library-list"></div></div><div class="card" id="resourceDetail"><div class="empty-panel">자료를 선택하면 원문과 출처를 확인할 수 있습니다.</div></div></div>
<form id="documentEditor" class="card hidden"><h2 id="docEditorHeading">새 자료 등록</h2><p class="small">허용된 대본·문서를 입력하거나 UTF-8 TXT·Markdown·SRT·VTT를 불러옵니다. 영상 링크에서 자동으로 등록하려면 위의 영상 링크 대본 기능을 사용하세요.</p>
<div class="grid"><div><label for="docTitle">문서 제목</label><input id="docTitle" maxlength="120" required><label for="docKind">자료 종류</label><select id="docKind"><option value="manual">매뉴얼</option><option value="onboarding">온보딩</option><option value="youtube_transcript">유튜브 대본</option></select><label for="docCategory">분류</label><input id="docCategory" maxlength="50" value="일반"><label for="docTags">태그 (쉼표로 구분)</label><input id="docTags" placeholder="예약, 신입, 고객응대"><label for="docSummary">요약</label><textarea id="docSummary" maxlength="500"></textarea><label for="docUrl">출처 URL / 유튜브 영상 URL</label><input id="docUrl" type="url"><label for="docAuthor">채널·작성자</label><input id="docAuthor" maxlength="120"><label for="docDate">자료 발행일</label><input id="docDate" type="date"></div>
<div><label for="docFile">원문 파일 (UTF-8 · 1MB 이하)</label><input id="docFile" type="file" accept=".txt,.md,.srt,.vtt"><label for="docContent">원문 (10만 자 이하)</label><textarea id="docContent" maxlength="100000" required style="min-height:260px"></textarea><h3 style="margin-top:18px">열람 역할</h3>${roleChecks('docRole')}<label class="tick" id="docApprovalLabel"><input type="checkbox" id="docApproved"> 검색 사용 승인 (관리자)</label><p id="doctorApprovalNote" class="small">의료진이 등록·수정한 자료는 관리자 승인 후 검색에 사용됩니다.</p><label for="docChangeNote">변경 메모</label><input id="docChangeNote" maxlength="300" placeholder="무엇을 수정했는지 기록하세요."></div></div>
<div class="row intranet-actions"><button type="submit" id="saveDocument" class="primary">문서 저장</button><button type="button" id="closeDocEditor">편집 닫기</button></div><p id="docSaveStatus" class="resource-status" role="status"></p></form>`;
$('intranet').append(resource);
const onboarding=document.createElement('div');onboarding.id='onboarding';onboarding.className='subpage';onboarding.innerHTML=`

<div class="card"><div class="resource-heading"><h2>나의 온보딩</h2><button id="refreshOnboarding">진행률 새로고침</button></div><div id="onboardingCourses"></div><div id="onboardingReader"></div><p id="onboardingStatus" class="resource-status" role="status"></p></div>
<div class="card hidden" id="courseAdmin"><div class="resource-heading"><h2>온보딩 과정 관리</h2><button id="newCourse">새 과정</button></div><div id="courseAdminList"></div><form id="courseForm"><label for="courseTitle">과정 제목</label><input id="courseTitle" maxlength="120" required><label for="courseDescription">과정 안내</label><textarea id="courseDescription" maxlength="500"></textarea><h3 style="margin-top:16px">대상 역할</h3>${roleChecks('courseRole')}<label for="courseDocuments">필수 자료 (Ctrl 또는 Command로 여러 개 선택)</label><select id="courseDocuments" class="course-doc-select" multiple required></select><p class="small">대상 역할 모두가 볼 수 있는 승인 자료를 선택하세요. 선택 목록의 순서대로 표시합니다.</p><label class="tick"><input id="courseActive" type="checkbox" checked> 과정 사용</label><button type="submit" class="primary" id="saveCourse">과정 저장</button><p id="courseSaveStatus" class="resource-status" role="status"></p></form></div>`;
$('intranet').append(onboarding);
const admin=document.createElement('div');admin.id='administration';admin.className='subpage';admin.innerHTML=`
<div class="banner amber">가상 직원 계정과 교육·접근 이력을 관리합니다. 생성하는 계정은 모두 공개 시연 비밀번호 ClinicDemo42!을 사용합니다.</div>
<div class="card"><div class="resource-heading"><h2>직원 계정·권한</h2><div class="row"><button id="newEmployee">새 데모 직원</button><button id="refreshAdmin">관리 현황 새로고침</button></div></div><div class="table-scroll" id="adminUsers"></div>
<form id="employeeForm" class="hidden"><div class="grid"><div><label for="employeeId">직원 아이디</label><input id="employeeId" pattern="[A-Za-z0-9][A-Za-z0-9_-]{1,39}" required><label for="employeeName">직원 표시 이름</label><input id="employeeName" maxlength="60" required></div><div><label for="employeeRole">직원 역할</label><select id="employeeRole"><option value="staff">CS 직원</option><option value="doctor">의료진</option><option value="admin">관리자</option></select><label class="tick"><input id="employeeActive" type="checkbox" checked> 계정 활성</label><p class="small">권한·활성 상태를 바꾸면 해당 계정의 기존 로그인과 촬영 연결이 만료됩니다.</p></div></div><button type="submit" id="saveEmployee" class="primary">계정 저장</button></form><p id="employeeStatus" class="resource-status" role="status"></p></div>
<div class="card"><h2>직원별 온보딩 현황</h2><div class="table-scroll" id="adminProgress"></div></div>
<div class="card"><h2>접근·변경 이력</h2><p class="small">로그인, 문서 열람·다운로드·변경, 검색, 온보딩, 계정 변경과 권한 거절을 기록합니다.</p>
<div class="admin-controls"><div><label for="auditActor">직원 필터</label><select id="auditActor"><option value="">전체 직원</option></select></div><div><label for="auditAction">작업 필터</label><select id="auditAction"><option value="">전체 작업</option>${Object.entries(actions).map(([id,name])=>`<option value="${id}">${name}</option>`).join('')}</select></div><div><label for="auditResult">결과 필터</label><select id="auditResult"><option value="">전체 결과</option><option value="success">정상</option><option value="denied">거절</option></select></div><div><label for="auditStart">시작일 (한국 시간)</label><input id="auditStart" type="date"></div><div><label for="auditEnd">종료일 (한국 시간)</label><input id="auditEnd" type="date"></div></div>
<div class="row intranet-actions"><button id="searchAudit">이력 조회</button><span id="auditCount" class="small"></span></div><div class="table-scroll"><table class="audit-table"><thead><tr><th>한국 시간</th><th>직원</th><th>작업</th><th>대상</th><th>결과·정보</th></tr></thead><tbody id="auditRows"></tbody></table></div><div class="row intranet-actions"><button id="auditPrev">이전 이력</button><button id="auditNext">다음 이력</button></div><p id="adminStatus" class="resource-status" role="status"></p></div>`;
$('intranet').append(admin);

const youtubeWidget=window.ClinicWorkspaceYouTube.create({api:call,getUser,deleteDocument:async id=>deleteDocument(await call('/api/documents/'+encodeURIComponent(id))),openDocument:id=>openDocument(id).catch(e=>{if(!e.stale)toast(e.message);}),onSaved:refreshDocs,toast});
function clear(){
 youtubeWidget.clear();youtubeFor=null;documentSeq++;epoch++;if($('wikiCount'))$('wikiCount').textContent='—';allDocs=[];selectedDoc=null;editingDoc=null;courses=[];adminCourses=[];users=[];editingUser=null;editingCourse=null;
 for(const id of ['libraryList','resourceDetail','onboardingCourses','onboardingReader','adminUsers','adminProgress','auditRows','courseAdminList'])$(id).replaceChildren();
 ['documentEditor','courseForm','employeeForm'].forEach(id=>$(id).reset());
 ['documentEditor','employeeForm'].forEach(id=>toggle(id,false));
 $('resourceDetail').innerHTML='<div class="empty-panel">자료를 선택하면 원문과 출처를 확인할 수 있습니다.</div>';
 ['docSaveStatus','libraryStatusText','employeeStatus','courseSaveStatus','onboardingStatus','adminStatus','libraryQuery'].forEach(id=>$(id).textContent='');
 $('libraryQuery').value='';$('libraryKind').value='';$('libraryStatus').value='active';
 $('auditActor').innerHTML='<option value="">전체 직원</option>';$('auditCount').textContent='';auditOffset=0;auditTotal=0;
}
function setUser(){
 clear();const manager=isAdmin();
 toggle('tab-administration',manager);toggle('courseAdmin',manager);toggle('newDocument',canEdit());toggle('libraryReindex',canEdit());
 toggle('docApprovalLabel',manager);toggle('doctorApprovalNote',!manager);
 for(const option of $('libraryStatus').options)option.hidden=!canEdit()&&['draft','archived'].includes(option.value)||!manager&&option.value==='archived';
 activate('portal');
}
function wikiList(){
 $('documentsList').innerHTML=allDocs.map(d=>`<div class="source"><strong>${esc(d.title)}</strong><div class="small">${kinds[d.kind]} · v${d.version} · ${d.approved?'승인':'미승인'} · 색인 ${d.indexed_chunks}개</div><button data-wiki-doc="${esc(d.id)}">원문 열람</button></div>`).join('')||'<p class="small">열람 가능한 자료가 없습니다.</p>';
 document.querySelectorAll('[data-wiki-doc]').forEach(b=>b.onclick=run(()=>openDocument(b.dataset.wikiDoc)));
}
async function refreshDocs(){
 if(!getUser())return;
 allDocs=(await call('/api/documents')).documents;if($('wikiCount'))$('wikiCount').textContent=allDocs.filter(d=>d.approved).length;wikiList();fillCourseDocuments();await loadLibrary(allDocs);
}
async function loadLibrary(known){
 const p=new URLSearchParams({q:$('libraryQuery').value.trim(),kind:$('libraryKind').value,status:$('libraryStatus').value});
 const r=Array.isArray(known)&&p.get('status')==='active'?{documents:known.filter(d=>(!p.get('kind')||d.kind===p.get('kind'))&&(!p.get('q')||[d.title,d.summary,d.source_author,...d.tags].join(' ').toLocaleLowerCase().includes(p.get('q').toLocaleLowerCase())))}:await call('/api/documents?'+p);$('libraryCount').textContent=`${r.documents.length}개 자료`;
 $('libraryList').innerHTML=r.documents.map(d=>`<article class="resource-item"><h3>${esc(d.title)}</h3><span class="pill">${kinds[d.kind]}</span> <span class="pill ${d.approved?'':'warn'}">${d.archived?'보관함':d.approved?'승인됨':'미승인'}</span><div class="small">${esc(d.category)} · v${d.version} · ${when(d.updated)}</div><p class="small">${esc(d.summary)}</p><div class="resource-tags">${d.tags.map(t=>`<span class="pill">${esc(t)}</span>`).join('')}</div><button data-open-resource="${esc(d.id)}">자료 열람</button></article>`).join('')||'<p class="empty-panel">조건에 맞는 자료가 없습니다.</p>';
 document.querySelectorAll('[data-open-resource]').forEach(b=>b.onclick=run(()=>openDocument(b.dataset.openResource),'libraryStatusText'));
}
async function deleteDocument(doc){
 if(!canEdit())return false;
 if(!confirm(`“${doc.title}” 자료를 삭제할까요?\n원문·버전 이력·검색 색인과 연결된 유튜브 작업을 삭제합니다. 온보딩에 연결된 항목도 제거되며 복원할 수 없습니다.`))return false;
 await call('/api/documents/'+encodeURIComponent(doc.id),{method:'DELETE',body:{expected_version:doc.version}});
 if(selectedDoc?.id===doc.id){selectedDoc=null;$('resourceDetail').innerHTML='<div class="empty-panel">자료를 삭제했습니다. 다른 자료를 선택하세요.</div>';}
 if(editingDoc?.id===doc.id){editingDoc=null;toggle('documentEditor',false);}
 await refreshDocs();await youtubeWidget.refresh();
 toast('자료와 연결된 검색 색인·유튜브 작업을 삭제했습니다.');
 return true;
}
async function openDocument(id){
 const seq=++documentSeq;activate('resources');$('resourceDetail').innerHTML='<div class="empty-panel" role="status">원문을 불러오는 중…</div>';
 const doc=await call('/api/documents/'+encodeURIComponent(id));if(seq!==documentSeq)return;selectedDoc=doc;
 const safeUrl=/^https?:\/\//i.test(doc.source_url||'')?`<p class="small">출처: <a href="${esc(doc.source_url)}" target="_blank" rel="noopener noreferrer">${esc(doc.source_url)}</a></p>`:'';
 $('resourceDetail').innerHTML=`<div class="resource-heading"><h2>${esc(doc.title)}</h2><span class="pill">v${doc.version} · ${doc.archived?'보관함':doc.approved?'승인':'미승인'}</span></div><p class="small">${kinds[doc.kind]} · ${esc(doc.category)} · ${esc(doc.roles.map(r=>roles[r]).join(', '))}</p><p class="small">채널·작성자 ${esc(doc.source_author||'미입력')} · 발행일 ${esc(doc.source_date||'미입력')}</p>${safeUrl}${doc.youtube_source?`<div class="banner">영상 자동 수집 · ${{manual_caption:'유튜브 제공 자막',auto_caption:'유튜브 자동 생성 자막',whisper:'Whisper 음성 전사'}[doc.youtube_source.method]||esc(doc.youtube_source.method)} · 언어 ${esc(doc.youtube_source.language)}<br><span class="small">수집일 ${when(doc.youtube_source.fetched)} · 원문·약명·수치 검토 후 검색에 사용하세요.</span></div>`:''}<p>${esc(doc.summary)}</p><pre class="resource-body">${esc(doc.content)}</pre><div class="row"><a class="button" href="/api/documents/${encodeURIComponent(doc.id)}/download">원문 TXT 다운로드</a>${canEdit()&&!doc.archived?'<button id="editResource">자료 수정</button>':''}${isAdmin()&&!doc.archived?`<button id="approveResource">${doc.approved?'검색 승인 해제':'위키 검색 사용 승인'}</button>`:''}${canEdit()?`<button id="archiveResource">${doc.archived?'보관 해제':'검색에서 숨기기·보관'}</button><button id="showVersions">버전 이력</button><button id="deleteResource" class="danger">자료 삭제</button>`:''}</div><div id="documentVersions"></div><p id="resourceActionStatus" class="resource-status" role="status"></p>`;
 if($('editResource'))$('editResource').onclick=()=>editDocument(doc);
 if($('approveResource'))$('approveResource').onclick=run(async()=>{await call(`/api/documents/${doc.id}/approval`,{method:'POST',body:{expected_version:doc.version,approved:!doc.approved}});await refreshDocs();await openDocument(doc.id);toast('검색 승인 상태를 변경했습니다. 승인한 자료는 다음 위키 검색 때 자동 반영됩니다.');},'resourceActionStatus');
 if($('archiveResource'))$('archiveResource').onclick=run(async()=>{await call(`/api/documents/${doc.id}/archive`,{method:'POST',body:{expected_version:doc.version,archived:!doc.archived}});await refreshDocs();if(isAdmin())await openDocument(doc.id);else $('resourceDetail').textContent='자료를 보관했습니다. 보관 해제는 관리자에게 요청하세요.';toast('자료 상태를 변경했습니다.');},'resourceActionStatus');
 if($('showVersions'))$('showVersions').onclick=run(()=>loadVersions(doc),'resourceActionStatus');
 if($('deleteResource'))$('deleteResource').onclick=run(()=>deleteDocument(doc),'resourceActionStatus');
}
function newDocument(){
 editingDoc=null;$('documentEditor').reset();$('docCategory').value='일반';$('docEditorHeading').textContent='새 자료 등록';$('docSaveStatus').textContent='저장 후 관리자 승인·색인 생성으로 검색에 반영합니다.';toggle('documentEditor',true);$('documentEditor').scrollIntoView({behavior:'smooth',block:'start'});
}
function editDocument(doc){
 editingDoc=doc;toggle('documentEditor',true);$('docEditorHeading').textContent=`자료 수정 · ${doc.id} · v${doc.version}`;
 for(const [id,key] of [['docTitle','title'],['docContent','content'],['docKind','kind'],['docUrl','source_url'],['docCategory','category'],['docSummary','summary'],['docAuthor','source_author'],['docDate','source_date']])$(id).value=doc[key]||'';
 $('docTags').value=doc.tags.join(', ');setRoles('docRole',doc.roles);$('docApproved').checked=isAdmin()&&doc.approved;$('docFile').value='';$('docChangeNote').value='';$('docSaveStatus').textContent='수정하면 새 버전이 보존되고 이전 검색 색인이 제거됩니다.';
 $('documentEditor').scrollIntoView({behavior:'smooth',block:'start'});
}
async function loadVersions(doc){
 const r=await call(`/api/documents/${doc.id}/versions`);
 $('documentVersions').innerHTML='<h3 style="margin-top:20px">보존한 버전</h3>'+r.versions.map(v=>`<div class="source"><strong>v${v.version} · ${esc(v.title)}</strong><p class="small">${when(v.created)} · ${esc(v.actor)}<br>${esc(v.note)}</p>${isAdmin()&&v.version!==doc.version?`<button data-restore-version="${v.version}">이 원문을 새 버전으로 복원</button>`:''}</div>`).join('');
 document.querySelectorAll('[data-restore-version]').forEach(b=>b.onclick=run(async()=>{await call(`/api/documents/${doc.id}/restore`,{method:'POST',body:{expected_version:doc.version,version:Number(b.dataset.restoreVersion)}});await refreshDocs();await openDocument(doc.id);toast('새 버전으로 복원했습니다. 관리자 재승인한 자료는 다음 위키 검색 때 자동 반영됩니다.');},'resourceActionStatus'));
}
$('documentEditor').onsubmit=run(async e=>{
 e.preventDefault();if(importingDoc)return toast('원문 파일을 불러온 뒤 저장하세요.');$('saveDocument').disabled=true;
 try{
  const body={title:$('docTitle').value,content:$('docContent').value,kind:$('docKind').value,source_url:$('docUrl').value,category:$('docCategory').value,tags:$('docTags').value.split(','),summary:$('docSummary').value,source_author:$('docAuthor').value,source_date:$('docDate').value,roles:selectedRoles('docRole'),approved:isAdmin()&&$('docApproved').checked,change_note:$('docChangeNote').value};
  if(editingDoc)body.expected_version=editingDoc.version;
  const r=await call('/api/documents'+(editingDoc?'/'+editingDoc.id:''),{method:editingDoc?'PUT':'POST',body});
  await refreshDocs();await openDocument(r.id);toggle('documentEditor',false);toast(`문서 v${r.version} 저장 완료. 승인 자료는 다음 위키 검색 때 자동 반영됩니다.`);
 }finally{$('saveDocument').disabled=false;}
},'docSaveStatus');
$('docFile').onchange=run(async e=>{
 const file=e.target.files[0];if(!file)return;const form=new FormData();form.append('file',file);
 importingDoc=true;['docTitle','docContent','docKind','docFile','saveDocument'].forEach(id=>$(id).disabled=true);
 $('docSaveStatus').textContent='원문 파일을 불러오는 중…';
 try{const r=await call('/api/documents/import',{method:'POST',form});$('docContent').value=r.content;if(!$('docTitle').value)$('docTitle').value=r.title;if(r.kind==='youtube_transcript')$('docKind').value=r.kind;$('docSaveStatus').textContent=r.message;}
 finally{importingDoc=false;['docTitle','docContent','docKind','docFile','saveDocument'].forEach(id=>$(id).disabled=false);}
},'docSaveStatus');
$('newDocument').onclick=newDocument;$('closeDocEditor').onclick=()=>toggle('documentEditor',false);
$('librarySearch').onclick=run(loadLibrary,'libraryStatusText');$('libraryQuery').onkeydown=e=>{if(e.key==='Enter')$('librarySearch').click();};
['libraryKind','libraryStatus'].forEach(id=>$(id).onchange=run(loadLibrary,'libraryStatusText'));
$('libraryRefresh').onclick=run(refreshDocs,'libraryStatusText');
async function reindex(){
 if(!canEdit())return;
 $('libraryReindex').disabled=true;$('reindexDocs').disabled=true;
 $('libraryStatusText').textContent=$('indexStatus').textContent='승인 자료의 실제 임베딩을 생성하는 중…';
 try{const r=await call('/api/documents/reindex',{method:'POST',body:{}});$('libraryStatusText').textContent=$('indexStatus').textContent=`색인 완료 · ${r.chunks}개 구간 · ${r.model}`;await refreshDocs();await onReindex();}
 finally{$('libraryReindex').disabled=false;$('reindexDocs').disabled=false;}
}
$('libraryReindex').onclick=run(reindex,'libraryStatusText');

async function loadCourses(){
 courses=(await call('/api/onboarding')).courses;
 $('onboardingCourses').innerHTML=courses.map(c=>`<article class="course-card"><div class="resource-heading"><h3>${esc(c.title)}</h3><span class="pill">${c.completed}/${c.total} · ${c.percent}%</span></div><p class="small">${esc(c.description)}</p><progress max="100" value="${c.percent}" aria-label="${esc(c.title)} 진행률"></progress>${c.blocked?`<p class="small">자료 변경으로 ${c.blocked}개 단계가 잠겼습니다. 관리자 확인이 필요합니다.</p>`:''}${c.steps.map(d=>`<div class="course-step"><strong>${d.ordinal}. ${esc(d.title)}</strong> <span class="pill ${d.needs_review?'warn':''}">${d.completed?'읽음 확인 완료':d.needs_review?'새 버전 재확인 필요':'확인 대기'} · v${d.version}</span><div class="row intranet-actions"><button data-course-read="${esc(d.id)}">자료 읽기</button><button data-complete-course="${esc(c.id)}" data-doc-id="${esc(d.id)}" data-doc-version="${d.version}" data-done="${d.completed}" ${!d.read_current&&!d.completed?'disabled':''}>${d.completed?'읽음 확인 취소':'읽음 확인 (본인 확인)'}</button></div></div>`).join('')}</article>`).join('')||'<p class="empty-panel">배정된 온보딩 과정이 없습니다.</p>';
 document.querySelectorAll('[data-course-read]').forEach(b=>b.onclick=run(async()=>{const d=await call('/api/documents/'+b.dataset.courseRead);$('onboardingReader').innerHTML=`<h3>${esc(d.title)} · v${d.version}</h3><pre class="resource-body">${esc(d.content)}</pre><p class="small">원문을 읽은 뒤 위 과정에서 읽음 확인을 완료하세요.</p>`;await loadCourses();$('onboardingReader').scrollIntoView({behavior:'smooth'});},'onboardingStatus'));
 document.querySelectorAll('[data-complete-course]').forEach(b=>b.onclick=run(async()=>{await call(`/api/onboarding/${b.dataset.completeCourse}/steps/${b.dataset.docId}`,{method:'POST',body:{version:Number(b.dataset.docVersion),completed:b.dataset.done!=='true'}});await loadCourses();toast('본인 온보딩 진행률을 저장했습니다.');},'onboardingStatus'));
}
function fillCourseDocuments(){
 const keep=new Set([...$('courseDocuments').selectedOptions].map(o=>o.value));
 $('courseDocuments').innerHTML=allDocs.filter(d=>d.approved&&!d.archived).map(d=>`<option value="${esc(d.id)}" ${keep.has(d.id)?'selected':''}>${esc(d.title)} · ${esc(d.roles.map(r=>roles[r]).join('/'))}</option>`).join('');
}
function newCourse(){editingCourse=null;$('courseForm').reset();$('courseSaveStatus').textContent='새 과정 작성 중';}
async function loadAdminCourses(){
 const r=await call('/api/admin/onboarding');adminCourses=r.courses;
 $('courseAdminList').innerHTML=r.courses.map(c=>`<div class="source"><strong>${esc(c.title)}</strong><span class="small"> · ${c.active?'사용 중':'중지'} · 개정 ${c.revision}</span> <button data-edit-course="${esc(c.id)}">과정 수정</button> <button data-toggle-course="${esc(c.id)}">${c.active?'과정 중지':'과정 재개'}</button></div>`).join('');
 document.querySelectorAll('[data-toggle-course]').forEach(b=>b.onclick=run(async()=>{const course=adminCourses.find(c=>c.id===b.dataset.toggleCourse);await call(`/api/admin/onboarding/${course.id}/active`,{method:'PATCH',body:{expected_revision:course.revision,active:!course.active}});await loadAdminCourses();await loadCourses();toast('과정 사용 상태를 변경했습니다.');},'courseSaveStatus'));
 document.querySelectorAll('[data-edit-course]').forEach(b=>b.onclick=()=>{editingCourse=adminCourses.find(c=>c.id===b.dataset.editCourse);$('courseTitle').value=editingCourse.title;$('courseDescription').value=editingCourse.description;$('courseActive').checked=!!editingCourse.active;setRoles('courseRole',editingCourse.roles);[...$('courseDocuments').options].forEach(o=>o.selected=editingCourse.document_ids.includes(o.value));$('courseSaveStatus').textContent=`${editingCourse.id} · 개정 ${editingCourse.revision} 수정 중`;});
 $('adminProgress').innerHTML='<table><thead><tr><th>직원</th><th>과정</th><th>읽음 확인</th><th>진행률</th></tr></thead><tbody>'+r.progress.map(p=>`<tr><td>${esc(p.name)}<br><span class="small">${esc(p.user_id)}</span></td><td>${esc(p.title)}</td><td>${p.completed}/${p.total}${p.blocked?' · 잠긴 자료 '+p.blocked+'개':''}</td><td>${p.percent}%</td></tr>`).join('')+'</tbody></table>';
}
$('courseForm').onsubmit=run(async e=>{e.preventDefault();const body={title:$('courseTitle').value,description:$('courseDescription').value,roles:selectedRoles('courseRole'),active:$('courseActive').checked,document_ids:[...$('courseDocuments').selectedOptions].map(o=>o.value)};if(editingCourse)body.expected_revision=editingCourse.revision;await call('/api/admin/onboarding'+(editingCourse?'/'+editingCourse.id:''),{method:editingCourse?'PUT':'POST',body});newCourse();await loadAdminCourses();await loadCourses();toast('온보딩 과정을 저장했습니다.');},'courseSaveStatus');
$('newCourse').onclick=newCourse;$('refreshOnboarding').onclick=run(loadCourses,'onboardingStatus');

function newEmployee(){editingUser=null;$('employeeForm').reset();$('employeeId').readOnly=false;$('employeeRole').disabled=false;$('employeeActive').disabled=false;toggle('employeeForm',true);$('employeeStatus').textContent='가상 직원 계정 생성 · 공통 비밀번호 ClinicDemo42!';}
async function loadUsers(){
 users=(await call('/api/users')).users;
 $('adminUsers').innerHTML='<table><thead><tr><th>직원</th><th>역할</th><th>상태</th><th>관리</th></tr></thead><tbody>'+users.map(u=>`<tr><td>${esc(u.name)}<br><span class="small">${esc(u.id)}</span></td><td>${roles[u.role]}</td><td>${u.active?'활성':'비활성'}</td><td><button data-edit-user="${esc(u.id)}">계정 수정</button></td></tr>`).join('')+'</tbody></table>';
 document.querySelectorAll('[data-edit-user]').forEach(b=>b.onclick=()=>{editingUser=users.find(u=>u.id===b.dataset.editUser);toggle('employeeForm',true);$('employeeId').value=editingUser.id;$('employeeId').readOnly=true;$('employeeName').value=editingUser.name;$('employeeRole').value=editingUser.role;$('employeeActive').checked=!!editingUser.active;const self=editingUser.id===getUser().id;$('employeeRole').disabled=self;$('employeeActive').disabled=self;$('employeeStatus').textContent=`${editingUser.id} · 개정 ${editingUser.revision} 수정 중`;});
 const actor=$('auditActor').value;$('auditActor').innerHTML='<option value="">전체 직원</option><option value="anonymous">비로그인·인증 실패</option>'+users.map(u=>`<option value="${esc(u.id)}">${esc(u.name)} (${esc(u.id)})</option>`).join('');$('auditActor').value=actor;
}
$('employeeForm').onsubmit=run(async e=>{e.preventDefault();const body={name:$('employeeName').value,role:$('employeeRole').value};if(editingUser){body.active=$('employeeActive').checked;body.expected_revision=editingUser.revision;}else body.id=$('employeeId').value;await call(editingUser?'/api/admin/users/'+editingUser.id:'/api/admin/users',{method:editingUser?'PATCH':'POST',body});toggle('employeeForm',false);await loadUsers();await loadAdminCourses();$('employeeStatus').textContent='계정을 저장했습니다. 새 계정의 시연 비밀번호는 ClinicDemo42!입니다.';toast('계정 정보를 저장했습니다.');},'employeeStatus');
async function loadAudit(reset=false){
 if(reset)auditOffset=0;
 const p=new URLSearchParams({actor:$('auditActor').value,action:$('auditAction').value,result:$('auditResult').value,start:$('auditStart').value,end:$('auditEnd').value,offset:auditOffset,limit:30});
 const r=await call('/api/admin/audit?'+p);auditTotal=r.total;
 $('auditRows').innerHTML=r.entries.map(e=>`<tr><td>${when(e.created)}</td><td>${esc(e.actor_name||e.actor)}<br><span class="small">${esc(e.actor)}</span></td><td>${esc(actions[e.action]||e.action)}</td><td>${esc(e.object_id||'—')}</td><td>${e.result==='denied'?'거절':'정상'}<br><span class="small">${esc(auditInfo(e.detail))}</span></td></tr>`).join('')||'<tr><td colspan="5">조건에 맞는 이력이 없습니다.</td></tr>';
 $('auditCount').textContent=`${r.total}건 · ${r.total?auditOffset+1:0}~${Math.min(auditOffset+r.entries.length,r.total)} 표시`;$('auditPrev').disabled=auditOffset===0;$('auditNext').disabled=auditOffset+r.limit>=r.total;
}
async function loadAdmin(){if(!isAdmin())return;await Promise.all([loadUsers(),loadAdminCourses(),loadAudit()]);}
$('newEmployee').onclick=newEmployee;$('refreshAdmin').onclick=run(loadAdmin,'adminStatus');$('searchAudit').onclick=run(()=>loadAudit(true),'adminStatus');
$('auditPrev').onclick=run(async()=>{auditOffset=Math.max(0,auditOffset-30);await loadAudit();},'adminStatus');
$('auditNext').onclick=run(async()=>{if(auditOffset+30<auditTotal){auditOffset+=30;await loadAudit();}},'adminStatus');
new MutationObserver(()=>{if(getUser()&&$('resources').classList.contains('active')&&youtubeFor!==getUser().id){youtubeFor=getUser().id;youtubeWidget.setUser();}if(!getUser())youtubeFor=null;}).observe($('resources'),{attributes:true,attributeFilter:['class']});
return {clear,setUser,refreshDocs,reindex:run(reindex,'libraryStatusText'),openDocument:run(openDocument,'libraryStatusText')};
}
};
