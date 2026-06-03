const ICONS = {
  chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  governance: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  ecosystem:
    '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  settings:
    '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  apps:
    '<rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/><rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/>',
  workflows:
    '<circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="6" cy="19" r="3"/><circle cx="18" cy="19" r="3"/><line x1="12" y1="12" x2="6" y2="16"/><line x1="12" y1="12" x2="18" y2="16"/>',
  skills:
    '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  tools:
    '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  agents:
    '<rect x="3" y="11" width="18" height="11" rx="2"/><circle cx="12" cy="5" r="4"/><line x1="8" y1="16" x2="8" y2="16.01"/><line x1="16" y1="16" x2="16" y2="16.01"/>',
  hub:
    '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  system: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v3"/><path d="M12 18v3"/><path d="M3 12h3"/><path d="M18 12h3"/>',
  memory: '<path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z"/><line x1="9" y1="21" x2="15" y2="21"/>',
  tracing: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  appMatrix: '<rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/><rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/><line x1="11" y1="6.5" x2="13" y2="6.5"/><line x1="11" y1="17.5" x2="13" y2="17.5"/><line x1="6.5" y1="11" x2="6.5" y2="13"/><line x1="17.5" y1="11" x2="17.5" y2="13"/>',
  ide: '<rect x="2" y="3" width="20" height="18" rx="2" ry="2"/><line x1="9" y1="3" x2="9" y2="21"/><line x1="9" y1="9" x2="22" y2="9"/>',
  integrations:
    '<path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>',
  team:
    '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
};

export const PRIMARY_SURFACES = Object.freeze([
  { key: 'chat', to: '/chat', icon: ICONS.chat },
  { key: 'ide', href: '/workspace', icon: ICONS.ide },
  { key: 'memory', to: '/memory', icon: ICONS.memory },
  { key: 'appMatrix', to: '/app-matrix', icon: ICONS.appMatrix },
  { key: 'governance', to: '/governance', icon: ICONS.governance },
  { key: 'ecosystem', to: '/ecosystem', icon: ICONS.ecosystem },
]);

const TASKS_ICON = '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>';

export const UTILITY_SURFACES = Object.freeze([
  { key: 'team', to: '/team', icon: ICONS.team },
  { key: 'tasks', to: '/tasks', icon: TASKS_ICON },
  { key: 'tracing', to: '/tracing', icon: ICONS.tracing },
  { key: 'integrations', to: '/integrations', icon: ICONS.integrations },
  { key: 'settings', to: '/settings', icon: ICONS.settings },
]);

export const ECOSYSTEM_FAMILY_ORDER = Object.freeze(['apps', 'workflows', 'skills', 'tools', 'agents']);

export const ECOSYSTEM_FAMILIES = Object.freeze({
  apps: {
    key: 'apps',
    singular: 'app',
    route: '/apps',
    accent: '#f472b6',
    gradient: 'linear-gradient(135deg,#ec4899,#f472b6)',
    icon: ICONS.apps,
  },
  workflows: {
    key: 'workflows',
    singular: 'workflow',
    route: '/workflows',
    accent: '#60a5fa',
    gradient: 'linear-gradient(135deg,#3b82f6,#60a5fa)',
    icon: ICONS.workflows,
  },
  skills: {
    key: 'skills',
    singular: 'skill',
    route: '/skills',
    accent: '#34d399',
    gradient: 'linear-gradient(135deg,#10b981,#34d399)',
    icon: ICONS.skills,
  },
  tools: {
    key: 'tools',
    singular: 'tool',
    route: '/tools',
    accent: '#fbbf24',
    gradient: 'linear-gradient(135deg,#f59e0b,#fbbf24)',
    icon: ICONS.tools,
  },
  agents: {
    key: 'agents',
    singular: 'agent',
    route: '/agents',
    accent: '#818cf8',
    gradient: 'linear-gradient(135deg,#6366f1,#818cf8)',
    icon: ICONS.agents,
  },
});

export const ECOSYSTEM_ADVANCED_LINKS = Object.freeze([
  {
    key: 'hub',
    to: '/hub',
    icon: ICONS.hub,
  },
  {
    key: 'system',
    to: '/system',
    icon: ICONS.system,
  },
]);

const SEARCH_TYPE_TO_FAMILY = Object.freeze({
  app: 'apps',
  workflow: 'workflows',
  skill: 'skills',
  tool: 'tools',
  agent: 'agents',
});

export function buildEcosystemRoute(asset = '', query = '') {
  const routeQuery = {};
  if (asset && asset !== 'all') routeQuery.asset = asset;
  if (query) routeQuery.q = query;
  return { path: '/ecosystem', query: routeQuery };
}

export function getSearchResultRoute(result) {
  const family = SEARCH_TYPE_TO_FAMILY[result?.type];
  if (!family) return '/chat';
  return buildEcosystemRoute(family, result?.name || '');
}

export function getSearchResultColor(type) {
  const family = SEARCH_TYPE_TO_FAMILY[type];
  return family ? ECOSYSTEM_FAMILIES[family].accent : 'var(--text-muted)';
}
