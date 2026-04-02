import { ref, reactive, onMounted, computed } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

export default {
  name: 'ScheduleList',
  setup() {
    const tasks = ref([]);
    const history = ref([]);
    const loading = ref(true);
    const showModal = ref(false);
    const editingTask = ref(null);
    const form = reactive({
      name: '', description: '', cron: '*/30 * * * *', prompt: '', enabled: false, run_once_at: null,
    });
    const tab = ref('tasks');

    async function load() {
      loading.value = true;
      try {
        const data = await API.listScheduleTasks();
        tasks.value = data.tasks || [];
        history.value = (data.history || []).slice().reverse();
      } catch (e) {
        toast('Failed to load schedules', 'error');
      } finally {
        loading.value = false;
      }
    }

    function openCreate() {
      editingTask.value = null;
      Object.assign(form, { name: '', description: '', cron: '*/30 * * * *', prompt: '', enabled: false, run_once_at: null });
      showModal.value = true;
    }

    function openEdit(task) {
      editingTask.value = task.name;
      Object.assign(form, {
        name: task.name, description: task.description || '', cron: task.cron,
        prompt: task.prompt, enabled: task.enabled, run_once_at: task.run_once_at || null,
      });
      showModal.value = true;
    }

    async function save() {
      try {
        if (editingTask.value) {
          await API.updateScheduleTask(editingTask.value, form);
          toast('Task updated', 'success');
        } else {
          await API.createScheduleTask(form);
          toast('Task created', 'success');
        }
        showModal.value = false;
        await load();
      } catch (e) {
        toast('Save failed: ' + e.message, 'error');
      }
    }

    async function toggle(name, enabled) {
      try {
        await API.toggleScheduleTask(name, enabled);
        toast(`Task ${enabled ? 'enabled' : 'disabled'}`, 'success');
        await load();
      } catch (e) {
        toast('Toggle failed: ' + e.message, 'error');
      }
    }

    async function remove(name) {
      if (!confirm(`Delete task "${name}"?`)) return;
      try {
        await API.deleteScheduleTask(name);
        toast('Task deleted', 'success');
        await load();
      } catch (e) {
        toast('Delete failed: ' + e.message, 'error');
      }
    }

    async function runNow(name) {
      try {
        const r = await API.runScheduleTaskNow(name);
        toast(`Task triggered (${r.task_id})`, 'success');
      } catch (e) {
        toast('Run failed: ' + e.message, 'error');
      }
    }

    function formatTime(ts) {
      if (!ts) return '—';
      return new Date(ts * 1000).toLocaleString();
    }

    function cronHint(expr) {
      const parts = (expr || '').split(' ');
      if (parts.length !== 5) return 'Invalid';
      const [m, h, d, mo, w] = parts;
      if (m === '*' && h === '*') return 'Every minute';
      if (m.startsWith('*/') && h === '*') return `Every ${m.slice(2)} min`;
      if (h.startsWith('*/')) return `Every ${h.slice(2)} hours`;
      if (m !== '*' && h !== '*' && d === '*') return `Daily at ${h}:${m.padStart(2,'0')}`;
      return expr;
    }

    const activeTasks = computed(() => tasks.value.filter(t => t.enabled));
    const disabledTasks = computed(() => tasks.value.filter(t => !t.enabled));

    onMounted(load);

    return {
      tasks, history, loading, showModal, editingTask, form, tab,
      load, openCreate, openEdit, save, toggle, remove, runNow,
      formatTime, cronHint, activeTasks, disabledTasks,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Scheduled Tasks</h1>
        <div style="display:flex;gap:8px;">
          <div class="mx-tab-bar">
            <button class="mx-tab" :class="{ active: tab === 'tasks' }" @click="tab='tasks'">Tasks ({{ tasks.length }})</button>
            <button class="mx-tab" :class="{ active: tab === 'history' }" @click="tab='history'">History ({{ history.length }})</button>
          </div>
          <button class="mx-btn mx-btn--primary" @click="openCreate">+ New Task</button>
          <button class="mx-btn mx-btn--ghost" @click="load">Refresh</button>
        </div>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <!-- Tasks Tab -->
      <div v-else-if="tab === 'tasks'">
        <div v-if="tasks.length === 0" class="mx-empty">
          <p>No scheduled tasks. Click <strong>+ New Task</strong> to create one.</p>
        </div>

        <div v-else>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px;">
            <div v-for="t in tasks" :key="t.name"
                 class="mx-card" style="padding:16px;position:relative;"
                 :style="{ borderLeft: t.enabled ? '3px solid var(--accent)' : '3px solid var(--border)' }">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px;">
                <div>
                  <div style="font-weight:600;font-size:14px;">{{ t.name }}</div>
                  <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">{{ t.description || t.prompt || '—' }}</div>
                </div>
                <span class="mx-badge" :class="t.enabled ? 'mx-badge--success' : 'mx-badge--draft'">
                  {{ t.enabled ? 'Active' : 'Disabled' }}
                </span>
              </div>

              <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 16px;font-size:12px;color:var(--text-muted);">
                <div>Cron: <code style="background:var(--bg-hover);padding:1px 4px;border-radius:3px;">{{ t.cron }}</code></div>
                <div>≈ {{ cronHint(t.cron) }}</div>
                <div>Last run: {{ formatTime(t.last_run) }}</div>
                <div>Runs: {{ t.run_count }} <span v-if="t.consecutive_failures > 0" style="color:var(--error);">({{ t.consecutive_failures }} fails)</span></div>
              </div>

              <div v-if="t.last_error" style="font-size:11px;color:var(--error);margin-top:6px;padding:4px 8px;background:var(--bg-hover);border-radius:4px;">
                {{ t.last_error }}
              </div>

              <div style="display:flex;gap:6px;margin-top:12px;flex-wrap:wrap;">
                <button class="mx-btn mx-btn--sm" :class="t.enabled ? 'mx-btn--ghost' : 'mx-btn--primary'" @click="toggle(t.name, !t.enabled)">
                  {{ t.enabled ? 'Disable' : 'Enable' }}
                </button>
                <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="runNow(t.name)" title="Run immediately">▶ Run Now</button>
                <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="openEdit(t)">Edit</button>
                <button class="mx-btn mx-btn--sm mx-btn--ghost" style="color:var(--error);" @click="remove(t.name)">Delete</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- History Tab -->
      <div v-else-if="tab === 'history'">
        <div v-if="history.length === 0" class="mx-empty"><p>No execution history yet.</p></div>
        <div v-else class="mx-table-wrap">
          <table class="mx-table">
            <thead>
              <tr><th>Task</th><th>Started</th><th>Status</th><th>Attempts</th><th>Error</th></tr>
            </thead>
            <tbody>
              <tr v-for="(h, i) in history" :key="i">
                <td style="font-weight:500;">{{ h.task }}</td>
                <td style="font-size:12px;">{{ formatTime(h.started_at) }}</td>
                <td>
                  <span class="mx-badge" :class="h.success ? 'mx-badge--success' : 'mx-badge--error'">
                    {{ h.success ? 'OK' : 'Failed' }}
                  </span>
                </td>
                <td>{{ h.attempt || 1 }}</td>
                <td style="font-size:11px;color:var(--error);max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ h.error || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Create/Edit Modal -->
      <div v-if="showModal" class="mx-modal-overlay" @click.self="showModal=false">
        <div class="mx-modal" style="max-width:500px;">
          <h3 style="margin:0 0 16px;">{{ editingTask ? 'Edit Task' : 'New Scheduled Task' }}</h3>
          <div style="display:flex;flex-direction:column;gap:12px;">
            <label class="mx-field">
              <span>Name</span>
              <input v-model="form.name" :disabled="!!editingTask" placeholder="my_daily_check" class="mx-input" />
            </label>
            <label class="mx-field">
              <span>Description</span>
              <input v-model="form.description" placeholder="What this task does" class="mx-input" />
            </label>
            <label class="mx-field">
              <span>Cron Expression <small>(min hour dom month dow)</small></span>
              <input v-model="form.cron" placeholder="*/30 * * * *" class="mx-input" style="font-family:monospace;" />
              <small style="color:var(--text-muted);">{{ cronHint(form.cron) }}</small>
            </label>
            <label class="mx-field">
              <span>Prompt / Action</span>
              <textarea v-model="form.prompt" placeholder="Agent instruction or TRIGGER_WORKFLOW:name" class="mx-input" rows="3" style="resize:vertical;"></textarea>
            </label>
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
              <input type="checkbox" v-model="form.enabled" />
              <span>Enable immediately</span>
            </label>
          </div>
          <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:20px;">
            <button class="mx-btn mx-btn--ghost" @click="showModal=false">Cancel</button>
            <button class="mx-btn mx-btn--primary" @click="save" :disabled="!form.name.trim()">
              {{ editingTask ? 'Update' : 'Create' }}
            </button>
          </div>
        </div>
      </div>
    </div>
  `
};
