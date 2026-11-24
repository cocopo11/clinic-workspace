'use strict';
window.ClinicWorkspacePhotoHistory={create(select,onSelect){
 let photos=[],selected='',markupKey='';
 function current(){return photos.find(p=>String(p.id)===selected)||null;}
 function update(items){
  photos=items||[];
  if(!photos.some(p=>String(p.id)===selected))selected=photos.length?String(photos[0].id):'';
  const key=JSON.stringify(photos.map(p=>[p.id,p.visit_date,p.site]));
  if(key!==markupKey){select.replaceChildren();if(!photos.length){const o=document.createElement('option');o.value='';o.textContent='이전 방문 사진 없음';select.append(o);}else for(const p of photos){const o=document.createElement('option');o.value=String(p.id);o.textContent=p.visit_date+' · '+(p.site||'사진')+' · 사진 #'+p.id;select.append(o);}markupKey=key;}
  select.disabled=!photos.length;select.value=selected;return current();
 }
 select.onchange=()=>{selected=select.value;onSelect(current());};
 function reset(){selected='';markupKey='';update([]);}
 reset();return {update,reset};
}};
