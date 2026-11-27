/* Presentation layer: preserve all existing nodes and business handlers. */
(()=>{'use strict';const q=s=>document.querySelector(s),all=s=>[...document.querySelectorAll(s)];
q('.brand').textContent='CLINIC WORKSPACE';q('.top>.small').textContent='클리닉 업무 시스템';
const titles={home:'업무 홈',clinical:'진료 사진 · 기록',intranet:'직원 업무',geo:'GEO'};
Object.entries(titles).forEach(([id,title])=>q('#'+id+' h1').textContent=title);
q('#home .intro p').textContent='공지와 나의 업무 현황을 확인하고 필요한 업무로 이동하세요.';q('#home .intro>.pill').textContent='시연 환경';
const oldOverview=q('#home table')?.closest('.card');if(oldOverview)oldOverview.remove();
// Keep the overview node used by the connected initializer, but make it concise.
q('#home>.banner').textContent='가상 데이터 시연 환경 · 진료기록은 모의 CRM에 저장됩니다.';
all('#home [data-page]').forEach(b=>b.textContent=titles[b.dataset.page]+' 열기');
const nav=q('aside nav');nav.setAttribute('aria-label','업무 메뉴');
const defs=[['home','업무 홈','⌂'],['clinical','진료 사진 · 기록','▣'],['notices','공지사항','▤'],['wiki','CS 위키','⌕'],['resources','자료실','▤'],['leave','연차 관리','▦'],['onboarding','온보딩','☑'],['charts','차트 대기열','≡'],['geo','GEO','▧'],['administration','관리자','⚙']];
const oldButtons=new Map(all('nav [data-page]').map(b=>[b.dataset.page,b]));nav.replaceChildren();
const toggle=document.createElement('button');toggle.className='nav-toggle';toggle.textContent='☰';toggle.setAttribute('aria-label','업무 메뉴 열기');toggle.setAttribute('aria-expanded','false');q('.top').prepend(toggle);
const close=()=>{document.body.classList.remove('menu-open');toggle.setAttribute('aria-expanded','false');};toggle.onclick=()=>{const open=document.body.classList.toggle('menu-open');toggle.setAttribute('aria-expanded',String(open));};document.addEventListener('keydown',e=>{if(e.key==='Escape')close();});document.addEventListener('click',e=>{if(!e.target.closest('aside')&&!e.target.closest('.nav-toggle'))close();});
for(const [id,label,icon] of defs){if(['home','geo','administration'].includes(id)){const group=document.createElement('div');group.className='nav-group';group.textContent=id==='home'?'업무':id==='geo'?'콘텐츠':'관리';nav.append(group);}const b=oldButtons.get(id)||document.createElement('button');b.innerHTML='<span aria-hidden="true">'+icon+'</span>'+label;b.dataset.destination=id;nav.append(b);if(!oldButtons.has(id)){b.onclick=()=>{const target=q('#intranet .tabs [data-sub="'+id+'"]');if(!target||target.classList.contains('hidden'))return;page('intranet');target.click();close();};}else b.addEventListener('click',close);}
function sync(){const active=q('.page.active')?.id,sub=q('#intranet .subpage.active')?.id;all('nav [data-destination]').forEach(b=>{const id=b.dataset.destination,target=q('#intranet .tabs [data-sub="'+id+'"]');if(target)b.classList.toggle('hidden',target.classList.contains('hidden'));const selected=id===(active==='intranet'?sub:active);b.classList.toggle('active',selected);if(selected)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});}
all('.page,.subpage,#intranet .tabs button').forEach(el=>new MutationObserver(sync).observe(el,{attributes:true,attributeFilter:['class']}));sync();
q('#patientSearch').closest('.card').classList.add('patient-search');q('#beforePhoto').closest('.card').classList.add('photo-workspace');q('#tabletRecordingMount').closest('.card').classList.add('record-workspace');q('#mobileEntry').parentElement.classList.add('mobile-access');
q('#patientResults').addEventListener('click',e=>{const b=e.target.closest('.patient-option');if(b)all('.patient-option').forEach(x=>x.classList.toggle('selected',x===b));});q('#wiki>.grid').classList.add('wiki-layout');
const instructions=all('#tabletRecording>p.small:not([id])');const note=document.createElement('details');note.className='help-details';note.innerHTML='<summary>등록 모드와 검토 안내</summary>';q('#tabletRecording').append(note);instructions.forEach(p=>note.append(p));
const status=q('#serverStatus'),detail=document.createElement('details');detail.className='system-details';detail.innerHTML='<summary>시스템 연결 상태</summary>';status.before(detail);detail.append(status);
q('#intranet .intro p').textContent='자료를 찾고, 휴가를 신청하고, 필요한 교육을 확인하세요.';

const importer=q('#youtubeImporter');if(importer){const d=document.createElement('details');d.innerHTML='<summary>유튜브 영상에서 새 자료 등록</summary>';[...importer.children].forEach(c=>d.append(c));importer.append(d);}
document.documentElement.removeAttribute('data-app-loading');
})();
