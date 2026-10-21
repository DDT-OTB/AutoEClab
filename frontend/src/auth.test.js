import test from 'node:test';
import assert from 'node:assert/strict';
import { createApi } from './api.js';
import { passwordError } from './password.js';

const response = (status, data) => ({ ok: status < 400, status, json: async () => data });
test('login obtains CSRF in memory and protects mutations without sending a bearer token', async () => {
  const requests = [];
  const api = createApi('', { fetcher: async (url, options) => {
    requests.push({ url, options });
    return response(200, url.endsWith('/login') ? { csrf_token: 'test-csrf', user: { username: 'lab' } } : { saved: true });
  } });
  await api.login('lab', 'Test password used only in unit tests');
  await api.save({ id: 'test' });
  await api.refreshStatus();
  assert.equal(requests[0].options.headers['X-Pump-Console'], '1');
  assert.equal(requests[0].options.headers['X-CSRF-Token'], undefined);
  assert.equal(requests[1].options.headers['X-CSRF-Token'], 'test-csrf');
  assert.equal(requests[1].options.credentials, 'same-origin');
  assert.equal(requests[1].options.headers.Authorization, undefined);
  assert.equal(requests[2].url, '/api/status/refresh');
  assert.equal(requests[2].options.headers['X-CSRF-Token'], 'test-csrf');
});
test('logout clears the in-memory CSRF token and unauthorized data requests notify the gate', async () => {
  let expired = 0;
  const calls = [];
  const api = createApi('', { onUnauthorized: () => expired++, fetcher: async (url, options) => {
    calls.push(options);
    if (url.endsWith('/session')) return response(200, { csrf_token: 'test-csrf' });
    if (url.endsWith('/logout')) return response(200, { logged_out: true });
    return response(401, { error: 'authentication_required', message: 'Please sign in' });
  } });
  await api.session();
  await api.logout();
  await assert.rejects(api.touch(), { status: 401 });
  assert.equal(calls[2].headers['X-CSRF-Token'], undefined);
  assert.equal(expired, 1);
});
test('first password change is handled distinctly from a bad password', async () => {
  let required = 0;
  const api = createApi('', { onUnauthorized: () => required++, fetcher: async () =>
    response(403, { error: 'password_change_required', message: 'Change password' }) });
  await assert.rejects(api.status(), { code: 'password_change_required' });
  assert.equal(required, 1);
});
test('password form requires more than eight characters, lowercase letters and digits', () => {
  for (const valid of ['labtest12', 'Labtest12!', '实验密码短语a12']) {
    assert.equal(passwordError(valid, valid), '');
  }
  for (const invalid of ['labtest1', 'lowercaseonly', '123456789', 'UPPERCASE123', ' '.repeat(20)]) {
    assert.ok(passwordError(invalid, invalid), invalid);
  }
  assert.ok(passwordError('labtest12', 'labtest13'));
  const oversized = 'a'.repeat(128) + '1';
  assert.ok(passwordError(oversized, oversized));
});
test('administrator account changes use authenticated CSRF-protected API requests', async()=>{
  const calls=[];
  const api=createApi('',{fetcher:async(url,options)=>{
    calls.push({url,options});
    return response(200,url.endsWith('/session')?{csrf_token:'admin-csrf'}:{users:[],created:true,reset:true});
  }});
  await api.session();await api.users();
  await api.createUser('observer','temporary123');
  await api.resetUserPassword('observer','newtemporary123');
  assert.equal(calls[1].url,'/api/admin/users');
  assert.deepEqual(JSON.parse(calls[2].options.body),{username:'observer',password:'temporary123',role:'viewer'});
  for(const call of calls.slice(2)){
    assert.equal(call.options.headers['X-CSRF-Token'],'admin-csrf');
    assert.equal(call.options.headers['X-Pump-Console'],'1');
    assert.equal(call.options.credentials,'same-origin');
    assert.doesNotMatch(call.url,/temporary123/);
  }
});
