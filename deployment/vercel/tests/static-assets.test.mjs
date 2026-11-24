import test from 'node:test';
import assert from 'node:assert/strict';
import {staticAsset} from '../lib/static-assets.mjs';
import {cloudHandle} from '../lib/cloud.mjs';

test('public scripts bypass database/PC and cache only immutable application code',async()=>{
 const never=()=>{throw Error('backend must not run for a static file');};
 const r=await cloudHandle(new Request('https://example.test/static/workspace.js?v=perf1'),{},never,never,{load:never});
 assert.equal(r.status,200);assert.equal(r.headers.get('X-Goodmona-Asset'),'static');
 assert.match(r.headers.get('cache-control'),/public.*immutable/);
 assert.match(await r.text(),/업무 홈/);
 const cached=await staticAsset(new Request('https://example.test/static/workspace.js?v=perf1',{headers:{'if-none-match':r.headers.get('etag')}}));
 assert.equal(cached.status,304);
 const head=await staticAsset(new Request('https://example.test/static/workspace.css',{method:'HEAD'}));
 assert.equal(await head.text(),'');assert.match(head.headers.get('content-type'),/text\/css/);
});

test('authenticated data and unsafe filenames never enter public asset cache',async()=>{
 for(const path of ['/api/documents','/api/photos/1','/static/record.html','/static/%2e%2e%2fconfig.json','/static/missing.js']){
  assert.equal(await staticAsset(new Request('https://example.test'+path)),null,path);
 }
 assert.equal(await staticAsset(new Request('https://example.test/static/workspace.js',{method:'POST'})),null);
});
