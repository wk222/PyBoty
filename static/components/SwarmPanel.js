import { ref, onMounted, onUnmounted, watch } from 'vue';
import { API } from '/static/api/index.js';

export default {
  name: 'SwarmPanel',
  props: {
    sessionKey: { type: String, required: true },
    visible: { type: Boolean, default: false }
  },
  setup(props) {
    const status = ref({ team_memory: {}, runs: [] });
    const loading = ref(false);
    let timer = null;

    async function refresh() {
      if (!props.sessionKey || !props.visible) return;
      loading.value = true;
      try {
        const data = await API.getSwarmStatus(props.sessionKey);
        status.value = data;
      } catch (e) {
        console.error('Swarm status fetch failed', e);
      } finally {
        loading.value = false;
      }
    }

    onMounted(() => {
      refresh();
      timer = setInterval(refresh, 5000);
    });

    onUnmounted(() => {
      if (timer) clearInterval(timer);
    });

    watch(() => props.visible, (newVal) => {
      if (newVal) refresh();
    });

    watch(() => props.sessionKey, refresh);

    function formatTime(ts) {
      if (!ts) return '';
      return new Date(ts * 1000).toLocaleTimeString();
    }

    return { status, loading, refresh, formatTime };
  },
  template: `
    <div v-if="visible" class="swarm-panel">
      <div class="swarm-panel-header">
        <h3 class="swarm-title">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
          Swarm Observability
        </h3>
        <button class="mx-btn-icon" @click="refresh" :disabled="loading">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{'mx-spin': loading}"><path d="M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
        </button>
      </div>

      <div class="swarm-content">
        <div class="swarm-section">
          <div class="swarm-section-label">Active Agents & Tasks</div>
          <div v-if="status.runs && status.runs.length" class="swarm-runs">
            <div v-for="run in status.runs" :key="run.run_id" class="swarm-run-card" :class="run.status">
              <div class="swarm-run-top">
                <span class="swarm-agent-name">{{ run.agent_name }}</span>
                <span class="swarm-status-badge">{{ run.status }}</span>
              </div>
              <div class="swarm-run-id">ID: {{ run.run_id }}</div>
              <div v-if="run.metadata && run.metadata.task" class="swarm-run-task">{{ run.metadata.task }}</div>
              <div v-if="run.error" class="swarm-run-error">{{ run.error }}</div>
              <div v-if="run.error_context" class="swarm-run-error" style="font-family:var(--font-mono);font-size:10px;overflow-x:auto;">{{ run.error_context }}</div>
            </div>
          </div>
          <div v-else class="swarm-empty">No active subagent runs in this session.</div>
        </div>

        <div class="swarm-section">
          <div class="swarm-section-label">Team Memory</div>
          <div v-if="status.team_memory && status.team_memory.recent_notes && status.team_memory.recent_notes.length" class="swarm-memory">
            <div v-for="note in status.team_memory.recent_notes" :key="note.note_id" class="swarm-note">
              <div class="swarm-note-header">
                <span class="swarm-note-author">{{ note.agent_name }}</span>
                <span class="swarm-note-time">{{ formatTime(note.timestamp) }}</span>
              </div>
              <div class="swarm-note-text">{{ note.note }}</div>
            </div>
          </div>
          <div v-else class="swarm-empty">Shared memory is empty.</div>
        </div>
      </div>
    </div>
  `
};
