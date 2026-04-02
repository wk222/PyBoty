import { onMounted, ref } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';

const ASSET_FAMILY_META = {
  apps: {
    route: '/apps',
    accent: '#f472b6',
    icon: '<rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/><rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/>',
  },
  workflows: {
    route: '/workflows',
    accent: '#60a5fa',
    icon: '<circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="6" cy="19" r="3"/><circle cx="18" cy="19" r="3"/><line x1="12" y1="12" x2="6" y2="16"/><line x1="12" y1="12" x2="18" y2="16"/>',
  },
  skills: {
    route: '/skills',
    accent: '#34d399',
    icon: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  },
  tools: {
    route: '/tools',
    accent: '#fbbf24',
    icon: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  },
  agents: {
    route: '/agents',
    accent: '#818cf8',
    icon: '<rect x="3" y="11" width="18" height="11" rx="2"/><circle cx="12" cy="5" r="4"/><line x1="8" y1="16" x2="8" y2="16.01"/><line x1="16" y1="16" x2="16" y2="16.01"/>',
  },
  hub: {
    route: '/hub',
    accent: '#fb923c',
    icon: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  },
};

export default {
  name: 'EcosystemView',
  setup() {
    const loading = ref(true);
    const ecosystem = ref({
      apps: { total: 0, detail: '' },
      workflows: { total: 0, detail: '' },
      skills: { total: 0, detail: '' },
      tools: { total: 0, detail: '' },
      agents: { total: 0, detail: '' },
      hub: { total: 0, detail: '' },
      capabilities: { total: 0, providers: 0, events: 0 },
    });

    function label(key) {
      return t(`ecosystem.${key}`);
    }

    function countLabel(count, suffixKey) {
      return `${count} ${label(suffixKey)}`;
    }

    async function load() {
      loading.value = true;
      try {
        const [apps, workflows, skills, tools, agents, capabilities] = await Promise.allSettled([
          API.listApps(),
          API.listWorkflows(),
          API.listSkills(),
          API.listTools(),
          API.listAgents(),
          API.getCapabilities(),
        ]);

        if (apps.status === 'fulfilled') {
          const list = apps.value.apps || [];
          const enabled = list.filter((item) => item.enabled !== false).length;
          ecosystem.value.apps = { total: list.length, detail: countLabel(enabled, 'enabledSuffix') };
        }
        if (workflows.status === 'fulfilled') {
          const list = workflows.value.saved || [];
          ecosystem.value.workflows = { total: list.length, detail: countLabel(list.length, 'readyToOrchestrate') };
        }
        if (skills.status === 'fulfilled') {
          const entries = Object.values(skills.value.skills || {});
          const enabled = entries.filter((item) => item.enabled).length;
          ecosystem.value.skills = { total: entries.length, detail: countLabel(enabled, 'enabledSuffix') };
        }
        if (tools.status === 'fulfilled') {
          const list = tools.value.tools || [];
          const top = list
            .slice()
            .sort((a, b) => (b.usage_count || 0) - (a.usage_count || 0))
            .slice(0, 2)
            .map((item) => item.name)
            .join(', ');
          ecosystem.value.tools = { total: list.length, detail: top || label('noFrequentTools') };
        }
        if (agents.status === 'fulfilled') {
          const list = agents.value.agents || [];
          const active = list.filter((item) => item.enabled !== false).length;
          ecosystem.value.agents = { total: list.length, detail: countLabel(active, 'activeSuffix') };
        }
        if (capabilities.status === 'fulfilled') {
          const data = capabilities.value || {};
          ecosystem.value.capabilities = {
            total: data.total_calls || 0,
            providers: data.total_providers || 0,
            events: (data.event_log || []).length || data.total_calls || 0,
          };
          ecosystem.value.hub = {
            total: data.total_providers || 0,
            detail: countLabel(data.total_calls || 0, 'capabilityCallsTracked'),
          };
        }
      } catch (error) {
        toast(`Failed to load ecosystem: ${error.message}`, 'error');
      } finally {
        loading.value = false;
      }
    }

    onMounted(load);

    function assetCards() {
      return Object.entries(ASSET_FAMILY_META).map(([key, meta]) => ({
        key,
        route: meta.route,
        accent: meta.accent,
        icon: meta.icon,
        label: label(key),
        total: ecosystem.value[key]?.total || 0,
        detail: ecosystem.value[key]?.detail || label('emptyDetail'),
      }));
    }

    return { loading, ecosystem, load, label, assetCards };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <div>
          <h1 class="mx-page-title">{{ label('title') }}</h1>
          <p style="margin-top:8px;color:var(--text-secondary);max-width:860px;line-height:1.75;">
            {{ label('subtitle') }}
          </p>
        </div>
        <button class="mx-btn mx-btn--ghost" @click="load">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          {{ label('refresh') }}
        </button>
      </div>

      <div v-if="loading" class="mx-loading">
        <div class="mx-spinner"></div>
        <span>{{ label('loading') }}</span>
      </div>

      <template v-else>
        <div class="mx-section">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;">
            <div>
              <h2 class="mx-section-title" style="margin:0;">{{ label('operatingModel') }}</h2>
              <p style="margin:8px 0 0;color:var(--text-secondary);line-height:1.7;max-width:840px;">
                {{ label('operatingHint') }}
              </p>
            </div>
            <router-link to="/chat" class="mx-btn mx-btn--primary">{{ label('startInChat') }}</router-link>
          </div>
        </div>

        <div class="mx-stats-grid" style="margin-top:18px;">
          <router-link
            v-for="card in assetCards()"
            :key="card.key"
            :to="card.route"
            class="mx-stat-card"
          >
            <div class="mx-stat-icon" :style="{ color: card.accent, background: card.accent + '18' }">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" v-html="card.icon"></svg>
            </div>
            <div class="mx-stat-body">
              <div class="mx-stat-value">{{ card.total }}</div>
              <div class="mx-stat-title">{{ card.label }}</div>
              <div class="mx-stat-sub">{{ card.detail }}</div>
            </div>
          </router-link>
        </div>

        <div class="mx-section" style="margin-top:20px;">
          <h2 class="mx-section-title">{{ label('registryTitle') }}</h2>
          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;">
            <div style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);">
              <div style="font-size:12px;color:var(--text-muted);font-weight:700;text-transform:uppercase;letter-spacing:0.06em;">{{ label('capabilityProviders') }}</div>
              <div style="margin-top:10px;font-size:28px;font-weight:800;">{{ ecosystem.capabilities.providers }}</div>
              <div style="margin-top:6px;color:var(--text-secondary);">{{ label('providerHint') }}</div>
            </div>
            <div style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);">
              <div style="font-size:12px;color:var(--text-muted);font-weight:700;text-transform:uppercase;letter-spacing:0.06em;">{{ label('capabilityEvents') }}</div>
              <div style="margin-top:10px;font-size:28px;font-weight:800;">{{ ecosystem.capabilities.events }}</div>
              <div style="margin-top:6px;color:var(--text-secondary);">{{ label('eventsHint') }}</div>
            </div>
            <div style="padding:16px;border:1px solid var(--border);border-radius:16px;background:var(--bg-secondary);">
              <div style="font-size:12px;color:var(--text-muted);font-weight:700;text-transform:uppercase;letter-spacing:0.06em;">{{ label('designRule') }}</div>
              <div style="margin-top:10px;font-size:15px;font-weight:700;line-height:1.6;">{{ label('designRuleBody') }}</div>
            </div>
          </div>
        </div>
      </template>
    </div>
  `,
};
