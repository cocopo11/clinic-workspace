'use strict';
/* Personal work home and persistent notices. No polling of open editors. */
window.ClinicWorkspaceStaffPortal = {
create({api, getUser, toast, openDocument}) {
 const $=id=>document.getElementById(id);
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const roles={admin:'관리자',doctor:'의료진',staff:'CS 직원'};
 const when=stamp=>new Date(stamp*1000).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul'});
 const manager=()=>getUser()?.role==='admin';
 let epoch=0, dashboardSeq=0, listSeq=0, detailSeq=0, selected=null, editing=null, offset=0, timer=null;
 const dashboard=document.createElement('div');dashboard.id='staffDashboard';
 $('home').querySelector('.intro').after(dashboard);
 $('home').querySelectorAll(':scope > .grid').forEach(el=>el.classList.add('hidden'));
 // Existing modules still update these three counters; keep their nodes without sample cards.
 const compatibility=document.createElement('div');compatibility.hidden=true;
 for(const id of ['balanceHome','chartCount','wikiCount'])compatibility.append($(id));
 $('portal').replaceChildren(compatibility);
 const homeLink=document.createElement('button');homeLink.textContent='나의 업무 홈 열기';homeLink.onclick=()=>page('home');$('portal').append(homeLink);
 document.querySelector('#intranet [data-sub="portal"]').onclick=()=>page('home');
 const tab=document.createElement('button');tab.dataset.sub='notices';tab.id='tab-notices';tab.textContent='공지사항';document.querySelector('#intranet .tabs').append(tab);
 const panel=document.createElement('div');panel.id='notices';panel.className='subpage';
 panel.innerHTML=`<div class="card"><div class="portal-heading"><div><h2>공지사항</h2></div><button id="newNotice" class="primary hidden">공지 작성</button></div>
 <form id="noticeSearchForm" class="notice-filters"><div><label for="noticeQuery">공지 검색</label><input id="noticeQuery" maxlength="120" placeholder="제목·내용 검색"></div><div id="noticeStateField" class="hidden"><label for="noticeState">게시 상태</label><select id="noticeState"><option value="published">게시 중</option><option value="draft">임시 저장</option><option value="archived">보관함</option><option value="all">전체</option></select></div><label class="tick"><input id="noticeUnread" type="checkbox"> 미확인만</label><button type="submit">검색</button></form>
 <p id="noticeListStatus" class="resource-status" role="status"></p></div>
 <div class="notice-layout"><div class="card"><div id="noticeList"></div><div class="row notice-pages"><button id="noticePrev">이전</button><span id="noticePageInfo" class="small"></span><button id="noticeNext">다음</button></div></div><div id="noticeDetail" class="card" aria-live="polite"></div></div>
 <form id="noticeEditor" class="card hidden"><div class="portal-heading"><h2 id="noticeEditorTitle">공지 작성</h2><button type="button" id="closeNoticeEditor">편집 닫기</button></div>
 <label for="noticeTitle">제목</label><input id="noticeTitle" maxlength="120" required>
 <label for="noticeBody">내용</label><textarea id="noticeBody" maxlength="20000" rows="10" required></textarea>
 <fieldset class="notice-roles"><legend>열람 대상</legend>${Object.entries(roles).map(([id,name])=>`<label class="tick"><input type="checkbox" name="noticeRole" value="${id}" checked> ${name}</label>`).join('')}</fieldset>
 <div class="row"><label class="tick"><input id="noticePinned" type="checkbox"> 상단 고정</label><label class="tick"><input id="noticePublished" type="checkbox" checked> 직원에게 게시</label></div>
 <p class="small">‘직원에게 게시’를 해제하면 관리자만 볼 수 있는 임시 저장 상태가 됩니다.</p>
 <div class="row"><button type="submit" id="saveNotice" class="primary">저장</button><span id="noticeSaveStatus" class="resource-status" role="status"></span></div></form>`;
 $('intranet').append(panel);

 async function call(path,options){const started=epoch;const result=await api(path,options);if(started!==epoch)throw {stale:true};return result;}
 function run(fn,target){return async(...args)=>{const started=epoch;try{return await fn(...args);}catch(e){if(e.stale||started!==epoch)return;if(target&&$(target))$(target).textContent=e.message;toast(e.message||'요청을 처리하지 못했습니다.');}};}
 function activate(){
  document.querySelectorAll('#intranet [data-sub]').forEach(b=>b.classList.toggle('active',b.dataset.sub==='notices'));
  document.querySelectorAll('#intranet .subpage').forEach(el=>el.classList.toggle('active',el.id==='notices'));
 }
 function navigate(id){
  if(['clinical','geo','home'].includes(id)){page(id);return;}
  page('intranet');document.querySelector(`#intranet .tabs [data-sub="${id}"]`)?.click();
 }
 const empty=text=>`<p class="portal-empty">${esc(text)}</p>`;
 const row=(title,meta,action,extra='')=>`<div class="portal-item"><button class="portal-link" ${action}>${esc(title)}</button><div class="small">${meta}</div>${extra}</div>`;
 function bindDashboard(){
  dashboard.querySelectorAll('[data-portal-go]').forEach(b=>b.onclick=()=>navigate(b.dataset.portalGo));
  dashboard.querySelectorAll('[data-portal-notice]').forEach(b=>b.onclick=run(async()=>{navigate('notices');await showNotice(b.dataset.portalNotice);},'noticeListStatus'));
  dashboard.querySelectorAll('[data-portal-doc]').forEach(b=>b.onclick=run(async()=>{navigate('resources');await openDocument(b.dataset.portalDoc);},'portalStatus'));
  dashboard.querySelectorAll('[data-portal-unread]').forEach(b=>b.onclick=()=>{$('noticeUnread').checked=true;$('noticeState').value='published';navigate('notices');});
  $('refreshPortal').onclick=run(refreshDashboard,'portalStatus');
 }
 async function refreshDashboard(){
  if(!getUser())return;
  const seq=++dashboardSeq,r=await call('/api/portal');if(seq!==dashboardSeq)return;
  const statuses={pending:'승인 대기',approved:'승인',rejected:'반려'};
  dashboard.innerHTML=`<div class="portal-heading portal-welcome"><div><h2>${esc(r.user.name)}님의 오늘 업무</h2><p class="small">${esc(r.today)} · ${roles[r.user.role]} · 본인 계정 기준</p></div><button id="refreshPortal">현황 새로고침</button></div>
  <div class="portal-stats">
   <button class="card portal-stat" data-portal-go="leave"><span class="small">내 잔여 연차</span><strong>${r.leave.balance}<small> 일</small></strong><span class="small">내 신청 승인 대기 ${r.leave.pending}건</span></button>
   <button class="card portal-stat" data-portal-unread><span class="small">확인할 공지</span><strong>${r.notices.unread}<small> 건</small></strong><span class="small">미확인 공지 보기 →</span></button>
   <button class="card portal-stat" data-portal-go="resources"><span class="small">읽지 않은 최신 자료</span><strong>${r.documents.unread}<small> 개</small></strong><span class="small">승인된 열람 가능 자료</span></button>
   <button class="card portal-stat" data-portal-go="onboarding"><span class="small">나의 온보딩</span><strong>${r.onboarding.percent}<small> %</small></strong><span class="small">${r.onboarding.completed} / ${r.onboarding.total}개 자료 확인</span></button>
  </div>
  ${r.approvals?`<div class="card portal-approvals"><div class="portal-heading"><h2>관리자 승인 대기</h2><span class="small">연차 ${r.approvals.leave_count}건 · 자료 ${r.approvals.document_count}건</span></div><div class="portal-columns"><div><h3>연차 신청</h3>${r.approvals.leaves.map(l=>row(l.name,`${esc(l.date)} · ${l.days}일`,'data-portal-go="leave"')).join('')||empty('승인 대기 중인 신청이 없습니다.')}<button data-portal-go="leave">연차 승인함</button></div><div><h3>미승인 자료</h3>${r.approvals.documents.map(d=>row(d.title,`v${d.version} · 검색 사용 승인 필요`,`data-portal-doc="${esc(d.id)}"`)).join('')||empty('승인 대기 중인 자료가 없습니다.')}<button data-portal-go="resources">자료 관리</button></div></div></div>`:''}
  <div class="portal-columns"><div class="card"><div class="portal-heading"><h2>최근 공지</h2><button class="quiet" data-portal-go="notices">전체 보기</button></div>${r.notices.items.map(n=>row(n.title,`${n.pinned?'상단 고정 · ':''}${when(n.updated)} · ${n.read?'확인 완료':'미확인'}`,`data-portal-notice="${esc(n.id)}"`)).join('')||empty('게시된 공지가 없습니다.')}</div>
  <div class="card"><div class="portal-heading"><h2>나의 연차</h2><button class="quiet" data-portal-go="leave">신청·내역</button></div>${r.leave.upcoming.length?`<p class="portal-next">다음 휴가 ${esc(r.leave.upcoming[0].date)} · ${r.leave.upcoming[0].days}일</p>`:''}${r.leave.recent.map(l=>`<div class="portal-item portal-leave"><span>${esc(l.date)} · ${l.days}일</span><span class="pill ${l.status==='pending'?'warn':''}">${statuses[l.status]}</span></div>`).join('')||empty('아직 신청한 연차가 없습니다.')}</div></div>
  <div class="portal-columns"><div class="card"><div class="portal-heading"><h2>확인할 자료</h2><button class="quiet" data-portal-go="resources">자료실</button></div>${r.documents.items.map(d=>row(d.title,`v${d.version} · ${when(d.updated)} 업데이트`,`data-portal-doc="${esc(d.id)}"`)).join('')||empty('열람 가능한 최신 자료를 모두 확인했습니다.')}</div>
  <div class="card"><div class="portal-heading"><h2>교육 진행 현황</h2><button class="quiet" data-portal-go="onboarding">온보딩</button></div>${r.onboarding.courses.map(c=>`<div class="portal-item"><button class="portal-link" data-portal-go="onboarding">${esc(c.title)}</button><div class="portal-progress"><progress max="100" value="${c.percent}" aria-label="${esc(c.title)} 진행률"></progress><span>${c.completed}/${c.total}</span></div>${c.blocked?'<p class="small">관리자의 자료 점검이 필요한 항목이 있습니다.</p>':''}</div>`).join('')||empty('배정된 교육 과정이 없습니다.')}</div></div>
  <div class="card"><h2>자주 사용하는 업무</h2><div class="row portal-shortcuts"><button class="primary" data-portal-go="wiki">CS 위키 검색</button>${['admin','doctor'].includes(r.user.role)?'<button data-portal-go="clinical">진료 사진·기록</button>':''}<button data-portal-go="geo">콘텐츠 작성</button><button data-portal-go="notices">공지사항</button></div></div><p id="portalStatus" class="resource-status" role="status">방금 갱신됨 · 공지 확인과 자료 열람은 본인 계정에 저장됩니다.</p>`;
  bindDashboard();
 }
 function schedule(){clearTimeout(timer);if(getUser()&&$('home').classList.contains('active'))timer=setTimeout(()=>{run(refreshDashboard,'portalStatus')();},200);}
 function badge(n){return `${n.pinned?'<span class="pill">고정</span> ':''}<span class="pill ${n.read?'':'warn'}">${n.archived?'보관':!n.published?'임시 저장':n.read?'확인 완료':'미확인'}</span>`;}
 async function loadNotices(){
  if(!getUser())return;
  const seq=++listSeq,params=new URLSearchParams({q:$('noticeQuery').value.trim(),state:$('noticeState').value,unread:$('noticeUnread').checked,offset,limit:20});
  const r=await call('/api/notices?'+params);if(seq!==listSeq)return;
  if(offset&&offset>=r.total){offset=Math.max(0,Math.floor((r.total-1)/20)*20);return loadNotices();}
  $('noticeList').innerHTML=r.notices.map(n=>`<article class="notice-list-item${selected?.id===n.id?' selected':''}">${badge(n)}<button class="portal-link" data-open-notice="${esc(n.id)}">${esc(n.title)}</button><p class="small">${when(n.updated)} · v${n.version}</p></article>`).join('')||empty('조건에 맞는 공지가 없습니다.');
  $('noticeListStatus').textContent=`${r.total}건`;$('noticePageInfo').textContent=r.total?`${offset+1}–${Math.min(offset+20,r.total)} / ${r.total}`:'0건';
  $('noticePrev').disabled=!offset;$('noticeNext').disabled=offset+20>=r.total;
  panel.querySelectorAll('[data-open-notice]').forEach(b=>b.onclick=run(()=>showNotice(b.dataset.openNotice),'noticeListStatus'));
 }
 async function showNotice(id){
  const seq=++detailSeq,n=await call('/api/notices/'+encodeURIComponent(id));if(seq!==detailSeq)return;selected=n;
  $('noticeDetail').innerHTML=`<div class="row">${badge(n)}</div><h2 class="notice-title">${esc(n.title)}</h2><p class="small">${when(n.updated)} · v${n.version} · ${esc(n.roles.map(r=>roles[r]).join(', '))}</p><div class="notice-body">${esc(n.body)}</div><div class="row notice-detail-actions">${n.published&&!n.archived?`<button id="acknowledgeNotice" class="primary" ${n.read?'disabled':''}>${n.read?'확인 완료':'확인했습니다'}</button>`:''}${manager()?`${!n.archived?'<button id="editNotice">수정</button>':''}<button id="archiveNotice">${n.archived?'보관 해제':'보관'}</button>`:''}</div><p id="noticeDetailStatus" class="resource-status" role="status"></p>`;
  panel.querySelectorAll('[data-open-notice]').forEach(b=>b.closest('article').classList.toggle('selected',b.dataset.openNotice===id));
  if($('acknowledgeNotice'))$('acknowledgeNotice').onclick=run(async()=>{await call(`/api/notices/${n.id}/read`,{method:'POST',body:{expected_version:n.version}});await showNotice(n.id);await loadNotices();schedule();},'noticeDetailStatus');
  if($('editNotice'))$('editNotice').onclick=()=>editNotice(n);
  if($('archiveNotice'))$('archiveNotice').onclick=run(async()=>{await call(`/api/notices/${n.id}/archive`,{method:'POST',body:{expected_version:n.version,archived:!n.archived}});await showNotice(n.id);await loadNotices();schedule();toast(n.archived?'공지 보관을 해제했습니다.':'공지를 보관했습니다.');},'noticeDetailStatus');
 }
 function editNotice(n=null){
  editing=n;$('noticeEditor').reset();$('noticeEditorTitle').textContent=n?'공지 수정':'공지 작성';
  $('noticeTitle').value=n?.title||'';$('noticeBody').value=n?.body||'';$('noticePinned').checked=!!n?.pinned;$('noticePublished').checked=n?!!n.published:true;
  panel.querySelectorAll('[name="noticeRole"]').forEach(el=>el.checked=!n||n.roles.includes(el.value));
  $('noticeSaveStatus').textContent='';$('noticeEditor').classList.remove('hidden');$('noticeEditor').scrollIntoView({behavior:'smooth',block:'start'});$('noticeTitle').focus({preventScroll:true});
 }
 $('noticeEditor').onsubmit=run(async e=>{
  e.preventDefault();if($('saveNotice').disabled)return;
  const started=epoch,n=editing,body={title:$('noticeTitle').value.trim(),body:$('noticeBody').value.trim(),roles:[...panel.querySelectorAll('[name="noticeRole"]:checked')].map(el=>el.value),pinned:$('noticePinned').checked,published:$('noticePublished').checked};
  if(!body.roles.length){$('noticeSaveStatus').textContent='열람 대상을 하나 이상 선택하세요.';return;}
  $('saveNotice').disabled=true;$('noticeSaveStatus').textContent='저장 중…';
  try{
   if(n)body.expected_version=n.version;
   const result=await call(n?`/api/notices/${n.id}`:'/api/notices',{method:n?'PUT':'POST',body});
   editing=null;$('noticeEditor').classList.add('hidden');$('noticeState').value=body.published?'published':'draft';$('noticeQuery').value='';$('noticeUnread').checked=false;offset=0;
   await loadNotices();await showNotice(result.id);schedule();toast(body.published?'공지를 게시했습니다.':'공지를 임시 저장했습니다.');
  }finally{if(started===epoch)$('saveNotice').disabled=false;}
 },'noticeSaveStatus');
 $('newNotice').onclick=()=>editNotice();$('closeNoticeEditor').onclick=()=>{editing=null;$('noticeEditor').classList.add('hidden');};
 $('noticeSearchForm').onsubmit=run(async e=>{e.preventDefault();offset=0;await loadNotices();},'noticeListStatus');
 $('noticePrev').onclick=run(async()=>{offset=Math.max(0,offset-20);await loadNotices();},'noticeListStatus');
 $('noticeNext').onclick=run(async()=>{offset+=20;await loadNotices();},'noticeListStatus');
 tab.onclick=run(async()=>{activate();await loadNotices();},'noticeListStatus');
 document.addEventListener('clinicworkspace:staff-data',schedule);
 new MutationObserver(()=>{if($('home').classList.contains('active'))schedule();}).observe($('home'),{attributes:true,attributeFilter:['class']});
 function clear(){
  epoch++;dashboardSeq++;listSeq++;detailSeq++;clearTimeout(timer);selected=null;editing=null;offset=0;
  dashboard.innerHTML='<p class="portal-empty">로그인하면 나의 업무 현황이 표시됩니다.</p>';
  $('noticeList').replaceChildren();$('noticeDetail').innerHTML=empty('공지를 선택하면 내용을 볼 수 있습니다.');
  $('noticeSearchForm').reset();$('noticeEditor').reset();$('noticeEditor').classList.add('hidden');$('saveNotice').disabled=false;
  for(const id of ['noticeListStatus','noticePageInfo','noticeSaveStatus'])$(id).textContent='';
  $('newNotice').classList.add('hidden');$('noticeStateField').classList.add('hidden');
 }
 function setUser(){clear();$('newNotice').classList.toggle('hidden',!manager());$('noticeStateField').classList.toggle('hidden',!manager());dashboard.innerHTML='<div class="card"><h2>업무 현황 불러오는 중</h2><p id="portalStatus" class="resource-status" role="status"></p><button id="refreshPortal">현황 새로고침</button></div>';$('refreshPortal').onclick=run(refreshDashboard,'portalStatus');schedule();}
 clear();return {clear,setUser,refresh:schedule};
}
};
