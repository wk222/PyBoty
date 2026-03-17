import { API } from '/static/api/index.js';
import { useEntityList } from '/static/composables/useEntityList.js';
import EntityCard from '/static/components/EntityCard.js';

const APP_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/><rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/></svg>';

export default {
  name: 'AppList',
  components: { EntityCard },
  setup() {
    const { items: apps, loading, load, toggle, remove } = useEntityList({
      fetchFn:    () => API.listApps(),
      mapFn:      (d) => d.apps || [],
      toggleFn:   (name, enabled) => API.toggleApp(name, enabled),
      deleteFn:   (name) => API.deleteApp(name),
      entityLabel: 'app',
    });

    return { apps, loading, load, toggle, remove, APP_ICON };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Apps</h1>
        <button class="mx-btn mx-btn--ghost" @click="load">Refresh</button>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <div v-else-if="apps.length === 0" class="mx-empty">
        <p>No apps yet. Tell PyBot to create one in Chat.</p>
        <router-link to="/chat" class="mx-btn mx-btn--primary">Go to Chat</router-link>
      </div>

      <div v-else class="mx-card-grid">
        <EntityCard
          v-for="a in apps" :key="a.name"
          :name="a.display_name || a.name"
          :description="a.description"
          :icon="APP_ICON"
          gradient="linear-gradient(135deg,#ec4899,#f472b6)"
          :disabled="a.enabled === false"
          :toggleable="true"
          :enabled="a.enabled !== false"
          :deletable="true"
          @toggle="toggle(a.name, $event)"
          @delete="remove(a.name)"
        >
          <template #actions>
            <a v-if="a.url" :href="a.url" target="_blank" class="mx-btn-icon" title="Open">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            </a>
          </template>
          <div class="mx-entity-card-meta">
            <span class="mx-tag">v{{ a.version || '1.0' }}</span>
            <span class="mx-tag" :style="{ color: a.enabled !== false ? 'var(--success)' : 'var(--error)' }">
              {{ a.enabled !== false ? 'Enabled' : 'Disabled' }}
            </span>
            <span v-for="t in (a.tags || [])" :key="t" class="mx-cap-tag">{{ t }}</span>
          </div>
        </EntityCard>
      </div>
    </div>
  `
};
