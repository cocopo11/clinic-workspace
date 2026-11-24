'use strict';
window.ClinicWorkspaceCloudTools={create({api,getUser,toast}){
 const card=document.createElement('div');card.className='card hidden';card.id='cloudStorageTools';
 card.innerHTML='<h2>클라우드 저장과 기존 자료 이전</h2><p class="small">일반 업무와 사진은 클라우드에 저장하고, LLM·음성 전사는 연결된 PC에서 처리합니다.</p><p class="small">기존 PC 자료 이전은 한 번만 실행합니다. 클라우드에 새 업무 기록이 있으면 덮어쓰지 않고 중단합니다. 이전 뒤에는 다시 로그인하세요.</p><div class="row"><button id="refreshCloudStorage">저장 상태 확인</button><button id="migrateLocalData">기존 PC 자료 옮기기</button></div><p id="cloudStorageStatus" class="resource-status" role="status"></p>';
 document.getElementById('administration').prepend(card);
 let epoch=0;
 async function status(){const started=epoch;try{const r=await api('/api/cloud/storage');if(started!==epoch)return;document.getElementById('cloudStorageStatus').textContent=r.local_import?'기존 PC 자료 이전 완료 · 클라우드 DB·비공개 파일 저장 사용 중':'클라우드 DB·비공개 파일 저장 사용 중 · 기존 PC 자료를 아직 옮기지 않았습니다.';document.getElementById('migrateLocalData').disabled=!!r.local_import;}catch(e){if(started===epoch)document.getElementById('cloudStorageStatus').textContent=e.message;}}
 document.getElementById('refreshCloudStorage').onclick=status;
 document.getElementById('migrateLocalData').onclick=async()=>{const started=epoch,b=document.getElementById('migrateLocalData');b.disabled=true;document.getElementById('cloudStorageStatus').textContent='PC 자료를 안전하게 복사하는 중입니다. 사진이 많으면 시간이 걸립니다…';try{await api('/api/cloud/migrate-local',{method:'POST',body:{}});if(started!==epoch)return;toast('자료 이전을 완료했습니다. 다시 로그인하세요.');location.reload();}catch(e){if(started===epoch){document.getElementById('cloudStorageStatus').textContent=e.message;b.disabled=false;}}};
 function clear(){epoch++;card.classList.add('hidden');document.getElementById('cloudStorageStatus').textContent='';}
 async function setUser(){clear();const started=epoch;if(getUser()?.role!=='admin')return;try{const r=await api('/api/health');if(started!==epoch||!r.cloud)return;card.classList.remove('hidden');await status();}catch{}}
 return{clear,setUser};
}};
