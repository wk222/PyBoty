import { onMounted, ref, watch } from 'vue';
import { API } from '/static/api/index.js';
import { locale, toggleLocale, t } from '/static/i18n.js';
import { getSearchResultColor, getSearchResultRoute } from '/static/config/navigation.js';

// ─── Theme ───────────────────────────────────────────────────────────────────
function getStoredTheme() {
  return localStorage.getItem('pybot_theme') || 'dark';
}
function applyTheme(theme) {
  if (theme === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
    // switch hljs stylesheet
    const dark = document.getElementById('hljs-theme-dark');
    const light = document.getElementById('hljs-theme-light');
    if (dark) dark.disabled = true;
    if (light) light.disabled = false;
  } else {
    document.documentElement.removeAttribute('data-theme');
    const dark = document.getElementById('hljs-theme-dark');
    const light = document.getElementById('hljs-theme-light');
    if (dark) dark.disabled = false;
    if (light) light.disabled = true;
  }
  localStorage.setItem('pybot_theme', theme);
}

// TopBar doesn't manage canvas — that's per-conversation in ChatView

export default {
  name: 'TopBar',
  setup() {
    const query = ref('');
    const results = ref([]);
    const showResults = ref(false);
    const currentMode = ref(null);
    const theme = ref(getStoredTheme());
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

    function route(item) { return getSearchResultRoute(item); }
    function color(type) { return getSearchResultColor(type); }
    function selectResult() { showResults.value = false; query.value = ''; }
    function closeResults() { showResults.value = false; }
    function delayClose() { setTimeout(() => closeResults(), 200); }
    function searchPlaceholder() { return t('common.search'); }
    function currentModeLabel() {
      const name = currentMode.value?.name;
      if (!name) return t('topbar.modeLoading');
      return t(`modes.${name}`);
    }
    async function loadCurrentMode() {
      try {
        const payload = await API.getSystemModes();
        currentMode.value = payload.current || null;
      } catch (_) { currentMode.value = null; }
    }

    function toggleTheme() {
      theme.value = theme.value === 'dark' ? 'light' : 'dark';
      applyTheme(theme.value);
    }

    onMounted(() => {
      loadCurrentMode();
      applyTheme(theme.value);
    });

    return {
      query, results, showResults,
      route, color, selectResult, closeResults, delayClose,
      locale, toggleLocale, t,
      searchPlaceholder, currentMode, currentModeLabel,
      theme, toggleTheme,
    };
  },
  template: `
    <header class="mx-topbar">
      <div class="mx-topbar-left">
        <router-link to="/chat" class="mx-topbar-brand">
          <span class="mx-topbar-logo">P</span>
          <span class="mx-topbar-title">PyBot <small>{{ t('topbar.spine') }}</small></span>
        </router-link>
      </div>
      <div class="mx-topbar-center">
        <div class="mx-search-box" @focusout="delayClose">
          <svg class="mx-search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input v-model="query" @focus="showResults = results.length > 0" type="text"
                 :placeholder="searchPlaceholder() + '  (Ctrl+K)'"
                 class="mx-search-input" />
          <div v-if="showResults" class="mx-search-results">
            <router-link v-for="(r, i) in results" :key="i" :to="route(r)"
                         class="mx-search-result-item" @click="selectResult">
              <span class="mx-search-result-type" :style="{ background: color(r.type) + '18', color: color(r.type), borderRadius: '100px', padding: '2px 8px', fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }">{{ r.type }}</span>
              <span style="font-weight:600;color:var(--text-primary);">{{ r.name }}</span>
              <span v-if="r.description" style="color:var(--text-muted);font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;">{{ r.description }}</span>
            </router-link>
          </div>
        </div>
      </div>
      <div class="mx-topbar-right">

        <router-link to="/chat" class="mx-topbar-action" :title="t('topbar.chat')">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </router-link>

        <span class="mx-topbar-version" :title="t('topbar.currentMode')">{{ currentModeLabel() }}</span>

        <!-- Language Toggle -->
        <button @click="toggleLocale" :title="locale === 'en' ? '切换中文' : 'Switch to English'"
          style="font-size:11px;font-weight:700;letter-spacing:0.03em;padding:4px 10px;border-radius:100px;border:1px solid var(--border);background:var(--bg-secondary);cursor:pointer;color:var(--text-primary);transition:all 0.15s;white-space:nowrap;"
          @mouseenter="$event.target.style.borderColor='var(--accent)';$event.target.style.color='var(--accent)'"
          @mouseleave="$event.target.style.borderColor='var(--border)';$event.target.style.color='var(--text-primary)'">
          {{ locale === 'en' ? '中文' : 'EN' }}
        </button>

        <!-- Theme Toggle -->
        <button class="theme-toggle-btn" @click="toggleTheme" :title="theme === 'dark' ? '切换浅色主题' : '切换深色主题'">
          <span v-if="theme === 'dark'">☀️</span>
          <span v-else>🌙</span>
        </button>

        <span class="mx-topbar-version">v5.1</span>
      </div>
    </header>
  `
};
