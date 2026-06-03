const BASE = '';

function getApiKey() {
  return localStorage.getItem('pybot_api_key') || 'dev-key';
}

function promptForApiKey(currentKey = getApiKey()) {
  return prompt("API Key required. Please enter your PyBot API key:", currentKey);
}

async function request(path, options = {}) {
  const headers = { 
    'Content-Type': 'application/json', 
    'Authorization': `Bearer ${getApiKey()}`,
    ...options.headers 
  };
  const res = await fetch(`${BASE}${path}`, {
    ...options,
    headers,
  });
  
  if (res.status === 401) {
    const newKey = promptForApiKey();
    if (newKey) {
      localStorage.setItem('pybot_api_key', newKey);
      return request(path, options);
    }
    throw new Error('Unauthorized — API key required');
  }
  
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || err.error || `HTTP ${res.status}`);
  }
  const data = await res.json();
  // Detect server-side errors returned with HTTP 200
  if (data && typeof data === 'object' && data.error && !data.success) {
    throw new Error(data.error);
  }
  return data;
}

export const API = {
  health: () => request('/api/health'),
  getSystemModel: () => request('/api/system/model'),
  getSystemModes: () => request('/api/system/modes'),

  listConversations: () => request('/api/conversations'),
  createConversation: () => request('/api/conversations', { method: 'POST', body: '{}' }),
  deleteConversation: (id) => request(`/api/conversations/${id}`, { method: 'DELETE' }),
  getHistory: (id) => request(`/api/conversations/${id}/history`),

  chatStream: async (threadId, message) => {
    let res = await fetch(`${BASE}/api/chat/stream`, {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${getApiKey()}`
      },
      body: JSON.stringify({ thread_id: threadId, message }),
    });
    if (res.status === 401) {
      const newKey = promptForApiKey();
      if (newKey) {
        localStorage.setItem('pybot_api_key', newKey);
        res = await fetch(`${BASE}/api/chat/stream`, {
          method: 'POST',
          headers: { 
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${newKey}`
          },
          body: JSON.stringify({ thread_id: threadId, message }),
        });
      }
    }
    return res;
  },

  listAgents: () => request('/api/agents'),
  getAgentControl: () => request('/api/agent-control'),
  getAgent: (name) => request(`/api/agents/${encodeURIComponent(name)}`),
  toggleAgent: (name, enabled) => request(`/api/agents/${encodeURIComponent(name)}/toggle`, {
    method: 'PATCH', body: JSON.stringify({ enabled })
  }),
  updateAgentCapabilities: (name, capabilityProfile) => request(`/api/agents/${encodeURIComponent(name)}/capabilities`, {
    method: 'PATCH', body: JSON.stringify({ capability_profile: capabilityProfile })
  }),
  deleteAgent: (name) => request(`/api/agents/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  listAgentTools: (name) => request(`/api/agents/${encodeURIComponent(name)}/tools`),
  assignTool: (agent, tool) => request(`/api/agents/${encodeURIComponent(agent)}/tools`, {
    method: 'POST', body: JSON.stringify({ tool_name: tool })
  }),
  removeTool: (agent, tool) => request(`/api/agents/${encodeURIComponent(agent)}/tools/${encodeURIComponent(tool)}`, { method: 'DELETE' }),
  syncAgentTool: (agent, tool, direction, overwrite = false) => request(
    `/api/agents/${encodeURIComponent(agent)}/tools/${encodeURIComponent(tool)}/sync`,
    { method: 'POST', body: JSON.stringify({ direction, overwrite }) }
  ),

  listTools: () => request('/api/tools'),
  deleteTool: (name) => request(`/api/tools/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  listSkills: () => request('/api/skills'),
  getSkill: (name) => request(`/api/skills/${encodeURIComponent(name)}`),
  toggleSkill: (name, enabled) => request(`/api/skills/${encodeURIComponent(name)}/toggle`, {
    method: 'PATCH', body: JSON.stringify({ enabled })
  }),
  deleteSkill: (name) => request(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  listWorkflows: () => request('/api/workflows'),
  getWorkflowGraph: (id) => request(`/api/workflows/${encodeURIComponent(id)}/graph`),
  getWorkflowDefinition: (id) => request(`/api/workflows/${encodeURIComponent(id)}/definition`),
  createWorkflow: (name, definition) => request('/api/workflows', {
    method: 'POST', body: JSON.stringify({ name, definition })
  }),
  updateWorkflow: (id, name, definition) => request(`/api/workflows/${encodeURIComponent(id)}`, {
    method: 'PUT', body: JSON.stringify({ name, definition })
  }),
  createWorkflowSpec: (name, specContent) => request('/api/workflows/from-spec', {
    method: 'POST', body: JSON.stringify({ name, spec_content: specContent })
  }),
  updateWorkflowSpec: (id, specContent) => request(`/api/workflows/${encodeURIComponent(id)}/from-spec`, {
    method: 'PUT', body: JSON.stringify({ name: id, spec_content: specContent })
  }),
  deleteWorkflow: (id) => request(`/api/workflows/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  getWorkflowVersions: (id, limit = 20) => request(`/api/workflows/${encodeURIComponent(id)}/versions?limit=${limit}`),
  getWorkflowVersion: (id, commitId) => request(`/api/workflows/${encodeURIComponent(id)}/versions/${commitId}`),
  publishWorkflow: (id, commitId) => request(`/api/workflows/${encodeURIComponent(id)}/publish`, {
    method: 'POST', body: JSON.stringify({ commit_id: commitId || null })
  }),
  rollbackWorkflow: (id, commitId) => request(`/api/workflows/${encodeURIComponent(id)}/rollback`, {
    method: 'POST', body: JSON.stringify({ commit_id: commitId })
  }),
  triggerWorkflow: (name, vars) => request('/api/workflows/trigger', {
    method: 'POST', body: JSON.stringify({ name, input_vars: vars || {} })
  }),
  getNodeTypes: () => request('/api/workflows/node-types'),
  testNodeRun: (nodeType, config) => request('/api/workflows/nodes/run', {
    method: 'POST', body: JSON.stringify({ node_type: nodeType, config })
  }),
  getWorkflowRuns: (name) => request(`/api/workflows/runs?workflow_name=${encodeURIComponent(name || '')}`),
  getWorkflowRun: (runId) => request(`/api/workflows/runs/${encodeURIComponent(runId)}`),

  listApprovals: () => request('/api/approvals'),
  resolveApproval: (approvalId, approved, note = '', approver = '') => request(`/api/approvals/${encodeURIComponent(approvalId)}/resolve`, {
    method: 'POST', body: JSON.stringify({ approved, note, approver })
  }),

  listApps: () => request('/api/apps'),
  getAppInfo: (name) => request(`/api/apps/${encodeURIComponent(name)}/info`),
  toggleApp: (name, enabled) => request(`/api/apps/${encodeURIComponent(name)}/toggle`, {
    method: 'PATCH', body: JSON.stringify({ enabled })
  }),
  switchAppMode: (name, mode, rebuildTemplate = false) => request(`/api/apps/${encodeURIComponent(name)}/mode`, {
    method: 'PATCH', body: JSON.stringify({ mode, rebuild_template: rebuildTemplate })
  }),
  deleteApp: (name) => request(`/api/apps/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  getStatus: (threadId) => request(`/api/status/${threadId}`),
  getTrace: (threadId) => request(`/api/conversations/${encodeURIComponent(threadId)}/trace`),

  listWorkspaceFiles: (workspaceId) => {
    const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
    return request(`/api/workspace/files${qs}`);
  },
  getWorkspaceFile: (name, workspaceId) => {
    const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
    return request(`/api/workspace/${encodeURIComponent(name)}${qs}`);
  },
  updateWorkspaceFile: (name, content, workspaceId) => {
    const qs = workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : '';
    return request(`/api/workspace/${encodeURIComponent(name)}${qs}`, {
      method: 'PUT', body: JSON.stringify({ content })
    });
  },
  listWorkspaces: () => request('/api/workspaces'),
  createWorkspace: (name, path) => request('/api/workspaces', {
    method: 'POST',
    body: JSON.stringify({ name, path: path || null }),
  }),
  deleteWorkspace: (workspaceId) => request(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {
    method: 'DELETE',
  }),

  getMemory: () => request('/api/memory'),
  getMemoryOverview: () => request('/api/memory/overview'),
  addMemory: (content) => request('/api/memory', { method: 'POST', body: JSON.stringify({ content }) }),
  listMemoryRecords: (modality = 'fact', status = 'active', limit = 200) =>
    request(`/api/memory/records?modality=${encodeURIComponent(modality)}&status=${encodeURIComponent(status)}&limit=${limit}`),
  updateMemoryRecord: (recordId, content) =>
    request(`/api/memory/records/${encodeURIComponent(recordId)}`, {
      method: 'PUT', body: JSON.stringify({ content })
    }),
  deleteMemoryRecord: (recordId) =>
    request(`/api/memory/records/${encodeURIComponent(recordId)}`, {
      method: 'DELETE'
    }),
  feedbackMemoryRecord: (recordId, signal) =>
    request(`/api/memory/records/${encodeURIComponent(recordId)}/feedback`, {
      method: 'POST', body: JSON.stringify({ signal })
    }),

  listScheduleTasks: () => request('/api/schedules'),
  createScheduleTask: (data) => request('/api/schedules', { method: 'POST', body: JSON.stringify(data) }),
  updateScheduleTask: (name, data) => request(`/api/schedules/${encodeURIComponent(name)}`, {
    method: 'PUT', body: JSON.stringify(data)
  }),
  toggleScheduleTask: (name, enabled) => request(`/api/schedules/${encodeURIComponent(name)}/toggle`, {
    method: 'PATCH', body: JSON.stringify({ enabled })
  }),
  deleteScheduleTask: (name) => request(`/api/schedules/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  runScheduleTaskNow: (name) => request(`/api/schedules/${encodeURIComponent(name)}/run`, { method: 'POST' }),
  getScheduleHistory: (limit = 50) => request(`/api/schedule/history?limit=${limit}`),

  getBackgroundTasks: () => request('/api/debug/tasks'),
  getBackgroundTask: (id) => request(`/api/debug/tasks/${encodeURIComponent(id)}`),
  cancelBackgroundTask: (id) => request(`/api/debug/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),

  // Long-running tasks (Codex-Cloud-style heartbeat)
  listLongRunningTasks: (params = {}) => {
    const q = new URLSearchParams();
    if (params.kind) q.set('kind', params.kind);
    if (params.status) q.set('status', params.status);
    if (params.thread_id) q.set('thread_id', params.thread_id);
    const qs = q.toString();
    return request(`/api/tasks${qs ? '?' + qs : ''}`);
  },
  getLongRunningTask: (id) => request(`/api/tasks/${encodeURIComponent(id)}`),
  cancelLongRunningTask: (id) => request(`/api/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  pauseLongRunningTask: (id) => request(`/api/tasks/${encodeURIComponent(id)}/pause`, { method: 'POST' }),
  resumeLongRunningTask: (id) => request(`/api/tasks/${encodeURIComponent(id)}/resume`, { method: 'POST' }),
  // SSE: returns an EventSource the caller is responsible for closing.
  streamLongRunningTaskEvents: (id = null) => {
    // EventSource cannot send custom Authorization headers natively, so we
    // append the API key as a query string fallback (the gateway accepts
    // ?api_key=… in addition to the Authorization header).
    const apiKey = getApiKey();
    const path = id
      ? `/api/tasks/${encodeURIComponent(id)}/events`
      : '/api/tasks/events';
    const sep = path.includes('?') ? '&' : '?';
    return new EventSource(`${BASE}${path}${sep}api_key=${encodeURIComponent(apiKey)}`);
  },

  listUvEnvs: () => request('/api/uv/envs'),
  getUvEnv: (name) => request(`/api/uv/envs/${encodeURIComponent(name)}`),
  createUvEnv: (data) => request('/api/uv/envs', { method: 'POST', body: JSON.stringify(data) }),
  deleteUvEnv: (name) => request(`/api/uv/envs/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  installPkg: (name, packages) => request(`/api/uv/envs/${encodeURIComponent(name)}/install`, {
    method: 'POST', body: JSON.stringify({ packages })
  }),
  uninstallPkg: (name, packages) => request(`/api/uv/envs/${encodeURIComponent(name)}/uninstall`, {
    method: 'POST', body: JSON.stringify({ packages })
  }),
  runInEnv: (name, code, timeout = 30) => request(`/api/uv/envs/${encodeURIComponent(name)}/run`, {
    method: 'POST', body: JSON.stringify({ code, timeout })
  }),

  listTemplates: () => request('/api/templates'),

  upload: async (file) => {
    const fd = new FormData();
    fd.append('file', file);
    let res = await fetch(`${BASE}/api/upload`, { 
      method: 'POST', 
      headers: { 'Authorization': `Bearer ${getApiKey()}` },
      body: fd 
    });
    if (res.status === 401) {
      const newKey = promptForApiKey();
      if (newKey) {
        localStorage.setItem('pybot_api_key', newKey);
        res = await fetch(`${BASE}/api/upload`, { 
          method: 'POST', 
          headers: { 'Authorization': `Bearer ${newKey}` },
          body: fd 
        });
      }
    }
    return res.json();
  },
  listUploads: () => request('/api/uploads'),

  listSkillFiles: (name) => request(`/api/skills/${encodeURIComponent(name)}/files`),
  getSkillFile: (skill, path) => request(`/api/skills/${encodeURIComponent(skill)}/files/${path}`),
  updateSkillFile: (skill, path, content) => request(`/api/skills/${encodeURIComponent(skill)}/files/${path}`, {
    method: 'PUT', body: JSON.stringify({ content })
  }),

  getLlmConfig: () => request('/api/config/llm'),
  updateLlmConfig: (data) => request('/api/config/llm', {
    method: 'PUT', body: JSON.stringify(data)
  }),
  testLlmConnection: (data) => request('/api/config/llm/test', {
    method: 'POST', body: JSON.stringify(data)
  }),
  getProviders: () => request('/api/debug/providers'),

  exportAppBundle: (name) => request(`/api/apps/${encodeURIComponent(name)}/bundle`),
  downloadAppZip: (name) => `${BASE}/api/apps/${encodeURIComponent(name)}/download`,
  getAppDependencies: (name) => request(`/api/apps/${encodeURIComponent(name)}/dependencies`),
  publishApp: (name, data) => request(`/api/apps/${encodeURIComponent(name)}/publish`, {
    method: 'POST', body: JSON.stringify(data)
  }),
  installAppFromHub: (data) => request('/api/apps/install-from-hub', {
    method: 'POST', body: JSON.stringify(data)
  }),
  importAppBundle: (data) => request('/api/apps/import', {
    method: 'POST', body: JSON.stringify(data)
  }),

  globalSearch: (q) => request(`/api/search?q=${encodeURIComponent(q)}`),

  getCapabilities: () => request('/api/capabilities'),
  getCapabilityGraph: () => request('/api/capabilities/graph'),
  getCapabilityEvents: () => request('/api/capabilities/events'),

  getGovernancePolicy: () => request('/api/governance/policy'),
  updateGovernancePolicy: (policy) => request('/api/governance/policy', {
    method: 'PUT', body: JSON.stringify({ policy })
  }),
  getGovernanceCenter: () => request('/api/governance/center'),
  getGovernanceOptions: () => request('/api/agents/governance/options'),
  getGatewayStatus: () => request('/api/gateway/status'),
  listGatewayPairings: () => request('/api/gateway/pairings'),
  approveGatewayPairing: (deviceId) => request(`/api/gateway/pairings/${encodeURIComponent(deviceId)}/approve`, {
    method: 'POST'
  }),
  rejectGatewayPairing: (deviceId) => request(`/api/gateway/pairings/${encodeURIComponent(deviceId)}/reject`, {
    method: 'POST'
  }),
  listGatewayChannelRoutes: () => request('/api/gateway/channel-routes'),

  listSessions: () => request('/api/sessions'),
  getSessionStatus: (sessionKey, modelName = '') => {
    const qs = modelName ? `?model_name=${encodeURIComponent(modelName)}` : '';
    return request(`/api/sessions/${encodeURIComponent(sessionKey)}/status${qs}`);
  },
  switchSessionMode: (sessionKey, mode) => request(`/api/sessions/${encodeURIComponent(sessionKey)}/mode`, {
    method: 'POST', body: JSON.stringify({ mode }),
  }),
  getSwarmStatus: (sessionKey) => request(`/api/sessions/${encodeURIComponent(sessionKey)}/swarm`),

  getCanvas: (threadId) => request(`/api/conversations/${encodeURIComponent(threadId)}/canvas`),
  setCanvas: (threadId, canvas) => request(`/api/conversations/${encodeURIComponent(threadId)}/canvas`, {
    method: 'PUT', body: JSON.stringify({ canvas }),
  }),

  getCostStats: () => request('/api/cost_stats'),

  getGlobalTrace: (limit = 200, eventType = '') => {
    let url = `/api/trace/global?limit=${limit}`;
    if (eventType) url += `&event_type=${encodeURIComponent(eventType)}`;
    return request(url);
  },

  getModelRouter: () => request('/api/model-router'),
  updateModelRouter: (config) => request('/api/model-router', {
    method: 'PUT', body: JSON.stringify(config)
  }),
  classifyPrompt: (prompt, canvas) => request('/api/model-router/classify', {
    method: 'POST', body: JSON.stringify({ prompt, canvas })
  }),

  getContextBudget: (threadId) => request(`/api/conversations/${threadId}/context-budget`),

  exportConversation: (threadId, fmt = 'markdown') =>
    request(`/api/conversations/${threadId}/export?fmt=${fmt}`),

  forkConversation: (threadId, forkAtIndex = null) => request(`/api/conversations/${encodeURIComponent(threadId)}/fork`, {
    method: 'POST', body: JSON.stringify({ fork_at_index: forkAtIndex }),
  }),

  getAppMatrixTopology: () => request('/api/app-matrix/topology'),
  registerAppMatrixNode: (data) => request('/api/app-matrix/register-node', {
    method: 'POST', body: JSON.stringify(data),
  }),
  addAppMatrixBinding: (data) => request('/api/app-matrix/add-binding', {
    method: 'POST', body: JSON.stringify(data),
  }),

  listPlugins: () => request('/api/plugins'),
  getPlugin: (pluginId) => request(`/api/plugins/${encodeURIComponent(pluginId)}`),
  discoverPlugins: (payload = {}) => request('/api/plugins/discover', {
    method: 'POST', body: JSON.stringify(payload),
  }),
  enablePlugin: (pluginId) => request(`/api/plugins/${encodeURIComponent(pluginId)}/enable`, { method: 'POST', body: '{}' }),
  disablePlugin: (pluginId) => request(`/api/plugins/${encodeURIComponent(pluginId)}/disable`, { method: 'POST', body: '{}' }),
  unloadPlugin: (pluginId) => request(`/api/plugins/${encodeURIComponent(pluginId)}/unload`, { method: 'POST', body: '{}' }),

  listChannels: () => request('/api/channels'),
  wechatClawStatus: () => request('/api/channels/wechat_claw/status'),
  wechatClawLogin: () => request('/api/channels/wechat_claw/login', { method: 'POST', body: '{}' }),
  wechatClawLoginPoll: (qrcode) => request(`/api/channels/wechat_claw/login/poll?qrcode=${encodeURIComponent(qrcode)}`),
  wechatClawLogout: () => request('/api/channels/wechat_claw/logout', { method: 'POST', body: '{}' }),
  wechatClawStartPolling: () => request('/api/channels/wechat_claw/start_polling', { method: 'POST', body: '{}' }),

  getDoctorReport: () => request('/api/doctor'),
  bootstrapTeamWorkspace: () => request('/api/doctor/bootstrap', { method: 'POST', body: '{}' }),
  distillMemory: (force = false) => request(`/api/memory/distill?force=${force ? 'true' : 'false'}`, { method: 'POST', body: '{}' }),
  listChannelPairings: () => request('/api/channels/pairing/pending'),
  approveChannelPairing: (channel, code) => request('/api/channels/pairing/approve', {
    method: 'POST', body: JSON.stringify({ channel, code }),
  }),
  getOpenclawReport: () => request('/api/openclaw/report'),
  importOpenclawChannels: () => request('/api/openclaw/import-channels', { method: 'POST', body: '{}' }),
};
