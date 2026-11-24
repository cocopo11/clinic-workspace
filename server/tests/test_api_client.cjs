/* Run with Node 18+: node tests/test_api_client.cjs */
const assert = require('node:assert/strict');
const {requestJSON, ApiError} = require('../static/api-client.js');
let passed = 0;
async function test(name, fn) { await fn(); passed++; console.log('PASS ' + name); }
async function rejects(response, code, status, message) {
  await assert.rejects(requestJSON('/api/test', {}, async () => response), error => {
    assert(error instanceof ApiError); assert.equal(error.code, code);
    assert.equal(error.status, status); assert.match(error.message, message); return true;
  });
}
(async () => {
  await test('JSON success', async () => {
    assert.deepEqual(await requestJSON('/api/patients', {}, async () => new Response('{"patients":[]}')), {patients: []});
  });
  await test('plain HTTP 500 identifies server failure', () => rejects(new Response('Internal Server Error', {status: 500}), 'SERVER_ERROR', 500, /HTTP 500/));
  await test('HTML 200 identifies wrong server response', () => rejects(new Response('<html>Frontend</html>', {headers: {'content-type': 'text/html'}}), 'INVALID_RESPONSE', 200, /localhost:8000/));
  await test('HTML 404 identifies missing API', () => rejects(new Response('<html>Missing</html>', {status: 404}), 'API_NOT_FOUND', 404, /run\.py/));
  await test('HTTP 401 preserves authentication status', () => rejects(new Response('{"detail":"로그인이 필요합니다."}', {status: 401}), 'HTTP_ERROR', 401, /로그인/));
  await test('safe JSON 500 shows correlation ID', () => rejects(new Response('{"detail":"서버 오류","code":"INTERNAL_ERROR","error_id":"123456abcdef"}', {status: 500}), 'INTERNAL_ERROR', 500, /123456abcdef/));
  await test('validation details are readable', () => rejects(new Response('{"detail":[{"msg":"Field required"}]}', {status: 422}), 'HTTP_ERROR', 422, /입력한 값을 확인/));
  await test('network error never retries POST', async () => {
    let count = 0;
    await assert.rejects(requestJSON('/api/leaves', {method: 'POST'}, async () => { count++; throw new TypeError('offline'); }), e => e.code === 'NETWORK_ERROR');
    assert.equal(count, 1);
  });
  console.log(`${passed} client checks passed`);
})().catch(error => { console.error(error); process.exitCode = 1; });
