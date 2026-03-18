import { ref, reactive, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t, locale } from '/static/i18n.js';

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

    const llmExpanded = ref(false);
    const llmConfig = reactive({
      provider: '', api_key: '', api_base: '', model: '', temperature: 0.7,
    });
    const llmFallback = ref([]);
    const llmSaving = ref(false);
    const llmTesting = ref(false);
    const llmTestResult = ref(null);
    const llmLoaded = ref(false);
    const providers = ref({});
    const showApiKey = ref(false);

    async function loadLlmConfig() {
      if (llmLoaded.value) return;
      try {
        const data = await API.getLlmConfig();
        if (data.llm_config) Object.assign(llmConfig, data.llm_config);
        if (data.llm_fallback) llmFallback.value = data.llm_fallback;
        llmLoaded.value = true;
      } catch (_) {}
      try {
        const p = await API.getProviders();
        providers.value = p.providers || {};
      } catch (_) {}
    }

    async function saveLlmConfig() {
      llmSaving.value = true;
      try {
        await API.updateLlmConfig({ llm_config: { ...llmConfig } });
        toast('LLM configuration saved', 'success');
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
      llmSaving.value = false;
    }

    async function testConnection() {
      llmTesting.value = true;
      llmTestResult.value = null;
      try {
        llmTestResult.value = await API.testLlmConnection({
          provider: llmConfig.provider,
          api_key: llmConfig.api_key,
          api_base: llmConfig.api_base,
          model: llmConfig.model,
        });
      } catch (e) {
        llmTestResult.value = { success: false, error: e.message };
      }
      llmTesting.value = false;
    }

    function toggleLlm() {
      llmExpanded.value = !llmExpanded.value;
      if (llmExpanded.value) loadLlmConfig();
    }

    function addFallback() {
      llmFallback.value.push({ provider: '', model: '', api_key: '', api_base: '' });
    }
    function removeFallback(idx) {
      llmFallback.value.splice(idx, 1);
    }

    async function saveFull() {
      llmSaving.value = true;
      try {
        const payload = { llm_config: { ...llmConfig } };
        if (llmFallback.value.length > 0) payload.llm_fallback = llmFallback.value;
        await API.updateLlmConfig(payload);
        toast('Configuration saved', 'success');
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
      llmSaving.value = false;
    }

    function maskKey(key) {
      if (!key) return '';
      if (key.length <= 8) return '••••••••';
      return key.slice(0, 4) + '••••' + key.slice(-4);
    }

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

    function d(key) { return t('dashboard.' + key); }

    return {
      stats, events, loading, loadAll,
      llmExpanded, llmConfig, llmFallback, llmSaving, llmTesting, llmTestResult,
      llmLoaded, providers, showApiKey,
      toggleLlm, saveLlmConfig, saveFull, testConnection,
      addFallback, removeFallback, maskKey, d, locale,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">{{ d('title') }}</h1>
        <button class="mx-btn mx-btn--ghost" @click="loadAll">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          {{ d('refresh') }}
        </button>
      </div>

      <div class="mx-stats-grid" v-if="!loading">
        <router-link to="/agents" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:var(--info);background:rgba(96,165,250,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="11" width="18" height="11" rx="2"/><circle cx="12" cy="5" r="4"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.agents.active }}<small>/{{ stats.agents.total }}</small></div>
            <div class="mx-stat-title">{{ d('agents') }}</div>
            <div class="mx-stat-sub">{{ d('activeTotal') }}</div>
          </div>
        </router-link>

        <router-link to="/tools" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:var(--warning);background:rgba(251,191,36,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.tools.total }}</div>
            <div class="mx-stat-title">{{ d('tools') }}</div>
            <div class="mx-stat-sub">{{ stats.tools.top.length ? stats.tools.top.join(', ') : d('noTools') }}</div>
          </div>
        </router-link>

        <router-link to="/skills" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:var(--success);background:rgba(52,211,153,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.skills.enabled }}<small>/{{ stats.skills.total }}</small></div>
            <div class="mx-stat-title">{{ d('skills') }}</div>
            <div class="mx-stat-sub">{{ d('enabledTotal') }}</div>
          </div>
        </router-link>

        <router-link to="/workflows" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:var(--accent);background:rgba(129,140,248,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="6" cy="19" r="3"/><circle cx="18" cy="19" r="3"/><line x1="12" y1="12" x2="6" y2="16"/><line x1="12" y1="12" x2="18" y2="16"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.workflows.total }}</div>
            <div class="mx-stat-title">{{ d('workflows') }}</div>
            <div class="mx-stat-sub">{{ d('savedWorkflows') }}</div>
          </div>
        </router-link>

        <router-link to="/apps" class="mx-stat-card">
          <div class="mx-stat-icon" style="color:#f472b6;background:rgba(244,114,182,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/><rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.apps.enabled }}<small>/{{ stats.apps.total }}</small></div>
            <div class="mx-stat-title">{{ d('apps') }}</div>
            <div class="mx-stat-sub">{{ d('enabledTotal') }}</div>
          </div>
        </router-link>

        <div class="mx-stat-card mx-stat-card--static">
          <div class="mx-stat-icon" style="color:#fb923c;background:rgba(251,146,60,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.capabilities.events }}</div>
            <div class="mx-stat-title">{{ d('capabilityBus') }}</div>
            <div class="mx-stat-sub">{{ stats.capabilities.providers }} {{ d('providers') }}</div>
          </div>
        </div>

        <div class="mx-stat-card mx-stat-card--static">
          <div class="mx-stat-icon" style="color:#a78bfa;background:rgba(167,139,250,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2a7 7 0 0 0-7 7c0 5.25 7 13 7 13s7-7.75 7-13a7 7 0 0 0-7-7z"/><circle cx="12" cy="9" r="2.5"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.memory.lines }}</div>
            <div class="mx-stat-title">{{ d('memory') }}</div>
            <div class="mx-stat-sub">{{ d('entriesMemory') }}</div>
          </div>
        </div>

        <div class="mx-stat-card mx-stat-card--static">
          <div class="mx-stat-icon" style="color:var(--success);background:rgba(52,211,153,0.1)">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
          </div>
          <div class="mx-stat-body">
            <div class="mx-stat-value">{{ stats.system.version }}</div>
            <div class="mx-stat-title">{{ d('system') }}</div>
            <div class="mx-stat-sub">LangChain + PyFlow</div>
          </div>
        </div>
      </div>

      <div v-if="loading" class="mx-loading">
        <div class="mx-spinner"></div>
        <span>{{ d('loading') }}</span>
      </div>

      <!-- LLM Quick Config -->
      <div class="mx-section" v-if="!loading" style="margin-top:20px;">
        <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer;" @click="toggleLlm">
          <div style="display:flex;align-items:center;gap:10px;">
            <div style="width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:rgba(129,140,248,0.1);color:var(--accent);">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
            </div>
            <div>
              <h2 class="mx-section-title" style="margin:0;font-size:15px;">{{ d('llmConfig') }}</h2>
              <div v-if="llmLoaded && !llmExpanded" style="font-size:11px;color:var(--text-muted);margin-top:2px;">
                {{ llmConfig.provider || 'auto' }} / {{ llmConfig.model || 'default' }} / temp={{ llmConfig.temperature }}
                <span v-if="llmConfig.api_key"> / key={{ maskKey(llmConfig.api_key) }}</span>
                <span v-if="llmFallback.length > 0"> / {{ llmFallback.length }} fallback(s)</span>
              </div>
            </div>
          </div>
          <svg :style="{ transform: llmExpanded ? 'rotate(180deg)' : 'rotate(0)', transition: 'transform 0.2s' }" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 12 15 18 9"/></svg>
        </div>

        <div v-if="llmExpanded" style="margin-top:16px;">
          <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
            <div>
              <label class="mx-label">{{ d('provider') }}</label>
              <select v-model="llmConfig.provider" class="mx-input" style="font-size:12px;">
                <option value="">{{ d('autoDetect') }}</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="google_genai">Google GenAI</option>
                <option value="deepseek">DeepSeek</option>
                <option value="openrouter">OpenRouter</option>
                <option value="ollama">Ollama (Local)</option>
                <option value="azure">Azure OpenAI</option>
                <option value="groq">Groq</option>
                <option value="mistral">Mistral</option>
              </select>
            </div>
            <div>
              <label class="mx-label">{{ d('model') }}</label>
              <input v-model="llmConfig.model" class="mx-input" style="font-size:12px;" placeholder="gpt-4o, claude-sonnet..." />
            </div>
            <div>
              <label class="mx-label">{{ d('temperature') }}</label>
              <div style="display:flex;align-items:center;gap:8px;">
                <input v-model.number="llmConfig.temperature" type="range" min="0" max="2" step="0.1"
                  style="flex:1;accent-color:var(--accent);height:4px;" />
                <span style="font-size:12px;color:var(--text-secondary);min-width:28px;text-align:right;">{{ llmConfig.temperature }}</span>
              </div>
            </div>
            <div>
              <label class="mx-label">{{ d('apiKey') }}</label>
              <div style="display:flex;gap:4px;">
                <input v-model="llmConfig.api_key" class="mx-input" style="flex:1;font-size:12px;"
                  :type="showApiKey ? 'text' : 'password'" placeholder="sk-..." autocomplete="off" />
                <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="showApiKey = !showApiKey"
                  style="min-width:32px;font-size:10px;padding:0 6px;">
                  {{ showApiKey ? d('hide') : d('show') }}
                </button>
              </div>
            </div>
            <div style="grid-column: span 2;">
              <label class="mx-label">{{ d('apiBase') }}</label>
              <input v-model="llmConfig.api_base" class="mx-input" style="font-size:12px;" placeholder="https://api.openai.com/v1 (leave empty for default)" />
            </div>
          </div>

          <!-- Fallback Models -->
          <div style="margin-top:14px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
              <label class="mx-label" style="margin:0;">{{ d('fallbackModels') }}</label>
              <button class="mx-btn mx-btn--ghost mx-btn--sm" style="font-size:11px;" @click="addFallback">+ Add</button>
            </div>
            <div v-for="(fb, idx) in llmFallback" :key="idx" style="display:flex;gap:6px;align-items:center;margin-top:6px;">
              <input v-model="fb.provider" class="mx-input" style="width:100px;font-size:11px;" placeholder="provider" />
              <input v-model="fb.model" class="mx-input" style="flex:1;font-size:11px;" placeholder="model" />
              <input v-model="fb.api_key" class="mx-input" style="width:120px;font-size:11px;" type="password" placeholder="api_key" />
              <input v-model="fb.api_base" class="mx-input" style="width:160px;font-size:11px;" placeholder="api_base (optional)" />
              <button class="mx-btn-icon mx-btn-icon--danger" style="font-size:14px;" @click="removeFallback(idx)">&times;</button>
            </div>
            <div v-if="llmFallback.length === 0" style="font-size:11px;color:var(--text-muted);padding:4px 0;">
              {{ d('noFallbacks') }}
            </div>
          </div>

          <!-- Provider Status -->
          <div v-if="Object.keys(providers).length" style="margin-top:12px;">
            <label class="mx-label" style="margin-bottom:6px;">{{ d('installedProviders') }}</label>
            <div style="display:flex;flex-wrap:wrap;gap:4px;">
              <span v-for="(ok, name) in providers" :key="name"
                style="font-size:10px;padding:2px 8px;border-radius:10px;"
                :style="{ background: ok ? 'rgba(16,185,129,0.1)' : 'rgba(107,114,128,0.08)', color: ok ? 'var(--success)' : 'var(--text-muted)', border: '1px solid ' + (ok ? 'rgba(16,185,129,0.3)' : 'var(--border)') }">
                {{ ok ? '\u2713' : '\u2717' }} {{ name }}
              </span>
            </div>
          </div>

          <!-- Actions -->
          <div style="display:flex;gap:8px;margin-top:14px;align-items:center;">
            <button class="mx-btn mx-btn--primary mx-btn--sm" @click="saveFull" :disabled="llmSaving">
              {{ llmSaving ? d('saving') : d('save') }}
            </button>
            <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="testConnection" :disabled="llmTesting">
              {{ llmTesting ? d('testing') : d('testConnection') }}
            </button>
            <router-link to="/settings" class="mx-btn mx-btn--ghost mx-btn--sm" style="font-size:11px;">
              {{ d('fullSettings') }} &rarr;
            </router-link>
            <div v-if="llmTestResult" style="flex:1;">
              <span v-if="llmTestResult.success" style="font-size:11px;color:var(--success);font-weight:600;">
                \u2713 {{ d('connectionOk') }}
              </span>
              <span v-else style="font-size:11px;color:var(--error);">
                \u2717 {{ llmTestResult.error }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="mx-section" v-if="events.length > 0">
        <h2 class="mx-section-title">{{ d('recentActivity') }}</h2>
        <div class="mx-activity-list">
          <div v-for="(evt, i) in events" :key="i" class="mx-activity-item">
            <span class="mx-activity-dot"></span>
            <span class="mx-activity-text">{{ typeof evt === 'string' ? evt : (evt.description || evt.type || JSON.stringify(evt)) }}</span>
          </div>
        </div>
      </div>

      <div class="mx-section" v-if="!loading">
        <h2 class="mx-section-title">{{ d('quickActions') }}</h2>
        <div class="mx-quick-actions">
          <router-link to="/chat" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
            {{ d('newChat') }}
          </router-link>
          <router-link to="/workflows" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            {{ d('runWorkflow') }}
          </router-link>
          <router-link to="/hub" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/></svg>
            {{ d('browseHub') }}
          </router-link>
          <router-link to="/governance" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            {{ d('governance') }}
          </router-link>
          <router-link to="/settings" class="mx-action-btn">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            {{ d('workspace') }}
          </router-link>
        </div>
      </div>
    </div>
  `
};
