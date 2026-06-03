import { ref, reactive, computed, onMounted, onUnmounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

// Codex-Cloud-style "task panel": left list of every long-running task,
// right detail card with live heartbeat / steps streamed via Server-Sent
// Events.  Shares zero state with the cron-style ScheduleList page.

const KIND_BADGE = {
  persistent: { label: 'Persistent', accent: '#818cf8' },
  monitor: { label: 'Monitor', accent: '#34d399' },
  cron: { label: 'Cron', accent: '#fbbf24' },
};

const STATUS_BADGE = {
  pending:   { label: 'Pending',   cls: 'mx-badge--draft' },
  running:   { label: 'Running',   cls: 'mx-badge--success' },
  paused:    { label: 'Paused',    cls: 'mx-badge--draft' },
  completed: { label: 'Completed', cls: 'mx-badge--success' },
  failed:    { label: 'Failed',    cls: 'mx-badge--error' },
  cancelled: { label: 'Cancelled', cls: 'mx-badge--draft' },
};

function formatTime(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString();
}

function formatRelative(ts) {
  if (!ts) return '—';
  const diff = (Date.now() / 1000) - ts;
  if (diff < 0) return 'in ' + Math.round(-diff) + 's';
  if (diff < 60) return Math.round(diff) + 's ago';
  if (diff < 3600) return Math.round(diff / 60) + 'm ago';
  if (diff < 86400) return Math.round(diff / 3600) + 'h ago';
  return Math.round(diff / 86400) + 'd ago';
}

export default {
  name: 'TaskPanel',
  setup() {
    const tasks = ref([]);
    const loading = ref(true);
    const selected = ref(null);
    const detail = ref(null);
    const recentEvents = ref([]);
    const filterKind = ref('');
    const filterStatus = ref('');

    let listSource = null;
    let detailSource = null;

    const filteredTasks = computed(() => {
      let xs = tasks.value;
      if (filterKind.value) xs = xs.filter(t => t.kind === filterKind.value);
      if (filterStatus.value) xs = xs.filter(t => t.status === filterStatus.value);
      return xs;
    });

    async function refresh() {
      loading.value = true;
      try {
        const data = await API.listLongRunningTasks({
          kind: filterKind.value || undefined,
          status: filterStatus.value || undefined,
        });
        tasks.value = data.tasks || [];
        if (selected.value) {
          const updated = tasks.value.find(t => t.task_id === selected.value);
          if (!updated) {
            selected.value = null;
            detail.value = null;
          }
        }
      } catch (e) {
        toast('Failed to load tasks: ' + e.message, 'error');
      } finally {
        loading.value = false;
      }
    }

    function startListStream() {
      stopListStream();
      try {
        listSource = API.streamLongRunningTaskEvents(null);
        listSource.addEventListener('snapshot', (e) => {
          try {
            const data = JSON.parse(e.data);
            if (Array.isArray(data.tasks)) {
              tasks.value = data.tasks;
              loading.value = false;
            }
          } catch (_) { /* ignore malformed */ }
        });
        const refreshOnAny = () => { refresh(); };
        ['task.spawned', 'task.completed', 'task.failed', 'task.cancelled']
          .forEach(name => listSource.addEventListener(name, refreshOnAny));
        listSource.addEventListener('task.heartbeat', (e) => {
          try {
            const data = JSON.parse(e.data);
            const snap = data.snapshot;
            if (!snap) return;
            const idx = tasks.value.findIndex(t => t.task_id === snap.task_id);
            if (idx >= 0) tasks.value[idx] = snap;
          } catch (_) { /* ignore */ }
        });
        listSource.onerror = () => {
          // Browsers reconnect EventSource automatically; nothing to do here.
        };
      } catch (e) {
        toast('Could not open task event stream: ' + e.message, 'error');
      }
    }

    function stopListStream() {
      if (listSource) { listSource.close(); listSource = null; }
    }

    function selectTask(taskId) {
      if (selected.value === taskId) return;
      selected.value = taskId;
      detail.value = tasks.value.find(t => t.task_id === taskId) || null;
      recentEvents.value = [];
      stopDetailStream();
      try {
        detailSource = API.streamLongRunningTaskEvents(taskId);
        detailSource.addEventListener('snapshot', (e) => {
          try {
            const data = JSON.parse(e.data);
            if (data.snapshot) detail.value = data.snapshot;
          } catch (_) { /* ignore */ }
        });
        ['task.heartbeat', 'task.step', 'task.spawned', 'task.completed', 'task.failed', 'task.cancelled']
          .forEach(name => detailSource.addEventListener(name, (e) => {
            try {
              const data = JSON.parse(e.data);
              if (data.snapshot) detail.value = data.snapshot;
              recentEvents.value.unshift({
                kind: name,
                ts: data.ts || (Date.now() / 1000),
                step: data.step,
                progress: data.progress,
                error: data.error,
                output: data.output,
              });
              if (recentEvents.value.length > 50) recentEvents.value.pop();
            } catch (_) { /* ignore */ }
          }));
        detailSource.addEventListener('error', (e) => {
          try {
            const data = JSON.parse(e.data || '{}');
            if (data.error) toast('Task stream error: ' + data.error, 'error');
          } catch (_) { /* ignore */ }
        });
      } catch (e) {
        toast('Could not open task detail stream: ' + e.message, 'error');
      }
    }

    function stopDetailStream() {
      if (detailSource) { detailSource.close(); detailSource = null; }
    }

    async function cancelTask(taskId) {
      if (!confirm('Cancel task ' + taskId + '?')) return;
      try {
        await API.cancelLongRunningTask(taskId);
        toast('Cancelled', 'success');
      } catch (e) {
        toast('Cancel failed: ' + e.message, 'error');
      }
    }

    async function pauseTask(taskId) {
      try { await API.pauseLongRunningTask(taskId); toast('Paused', 'success'); }
      catch (e) { toast('Pause failed: ' + e.message, 'error'); }
    }

    async function resumeTask(taskId) {
      try { await API.resumeLongRunningTask(taskId); toast('Resumed', 'success'); }
      catch (e) { toast('Resume failed: ' + e.message, 'error'); }
    }

    onMounted(() => {
      refresh();
      startListStream();
    });

    onUnmounted(() => {
      stopListStream();
      stopDetailStream();
    });

    return {
      tasks, loading, selected, detail, recentEvents, filterKind, filterStatus,
      filteredTasks, refresh, selectTask, cancelTask, pauseTask, resumeTask,
      formatTime, formatRelative, KIND_BADGE, STATUS_BADGE,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Long-running Tasks</h1>
        <div style="display:flex;gap:8px;align-items:center;">
          <select v-model="filterKind" class="mx-input" style="width:auto;" @change="refresh">
            <option value="">All kinds</option>
            <option value="persistent">Persistent</option>
            <option value="monitor">Monitor</option>
            <option value="cron">Cron</option>
          </select>
          <select v-model="filterStatus" class="mx-input" style="width:auto;" @change="refresh">
            <option value="">All status</option>
            <option value="running">Running</option>
            <option value="paused">Paused</option>
            <option value="pending">Pending</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <button class="mx-btn mx-btn--ghost" @click="refresh">Refresh</button>
        </div>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <div v-else style="display:grid;grid-template-columns:minmax(320px,1fr) 2fr;gap:16px;align-items:start;">
        <!-- Left: list -->
        <div>
          <div v-if="filteredTasks.length === 0" class="mx-empty">
            <p>No long-running tasks. Ask an admin agent to <code>schedule_monitor</code> or <code>schedule_recurring_task</code>.</p>
          </div>
          <div v-else style="display:flex;flex-direction:column;gap:8px;">
            <div v-for="t in filteredTasks" :key="t.task_id"
                 class="mx-card"
                 :style="{
                   padding:'12px',
                   cursor:'pointer',
                   borderLeft: '3px solid ' + (KIND_BADGE[t.kind]?.accent || 'var(--border)'),
                   outline: selected === t.task_id ? '2px solid var(--accent)' : 'none',
                 }"
                 @click="selectTask(t.task_id)">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">
                <div style="font-weight:600;font-size:13px;">{{ t.name }}</div>
                <span class="mx-badge" :class="STATUS_BADGE[t.status]?.cls">{{ STATUS_BADGE[t.status]?.label || t.status }}</span>
              </div>
              <div style="font-size:11px;color:var(--text-muted);font-family:monospace;">{{ t.task_id }}</div>
              <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted);margin-top:6px;">
                <span>{{ KIND_BADGE[t.kind]?.label || t.kind }} · {{ Math.round((t.progress || 0) * 100) }}%</span>
                <span>♥ {{ formatRelative(t.heartbeat_at) }}</span>
              </div>
              <div v-if="t.last_step" style="font-size:11px;color:var(--text);margin-top:4px;font-style:italic;">↳ {{ t.last_step }}</div>
            </div>
          </div>
        </div>

        <!-- Right: detail -->
        <div>
          <div v-if="!detail" class="mx-empty"><p>Select a task on the left to see live progress.</p></div>
          <div v-else class="mx-card" style="padding:16px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
              <div>
                <h2 style="font-size:18px;margin:0;">{{ detail.name }}</h2>
                <div style="font-size:12px;color:var(--text-muted);font-family:monospace;margin-top:2px;">{{ detail.task_id }}</div>
              </div>
              <div style="display:flex;gap:6px;">
                <button v-if="detail.status === 'running'" class="mx-btn mx-btn--sm mx-btn--ghost" @click="pauseTask(detail.task_id)">Pause</button>
                <button v-if="detail.status === 'paused'" class="mx-btn mx-btn--sm" @click="resumeTask(detail.task_id)">Resume</button>
                <button v-if="['running','paused','pending'].includes(detail.status)" class="mx-btn mx-btn--sm mx-btn--ghost" style="color:var(--error);" @click="cancelTask(detail.task_id)">Cancel</button>
              </div>
            </div>

            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 16px;font-size:12px;margin-bottom:12px;">
              <div><span style="color:var(--text-muted);">Status:</span> <span class="mx-badge" :class="STATUS_BADGE[detail.status]?.cls">{{ detail.status }}</span></div>
              <div><span style="color:var(--text-muted);">Kind:</span> {{ detail.kind }}</div>
              <div><span style="color:var(--text-muted);">Progress:</span> {{ Math.round((detail.progress || 0) * 100) }}%</div>
              <div><span style="color:var(--text-muted);">Heartbeat:</span> {{ formatRelative(detail.heartbeat_at) }}</div>
              <div><span style="color:var(--text-muted);">Started:</span> {{ formatTime(detail.started_at) }}</div>
              <div><span style="color:var(--text-muted);">Next run:</span> {{ formatTime(detail.next_run_at) }}</div>
              <div v-if="detail.parent_thread_id"><span style="color:var(--text-muted);">Spawned by:</span> {{ detail.parent_thread_id }}</div>
              <div v-if="detail.error" style="grid-column:1/-1;color:var(--error);">⚠ {{ detail.error }}</div>
            </div>

            <div v-if="detail.description" style="font-size:13px;margin-bottom:12px;padding:10px;background:var(--bg-hover);border-radius:6px;">
              {{ detail.description }}
            </div>

            <div>
              <div style="font-weight:600;margin-bottom:6px;">Recent activity</div>
              <div v-if="recentEvents.length === 0" style="font-size:12px;color:var(--text-muted);">Waiting for events…</div>
              <div v-else style="display:flex;flex-direction:column;gap:4px;max-height:320px;overflow-y:auto;">
                <div v-for="(ev, i) in recentEvents" :key="i" style="font-size:12px;padding:6px 8px;border-left:2px solid var(--border);background:var(--bg-hover);border-radius:0 4px 4px 0;">
                  <div style="display:flex;justify-content:space-between;color:var(--text-muted);">
                    <span>{{ ev.kind }}</span>
                    <span>{{ formatTime(ev.ts) }}</span>
                  </div>
                  <div v-if="ev.step">{{ ev.step }}<span v-if="ev.progress != null"> · {{ Math.round(ev.progress * 100) }}%</span></div>
                  <div v-else-if="ev.error" style="color:var(--error);">{{ ev.error }}</div>
                  <div v-else-if="ev.output" style="font-family:monospace;font-size:11px;">{{ JSON.stringify(ev.output).slice(0, 200) }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
};
