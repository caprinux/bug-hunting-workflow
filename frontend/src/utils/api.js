const API_BASE = '/api'

function getAuthHeader() {
  const token = sessionStorage.getItem('bhw_token') || ''
  if (!token) return {}
  return { 'Authorization': 'Bearer ' + token }
}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...getAuthHeader(), ...options.headers },
    ...options,
  })
  if (res.status === 401) {
    sessionStorage.removeItem('bhw_token')
    window.location.reload()
    throw new Error('Unauthorized')
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  return res.json()
}

export const api = {
  listEngagements: () => request('/engagements'),
  createEngagement: (data) => request('/engagements', { method: 'POST', body: JSON.stringify(data) }),
  getEngagement: (id) => request(`/engagements/${id}`),
  deleteEngagement: (id) => request(`/engagements/${id}`, { method: 'DELETE' }),
  updateEngagementConfig: (id, config) => request(`/engagements/${id}/config`, { method: 'PATCH', body: JSON.stringify(config) }),
  startRun: (id, data) => request(`/engagements/${id}/runs`, { method: 'POST', body: JSON.stringify(data) }),
  listRuns: (id) => request(`/engagements/${id}/runs`),
  getRun: (engId, runId) => request(`/engagements/${engId}/runs/${runId}`),
  listBugs: (id, status) => request(`/engagements/${id}/bugs${status ? `?status=${status}` : ''}`),
  listChains: (id) => request(`/engagements/${id}/chains`),
  getStageOutput: (engId, runId, stage, path = '') =>
    request(`/engagements/${engId}/runs/${runId}/stages/${stage}/output?path=${encodeURIComponent(path)}`),
  getCumulative: (engId, filename) => request(`/engagements/${engId}/cumulative/${filename}`),
  getReport: (engId) => request(`/engagements/${engId}/report`),
  generateReport: (engId) => request(`/engagements/${engId}/report/generate`, { method: 'POST' }),
  reportStatus: (engId) => request(`/engagements/${engId}/report/status`),

  deleteRun: (engId, runId) => request(`/engagements/${engId}/runs/${runId}`, { method: 'DELETE' }),
  cancelRun: (engId, runId) => request(`/engagements/${engId}/runs/${runId}/cancel`, { method: 'POST' }),
  pauseRun: (engId, runId) => request(`/engagements/${engId}/runs/${runId}/pause`, { method: 'POST' }),
  resumeRun: (engId, runId) => request(`/engagements/${engId}/runs/${runId}/resume`, { method: 'POST' }),
  getRunEvents: (engId, runId) => request(`/engagements/${engId}/runs/${runId}/events`),

  // Platforms
  listPlatforms: () => request('/platforms'),
  scrapePlatform: (name, creds) => request(`/platforms/${name}/scrape`, { method: 'POST', body: JSON.stringify(creds) }),
  scrapeStatus: (name) => request(`/platforms/${name}/scrape/status`),
  listPlatformPrograms: (name) => request(`/platforms/${name}/programs`),
  getPlatformProgram: (name, id) => request(`/platforms/${name}/programs/${id}`),
  importProgram: (name, id) => request(`/platforms/${name}/programs/${id}/import`, { method: 'POST' }),
  importStatus: (name, id) => request(`/platforms/${name}/programs/${id}/import/status`),

  // Chat
  listChats: (engId) => request(`/engagements/${engId}/chats`),
  createChat: (engId, title) => request(`/engagements/${engId}/chats`, { method: 'POST', body: JSON.stringify({ title }) }),
  getChat: (engId, chatId) => request(`/engagements/${engId}/chats/${chatId}`),
  deleteChat: (engId, chatId) => request(`/engagements/${engId}/chats/${chatId}`, { method: 'DELETE' }),
  updateChat: (engId, chatId, title) => request(`/engagements/${engId}/chats/${chatId}`, { method: 'PATCH', body: JSON.stringify({ title }) }),
  sendChatMessage: (engId, chatId, content) => request(`/engagements/${engId}/chats/${chatId}/messages`, { method: 'POST', body: JSON.stringify({ content }) }),

  getSettings: () => request('/settings'),
  updateSettings: (data) => request('/settings', { method: 'PUT', body: JSON.stringify(data) }),

  login: async (password) => {
    const res = await fetch(`${API_BASE}/auth/session`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Basic ' + btoa('user:' + password),
      },
    })
    if (res.status === 401) throw new Error('Invalid password')
    if (!res.ok) throw new Error('Server error')
    const data = await res.json()
    if (!data.token) throw new Error('Authentication failed')
    return data.token
  },
}
