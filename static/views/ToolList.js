import { ref, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { useEntityList } from '/static/composables/useEntityList.js';
import EntityCard from '/static/components/EntityCard.js';

const TOOL_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>';

export default {
  name: 'ToolList',
  components: { EntityCard },
  setup() {
    const templates = ref({});
    const filter = ref('all');

    const { items: tools, loading, load: baseLoad, remove } = useEntityList({
      fetchFn:    () => API.listTools(),
      mapFn:      (d) => d.tools || [],
      deleteFn:   (name) => API.deleteTool(name),
      entityLabel: 'tool',
    });

    async function load() {
      const tmplRes = API.listTemplates().catch(() => ({ by_category: {} }));
      await baseLoad();
      templates.value = (await tmplRes).by_category || {};
    }

    const sorted = () => {
      let list = [...tools.value];
      if (filter.value === 'most-used') list.sort((a, b) => (b.usage_count || 0) - (a.usage_count || 0));
      return list;
    };

    onMounted(load);

    return { tools, templates, loading, filter, load, remove, sorted, TOOL_ICON };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Tools</h1>
        <div style="display:flex;gap:8px;align-items:center;">
          <select v-model="filter" class="mx-select">
            <option value="all">All</option>
            <option value="most-used">Most Used</option>
          </select>
          <button class="mx-btn mx-btn--ghost" @click="load">Refresh</button>
        </div>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <div v-else>
        <div v-if="tools.length === 0" class="mx-empty">
          <p>No custom tools yet. Tell PyBot to create one in Chat.</p>
          <router-link to="/chat" class="mx-btn mx-btn--primary">Go to Chat</router-link>
        </div>

        <div v-else class="mx-card-grid">
          <EntityCard
            v-for="t in sorted()" :key="t.name"
            :name="t.name"
            :description="t.description"
            :icon="TOOL_ICON"
            gradient="linear-gradient(135deg,#f59e0b,#fbbf24)"
            :deletable="true"
            @delete="remove(t.name)"
          >
            <div class="mx-entity-card-meta">
              <span class="mx-tag">Used: {{ t.usage_count || 0 }}x</span>
            </div>
          </EntityCard>
        </div>

        <div class="mx-section" v-if="Object.keys(templates).length > 0">
          <h2 class="mx-section-title">Templates</h2>
          <div v-for="(tmps, cat) in templates" :key="cat" style="margin-bottom:16px;">
            <h3 class="mx-subsection-title">{{ cat }}</h3>
            <div class="mx-card-grid mx-card-grid--compact">
              <div v-for="t in tmps" :key="t.name" class="mx-entity-card mx-entity-card--compact">
                <div class="mx-entity-card-name">{{ t.display_name }}</div>
                <div class="mx-entity-card-desc">{{ t.name }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `
};
