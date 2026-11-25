'use strict';
(()=>{
 const $=id=>document.getElementById(id),token=new URLSearchParams(location.search).get('token')||'';
 const endpoint='/api/recording/'+encodeURIComponent(token);
 let valid=false,used=false,recorder=null,media=null,chunks=[],blob=null,audioUrl='',timer=null,poller=null,started=0,bytes=0,stopping=false,uploading=false,starting=false,linkError='';
 const api=(path,options={})=>window.ClinicWorkspaceHTTP.requestJSON(path,{credentials:'same-origin',cache:'no-store',...options});
 function message(text){$('status').textContent=text;}
 function release(){media?.getTracks().forEach(t=>t.stop());media=null;clearInterval(timer);timer=null;}
 function recordingBlocker(){
  if(!window.isSecureContext)return '녹음하려면 HTTPS 연결이 필요합니다. 현재 HTTP 주소에서는 아이패드 마이크를 사용할 수 없습니다. PC에서 인증서가 준비된 HTTPS 서버를 실행하고, 그 HTTPS 주소로 새 QR을 만드세요.';
  if(typeof navigator.mediaDevices?.getUserMedia!=='function')return '이 브라우저에서 마이크 기능을 사용할 수 없습니다. 아이패드 Safari에서 직접 여세요. HTTPS 주소인데도 같으면 Safari의 사이트 마이크 권한과 기기 제한 설정을 확인하세요.';
  if(typeof window.MediaRecorder!=='function'||typeof window.MediaRecorder.isTypeSupported!=='function')return '이 브라우저에서 웹 녹음을 지원하지 않습니다. iPadOS를 업데이트하고 Safari에서 직접 여세요.';
  return '';
 }
 function controls(){
  // Patient confirmation does not depend on microphone/HTTPS availability.
  const ready=valid&&!used&&!uploading&&!recorder&&!starting,blocker=recordingBlocker();
  $('confirmPatient').disabled=!ready;$('language').disabled=!ready;
  $('start').disabled=!ready||!$('confirmPatient').checked||!!blocker;
  $('refreshConnection').disabled=!!recorder||uploading||starting;
  const notice=linkError||blocker;
  $('environment').textContent=notice||(valid?'녹음 가능한 연결입니다. 시작할 때 마이크 권한을 요청합니다.':'녹음 연결을 확인하고 있습니다.');
  $('environment').className=notice?'recording-blocker':'small';
 }
 function readyMessage(){message(recordingBlocker()||($('confirmPatient').checked?'환자 확인 완료 · 경과 녹음 시작을 누르세요.':'환자를 확인한 뒤 녹음 시작을 누르세요.'));}
 function showJob(job){
  used=true;$('retryUpload').hidden=true;$('progress').hidden=false;$('progress').value=job.progress;
  message(job.stage+(job.error?'\n'+job.error:''));controls();
  if(job.status==='succeeded'){
   $('result').hidden=false;$('registered').textContent=`REC-${job.note_id} · ${job.patient_id} · ${job.visit_date}\n${job.registration_mode==='manual'&&!job.reviewed?'전사 완료 · PC에서 확인 후 진료차트 등록 대기':'모의 CRM 등록 완료 · '+(job.reviewed?'의료진 검토 완료':job.auto_reviewed?'자동 검토 완료 · 추가 등록 불필요':'검토 대기')}`;
   $('transcript').textContent=job.text;return false;
  }
  return ['queued','running'].includes(job.status);
 }
 async function refresh(){
  clearTimeout(poller);
  try{const r=await api(endpoint);valid=true;linkError='';$('visit').textContent=`${r.patient_id} · ${r.visit_date} 방문`;
   if(r.job){if(showJob(r.job))poller=setTimeout(refresh,2000);}else{readyMessage();controls();}
  }catch(e){valid=false;linkError=e.message;controls();message(e.message+(used?'\nPC에서도 처리 결과를 확인할 수 있습니다.':''));}
 }
 async function upload(){
  if(!blob||uploading)return;uploading=true;controls();$('retryUpload').hidden=true;message('녹음을 전송하는 중… 전송 후 자동으로 전사·등록합니다.');
  const ext=blob.type.includes('mp4')?'m4a':blob.type.includes('ogg')?'ogg':'webm',form=new FormData();form.append('file',blob,'tablet-recording.'+ext);form.append('language',$('language').value);
  try{const r=await api(endpoint,{method:'POST',body:form});if(showJob(r.job))poller=setTimeout(refresh,1000);}
  catch(e){
   // Do not resend a POST automatically: first resolve an accepted but lost response.
   let accepted=false;
   try{const r=await api(endpoint);if(r.job){accepted=true;if(showJob(r.job))poller=setTimeout(refresh,1000);}}catch(_){}
   if(!accepted){message('전송을 확인하지 못했습니다. '+e.message+'\n녹음은 이 화면에 남아 있습니다. 연결 확인 후 같은 녹음 다시 전송을 누르세요.');$('retryUpload').hidden=false;}
  }finally{uploading=false;controls();}
 }
 async function start(){
  if(!valid||used||recorder||uploading||starting||!$('confirmPatient').checked)return;
  if(recordingBlocker()){controls();readyMessage();return;}
  starting=true;controls();message('마이크 권한 확인 중…');
  try{
   // Recheck expiry and patient before requesting microphone access.
   const link=await api(endpoint);if(link.job){showJob(link.job);return;}
   media=await navigator.mediaDevices.getUserMedia({audio:true});
   const mime=['audio/mp4','audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus'].find(x=>MediaRecorder.isTypeSupported(x));
   recorder=new MediaRecorder(media,mime?{mimeType:mime}:undefined);chunks=[];bytes=0;stopping=false;
   recorder.ondataavailable=e=>{if(e.data.size){chunks.push(e.data);bytes+=e.data.size;if(bytes>=149*1024*1024)stop();}};
   recorder.onerror=()=>{stopping=true;message('녹음 장치 오류로 중단했습니다. 저장된 음성 구간이 있으면 전송합니다.');if(recorder?.state==='recording')recorder.stop();else{release();recorder=null;controls();}};
   recorder.onstop=async()=>{
    const mimeType=recorder.mimeType||mime||chunks[0]?.type||'audio/mp4';blob=new Blob(chunks,{type:mimeType});recorder=null;used=true;release();$('stop').hidden=true;
    if(!blob.size){message('녹음된 음성이 없습니다. PC에서 새 링크를 만들어 다시 녹음하세요.');controls();return;}
    if(audioUrl)URL.revokeObjectURL(audioUrl);audioUrl=URL.createObjectURL(blob);$('playback').src=audioUrl;$('playback').hidden=false;
    $('saveAudio').href=audioUrl;$('saveAudio').download='tablet-recording.'+(mimeType.includes('mp4')?'m4a':mimeType.includes('ogg')?'ogg':'webm');$('saveAudio').hidden=false;
    await upload();
   };
   recorder.start(1000);started=Date.now();$('stop').hidden=false;controls();message('녹음 중입니다. 종료하면 자동 등록을 시작합니다.');
   timer=setInterval(()=>{const elapsed=Math.floor((Date.now()-started)/1000);$('timer').textContent=String(Math.floor(elapsed/60)).padStart(2,'0')+':'+String(elapsed%60).padStart(2,'0');if(elapsed>=1795)stop();},500);
  }catch(e){release();recorder=null;if(e.status===410||e.status===401){valid=false;linkError=e.message;}message(e.name==='NotAllowedError'?'마이크 사용을 허용해야 녹음할 수 있습니다. Safari의 사이트 마이크 권한을 확인하세요.':e.message||'마이크를 열지 못했습니다.');}
  finally{starting=false;controls();}
 }
 function stop(){if(recorder?.state==='recording'&&!stopping){stopping=true;$('stop').disabled=true;message('녹음을 종료하고 자동 전송합니다…');recorder.stop();}}
 $('start').onclick=start;$('stop').onclick=stop;$('retryUpload').onclick=upload;
 $('confirmPatient').onchange=()=>{controls();if(valid&&!used&&!recorder&&!uploading&&!starting)readyMessage();};
 $('refreshConnection').onclick=()=>{if(!recorder&&!uploading&&!starting)refresh();};
 document.addEventListener('visibilitychange',()=>{if(document.hidden&&recorder?.state==='recording')stop();});
 window.addEventListener('pagehide',()=>{clearTimeout(poller);release();});
 window.addEventListener('beforeunload',e=>{if(recorder||uploading){e.preventDefault();e.returnValue='';}});
 controls();
 if(!token){message('PC의 환자 화면에서 아이패드 녹음 링크·QR을 먼저 만드세요.');return;}refresh();
})();
