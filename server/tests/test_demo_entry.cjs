const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
class Element{
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.attributes={};this.textContent='';this.value='';}
 append(...nodes){for(const n of nodes){if(n.parentNode)n.parentNode.children.splice(n.parentNode.children.indexOf(n),1);n.parentNode=this;this.children.push(n);}}
 before(node){const p=this.parentNode;p.children.splice(p.children.indexOf(this),0,node);node.parentNode=p;}
 get nextSibling(){if(!this.parentNode)return null;return this.parentNode.children[this.parentNode.children.indexOf(this)+1]||null;}
 closest(tag){return this.parentNode?.tagName===tag.toUpperCase()?this.parentNode:null;}
 querySelector(tag){return this.children.find(e=>e.tagName===tag.toUpperCase())||null;}
 setAttribute(k,v){this.attributes[k]=v;}
}
(async()=>{
 const form=new Element('form'),label=new Element('label'),username=new Element('input'),password=new Element('input'),login=new Element('button'),error=new Element('p'),account=new Element('div'),top=new Element('div');
 form.append(label,username,password,login,error);top.append(account);
 const ids={loginForm:form,username,password,loginError:error,accountArea:account};
 let finishInfo;
 const ctx={document:{readyState:'complete',createElement:t=>new Element(t),getElementById:id=>ids[id],querySelector:()=>null},window:{ClinicWorkspaceRemoteMode:true},location:{hash:'',pathname:'/',search:'',origin:'https://example.test'},URLSearchParams,history:{replaceState(){}},fetch:()=>new Promise(r=>{finishInfo=r;})};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/demo-space.js'),'utf8'),ctx);
 const init=ctx.window.ClinicWorkspaceDemo.init();await new Promise(r=>setImmediate(r));
 const descendants=e=>[e,...e.children.flatMap(descendants)];
 assert.ok(descendants(form).some(e=>e.tagName==='BUTTON'&&e.textContent==='바로 체험하기'),'A slow info request must not hide the primary action');
 assert.ok(descendants(form).some(e=>e.textContent==='체험 공간 연결 상태 확인 중…'));
 assert.ok(descendants(form).some(e=>e.tagName==='DETAILS'&&e.children.includes(username)),'Credentials remain available in the optional login section');
 finishInfo({ok:true,json:async()=>({active:false})});await init;
 assert.equal(account.parentNode.children[0].hidden,true,'No sharing action before a workspace exists');
 console.log('PASS first-time demo action renders during a pending connection; credentials stay optional; sharing starts hidden');
})().catch(e=>{console.error(e);process.exitCode=1;});
