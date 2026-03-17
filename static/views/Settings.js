import { ref, reactive, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

function formatBytes(b) {
  if (!b) return '0 B';
  const k = 1024, s = ['B', 'KB', 'MB'];
  const i = Math.floor(Math.log(b) / Math.log(k));
  return parseFloat((b / Math.pow(k, i)).toFixed(1)) + ' ' + s[i];
}

export default {
  name: 'SettingsView',
  setup() {
    const tab = ref('llm');
    const workspaceFiles = ref([]);
    const schedTasks = ref([]);
    const memory = ref('');
    const uvEnvs = ref([]);
    const uploads = ref([]);
    const editFile = ref(null);
    const editContent = ref('');
    const showEditor = ref(false);

    const envDetail = ref(null);
    const envPkgInput = ref('');
    const envCode = ref('');
    const envOutput = ref('');

    const llmConfig = reactive({
      provider: '', api_key: '', api_base: '', model: '', temperature: 0.7,
    });
    const llmFallback = ref([]);
    const obsConfig = reactive({ backend: 'none', langfuse_public_key: '', langfuse_secret_key: '', langfuse_host: '', log_level: 'INFO' });
    const ragConfig = reactive({ enabled: false, backend: 'chroma', embedding_model: '', chunk_size: 1000, chunk_overlap: 200 });
    const providers = ref({});
    const llmTesting = ref(false);
    const llmTestResult = ref(null);
    const llmSaving = ref(false);

    async function loadLlmConfig() {
      try {
        const data = await API.getLlmConfig();
        if (data.llm_config) {
          Object.assign(llmConfig, data.llm_config);
        }
        if (data.llm_fallback) llmFallback.value = data.llm_fallback;
        if (data.observability) Object.assign(obsConfig, data.observability);
        if (data.rag_config) Object.assign(ragConfig, data.rag_config);
      } catch (e) { toast('Failed to load LLM config', 'error'); }
      try {
        const p = await API.getProviders();
        providers.value = p.providers || {};
      } catch (_) {}
    }

    async function saveLlmConfig() {
      llmSaving.value = true;
      try {
        const payload = {
          llm_config: { ...llmConfig },
          observability: { ...obsConfig },
          rag_config: { ...ragConfig },
        };
        if (llmFallback.value.length > 0) payload.llm_fallback = llmFallback.value;
        await API.updateLlmConfig(payload);
        toast('Configuration saved', 'success');
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
      llmSaving.value = false;
    }

    async function testConnection() {
      llmTesting.value = true;
      llmTestResult.value = null;
      try {
        const result = await API.testLlmConnection({
          provider: llmConfig.provider,
          api_key: llmConfig.api_key,
          api_base: llmConfig.api_base,
          model: llmConfig.model,
        });
        llmTestResult.value = result;
      } catch (e) {
        llmTestResult.value = { success: false, error: e.message };
      }
      llmTesting.value = false;
    }

    function addFallback() {
      llmFallback.value.push({ provider: '', model: '', api_key: '', api_base: '' });
    }
    function removeFallback(idx) {
      llmFallback.value.splice(idx, 1);
    }

    async function loadWorkspace() {
      try {
        const data = await API.listWorkspaceFiles();
        workspaceFiles.value = Object.entries(data.files || {}).map(([name, info]) => ({ name, ...info }));
      } catch (_) {}
    }

    async function loadSchedule() {
      try {
        const data = await API.listScheduleTasks();
        schedTasks.value = data.tasks || [];
      } catch (_) {}
    }

    async function loadMemory() {
      try {
        const data = await API.getMemory();
        memory.value = data.content || '';
      } catch (_) {}
    }

    async function loadUvEnvs() {
      try {
        const data = await API.listUvEnvs();
        uvEnvs.value = data.envs || [];
      } catch (_) {}
    }

    async function loadUploads() {
      try {
        const data = await API.listUploads();
        uploads.value = data.files || [];
      } catch (_) {}
    }

    async function openFile(name) {
      try {
        const data = await API.getWorkspaceFile(name);
        editFile.value = name;
        editContent.value = data.content || '';
        showEditor.value = true;
      } catch (e) { toast('Failed to load file', 'error'); }
    }

    async function saveFile() {
      if (!editFile.value) return;
      try {
        await API.updateWorkspaceFile(editFile.value, editContent.value);
        showEditor.value = false;
        toast('File saved', 'success');
        loadWorkspace();
      } catch (e) { toast('Save failed', 'error'); }
    }

    async function toggleSched(name, enabled) {
      try { await API.toggleScheduleTask(name, enabled); loadSchedule(); }
      catch (e) { toast('Toggle failed', 'error'); }
    }

    async function deleteSched(name) {
      if (!confirm(`Delete task "${name}"?`)) return;
      try { await API.deleteScheduleTask(name); loadSchedule(); toast('Deleted', 'success'); }
      catch (e) { toast('Delete failed', 'error'); }
    }

    async function showEnv(name) {
      try {
        envDetail.value = await API.getUvEnv(name);
        envDetail.value._name = name;
        envPkgInput.value = '';
        envCode.value = '';
        envOutput.value = '';
      } catch (e) { toast('Load failed', 'error'); }
    }

    async function createEnv() {
      const name = prompt('Environment name:');
      if (!name) return;
      const desc = prompt('Description:', '') || '';
      try {
        await API.createUvEnv({ name, description: desc });
        loadUvEnvs();
        toast('Environment created', 'success');
      } catch (e) { toast('Create failed: ' + e.message, 'error'); }
    }

    async function deleteEnv(name) {
      if (!confirm(`Delete env "${name}"?`)) return;
      try {
        await API.deleteUvEnv(name);
        envDetail.value = null;
        loadUvEnvs();
        toast('Deleted', 'success');
      } catch (e) { toast('Delete failed', 'error'); }
    }

    async function installPkg() {
      if (!envDetail.value || !envPkgInput.value.trim()) return;
      try {
        await API.installPkg(envDetail.value._name, envPkgInput.value.split(/[\s,]+/).filter(Boolean));
        envPkgInput.value = '';
        showEnv(envDetail.value._name);
      } catch (e) { toast('Install failed', 'error'); }
    }

    async function uninstallPkg(pkg) {
      if (!envDetail.value) return;
      try {
        await API.uninstallPkg(envDetail.value._name, [pkg]);
        showEnv(envDetail.value._name);
      } catch (e) { toast('Uninstall failed', 'error'); }
    }

    async function runCode() {
      if (!envDetail.value || !envCode.value.trim()) return;
      envOutput.value = 'Running...';
      try {
        const data = await API.runInEnv(envDetail.value._name, envCode.value);
        let out = '';
        if (data.stdout) out += data.stdout;
        if (data.stderr) out += (out ? '\n' : '') + '[stderr] ' + data.stderr;
        if (data.error) out += (out ? '\n' : '') + '[error] ' + data.error;
        envOutput.value = out || '(no output)';
      } catch (e) { envOutput.value = 'Error: ' + e.message; }
    }

    function switchTab(t) {
      tab.value = t;
      if (t === 'llm') loadLlmConfig();
      if (t === 'workspace') { loadWorkspace(); loadUploads(); }
      if (t === 'schedule') loadSchedule();
      if (t === 'memory') loadMemory();
      if (t === 'uv') loadUvEnvs();
    }

    onMounted(() => { loadLlmConfig(); });

    return {
      tab, workspaceFiles, schedTasks, memory, uvEnvs, uploads,
      editFile, editContent, showEditor, envDetail, envPkgInput, envCode, envOutput,
      llmConfig, llmFallback, obsConfig, ragConfig, providers,
      llmTesting, llmTestResult, llmSaving,
      switchTab, openFile, saveFile, toggleSched, deleteSched,
      showEnv, createEnv, deleteEnv, installPkg, uninstallPkg, runCode,
      saveLlmConfig, testConnection, addFallback, removeFallback, loadLlmConfig,
      formatBytes,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Settings</h1>
      </div>

      <div class="mx-tabs">
        <button class="mx-tab" :class="{ active: tab==='llm' }" @click="switchTab('llm')">LLM Config</button>
        <button class="mx-tab" :class="{ active: tab==='workspace' }" @click="switchTab('workspace')">Workspace</button>
        <button class="mx-tab" :class="{ active: tab==='schedule' }" @click="switchTab('schedule')">Schedule</button>
        <button class="mx-tab" :class="{ active: tab==='memory' }" @click="switchTab('memory')">Memory</button>
        <button class="mx-tab" :class="{ active: tab==='uv' }" @click="switchTab('uv')">UV Envs</button>
      </div>

      <!-- LLM Config Tab -->
      <div v-if="tab==='llm'">
        <div class="mx-section">
          <h2 class="mx-section-title">Primary LLM</h2>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            <div>
              <label class="mx-label">Provider</label>
              <select v-model="llmConfig.provider" class="mx-input">
                <option value="">Auto-detect</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="google_genai">Google GenAI</option>
                <option value="deepseek">DeepSeek</option>
                <option value="openrouter">OpenRouter</option>
                <option value="ollama">Ollama (Local)</option>
                <option value="azure">Azure OpenAI</option>
              </select>
            </div>
            <div>
              <label class="mx-label">Model</label>
              <input v-model="llmConfig.model" class="mx-input" placeholder="gpt-4, claude-3-opus, gemini-pro..." />
            </div>
            <div>
              <label class="mx-label">API Key</label>
              <input v-model="llmConfig.api_key" class="mx-input" type="password" placeholder="sk-..." autocomplete="off" />
            </div>
            <div>
              <label class="mx-label">API Base URL (optional)</label>
              <input v-model="llmConfig.api_base" class="mx-input" placeholder="https://api.openai.com/v1" />
            </div>
            <div>
              <label class="mx-label">Temperature</label>
              <input v-model.number="llmConfig.temperature" class="mx-input" type="number" min="0" max="2" step="0.1" />
            </div>
          </div>
          <div style="display:flex;gap:8px;margin-top:16px;">
            <button class="mx-btn mx-btn--primary" @click="saveLlmConfig" :disabled="llmSaving">
              {{ llmSaving ? 'Saving...' : 'Save Configuration' }}
            </button>
            <button class="mx-btn mx-btn--ghost" @click="testConnection" :disabled="llmTesting">
              {{ llmTesting ? 'Testing...' : 'Test Connection' }}
            </button>
          </div>
          <div v-if="llmTestResult" style="margin-top:12px;padding:10px;border-radius:var(--radius-sm);"
               :style="{ background: llmTestResult.success ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)', border: '1px solid ' + (llmTestResult.success ? 'var(--success)' : 'var(--error)') }">
            <div v-if="llmTestResult.success" style="color:var(--success);font-weight:600;">Connection OK</div>
            <div v-else style="color:var(--error);font-weight:600;">Connection Failed: {{ llmTestResult.error }}</div>
            <div v-if="llmTestResult.response_preview" style="font-size:11px;color:var(--text-muted);margin-top:4px;">
              {{ llmTestResult.response_preview }}
            </div>
          </div>
        </div>

        <div class="mx-section">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <h2 class="mx-section-title" style="margin:0;">Fallback Models</h2>
            <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="addFallback">+ Add Fallback</button>
          </div>
          <div v-if="llmFallback.length === 0" class="mx-text-muted" style="padding:8px 0;font-size:12px;">
            No fallback models configured. Primary model failure will not auto-recover.
          </div>
          <div v-for="(fb, idx) in llmFallback" :key="idx" style="display:flex;gap:8px;align-items:center;margin-top:8px;">
            <input v-model="fb.provider" class="mx-input" style="width:120px;" placeholder="provider" />
            <input v-model="fb.model" class="mx-input" style="flex:1;" placeholder="model name" />
            <input v-model="fb.api_key" class="mx-input" style="width:140px;" type="password" placeholder="api_key (optional)" />
            <button class="mx-btn-icon mx-btn-icon--danger" @click="removeFallback(idx)">&times;</button>
          </div>
        </div>

        <div class="mx-section">
          <h2 class="mx-section-title">Observability</h2>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            <div>
              <label class="mx-label">Backend</label>
              <select v-model="obsConfig.backend" class="mx-input">
                <option value="none">None</option>
                <option value="langfuse">LangFuse</option>
                <option value="langsmith">LangSmith</option>
              </select>
            </div>
            <div>
              <label class="mx-label">Log Level</label>
              <select v-model="obsConfig.log_level" class="mx-input">
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </div>
            <div v-if="obsConfig.backend === 'langfuse'">
              <label class="mx-label">LangFuse Public Key</label>
              <input v-model="obsConfig.langfuse_public_key" class="mx-input" placeholder="pk-lf-..." />
            </div>
            <div v-if="obsConfig.backend === 'langfuse'">
              <label class="mx-label">LangFuse Secret Key</label>
              <input v-model="obsConfig.langfuse_secret_key" class="mx-input" type="password" placeholder="sk-lf-..." />
            </div>
            <div v-if="obsConfig.backend === 'langfuse'" style="grid-column: 1 / -1;">
              <label class="mx-label">LangFuse Host</label>
              <input v-model="obsConfig.langfuse_host" class="mx-input" placeholder="https://cloud.langfuse.com" />
            </div>
          </div>
        </div>

        <div class="mx-section">
          <h2 class="mx-section-title">RAG / Knowledge Base</h2>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
            <div>
              <label class="mx-label">Enabled</label>
              <label class="toggle-switch">
                <input type="checkbox" v-model="ragConfig.enabled" />
                <span class="toggle-slider"></span>
              </label>
            </div>
            <div>
              <label class="mx-label">Backend</label>
              <select v-model="ragConfig.backend" class="mx-input" :disabled="!ragConfig.enabled">
                <option value="chroma">ChromaDB</option>
                <option value="memory">In-Memory</option>
              </select>
            </div>
            <div>
              <label class="mx-label">Embedding Model</label>
              <input v-model="ragConfig.embedding_model" class="mx-input" placeholder="Auto-detect" :disabled="!ragConfig.enabled" />
            </div>
            <div>
              <label class="mx-label">Chunk Size</label>
              <input v-model.number="ragConfig.chunk_size" class="mx-input" type="number" :disabled="!ragConfig.enabled" />
            </div>
          </div>
        </div>

        <div class="mx-section">
          <h2 class="mx-section-title">Provider Status</h2>
          <div style="display:flex;flex-wrap:wrap;gap:8px;">
            <span v-for="(installed, name) in providers" :key="name"
                  class="mx-tag" :style="{ background: installed ? 'rgba(16,185,129,0.1)' : 'rgba(107,114,128,0.1)', color: installed ? 'var(--success)' : 'var(--text-muted)', border: '1px solid ' + (installed ? 'var(--success)' : 'var(--border)') }">
              {{ installed ? '\u2713' : '\u2717' }} {{ name }}
            </span>
          </div>
        </div>
      </div>

      <!-- Workspace Tab -->
      <div v-if="tab==='workspace'">
        <div class="mx-section">
          <h2 class="mx-section-title">Workspace Files</h2>
          <div v-if="workspaceFiles.length === 0" class="mx-text-muted" style="padding:12px;">No workspace files</div>
          <div v-for="f in workspaceFiles" :key="f.name" class="workspace-file-card" @click="openFile(f.name)">
            <div>
              <div class="file-name">{{ f.name }}</div>
              <div class="file-desc">{{ f.description || '' }}</div>
            </div>
            <div class="file-size">{{ f.exists ? formatBytes(f.size) : '(empty)' }}</div>
          </div>
        </div>
        <div class="mx-section" v-if="uploads.length">
          <h2 class="mx-section-title">Uploaded Files</h2>
          <div v-for="f in uploads" :key="f.name" class="workspace-file-card" style="cursor:default;">
            <div class="file-name">{{ f.name }}</div>
            <div class="file-size">{{ formatBytes(f.size) }}</div>
          </div>
        </div>
      </div>

      <!-- Schedule Tab -->
      <div v-if="tab==='schedule'">
        <div v-if="schedTasks.length === 0" class="mx-empty"><p>No scheduled tasks. Configure via SCHEDULE.md or API.</p></div>
        <div v-for="t in schedTasks" :key="t.name" class="mx-entity-card" style="margin-bottom:8px;">
          <div class="mx-entity-card-header">
            <div class="mx-entity-card-info">
              <div class="mx-entity-card-name">{{ t.name }}</div>
              <div class="mx-entity-card-desc">{{ t.description || '' }}</div>
            </div>
            <div class="mx-entity-card-actions">
              <span class="mx-tag" style="font-family:monospace;color:var(--accent)">{{ t.cron }}</span>
              <label class="toggle-switch">
                <input type="checkbox" :checked="t.enabled" @change="toggleSched(t.name, $event.target.checked)">
                <span class="toggle-slider"></span>
              </label>
              <button class="mx-btn-icon mx-btn-icon--danger" @click="deleteSched(t.name)">&times;</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Memory Tab -->
      <div v-if="tab==='memory'">
        <div v-if="!memory || memory.trim().length < 30" class="mx-empty"><p>No memory entries yet. They accumulate through conversations.</p></div>
        <pre v-else class="memory-preview" style="margin:16px 0;">{{ memory.split('\\n').slice(0,50).join('\\n') }}</pre>
      </div>

      <!-- UV Envs Tab -->
      <div v-if="tab==='uv'">
        <div style="display:flex;justify-content:flex-end;margin-bottom:12px;">
          <button class="mx-btn mx-btn--primary" @click="createEnv">New Environment</button>
        </div>
        <div v-if="uvEnvs.length === 0" class="mx-empty"><p>No UV environments. Click "New Environment" to create one.</p></div>
        <div class="mx-card-grid" v-else>
          <div v-for="e in uvEnvs" :key="e.name" class="mx-entity-card" style="cursor:pointer;" @click="showEnv(e.name)">
            <div class="mx-entity-card-name">{{ e.name }}</div>
            <div class="mx-entity-card-desc">{{ e.description || e.python_version || 'No description' }}</div>
            <div style="margin-top:4px;">
              <span v-for="t in (e.tags||[])" :key="t" class="mx-cap-tag">{{ t }}</span>
            </div>
          </div>
        </div>
        <!-- Env Detail -->
        <div v-if="envDetail" class="mx-section" style="margin-top:16px;border:1px solid var(--border);border-radius:var(--radius-md);padding:16px;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h2 class="mx-section-title" style="margin:0;">{{ envDetail._name }}</h2>
            <div style="display:flex;gap:8px;">
              <button class="mx-btn mx-btn--ghost mx-btn--sm" style="color:var(--error)" @click="deleteEnv(envDetail._name)">Delete</button>
              <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="envDetail = null">Close</button>
            </div>
          </div>
          <div class="mx-text-muted" style="font-size:12px;line-height:1.8;margin-bottom:12px;">
            <div>Python: <span style="color:var(--success)">{{ envDetail.python_version || 'default' }}</span></div>
            <div>Disk: {{ envDetail.disk_size || 'N/A' }}</div>
          </div>
          <div style="margin-bottom:12px;">
            <div class="mx-subsection-title">Packages ({{ (envDetail.packages||[]).length }})</div>
            <div style="display:flex;gap:4px;margin-bottom:6px;">
              <input v-model="envPkgInput" class="mx-input" style="flex:1;" placeholder="package name (e.g. requests)" @keydown.enter="installPkg" />
              <button class="mx-btn mx-btn--primary mx-btn--sm" @click="installPkg">Install</button>
            </div>
            <div v-if="(envDetail.packages||[]).length > 0" style="max-height:200px;overflow-y:auto;">
              <div v-for="p in envDetail.packages" :key="p" style="display:flex;justify-content:space-between;align-items:center;font-size:11px;padding:3px 0;border-bottom:1px solid var(--border);">
                <span style="color:var(--text-secondary)">{{ p }}</span>
                <button class="mx-btn mx-btn--ghost mx-btn--sm" style="color:var(--error);font-size:10px;" @click="uninstallPkg(p.split('==')[0])">Uninstall</button>
              </div>
            </div>
          </div>
          <div>
            <div class="mx-subsection-title">Run Code</div>
            <textarea v-model="envCode" class="mx-textarea" rows="3" placeholder="print('hello')"></textarea>
            <button class="mx-btn mx-btn--primary mx-btn--sm" style="margin-top:4px;" @click="runCode">Run</button>
            <pre v-if="envOutput" style="font-size:10px;color:var(--text-muted);max-height:120px;overflow:auto;white-space:pre-wrap;margin-top:6px;background:var(--bg-primary);padding:8px;border-radius:var(--radius-sm);">{{ envOutput }}</pre>
          </div>
        </div>
      </div>

      <!-- File Editor Modal -->
      <teleport to="body">
        <div v-if="showEditor" class="mx-modal-overlay" @click.self="showEditor = false">
          <div class="mx-modal" style="max-width:700px;height:80vh;">
            <div class="mx-modal-header">
              <span>{{ editFile }}</span>
              <button class="mx-modal-close" @click="showEditor = false">&times;</button>
            </div>
            <div class="mx-modal-body" style="flex:1;display:flex;flex-direction:column;">
              <textarea v-model="editContent" class="modal-editor" style="flex:1;"></textarea>
            </div>
            <div class="mx-modal-footer">
              <button class="mx-btn mx-btn--ghost" @click="showEditor = false">Cancel</button>
              <button class="mx-btn mx-btn--primary" @click="saveFile">Save</button>
            </div>
          </div>
        </div>
      </teleport>
    </div>
  `
};
