import { ref, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

export default {
  name: 'DashboardView',
  setup() {
    const stats = ref({
      agents: { active: 0, total: 0 },
      tools: { total: 0, top: [] },
      skills: { enabled: 0, total: 0 },
      workflows: { total: 0 },
      apps: { enabled: 0, total: 0 },
      capabilities: { providers: 0, events: 0 },
      memory: { lines: 0 },
      system: { version: 'v5.0', uptime: '-' },
    });
    const events = ref([]);
    const loading = ref(true);

    async function loadAll() {
      loading.value = true;
      try {
        const [agents, tools, skills, workflows, apps, health] = await Promise.allSettled([
          API.listAgents(),
          API.listTools(),
          API.listSkills(),
          API.listWorkflows(),
          API.listApps(),
          API.health(),
        ]);

        if (agents.status === 'fulfilled') {
          const list = agents.value.agents || [];
          stats.value.agents = { active: list.filter(a => a.enabled !== false).length, total: list.length };
        }
        if (tools.status === 'fulfilled') {
          const list = tools.value.tools || [];
          stats.value.tools = {
            total: list.length,
            top: list.sort((a, b) => (b.usage_count || 0) - (a.usage_count || 0)).slice(0, 3).map(t => t.name),
          };
        }
        if (skills.status === 'fulfilled') {
          const entries = Object.entries(skills.value.skills || {});
          stats.value.skills = {
            enabled: entries.filter(([, v]) => v.enabled).length,
            total: entries.length,
          };
        }
        if (workflows.status === 'fulfilled') {
          const saved = workflows.value.saved || [];
          stats.value.workflows = { total: saved.length };
        }
        if (apps.status === 'fulfilled') {
          const list = apps.value.apps || [];
          stats.value.apps = {
            enabled: list.filter(a => a.enabled !== false).length,
            total: list.length,
          };
        }

        const [capRes, evtRes, memRes] = await Promise.allSettled([
          API.getCapabilities(),
          API.getCapabilityEvents(),
          API.getMemory(),
        ]);
        if (capRes.status === 'fulfilled' && capRes.value) {
          const c = capRes.value;
          stats.value.capabilities = { providers: c.total_providers || 0, events: c.total_calls || 0 };
        }
        if (evtRes.status === 'fulfilled') {
          events.value = (evtRes.value.events || []).slice(0, 20);
        }
        if (memRes.status === 'fulfilled') {
          const content = memRes.value.content || '';
          stats.value.memory = { lines: content.split('\n').filter(l => l.trim()).length };
        }
      } catch (e) {
        toast('Failed to load dashboard: ' + e.message, 'error');
      } finally {
        loading.value = false;
      }
    }

    onMounted(loadAll);

    return { stats, events, loading, loadAll };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Dashboard</h1>
        <button class="mx-btn mx-btn--ghost" @click="loadAll">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          Refresh
        </button>
      </div>

      <div class="mx-stats-grid" v-if="!loading">
        <router-link to="/agents" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:var(--info);background:rgba(96,165,250,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="11" width="18" height="11" rx="2"/><circle cx="12" cy="5" r="4"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.agents.active }}<small>/{{ stats.agents.total }}</small></div>
            <div class="mx-stat-title">Agents</div>
            <div class="mx-stat-sub">active / total</div>
          </div>
        </router-link>

        <router-link to="/tools" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:var(--warning);background:rgba(251,191,36,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.tools.total }}</div>
            <div class="mx-stat-title">Tools</div>
            <div class="mx-stat-sub">{{ stats.tools.top.length ? stats.tools.top.join(', ') : 'no tools yet' }}</div>
          </div>
        </router-link>

        <router-link to="/skills" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:var(--success);background:rgba(52,211,153,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.skills.enabled }}<small>/{{ stats.skills.total }}</small></div>
            <div class="mx-stat-title">Skills</div>
            <div class="mx-stat-sub">enabled / total</div>
          </div>
        </router-link>

        <router-link to="/workflows" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:var(--accent);background:rgba(129,140,248,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="6" cy="19" r="3"/><circle cx="18" cy="19" r="3"/><line x1="12" y1="12" x2="6" y2="16"/><line x1="12" y1="12" x2="18" y2="16"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.workflows.total }}</div>
            <div class="mx-stat-title">Workflows</div>
            <div class="mx-stat-sub">saved workflows</div>
          </div>
        </router-link>

        <router-link to="/apps" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:#f472b6;background:rgba(244,114,182,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/><rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.apps.enabled }}<small>/{{ stats.apps.total }}</small></div>
            <div class="mx-stat-title">Apps</div>
            <div class="mx-stat-sub">enabled / total</div>
          </div>
        </router-link>

        <div class="mx-stat-card mx-stat-card--static">
          <div class="mx-stat-icon" style="color:#fb923c;background:rgba(251,146,60,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.capabilities.events }}</div>
            <div class="mx-stat-title">Capability Bus</div>
            <div class="mx-stat-sub">{{ stats.capabilities.providers }} providers</div>
          </div>
        </div>

        <div class="mx-stat-card mx-stat-card--static">
          <div class="mx-stat-icon" style="color:#a78bfa;background:rgba(167,139,250,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7z"/><circle cx="12" cy="9" r="2.5"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.memory.lines }}</div>
            <div class="mx-stat-title">Memory</div>
            <div class="mx-stat-sub">entries in MEMORY.md</div>
          </div>
        </div>

        <div class="mx-stat-card mx-stat-card--static">
          <div class="mx-stat-icon" style="color:var(--success);background:rgba(52,211,153,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.system.version }}</div>
            <div class="mx-stat-title">System</div>
            <div class="mx-stat-sub">LangChain + PyFlow</div>
          </div>
        </div>
      </div>

      <div v-if="loading" class="mx-loading">
        <div class="mx-spinner"></div>
        <span>Loading dashboard...</span>
      </div>

      <div class="mx-section" v-if="events.length > 0">
        <h2 class="mx-section-title">Recent Activity</h2>
        <div class="mx-activity-list">
          <div v-for="(evt, i) in events" :key="i" class="mx-activity-item">
            <span class="mx-activity-dot"></span>
            <span class="mx-activity-text">{{ typeof evt === 'string' ? evt : (evt.description || evt.type || JSON.stringify(evt)) }}</span>
          </div>
        </div>
      </div>

      <div class="mx-section" v-if="!loading">
        <h2 class="mx-section-title">Quick Actions</h2>
        <div class="mx-quick-actions">
          <router-link to="/chat" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            New Chat
          </router-link>
          <router-link to="/workflows" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            Run Workflow
          </router-link>
          <router-link to="/hub" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
            Browse Hub
          </router-link>
          <router-link to="/governance" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            Governance
          </router-link>
          <router-link to="/settings" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            Workspace
          </router-link>
        </div>
      </div>
    </div>
  `
};
