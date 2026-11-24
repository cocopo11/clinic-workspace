/* Shared JSON transport. Never retries a write after an uncertain response. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ClinicWorkspaceHTTP = api;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';
  class ApiError extends Error {
    constructor(message, code, status = 0) {
      super(message); this.name = 'ApiError'; this.code = code; this.status = status;
    }
  }
  function unexpectedResponse(response) {
    const status = response.status;
    if (status === 401) return new ApiError('로그인이 만료되었습니다. 다시 로그인하세요.', 'AUTH_REQUIRED', status);
    if (status >= 500) return new ApiError(`서버 내부 오류가 발생했습니다 (HTTP ${status}). 서버 실행 창의 오류를 확인하세요.`, 'SERVER_ERROR', status);
    if (status === 404 || status === 405) return new ApiError(`서버 기능을 찾지 못했습니다 (HTTP ${status}). python server/run.py 명령으로 서버를 실행하고 http://localhost:8000에서 접속하세요.`, 'API_NOT_FOUND', status);
    return new ApiError(`서버의 JSON 응답 대신 웹페이지 또는 다른 형식의 응답을 받았습니다 (HTTP ${status}). 로컬 서버 주소 http://localhost:8000에서 접속했는지 확인하세요.`, 'INVALID_RESPONSE', status);
  }
  async function requestJSON(path, options = {}, fetchImpl = fetch) {
    let response;
    try { response = await fetchImpl(path, options); }
    catch (_) { throw new ApiError('서버에 연결할 수 없습니다. 서버 실행 창을 열어 둔 상태인지 확인하세요.', 'NETWORK_ERROR'); }
    let raw;
    try { raw = await response.text(); }
    catch (_) { throw new ApiError('응답을 받는 도중 연결이 끊겼습니다. 저장 요청이었다면 목록에서 처리 여부를 확인하세요.', 'READ_ERROR', response.status); }
    let data;
    try { data = JSON.parse(raw); }
    catch (_) { throw unexpectedResponse(response); }
    if (data === null || typeof data !== 'object') throw unexpectedResponse(response);
    if (!response.ok) {
      let detail = typeof data.detail === 'string' ? data.detail : '';
      if (Array.isArray(data.detail)) detail = '입력한 값을 확인하세요. ' + data.detail.map(d => d.msg || '').filter(Boolean).join(' / ');
      if (!detail) detail = response.status === 401 ? '로그인이 필요합니다.' : '요청을 처리하지 못했습니다.';
      const errorId = typeof data.error_id === 'string' && /^[a-f0-9]{12}$/.test(data.error_id) ? ` · 오류 번호 ${data.error_id}` : '';
      throw new ApiError(`${detail.slice(0, 500)} (HTTP ${response.status})${errorId}`, data.code || 'HTTP_ERROR', response.status);
    }
    return data;
  }
  return {ApiError, requestJSON};
});
