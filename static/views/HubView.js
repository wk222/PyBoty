import { ref, computed, onMounted, watch } from 'vue';
import { toast } from '/static/stores/global.js';

const HUB_URL = localStorage.getItem('pyhub_url') || 'http://localhost:8000';
const HUB_TOKEN = localStorage.getItem('pyhub_token') || '';

async function hubRequest(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...options.headers };
  if (HUB_TOKEN) headers['Authorization'] = `Bearer ${HUB_TOKEN}`;
  const res = await fetch(`${HUB_URL}/api/v1${path}`, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export default {
  name: 'HubView',
  setup() {
    const packages = ref([]);
    const total = ref(0);
    const loading = ref(false);
    const searchQuery = ref('');
    const activeType = ref(null);
    const sortBy = ref('updated');
    const page = ref(1);
    const pageSize = 12;
    const selectedPkg = ref(null);
    const detailLoading = ref(false);
    const hubConnected = ref(false);
    const configOpen = ref(false);
    const hubUrlInput = ref(HUB_URL);

    const types = [
      { key: null, label: 'All', icon: 'grid' },
      { key: 'skill', label: 'Skills', icon: 'book' },
      { key: 'tool', label: 'Tools', icon: 'wrench' },
      { key: 'workflow', label: 'Workflows', icon: 'flow' },
      { key: 'app', label: 'Apps', icon: 'app' },
    ];

    const totalPages = computed(() => Math.ceil(total.value / pageSize));

    async function loadPackages() {
      loading.value = true;
      try {
        const params = new URLSearchParams({ page: page.value, page_size: pageSize, sort: sortBy.value });
        if (activeType.value) params.set('type', activeType.value);
        const data = await hubRequest(`/packages?${params}`);
        packages.value = data.items || [];
        total.value = data.total || 0;
        hubConnected.value = true;
      } catch (e) {
        hubConnected.value = false;
        packages.value = [];
      } finally {
        loading.value = false;
      }
    }

    async function searchPackages() {
      if (!searchQuery.value.trim()) { loadPackages(); return; }
      loading.value = true;
      try {
        const params = new URLSearchParams({ q: searchQuery.value, page: page.value, page_size: pageSize });
        if (activeType.value) params.set('type', activeType.value);
        const data = await hubRequest(`/search?${params}`);
        packages.value = (data.items || []).map(i => i.package);
        total.value = data.total || 0;
        hubConnected.value = true;
      } catch (e) {
        hubConnected.value = false;
      } finally {
        loading.value = false;
      }
    }

    async function openDetail(slug) {
      detailLoading.value = true;
      try {
        selectedPkg.value = await hubRequest(`/packages/${slug}`);
      } catch (e) {
        toast.error(`Failed to load: ${e.message}`);
      } finally {
        detailLoading.value = false;
      }
    }

    function closeDetail() { selectedPkg.value = null; }

    function setType(t) { activeType.value = t; page.value = 1; doSearch(); }
    function setSort(s) { sortBy.value = s; page.value = 1; doSearch(); }
    function prevPage() { if (page.value > 1) { page.value--; doSearch(); } }
    function nextPage() { if (page.value < totalPages.value) { page.value++; doSearch(); } }

    function doSearch() {
      if (searchQuery.value.trim()) searchPackages();
      else loadPackages();
    }

    function saveConfig() {
      localStorage.setItem('pyhub_url', hubUrlInput.value);
      configOpen.value = false;
      loadPackages();
    }

    function typeColor(t) {
      const m = { skill: '#818cf8', tool: '#34d399', workflow: '#fbbf24', app: '#f87171' };
      return m[t] || '#94a3b8';
    }
    function typeIcon(t) {
      const m = {
        skill: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
        tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
        workflow: '<circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="6" cy="19" r="3"/><circle cx="18" cy="19" r="3"/><line x1="12" y1="12" x2="6" y2="16"/><line x1="12" y1="12" x2="18" y2="16"/>',
        app: '<rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/><rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/>',
      };
      return m[t] || m.skill;
    }

    function formatDate(d) {
      if (!d) return '';
      return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }
    function formatSize(bytes) {
      if (!bytes) return '0 B';
      const u = ['B', 'KB', 'MB', 'GB'];
      let i = 0;
      let s = bytes;
      while (s >= 1024 && i < u.length - 1) { s /= 1024; i++; }
      return `${s.toFixed(i ? 1 : 0)} ${u[i]}`;
    }

    onMounted(loadPackages);

    return {
      packages, total, loading, searchQuery, activeType, sortBy,
      page, totalPages, selectedPkg, detailLoading, hubConnected,
      configOpen, hubUrlInput,
      types, loadPackages, searchPackages, openDetail, closeDetail,
      setType, setSort, prevPage, nextPage, doSearch, saveConfig,
      typeColor, typeIcon, formatDate, formatSize,
    };
  },
  template: `
    <div class="hub-view">
      <header class="hub-header">
        <div class="hub-title-row">
          <div class="hub-title">
            <svg class="hub-logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
              <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
              <line x1="12" y1="22.08" x2="12" y2="12"/>
            </svg>
            <h1>PyHub <span class="hub-subtitle">Marketplace</span></h1>
          </div>
          <div class="hub-actions">
            <span class="hub-status" :class="hubConnected ? 'connected' : 'disconnected'">
              {{ hubConnected ? 'Connected' : 'Disconnected' }}
            </span>
            <button class="hub-config-btn" @click="configOpen = !configOpen" title="Configure registry">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                <circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
              </svg>
            </button>
          </div>
        </div>

        <div v-if="configOpen" class="hub-config-panel">
          <label>Registry URL</label>
          <div class="hub-config-row">
            <input v-model="hubUrlInput" placeholder="http://localhost:8000" class="hub-input" />
            <button class="mx-btn mx-btn-primary" @click="saveConfig">Save</button>
          </div>
        </div>

        <div class="hub-search-bar">
          <svg class="hub-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input v-model="searchQuery" @keyup.enter="doSearch" placeholder="Search packages..." class="hub-search-input" />
        </div>

        <div class="hub-filters">
          <div class="hub-type-tabs">
            <button v-for="t in types" :key="t.key"
                    class="hub-type-tab" :class="{ active: activeType === t.key }"
                    @click="setType(t.key)">
              {{ t.label }}
            </button>
          </div>
          <select v-model="sortBy" @change="doSearch" class="hub-sort-select">
            <option value="updated">Recently Updated</option>
            <option value="downloads">Most Downloads</option>
            <option value="stars">Most Stars</option>
            <option value="created">Newest</option>
          </select>
        </div>
      </header>

      <div v-if="loading" class="hub-loading">
        <div class="mx-spinner"></div>
      </div>

      <div v-else-if="!hubConnected" class="hub-empty">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="48" height="48" style="opacity:.4">
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
        </svg>
        <p>Cannot connect to PyHub registry</p>
        <p class="hub-empty-sub">Click the settings icon to configure registry URL</p>
      </div>

      <div v-else-if="packages.length === 0" class="hub-empty">
        <p>No packages found</p>
      </div>

      <div v-else class="hub-grid">
        <div v-for="pkg in packages" :key="pkg.slug" class="hub-card" @click="openDetail(pkg.slug)">
          <div class="hub-card-header">
            <div class="hub-card-icon" :style="{ background: typeColor(pkg.type) + '18', color: typeColor(pkg.type) }">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20" v-html="typeIcon(pkg.type)"></svg>
            </div>
            <div class="hub-card-meta">
              <h3 class="hub-card-name">{{ pkg.display_name }}</h3>
              <span class="hub-card-slug">{{ pkg.slug }}</span>
            </div>
          </div>
          <p class="hub-card-summary">{{ pkg.summary || 'No description' }}</p>
          <div class="hub-card-footer">
            <span class="hub-card-type" :style="{ color: typeColor(pkg.type) }">{{ pkg.type }}</span>
            <div class="hub-card-stats">
              <span title="Downloads">↓ {{ pkg.stats_downloads }}</span>
              <span title="Stars">★ {{ pkg.stats_stars }}</span>
            </div>
          </div>
          <div v-if="pkg.badges && pkg.badges.length" class="hub-card-badges">
            <span v-for="b in pkg.badges" :key="b" class="hub-badge" :class="'hub-badge-' + b">{{ b }}</span>
          </div>
        </div>
      </div>

      <div v-if="totalPages > 1" class="hub-pagination">
        <button class="hub-page-btn" :disabled="page <= 1" @click="prevPage">← Prev</button>
        <span class="hub-page-info">{{ page }} / {{ totalPages }}</span>
        <button class="hub-page-btn" :disabled="page >= totalPages" @click="nextPage">Next →</button>
      </div>

      <!-- Detail modal -->
      <div v-if="selectedPkg" class="hub-modal-overlay" @click.self="closeDetail">
        <div class="hub-modal">
          <button class="hub-modal-close" @click="closeDetail">×</button>
          <div class="hub-detail-header">
            <div class="hub-card-icon hub-detail-icon" :style="{ background: typeColor(selectedPkg.type) + '18', color: typeColor(selectedPkg.type) }">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="28" height="28" v-html="typeIcon(selectedPkg.type)"></svg>
            </div>
            <div>
              <h2>{{ selectedPkg.display_name }}</h2>
              <span class="hub-card-slug">{{ selectedPkg.slug }} · {{ selectedPkg.type }}</span>
            </div>
          </div>
          <p class="hub-detail-summary">{{ selectedPkg.summary || 'No description' }}</p>
          <div class="hub-detail-stats">
            <div class="hub-stat"><span class="hub-stat-val">{{ selectedPkg.stats_downloads }}</span><span class="hub-stat-label">Downloads</span></div>
            <div class="hub-stat"><span class="hub-stat-val">{{ selectedPkg.stats_stars }}</span><span class="hub-stat-label">Stars</span></div>
            <div class="hub-stat"><span class="hub-stat-val">{{ selectedPkg.stats_versions }}</span><span class="hub-stat-label">Versions</span></div>
            <div class="hub-stat"><span class="hub-stat-val">{{ selectedPkg.stats_comments || 0 }}</span><span class="hub-stat-label">Comments</span></div>
          </div>
          <div v-if="selectedPkg.latest_version" class="hub-detail-version">
            <h4>Latest: v{{ selectedPkg.latest_version.version }}</h4>
            <p v-if="selectedPkg.latest_version.changelog" class="hub-detail-changelog">{{ selectedPkg.latest_version.changelog }}</p>
            <div class="hub-detail-files">{{ selectedPkg.latest_version.file_count }} files · {{ formatSize(selectedPkg.latest_version.total_size) }}</div>
          </div>
          <div v-if="selectedPkg.versions && selectedPkg.versions.length > 1" class="hub-detail-versions">
            <h4>All Versions</h4>
            <div class="hub-version-list">
              <div v-for="v in selectedPkg.versions" :key="v.id" class="hub-version-row">
                <span class="hub-version-tag">v{{ v.version }}</span>
                <span class="hub-version-date">{{ formatDate(v.created_at) }}</span>
                <span class="hub-version-size">{{ v.file_count }} files</span>
              </div>
            </div>
          </div>
          <div class="hub-detail-meta">
            <span>Owner: {{ selectedPkg.owner_username }}</span>
            <span>Created: {{ formatDate(selectedPkg.created_at) }}</span>
          </div>
        </div>
      </div>
    </div>
  `,
};
