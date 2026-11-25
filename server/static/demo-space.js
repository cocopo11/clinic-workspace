/* Only the server-issued HttpOnly workspace cookie selects an isolated dataset. */
(()=>{
'use strict';
const ready=new Promise(resolve=>document.readyState==='loading'?document.addEventListener('DOMContentLoaded',resolve,{once:true}):resolve());
const request=async(path,body)=>{
 const r=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',cache:'no-store',headers:body===undefined?{}:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
 const data=await r.json();if(!r.ok)throw Error(data.detail||'체험 공간 연결 실패');return data;
};
window.ClinicWorkspaceDemo={
 async init(){
  await ready;
  if(!window.ClinicWorkspaceRemoteMode)return;
  let info;
  try{
   // Fragments do not enter HTTP access logs or Referer headers.
   const hash=new URLSearchParams(location.hash.slice(1)),code=hash.get('workspace');
   if(code){history.replaceState(null,'',location.pathname+location.search);await request('/api/demo/join',{code});}
   info=await request('/api/demo/info');
  }catch(e){alert(e.message);return;}
  const form=document.getElementById('loginForm');if(!form)return;
  const panel=document.createElement('div');panel.className='demo-space-panel';panel.style.cssText='padding:14px 0;border-bottom:1px solid #ddd;margin-bottom:16px';
  const message=document.createElement('p');message.className='small';message.textContent=info.active?'나만의 체험 공간 · 24시간 후 만료 · 파일 100MB':'다른 방문자와 분리된 가상 데이터로 업로드·편집·AI 기능을 체험하세요. 24시간 후 만료됩니다.';
  const start=document.createElement('button');start.type='button';start.textContent=info.active?'체험 계정으로 로그인':'내 체험 공간 만들기';start.className='primary';
  const owner=document.createElement('button');owner.type='button';owner.textContent='운영자 로그인';owner.style.marginLeft='8px';
  panel.append(message,start,owner);form.prepend(panel);
  const username=document.getElementById('username'),password=document.getElementById('password');
  if(!info.active){username.value='';password.value='';}
  let share;
  start.onclick=async()=>{start.disabled=true;start.textContent='체험 공간 준비 중…';try{await request('/api/demo/start',{});username.value='admin';password.value='ClinicDemo42!';form.requestSubmit();message.textContent='나만의 체험 공간 · 24시간 후 만료 · 파일 100MB';share.hidden=false;}catch(e){message.textContent=e.message;}finally{start.disabled=false;start.textContent='체험 계정으로 로그인';}};
  owner.onclick=async()=>{try{await request('/api/demo/leave',{});location.replace(location.pathname);}catch(e){message.textContent=e.message;}};
  {
   share=document.createElement('button');share.textContent='다른 기기에서 같은 체험 공간 열기';share.type='button';share.hidden=!info.active;share.style.cssText='margin:8px 16px';
   const target=document.getElementById('accountArea')||document.getElementById('account')||document.body;
   target.parentNode.insertBefore(share,target);
   share.onclick=async()=>{try{const me=await request('/api/me');const r=await fetch('/api/demo/link',{method:'POST',credentials:'same-origin',headers:{'X-CSRF-Token':me.csrf,'Content-Type':'application/json'},body:'{}'});const data=await r.json();if(!r.ok)throw Error(data.detail);const url=location.origin+'/mobile#workspace='+data.code;prompt('본인 기기에만 공유하세요. 링크를 가진 기기는 같은 체험 공간에 접근할 수 있습니다.',url);}catch(e){alert(e.message);}};
   if(info.active){username.value='admin';password.value='ClinicDemo42!';}
  }
 }
};
})();
