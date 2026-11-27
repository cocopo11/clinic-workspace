import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {cloudHandle} from '../lib/cloud.mjs';
import {publicPage} from '../lib/public-pages.mjs';

const origin='https://example.test',never=()=>{throw Error('public shell cannot depend on DB or PC');};
test('first PC and mobile renders include full CSS without a database or extra stylesheet request',async()=>{
 for(const path of ['/','/mobile','/capture','/record','/ipad-check']){
  const r=await cloudHandle(new Request(origin+path),{},never,never,{load:never});
  assert.equal(r.status,200,path);assert.equal(r.headers.get('x-clinicworkspace-page'),'public-shell');assert.equal(r.headers.get('cache-control'),'no-store');
  const html=await r.text();
  for(const script of html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/gi))new vm.Script(script[1]);
  assert.match(html,/ClinicWorkspaceRemoteMode/);assert.match(html,/nonce=/);
  assert.doesNotMatch(html,/<link\s+rel="stylesheet"[^>]*\/static\//);
  if(path==='/')assert.match(html,/<style data-stylesheet="workspace.css">/);
  if(path==='/mobile')assert.match(html,/<style data-stylesheet="mobile-ui.css">/);
 }
});
test('public HTML cannot select a data file or bypass authenticated API routing',async()=>{
 for(const path of ['/api/users','/api/photos/1','/api/documents','/static/config.json','/server/config.json','/index.html']){
  assert.equal(await publicPage(new Request(origin+path)),null,path);
 }
 assert.equal(await publicPage(new Request(origin+'/',{method:'POST',body:'{}'})),null);
 const head=await cloudHandle(new Request(origin+'/',{method:'HEAD'}),{},never,never,{load:never});
 assert.equal(head.status,200);assert.equal(await head.text(),'');
});
