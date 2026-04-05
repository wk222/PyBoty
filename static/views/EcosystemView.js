import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';
import EntityCard from '/static/components/EntityCard.js';
import {
  buildEcosystemRoute,
  ECOSYSTEM_ADVANCED_LINKS,
  ECOSYSTEM_FAMILIES,
  ECOSYSTEM_FAMILY_ORDER,
} from '/static/config/navigation.js';

function normalizeActiveAsset(value) {
  if (value && value in ECOSYSTEM_FAMILIES) return value;
  return 'all';
}

function familyLabel(key) {
  return t(`ecosystem.${key}`);
}

export default {
  name: 'EcosystemView',
  components: { EntityCard },
  setup() {
    const route = useRoute();
    const router = useRouter();
    const loading = ref(true);
    const searchQuery = ref('');
    const ecosystem = ref({
      apps: { total: 0, detail: '' },
      workflows: { total: 0, detail: '' },
      skills: { total: 0, detail: '' },
      tools: { total: 0, detail: '' },
      agents: { total: 0, detail: '' },
      capabilities: { providers: 0, events: 0 },
    });
    const catalog = ref({
      apps: [],
      workflows: [],
      skills: [],
      tools: [],
      agents: [],
    });

    const activeAsset = computed(() => normalizeActiveAsset(route.query.asset));
    const activeFamilyMeta = computed(() =>
      activeAsset.value === 'all'
        ? null
        : {
            ...ECOSYSTEM_FAMILIES[activeAsset.value],
            label: familyLabel(activeAsset.value),
          },
    );

    const allItems = computed(() =>
      ECOSYSTEM_FAMILY_ORDER.flatMap((key) => catalog.value[key] || []),
    );

    const filteredItems = computed(() => {
      const baseItems = activeAsset.value === 'all' ? allItems.value : catalog.value[activeAsset.value] || [];
      const query = searchQuery.value.trim().toLowerCase();
      if (!query) return baseItems;
      return baseItems.filter((item) => {
        const haystack = [
          item.name,
          item.description,
          item.familyLabel,
          ...(item.tags || []),
        ]
          .join(' ')
          .toLowerCase();
        return haystack.includes(query);
      });
    });

    const visibleItems = computed(() => filteredItems.value.slice(0, 24));
    const hasOverflow = computed(() => filteredItems.value.length > visibleItems.value.length);
    const visibleCountText = computed(() => `${visibleItems.value.length} / ${filteredItems.value.length}`);

    const familyTabs = computed(() => [
      {
        key: 'all',
        label: t('ecosystem.allAssets'),
        count: allItems.value.length,
      },
      ...ECOSYSTEM_FAMILY_ORDER.map((key) => ({
        key,
        label: familyLabel(key),
        count: catalog.value[key].length,
      })),
    ]);

    const familyCards = computed(() =>
      ECOSYSTEM_FAMILY_ORDER.map((key) => {
        const meta = ECOSYSTEM_FAMILIES[key];
        return {
          ...meta,
          label: familyLabel(key),
          total: ecosystem.value[key]?.total || 0,
          detail: ecosystem.value[key]?.detail || t('ecosystem.emptyDetail'),
          to: buildEcosystemRoute(key, searchQuery.value.trim()),
        };
      }),
    );

    const advancedLinks = computed(() =>
      ECOSYSTEM_ADVANCED_LINKS.map((item) => ({
        ...item,
        title: item.key === 'hub' ? t('ecosystem.openMarketplace') : t('ecosystem.openSystemMap'),
        body: item.key === 'hub' ? t('ecosystem.marketplaceHint') : t('ecosystem.systemHint'),
      })),
    );

    const openManagerRoute = computed(() => activeFamilyMeta.value?.route || null);

    function label(key) {
      return t(`ecosystem.${key}`);
    }

    function countLabel(count, suffixKey) {
      return `${count} ${label(suffixKey)}`;
    }

    function buildItem(key, item) {
      const meta = ECOSYSTEM_FAMILIES[key];
      const familyLabelText = familyLabel(key);

      if (key === 'apps') {
        const enabled = item.enabled !== false;
        return {
          family: key,
          familyLabel: familyLabelText,
          name: item.display_name || item.name,
          description: item.description || item.name,
          icon: meta.icon,
          gradient: meta.gradient,
          managerRoute: meta.route,
          url: item.url || '',
          tags: [
            enabled ? t('common.enabled') : t('common.disabled'),
            item.version ? `v${item.version}` : 'v1.0',
            ...(item.mode && item.mode !== 'static' ? [item.mode] : []),
          ],
        };
      }

      if (key === 'workflows') {
        return {
          family: key,
          familyLabel: familyLabelText,
          name: item.name || item.id || '',
          description: item.description || label('workflowHint'),
          icon: meta.icon,
          gradient: meta.gradient,
          managerRoute: meta.route,
          url: '',
          tags: [
            `${item.nodes_count || 0} nodes`,
            item.schedule ? 'scheduled' : 'manual',
          ],
        };
      }

      if (key === 'skills') {
        return {
          family: key,
          familyLabel: familyLabelText,
          name: item.name || item.key,
          description: item.description || item.key,
          icon: meta.icon,
          gradient: meta.gradient,
          managerRoute: meta.route,
          url: '',
          tags: [
            item.enabled ? t('common.enabled') : t('common.disabled'),
            item.version ? `v${item.version}` : 'v1.0',
            item.author || 'system',
          ],
        };
      }

      if (key === 'tools') {
        return {
          family: key,
          familyLabel: familyLabelText,
          name: item.name,
          description: item.description || item.name,
          icon: meta.icon,
          gradient: meta.gradient,
          managerRoute: meta.route,
          url: '',
          tags: [`${item.usage_count || 0}x`],
        };
      }

      return {
        family: key,
        familyLabel: familyLabelText,
        name: item.name,
        description: item.description || item.role || item.name,
        icon: meta.icon,
        gradient: meta.gradient,
        managerRoute: meta.route,
        url: '',
        tags: [
          item.enabled !== false ? t('common.enabled') : t('common.disabled'),
          item.model || 'default',
          `${item.usage_count || 0}x`,
        ],
      };
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
          catalog.value.apps = list.map((item) => buildItem('apps', item));
        }
        if (workflows.status === 'fulfilled') {
          const list = workflows.value.saved || [];
          ecosystem.value.workflows = { total: list.length, detail: countLabel(list.length, 'readyToOrchestrate') };
          catalog.value.workflows = list.map((item) => buildItem('workflows', item));
        }
        if (skills.status === 'fulfilled') {
          const entries = Object.entries(skills.value.skills || {}).map(([key, info]) => ({ key, ...info }));
          const enabled = entries.filter((item) => item.enabled).length;
          ecosystem.value.skills = { total: entries.length, detail: countLabel(enabled, 'enabledSuffix') };
          catalog.value.skills = entries.map((item) => buildItem('skills', item));
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
          catalog.value.tools = list.map((item) => buildItem('tools', item));
        }
        if (agents.status === 'fulfilled') {
          const list = agents.value.agents || [];
          const active = list.filter((item) => item.enabled !== false).length;
          ecosystem.value.agents = { total: list.length, detail: countLabel(active, 'activeSuffix') };
          catalog.value.agents = list.map((item) => buildItem('agents', item));
        }
        if (capabilities.status === 'fulfilled') {
          const data = capabilities.value || {};
          ecosystem.value.capabilities = {
            providers: data.total_providers || 0,
            events: (data.event_log || []).length || data.total_calls || 0,
          };
        }
      } catch (error) {
        toast(`Failed to load ecosystem: ${error.message}`, 'error');
      } finally {
        loading.value = false;
      }
    }

    function setAsset(asset) {
      router.replace(buildEcosystemRoute(asset, searchQuery.value.trim()));
    }

    function clearSearch() {
      searchQuery.value = '';
      if (route.query.q) {
        router.replace(buildEcosystemRoute(activeAsset.value === 'all' ? '' : activeAsset.value, ''));
      }
    }

    watch(
      () => route.query.q,
      (value) => {
        searchQuery.value = typeof value === 'string' ? value : '';
      },
      { immediate: true },
    );

    onMounted(load);

    return {
      loading,
      searchQuery,
      ecosystem,
      activeAsset,
      activeFamilyMeta,
      familyTabs,
      familyCards,
      advancedLinks,
      visibleItems,
      filteredItems,
      hasOverflow,
      visibleCountText,
      openManagerRoute,
      load,
      label,
      setAsset,
      clearSearch,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <div>
          <h1 class="mx-page-title">{{ label('title') }}</h1>
          <p style="margin-top:8px;color:var(--text-secondary);max-width:900px;line-height:1.75;">
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
            v-for="card in familyCards"
            :key="card.key"
            :to="card.to"
            class="mx-stat-card"
            :style="card.key === activeAsset ? { borderColor: card.accent, boxShadow: '0 0 0 1px ' + card.accent + '55' } : {}"
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
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;">
            <div>
              <h2 class="mx-section-title" style="margin:0;">{{ label('workspaceTitle') }}</h2>
              <p style="margin:8px 0 0;color:var(--text-secondary);line-height:1.7;max-width:900px;">
                {{ label('workspaceHint') }}
              </p>
            </div>
            <router-link v-if="openManagerRoute" :to="openManagerRoute" class="mx-btn mx-btn--ghost">
              {{ label('openFullManager') }}
            </router-link>
          </div>

          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:16px;">
            <input
              v-model="searchQuery"
              class="mx-input"
              style="flex:1;min-width:260px;"
              :placeholder="label('searchPlaceholder')"
            />
            <button v-if="searchQuery" class="mx-btn mx-btn--ghost" @click="clearSearch">
              {{ label('clearSearch') }}
            </button>
          </div>

          <div class="mx-tabs" style="margin-top:16px;">
            <button
              v-for="tab in familyTabs"
              :key="tab.key"
              class="mx-tab"
              :class="{ active: activeAsset === tab.key }"
              @click="setAsset(tab.key)"
            >
              {{ tab.label }} ({{ tab.count }})
            </button>
          </div>

          <div v-if="visibleItems.length === 0" class="mx-empty">
            <p>{{ label('noMatches') }}</p>
            <p style="margin:0;color:var(--text-secondary);">{{ label('noMatchesHint') }}</p>
          </div>

          <div v-else class="mx-card-grid">
            <EntityCard
              v-for="item in visibleItems"
              :key="item.family + ':' + item.name"
              :name="item.name"
              :description="item.description"
              :icon="item.icon"
              :gradient="item.gradient"
            >
              <template #actions>
                <router-link :to="item.managerRoute" class="mx-btn-icon" :title="label('openFullManager')">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 3h7v7"/><path d="M10 14L21 3"/><path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/></svg>
                </router-link>
                <a v-if="item.url" :href="item.url" target="_blank" class="mx-btn-icon" :title="label('openAsset')">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                </a>
              </template>
              <div class="mx-entity-card-meta">
                <span class="mx-cap-tag">{{ item.familyLabel }}</span>
                <span v-for="tag in item.tags" :key="tag" class="mx-tag">{{ tag }}</span>
              </div>
            </EntityCard>
          </div>

          <div v-if="hasOverflow" style="margin-top:12px;color:var(--text-muted);font-size:12px;">
            {{ label('showingSubset') }} {{ visibleCountText }}
          </div>
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

        <div class="mx-section" style="margin-top:20px;">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;">
            <div>
              <h2 class="mx-section-title" style="margin:0;">{{ label('advancedTitle') }}</h2>
              <p style="margin:8px 0 0;color:var(--text-secondary);line-height:1.7;max-width:900px;">
                {{ label('advancedHint') }}
              </p>
            </div>
          </div>
          <div class="mx-stats-grid" style="margin-top:16px;">
            <router-link
              v-for="item in advancedLinks"
              :key="item.key"
              :to="item.to"
              class="mx-stat-card"
            >
              <div class="mx-stat-icon" style="color:var(--accent);background:rgba(129,140,248,0.12);">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" v-html="item.icon"></svg>
              </div>
              <div class="mx-stat-body">
                <div class="mx-stat-value" style="font-size:18px;">{{ item.title }}</div>
                <div class="mx-stat-title">{{ item.body }}</div>
              </div>
            </router-link>
          </div>
        </div>
      </template>
    </div>
  `,
};
