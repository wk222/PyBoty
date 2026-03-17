import { ref, watch } from 'vue';
import { API } from '/static/api/index.js';

const TYPE_ROUTES = {
  tool: '/tools',
  skill: '/skills',
  agent: '/agents',
  workflow: '/workflows',
  app: '/apps',
};

const TYPE_COLORS = {
  tool: '#fbbf24',
  skill: '#34d399',
  agent: '#818cf8',
  workflow: '#60a5fa',
  app: '#f472b6',
};

export default {
  name: 'TopBar',
  setup() {
    const query = ref('');
    const results = ref([]);
    const showResults = ref(false);
    let debounce = null;

    watch(query, (val) => {
      clearTimeout(debounce);
      if (!val || val.trim().length < 2) { results.value = []; showResults.value = false; return; }
      debounce = setTimeout(async () => {
        try {
          const data = await API.globalSearch(val.trim());
          results.value = data.results || [];
          showResults.value = results.value.length > 0;
        } catch (_) { results.value = []; }
      }, 300);
    });

    function route(item) {
      showResults.value = false;
      query.value = '';
      return TYPE_ROUTES[item.type] || '/';
    }

    function color(type) { return TYPE_COLORS[type] || 'var(--text-muted)'; }

    function closeResults() { showResults.value = false; }
    function delayClose() { setTimeout(() => closeResults(), 200); }

    return { query, results, showResults, route, color, closeResults, delayClose };
  },
  template: `
    <header class="mx-topbar">
      <div class="mx-topbar-left">
        <router-link to="/" class="mx-topbar-brand">
          <span class="mx-topbar-logo">P</span>
          <span class="mx-topbar-title">PyBot <small>Matrix</small></span>
        </router-link>
      </div>
      <div class="mx-topbar-center">
        <div class="mx-search-box" @focusout="delayClose">
          <svg class="mx-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input v-model="query" @focus="showResults = results.length > 0" type="text"
                 placeholder="Search tools, skills, workflows, agents, apps..."
                 class="mx-search-input" />
          <div v-if="showResults" class="mx-search-results">
            <router-link v-for="(r, i) in results" :key="i" :to="route(r)"
                         class="mx-search-result-item" @click="showResults = false; query = ''">
              <span class="mx-search-result-type" :style="{ background: color(r.type) + '18', color: color(r.type), borderRadius: '100px', padding: '2px 8px', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }">{{ r.type }}</span>
              <span style="font-weight:600;color:var(--text-primary);">{{ r.name }}</span>
              <span v-if="r.description" style="color:var(--text-muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;">{{ r.description }}</span>
            </router-link>
          </div>
        </div>
      </div>
      <div class="mx-topbar-right">
        <router-link to="/chat" class="mx-topbar-action" title="Chat">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </router-link>
        <span class="mx-topbar-version">v5.0</span>
      </div>
    </header>
  `
};
