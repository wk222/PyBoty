import { ref, onMounted, onUnmounted } from 'vue';
import { API } from '/static/api/index.js';
import StatCard from '/static/components/StatCard.js';

export default {
  name: 'DebugPanel',
  components: { StatCard },
  setup() {
    const cost = ref(null);
    const tasks = ref({ tasks: [], summary: {} });
    const mcp = ref({ servers: {}, tools: [], resources: [] });
    const memory = ref({});
    const rag = ref({});
    const providers = ref({});
    const loading = ref(true);
    const autoRefresh = ref(true);
    let timer = null;

    async function fetchWithAuth(url) {
      let apiKey = localStorage.getItem('pybot_api_key') || 'dev-key';
      let res = await fetch(url, { headers: { 'Authorization': `Bearer ${apiKey}` } });
      if (res.status === 401) {
        const newKey = prompt("API Key required. Please enter your PyBot API key:", apiKey);
        if (newKey) {
          localStorage.setItem('pybot_api_key', newKey);
          res = await fetch(url, { headers: { 'Authorization': `Bearer ${newKey}` } });
        }
      }
      return res.json();
    }

    async function loadAll() {
      loading.value = true;
      const results = await Promise.allSettled([
        fetchWithAuth('/api/debug/cost'),
        fetchWithAuth('/api/debug/tasks'),
        fetchWithAuth('/api/debug/mcp'),
        fetchWithAuth('/api/debug/memory'),
        fetchWithAuth('/api/debug/rag'),
        fetchWithAuth('/api/debug/providers'),
      ]);
      if (results[0].status === 'fulfilled') cost.value = results[0].value.cost_summary || {};
      if (results[1].status === 'fulfilled') tasks.value = results[1].value;
      if (results[2].status === 'fulfilled') mcp.value = results[2].value;
      if (results[3].status === 'fulfilled') memory.value = results[3].value.memory || {};
      if (results[4].status === 'fulfilled') rag.value = results[4].value.rag || {};
      if (results[5].status === 'fulfilled') providers.value = results[5].value.providers || {};
      loading.value = false;
    }

    function startAutoRefresh() {
      if (timer) clearInterval(timer);
      timer = setInterval(() => { if (autoRefresh.value) loadAll(); }, 10000);
    }

    onMounted(() => { loadAll(); startAutoRefresh(); });
    onUnmounted(() => { if (timer) clearInterval(timer); });

    return { cost, tasks, mcp, memory, rag, providers, loading, autoRefresh, loadAll };
  },
  template: `
    <div class="mx-content">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">
        <h2 style="margin:0;font-size:1.5rem;font-weight:700;color:#1a1a2e">Debug Panel</h2>
        <div style="display:flex;gap:12px;align-items:center">
          <label style="display:flex;align-items:center;gap:6px;font-size:0.85rem;color:#666;cursor:pointer">
            <input type="checkbox" v-model="autoRefresh" style="cursor:pointer"> Auto-refresh
          </label>
          <button @click="loadAll" :disabled="loading"
            style="padding:6px 16px;background:#3a86ff;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:0.85rem">
            {{ loading ? 'Loading...' : 'Refresh' }}
          </button>
        </div>
      </div>

      <!-- Cost Summary -->
      <div class="debug-section">
        <h3 class="debug-section-title">LLM Cost & Usage</h3>
        <div v-if="cost" class="debug-grid">
          <div class="debug-metric">
            <span class="debug-metric-label">Total Calls</span>
            <span class="debug-metric-value">{{ cost.total_llm_calls || 0 }}</span>
          </div>
          <div class="debug-metric">
            <span class="debug-metric-label">Input Tokens</span>
            <span class="debug-metric-value">{{ (cost.total_input_tokens || 0).toLocaleString() }}</span>
          </div>
          <div class="debug-metric">
            <span class="debug-metric-label">Output Tokens</span>
            <span class="debug-metric-value">{{ (cost.total_output_tokens || 0).toLocaleString() }}</span>
          </div>
          <div class="debug-metric">
            <span class="debug-metric-label">Est. Cost</span>
            <span class="debug-metric-value" style="color:#e63946">\${{ (cost.total_cost_usd || 0).toFixed(4) }}</span>
          </div>
          <div class="debug-metric">
            <span class="debug-metric-label">Tool Calls</span>
            <span class="debug-metric-value">{{ cost.total_tool_calls || 0 }}</span>
          </div>
          <div class="debug-metric">
            <span class="debug-metric-label">LLM Duration</span>
            <span class="debug-metric-value">{{ ((cost.total_llm_duration_ms || 0) / 1000).toFixed(1) }}s</span>
          </div>
        </div>
        <div v-if="cost && cost.model_breakdown && Object.keys(cost.model_breakdown).length" style="margin-top:12px">
          <h4 style="font-size:0.85rem;color:#555;margin-bottom:8px">Per-Model Breakdown</h4>
          <table class="debug-table">
            <thead><tr><th>Model</th><th>Calls</th><th>In Tokens</th><th>Out Tokens</th><th>Cost</th></tr></thead>
            <tbody>
              <tr v-for="(info, model) in cost.model_breakdown" :key="model">
                <td style="font-family:monospace;font-size:0.8rem">{{ model }}</td>
                <td>{{ info.calls }}</td>
                <td>{{ (info.input_tokens || 0).toLocaleString() }}</td>
                <td>{{ (info.output_tokens || 0).toLocaleString() }}</td>
                <td style="color:#e63946">\${{ (info.cost_usd || 0).toFixed(4) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- LLM Providers -->
      <div class="debug-section">
        <h3 class="debug-section-title">LLM Providers</h3>
        <div class="debug-providers">
          <span v-for="(installed, name) in providers" :key="name"
            :class="['debug-provider-badge', installed ? 'installed' : 'missing']">
            {{ name }} {{ installed ? '✓' : '✗' }}
          </span>
        </div>
      </div>

      <!-- Task Queue -->
      <div class="debug-section">
        <h3 class="debug-section-title">Task Queue</h3>
        <div class="debug-grid">
          <div class="debug-metric" v-for="(count, status) in tasks.summary" :key="status">
            <span class="debug-metric-label">{{ status }}</span>
            <span class="debug-metric-value">{{ count }}</span>
          </div>
        </div>
        <div v-if="tasks.tasks && tasks.tasks.length" style="margin-top:12px">
          <table class="debug-table">
            <thead><tr><th>ID</th><th>Name</th><th>Status</th><th>Error</th></tr></thead>
            <tbody>
              <tr v-for="t in tasks.tasks.slice(-10)" :key="t.task_id">
                <td style="font-family:monospace;font-size:0.75rem">{{ t.task_id }}</td>
                <td>{{ t.name }}</td>
                <td><span :class="'task-status-' + t.status">{{ t.status }}</span></td>
                <td style="color:#e63946;font-size:0.75rem">{{ t.error || '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else style="color:#999;font-size:0.85rem;padding:8px 0">No tasks in queue</div>
      </div>

      <!-- MCP Servers -->
      <div class="debug-section">
        <h3 class="debug-section-title">MCP Servers</h3>
        <div v-if="Object.keys(mcp.servers || {}).length">
          <table class="debug-table">
            <thead><tr><th>Server</th><th>Enabled</th><th>Running</th><th>Tools</th><th>Resources</th></tr></thead>
            <tbody>
              <tr v-for="(info, name) in mcp.servers" :key="name">
                <td style="font-family:monospace">{{ name }}</td>
                <td>{{ info.enabled ? '✓' : '✗' }}</td>
                <td :style="{color: info.running ? '#2d6a4f' : '#999'}">{{ info.running ? 'Running' : 'Stopped' }}</td>
                <td>{{ info.tools_count }}</td>
                <td>{{ info.resources_count }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else style="color:#999;font-size:0.85rem;padding:8px 0">No MCP servers configured</div>
      </div>

      <!-- Memory & RAG -->
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="debug-section">
          <h3 class="debug-section-title">Memory</h3>
          <div class="debug-grid" style="grid-template-columns:1fr 1fr">
            <div class="debug-metric">
              <span class="debug-metric-label">File Lines</span>
              <span class="debug-metric-value">{{ memory.file_lines || 0 }}</span>
            </div>
            <div class="debug-metric">
              <span class="debug-metric-label">Vector Backed</span>
              <span class="debug-metric-value" :style="{color: memory.vector_backed ? '#2d6a4f' : '#999'}">
                {{ memory.vector_backed ? 'Yes' : 'No' }}
              </span>
            </div>
            <div class="debug-metric" v-if="memory.vector_count !== undefined">
              <span class="debug-metric-label">Vector Count</span>
              <span class="debug-metric-value">{{ memory.vector_count }}</span>
            </div>
          </div>
        </div>
        <div class="debug-section">
          <h3 class="debug-section-title">RAG Knowledge Base</h3>
          <div class="debug-grid" style="grid-template-columns:1fr 1fr">
            <div class="debug-metric">
              <span class="debug-metric-label">Enabled</span>
              <span class="debug-metric-value" :style="{color: rag.enabled ? '#2d6a4f' : '#999'}">
                {{ rag.enabled ? 'Yes' : 'No' }}
              </span>
            </div>
            <div class="debug-metric">
              <span class="debug-metric-label">Tools</span>
              <span class="debug-metric-value">{{ rag.tools_count || 0 }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
};
