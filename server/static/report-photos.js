 'use strict';
(()=>{
 const token=new URLSearchParams(location.search).get('token')||'',base='/api/recording/'+encodeURIComponent(token),$=id=>document.getElementById(id);
 let timer=null,loading=false,closed=false;const shown={};
 const previousVisits=window.ClinicWorkspacePhotoHistory.create($('previousPhoto'),photo=>{ $('reportBeforeDate').textContent=photo?.visit_date||'기록 없음';show('reportBefore',photo);});
 function clear(){previousVisits.reset();for(const id of ['reportBefore','reportToday']){$(id).replaceChildren();$(id).textContent='연결을 다시 확인하세요.';delete shown[id];}}
 function show(id,photo){if(Object.prototype.hasOwnProperty.call(shown,id)&&shown[id]===photo?.id)return;shown[id]=photo?.id;$(id).replaceChildren();if(!photo){$(id).textContent=id==='reportBefore'?'이전 방문 사진 없음':'아이폰 촬영 대기';return;}const image=document.createElement('img');image.alt=id==='reportBefore'?'이전 방문 사진':'아이폰에서 전송한 오늘 사진';image.src=base+'/photos/'+photo.id;image.onerror=()=>{$(id).textContent='사진을 불러오지 못했습니다. 사진 다시 확인을 누르세요.';delete shown[id];};$(id).append(image);}
 async function refresh(){if(loading||closed)return;clearTimeout(timer);loading=true;let retry=true;
 try{const response=await fetch(base+'/photos',{credentials:'omit',cache:'no-store'});if(!response.ok){if([401,403,404,410].includes(response.status)){retry=false;clear();}throw Error('사진 연결을 확인하지 못했습니다. 만료된 경우 모바일 진료에서 환자를 다시 검색하세요.');}const r=await response.json();if(closed)return;$('reportPatient').textContent=r.patient_id+' · '+r.visit_date+' 방문';const previous=previousVisits.update(r.history||(r.before?[r.before]:[]));$('reportBeforeDate').textContent=previous?.visit_date||'기록 없음';$('reportTodayDate').textContent=r.visit_date;show('reportBefore',previous);show('reportToday',r.today);$('photoSync').textContent=r.today?'오늘 사진 반영됨 · 2초 간격 자동 확인':'아이폰 촬영 대기 · 전송하면 오른쪽 칸에 자동 반영됩니다.';
 }catch(e){$('photoSync').textContent=e.message;}finally{loading=false;if(retry&&!closed)timer=setTimeout(refresh,2000);}}
 $('refreshPhotos').onclick=refresh;document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});window.addEventListener('pagehide',()=>{closed=true;clearTimeout(timer);});window.addEventListener('pageshow',()=>{closed=false;refresh();});if(token)refresh();else clear();
})();
