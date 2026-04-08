import { ref } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
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

    const showPublish = ref(false);
    const publishTarget = ref('');
    const publishVersion = ref('0.1.0');
    const publishChangelog = ref('');
    const publishHubUrl = ref(localStorage.getItem('pyhub_url') || '');
    const publishHubToken = ref(localStorage.getItem('pyhub_token') || '');
    const publishing = ref(false);

    const showModeSwitch = ref(false);
    const modeSwitchTarget = ref('');
    const newMode = ref('static');
    const rebuildTemplate = ref(false);
    const switchingMode = ref(false);

    function openPublish(name) {
      publishTarget.value = name;
      publishVersion.value = '0.1.0';
      publishChangelog.value = '';
      showPublish.value = true;
    }

    function openModeSwitch(app) {
      modeSwitchTarget.value = app.name;
      newMode.value = app.mode || 'static';
      rebuildTemplate.value = false;
      showModeSwitch.value = true;
    }

    async function doSwitchMode() {
      switchingMode.value = true;
      try {
        await API.switchAppMode(modeSwitchTarget.value, newMode.value, rebuildTemplate.value);
        toast('Mode switched successfully', 'success');
        showModeSwitch.value = false;
        load();
      } catch (e) {
        toast('Switch failed: ' + e.message, 'error');
      }
      switchingMode.value = false;
    }

    async function doPublish() {
      publishing.value = true;
      try {
        await API.publishApp(publishTarget.value, {
          version: publishVersion.value,
          changelog: publishChangelog.value,
          hub_url: publishHubUrl.value,
          hub_token: publishHubToken.value,
        });
        if (publishHubUrl.value) localStorage.setItem('pyhub_url', publishHubUrl.value);
        if (publishHubToken.value) localStorage.setItem('pyhub_token', publishHubToken.value);
        toast('Published to Hub', 'success');
        showPublish.value = false;
      } catch (e) {
        toast('Publish failed: ' + e.message, 'error');
      }
      publishing.value = false;
    }

    function downloadZip(name) {
      window.open(API.downloadAppZip(name), '_blank');
    }

    return {
      apps, loading, load, toggle, remove, APP_ICON,
      showPublish, publishTarget, publishVersion, publishChangelog,
      publishHubUrl, publishHubToken, publishing,
      openPublish, doPublish, downloadZip,
      showModeSwitch, modeSwitchTarget, newMode, rebuildTemplate, switchingMode, openModeSwitch, doSwitchMode,
    };
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
            <button class="mx-btn-icon" title="Switch Mode" @click.stop="openModeSwitch(a)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
            </button>
            <a v-if="a.url" :href="a.url" target="_blank" class="mx-btn-icon" title="Open">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
            </a>
            <button class="mx-btn-icon" title="Download ZIP" @click.stop="downloadZip(a.name)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            </button>
            <button class="mx-btn-icon" title="Publish to Hub" @click.stop="openPublish(a.name)">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8"/><polyline points="16 6 12 2 8 6"/><line x1="12" y1="2" x2="12" y2="15"/></svg>
            </button>
          </template>
          <div class="mx-entity-card-meta">
            <span class="mx-tag">v{{ a.version || '1.0' }}</span>
            <span class="mx-tag" :style="{ color: a.enabled !== false ? 'var(--success)' : 'var(--error)' }">
              {{ a.enabled !== false ? 'Enabled' : 'Disabled' }}
            </span>
            <span class="mx-tag" style="color:var(--accent); cursor:pointer;" @click="openModeSwitch(a)">{{ a.mode || 'static' }}</span>
            <span v-for="t in (a.tags || [])" :key="t" class="mx-cap-tag">{{ t }}</span>
          </div>
        </EntityCard>
      </div>

      <!-- Publish Modal -->
      <teleport to="body">
        <div v-if="showPublish" class="mx-modal-overlay" @click.self="showPublish = false">
          <div class="mx-modal" style="max-width:480px;">
            <div class="mx-modal-header">
              <span>Publish "{{ publishTarget }}" to Hub</span>
              <button class="mx-modal-close" @click="showPublish = false">&times;</button>
            </div>
            <div class="mx-modal-body">
              <div style="display:grid;gap:10px;">
                <div>
                  <label class="mx-label">Version</label>
                  <input v-model="publishVersion" class="mx-input" placeholder="0.1.0" />
                </div>
                <div>
                  <label class="mx-label">Changelog</label>
                  <textarea v-model="publishChangelog" class="mx-textarea" rows="2" placeholder="What's new in this version..."></textarea>
                </div>
                <div>
                  <label class="mx-label">Hub URL</label>
                  <input v-model="publishHubUrl" class="mx-input" placeholder="http://localhost:8000" />
                </div>
                <div>
                  <label class="mx-label">Hub Token</label>
                  <input v-model="publishHubToken" class="mx-input" type="password" placeholder="Bearer token" />
                </div>
              </div>
            </div>
            <div class="mx-modal-footer">
              <button class="mx-btn mx-btn--ghost" @click="showPublish = false">Cancel</button>
              <button class="mx-btn mx-btn--primary" @click="doPublish" :disabled="publishing">
                {{ publishing ? 'Publishing...' : 'Publish' }}
              </button>
            </div>
          </div>
        </div>

        <div v-if="showModeSwitch" class="mx-modal-overlay" @click.self="showModeSwitch = false">
          <div class="mx-modal" style="max-width:400px;">
            <div class="mx-modal-header">
              <span>Switch Mode for "{{ modeSwitchTarget }}"</span>
              <button class="mx-modal-close" @click="showModeSwitch = false">&times;</button>
            </div>
            <div class="mx-modal-body">
              <div style="display:grid;gap:16px;">
                <div>
                  <label class="mx-label">New Mode</label>
                  <select v-model="newMode" class="mx-input">
                    <option value="static">Static</option>
                    <option value="chat">Chat</option>
                    <option value="workflow">Workflow</option>
                    <option value="assistant">Assistant (AaaS)</option>
                    <option value="rag">RAG</option>
                  </select>
                </div>
                <div>
                  <label class="mx-label" style="display:flex;align-items:center;gap:8px;">
                    <input type="checkbox" v-model="rebuildTemplate" />
                    <span>Rebuild Template files (WARNING: Overwrites index.html, app.js, style.css)</span>
                  </label>
                </div>
              </div>
            </div>
            <div class="mx-modal-footer">
              <button class="mx-btn mx-btn--ghost" @click="showModeSwitch = false">Cancel</button>
              <button class="mx-btn mx-btn--primary" @click="doSwitchMode" :disabled="switchingMode">
                {{ switchingMode ? 'Switching...' : 'Confirm' }}
              </button>
            </div>
          </div>
        </div>
      </teleport>
    </div>
  `
};
