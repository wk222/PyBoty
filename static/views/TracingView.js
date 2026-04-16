import { ref, computed, onMounted, onUnmounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

const EVENT_ICONS = {
  tool_call: '🔧', tool_result: '📋',
  agent_start: '🤖', agent_end: '✅',
  approval_request: '🔒', approval_resolved: '✓',
  memory_write: '🧠', memory_search: '🔍',
  cost_record: '💰', model_usage: '📊',
  model_failover: '⚡', error: '❌',
  workflow_start: '▶️', workflow_end: '⏹️', workflow_step: '⏩',
  session_start: '🟢', session_end: '🔴',
  canvas_changed: '🎨',
};

const EVENT_COLORS = {
  tool_call: '#f59e0b', tool_result: '#10b981',
  agent_start: '#6366f1', agent_end: '#6366f1',
  error: '#ef4444', model_failover: '#f97316',
  approval_request: '#8b5cf6', cost_record: '#14b8a6',
  memory_write: '#ec4899', workflow_start: '#3b82f6',
};

export default {
  name: 'TracingView',
  setup() {
    const events = ref([]);
    const typeCounts = ref({});
    const total = ref(0);
    const loading = ref(true);
    const filterType = ref('');
    const autoRefresh = ref(false);
    let timer = null;

    async function loadTrace() {
      loading.value = true;
      try {
        const data = await API.getGlobalTrace(300, filterType.value);
        events.value = data.events || [];
        typeCounts.value = data.type_counts || {};
        total.value = data.total || 0;
      } catch (e) {
        toast('Failed to load trace: ' + e.message, 'error');
      }
      loading.value = false;
    }

    function toggleAutoRefresh() {
      autoRefresh.value = !autoRefresh.value;
      if (autoRefresh.value) {
        timer = setInterval(loadTrace, 3000);
      } else if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    function formatTime(ts) {
      if (!ts) return '';
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString() + '.' + String(d.getMilliseconds()).padStart(3, '0');
    }

    function getIcon(type) { return EVENT_ICONS[type] || '📌'; }
    function getColor(type) { return EVENT_COLORS[type] || 'var(--text-muted)'; }

    const sortedTypes = computed(() => {
      return Object.entries(typeCounts.value)
        .sort((a, b) => b[1] - a[1])
        .map(([type, count]) => ({ type, count }));
    });

    function setFilter(type) {
      filterType.value = filterType.value === type ? '' : type;
      loadTrace();
    }

    function payloadPreview(payload) {
      if (!payload) return '';
      const keys = Object.keys(payload);
      if (keys.length === 0) return '';
      const parts = [];
      for (const k of keys.slice(0, 3)) {
        let v = payload[k];
        if (typeof v === 'string' && v.length > 60) v = v.slice(0, 60) + '…';
        if (typeof v === 'object') v = JSON.stringify(v).slice(0, 60) + '…';
        parts.push(k + ': ' + v);
      }
      return parts.join(' | ');
    }

    onMounted(loadTrace);
    onUnmounted(() => { if (timer) clearInterval(timer); });

    return {
      events, typeCounts, total, loading, filterType, autoRefresh,
      sortedTypes, loadTrace, toggleAutoRefresh, formatTime,
      getIcon, getColor, setFilter, payloadPreview,
    };
  },
  template: `
<div class="trace-view">
  <div class="trace-header">
    <h2 class="trace-title">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
      Event Tracing
    </h2>
    <div class="trace-controls">
      <button class="trace-btn" :class="{ active: autoRefresh }" @click="toggleAutoRefresh">
        {{ autoRefresh ? '⏸ Pause' : '▶ Live' }}
      </button>
      <button class="trace-btn" @click="loadTrace" :disabled="loading">↻ Refresh</button>
      <span class="trace-count">{{ total }} events</span>
    </div>
  </div>

  <!-- Type filter chips -->
  <div class="trace-chips">
    <button
      v-for="t in sortedTypes" :key="t.type"
      class="trace-chip"
      :class="{ active: filterType === t.type }"
      :style="{ borderColor: getColor(t.type) }"
      @click="setFilter(t.type)"
    >
      {{ getIcon(t.type) }} {{ t.type }} <span class="trace-chip-count">{{ t.count }}</span>
    </button>
  </div>

  <div v-if="loading && !events.length" class="trace-loading">Loading...</div>

  <!-- Timeline -->
  <div class="trace-timeline">
    <div v-for="e in events" :key="e.id" class="trace-event">
      <div class="trace-event-time">{{ formatTime(e.timestamp) }}</div>
      <div class="trace-event-dot" :style="{ background: getColor(e.type) }"></div>
      <div class="trace-event-body">
        <div class="trace-event-head">
          <span class="trace-event-icon">{{ getIcon(e.type) }}</span>
          <span class="trace-event-type" :style="{ color: getColor(e.type) }">{{ e.type }}</span>
          <span v-if="e.source" class="trace-event-source">{{ e.source }}</span>
          <span v-if="e.session_id" class="trace-event-session">{{ e.session_id.slice(0, 12) }}</span>
        </div>
        <div v-if="payloadPreview(e.payload)" class="trace-event-preview">{{ payloadPreview(e.payload) }}</div>
      </div>
    </div>
    <div v-if="!events.length && !loading" class="trace-empty">No events recorded yet.</div>
  </div>
</div>
  `,
};
