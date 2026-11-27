/* A first-time visitor can start without finding or typing demo credentials. */
(()=>{
'use strict';
const ready=new Promise(resolve=>document.readyState==='loading'?document.addEventListener('DOMContentLoaded',resolve,{once:true}):resolve());
const request=async(path,body)=>{
 const r=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
 let data;try{data=await r.json();}catch{throw Error('서버 응답이 지연되거나 일시적으로 연결되지 않았습니다. 잠시 후 다시 시도해 주세요.');}
 if(!r.ok)throw Error(data.detail||'체험 공간에 연결하지 못했습니다.');return data;
};
window.ClinicWorkspaceDemo={
 async init(){
  await ready;if(!window.ClinicWorkspaceRemoteMode)return;
  const form=document.getElementById('loginForm');if(!form)return;
  const username=document.getElementById('username'),password=document.getElementById('password');
  const title=form.querySelector('h1');if(title){title.textContent='업무 시스템 체험';title.nextElementSibling.textContent='가상 환자와 문서로 주요 기능을 둘러보세요.';}
  else{const heading=document.querySelector('#login h2');if(heading)heading.textContent='모바일 서비스 체험';}
  const details=document.createElement('details');details.className='demo-login-details';
  const summary=document.createElement('summary');summary.textContent='직원·운영자 계정으로 로그인';details.append(summary);
  const loginError=document.getElementById('loginError');
  const first=username.closest('label')||form.querySelector('label');let current=first;
  while(current){const next=current.nextSibling;if(current!==loginError)details.append(current);current=next;}
  const panel=document.createElement('div');panel.className='demo-space-panel';
  const message=document.createElement('p');message.className='small';message.textContent='다른 방문자와 분리된 체험 공간을 사용합니다. 별도 계정 입력 없이 시작할 수 있습니다.';
  const start=document.createElement('button');start.type='button';start.textContent='바로 체험하기';start.className='primary';
  const status=document.createElement('p');status.className='demo-space-status';status.setAttribute('role','status');status.textContent='체험 공간 연결 상태 확인 중…';
  panel.append(message,start,status);form.append(panel);if(loginError)form.append(loginError);form.append(details);
  username.value='';password.value='';
  let info={active:false},joining=false;
  const share=document.createElement('button');share.textContent='다른 기기에서 같은 체험 공간 열기';share.type='button';share.className='demo-share';share.hidden=true;
  const target=document.getElementById('accountArea')||document.getElementById('account');
  if(target)target.before(share);
  const update=()=>{share.hidden=!info.active;status.textContent=info.active?'내 체험 공간이 준비되어 있습니다.':'체험 공간은 24시간 후 만료됩니다. 사진·문서는 100MB까지 저장할 수 있습니다.';};
  const fail=e=>{status.setAttribute('role','alert');status.textContent=e.message||'연결하지 못했습니다. 잠시 후 다시 시도해 주세요.';};
  // Render every control before the first network request finishes.
  start.onclick=async()=>{
   if(joining)return;start.disabled=true;start.textContent='체험 공간 준비 중…';status.setAttribute('role','status');status.textContent='개인 체험 공간을 연결하고 있습니다. 처음 접속하면 잠시 걸릴 수 있습니다.';
   try{const result=await request('/api/demo/start',{});info={active:true,expires:result.expires};update();username.value='admin';password.value='ClinicDemo42!';status.textContent='체험 공간에 로그인하고 있습니다…';form.requestSubmit();}
   catch(e){fail(e);}finally{start.disabled=false;start.textContent='바로 체험하기';}
  };
  const owner=document.createElement('button');owner.type='button';owner.textContent='운영자 로그인으로 전환';details.append(owner);
  owner.onclick=async()=>{owner.disabled=true;try{await request('/api/demo/leave',{});info={active:false};username.value='admin';password.value='';details.open=true;update();password.focus();}catch(e){fail(e);}finally{owner.disabled=false;}};
  share.onclick=async()=>{
   try{const me=await request('/api/me');const r=await fetch('/api/demo/link',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':me.csrf,'Content-Type':'application/json'},body:'{}'});const data=await r.json();if(!r.ok)throw Error(data.detail);prompt('본인 기기에만 공유하세요. 링크를 가진 기기는 같은 체험 공간에 접근할 수 있습니다.',location.origin+'/mobile#workspace='+data.code);}
   catch(e){fail(e);}
  };
  try{
   const hash=new URLSearchParams(location.hash.slice(1)),code=hash.get('workspace');
   if(code){joining=true;start.disabled=true;status.textContent='공유받은 체험 공간에 연결하고 있습니다…';history.replaceState(null,'',location.pathname+location.search);await request('/api/demo/join',{code});}
   info=await request('/api/demo/info');update();
  }catch(e){fail(e);}finally{joining=false;start.disabled=false;}
 }
};
})();
