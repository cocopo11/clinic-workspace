import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {remoteScript} from '../lib/remote.mjs';

test('photo polling stops permanently after authentication or permission failure',async()=>{
 for(const status of [401,403,404]){
  let timers=0,requests=0;
  const window={fetch:async()=>{requests++;return new Response('{}',{status});},location:{origin:'https://example.test',href:'https://example.test/'}};
  const context={window,location:window.location,URL,Request,Response,Headers,Blob,FormData,DOMException,AbortSignal,Uint8Array,setTimeout:()=>{timers++;},clearTimeout(){}};
  vm.runInNewContext(remoteScript,context);
  const poll=new window.EventSource('/api/patients/DEMO-101/events');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(poll.closed,true);assert.equal(timers,0);assert.equal(requests,1);
 }
});
