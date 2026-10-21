export function createApi(base = '', { fetcher = (...args) => fetch(...args), onUnauthorized } = {}) {
  let csrfToken = null;
  async function request(path, options = {}) {
    const mutating = options.method && options.method !== 'GET';
    const headers = { ...(options.body ? { 'Content-Type': 'application/json' } : {}) };
    if (mutating) {
      headers['X-Pump-Console'] = '1';
      if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
    }
    const response = await fetcher(base + path, { ...options, credentials: 'same-origin', headers });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 401 && data.error !== 'invalid_current_password') csrfToken = null;
      if ((data.error === 'authentication_required' || data.error === 'password_change_required') && path !== '/api/auth/session') {
        onUnauthorized?.();
        if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('pump-console-auth-required'));
      }
      const error = new Error(data.message || ('HTTP ' + response.status));
      error.status = response.status; error.code = data.error;
      throw error;
    }
    if (data.csrf_token) csrfToken = data.csrf_token;
    if (data.logged_out || data.reauthenticate) csrfToken = null;
    return data;
  }
  return {
    session: () => request('/api/auth/session'),
    login: (username, password) => request('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
    logout: () => request('/api/auth/logout', { method: 'POST' }),
    touch: () => request('/api/auth/touch', { method: 'POST' }),
    changePassword: (current_password, new_password) => request('/api/auth/password', {
      method: 'POST', body: JSON.stringify({ current_password, new_password }),
    }),
    users: () => request('/api/admin/users'),
    createUser: (username, password, role = 'viewer') => request('/api/admin/users', {
      method: 'POST', body: JSON.stringify({ username, password, role }),
    }),
    resetUserPassword: (username, password) => request('/api/admin/users/reset-password', {
      method: 'POST', body: JSON.stringify({ username, password }),
    }),
    health: () => request('/api/health'),
    templates: () => request('/api/templates'),
    status: () => request('/api/status'),
    refreshStatus: () => request('/api/status/refresh', { method: 'POST', body: '{}', signal: AbortSignal.timeout(15000) }),
    experiments: () => request('/api/experiments'),
    experiment: id => request('/api/experiments/' + encodeURIComponent(id)),
    experimentContent: id => request('/api/experiments/' + encodeURIComponent(id) + '/content'),
    renameExperiment: (id, new_id, expected_version) => request('/api/experiments/' + encodeURIComponent(id) + '/rename', {
      method: 'POST', body: JSON.stringify({ new_id, expected_version }),
    }),
    duplicateExperiment: (id, new_id, expected_version) => request('/api/experiments/' + encodeURIComponent(id) + '/duplicate', {
      method: 'POST', body: JSON.stringify({ new_id, expected_version }),
    }),
    deleteExperiment: (id, expected_version) => request('/api/experiments/' + encodeURIComponent(id), {
      method: 'DELETE', body: JSON.stringify({ expected_version }),
    }),
    runs: () => request('/api/runs'),
    preview: sequence => request('/api/preview', { method: 'POST', body: JSON.stringify({ sequence }) }),
    save: (sequence, version) => request('/api/experiments', {
      method: 'POST', body: JSON.stringify({ sequence, expected_version: version }),
    }),
  };
}
export const appApi = createApi();
