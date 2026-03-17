import { ref, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

export default {
  name: 'ScheduleList',
  setup() {
    const tasks = ref([]);
    const loading = ref(true);

    async function load() {
      loading.value = true;
      try {
        const data = await API.listScheduleTasks();
        tasks.value = data.tasks || [];
      } catch (e) {
        toast('Failed to load schedules', 'error');
      } finally {
        loading.value = false;
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

    function formatTime(ts) {
      if (!ts) return 'Never';
      return new Date(ts * 1000).toLocaleString();
    }

    onMounted(load);

    return { tasks, loading, load, toggle, formatTime };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Schedules</h1>
        <button class="mx-btn mx-btn--ghost" @click="load">Refresh</button>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <div v-else>
        <div v-if="tasks.length === 0" class="mx-empty">
          <p>No scheduled tasks found.</p>
          <p style="font-size:12px;margin-top:8px;">Add a \`schedule: "cron"\` field to your workflows or edit SCHEDULE.md.</p>
        </div>

        <div v-else class="mx-table-wrap">
          <table class="mx-table">
            <thead>
              <tr>
                <th>Name / Description</th>
                <th>Cron</th>
                <th>Status</th>
                <th>Last Run</th>
                <th>Runs</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in tasks" :key="t.name">
                <td>
                  <div class="mx-table-name">{{ t.name }}</div>
                  <div style="font-size:12px;color:var(--text-muted);margin-top:2px;">{{ t.description || t.prompt }}</div>
                  <div v-if="t.last_error" style="font-size:11px;color:var(--error);margin-top:2px;">Error: {{ t.last_error }}</div>
                </td>
                <td><code style="background:var(--bg-hover);padding:2px 6px;border-radius:4px;">{{ t.cron }}</code></td>
                <td>
                  <span class="mx-badge" :class="t.enabled ? 'mx-badge--success' : 'mx-badge--draft'">
                    {{ t.enabled ? 'Active' : 'Disabled' }}
                  </span>
                </td>
                <td style="font-size:12px;color:var(--text-muted);">{{ formatTime(t.last_run) }}</td>
                <td>{{ t.run_count }}</td>
                <td>
                  <button class="mx-btn mx-btn--sm" :class="t.enabled ? 'mx-btn--ghost' : 'mx-btn--primary'" @click="toggle(t.name, !t.enabled)">
                    {{ t.enabled ? 'Disable' : 'Enable' }}
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  `
};
