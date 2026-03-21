const BASE = '';

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  });
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

  listConversations: () => request('/api/conversations'),
  createConversation: () => request('/api/conversations', { method: 'POST', body: '{}' }),
  deleteConversation: (id) => request(`/api/conversations/${id}`, { method: 'DELETE' }),
  getHistory: (id) => request(`/api/conversations/${id}/history`),

  chatStream: (threadId, message) => fetch(`${BASE}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ thread_id: threadId, message }),
  }),

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

  listApprovals: () => request('/api/approvals'),
  resolveApproval: (approvalId, approved, note = '', approver = '') => request(`/api/approvals/${encodeURIComponent(approvalId)}/resolve`, {
    method: 'POST', body: JSON.stringify({ approved, note, approver })
  }),

  listApps: () => request('/api/apps'),
  getAppInfo: (name) => request(`/api/apps/${encodeURIComponent(name)}/info`),
  toggleApp: (name, enabled) => request(`/api/apps/${encodeURIComponent(name)}/toggle`, {
    method: 'PATCH', body: JSON.stringify({ enabled })
  }),
  deleteApp: (name) => request(`/api/apps/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  getStatus: (threadId) => request(`/api/status/${threadId}`),

  listWorkspaceFiles: () => request('/api/workspace/files'),
  getWorkspaceFile: (name) => request(`/api/workspace/${encodeURIComponent(name)}`),
  updateWorkspaceFile: (name, content) => request(`/api/workspace/${encodeURIComponent(name)}`, {
    method: 'PUT', body: JSON.stringify({ content })
  }),

  getMemory: () => request('/api/memory'),
  addMemory: (content) => request('/api/memory', { method: 'POST', body: JSON.stringify({ content }) }),

  listScheduleTasks: () => request('/api/schedule/tasks'),
  createScheduleTask: (data) => request('/api/schedule/tasks', { method: 'POST', body: JSON.stringify(data) }),
  toggleScheduleTask: (name, enabled) => request(`/api/schedule/tasks/${encodeURIComponent(name)}/toggle`, {
    method: 'PATCH', body: JSON.stringify({ enabled })
  }),
  deleteScheduleTask: (name) => request(`/api/schedule/tasks/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  getScheduleHistory: (limit = 50) => request(`/api/schedule/history?limit=${limit}`),

  getBackgroundTasks: () => request('/api/debug/tasks'),
  getBackgroundTask: (id) => request(`/api/debug/tasks/${encodeURIComponent(id)}`),
  cancelBackgroundTask: (id) => request(`/api/debug/tasks/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),

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

  upload: (file) => {
    const fd = new FormData();
    fd.append('file', file);
    return fetch(`${BASE}/api/upload`, { method: 'POST', body: fd }).then(r => r.json());
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
  getGovernanceOptions: () => request('/api/agents/governance/options'),
};
