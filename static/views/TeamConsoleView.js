import { computed, onMounted, ref } from 'vue';

import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';

function statusClass(status) {
  if (status === 'pass') return 'team-status-pass';
  if (status === 'warn') return 'team-status-warn';
  if (status === 'fail') return 'team-status-fail';
  return 'team-status-skip';
}

export default {
  name: 'TeamConsoleView',
  setup() {
    const loading = ref(true);
    const doctor = ref({ checks: [], summary: {}, ready: false });
    const workspaceFiles = ref([]);
    const pendingPairings = ref([]);
    const openclawReport = ref(null);
    const bootstrapping = ref(false);
    const distilling = ref(false);

    const failCount = computed(() => doctor.value.summary?.fail || 0);
    const warnCount = computed(() => doctor.value.summary?.warn || 0);

    async function loadDoctor() {
      doctor.value = await API.getDoctorReport();
    }

    async function loadWorkspace() {
      const data = await API.listWorkspaceFiles();
      workspaceFiles.value = Object.entries(data.files || {}).map(([name, info]) => ({ name, ...info }));
    }

    async function loadPairings() {
      try {
        const data = await API.listChannelPairings();
        pendingPairings.value = data.pending || [];
      } catch (_) {
        pendingPairings.value = [];
      }
    }

    async function loadOpenclaw() {
      try {
        openclawReport.value = await API.getOpenclawReport();
      } catch (_) {
        openclawReport.value = null;
      }
    }

    async function refreshAll() {
      loading.value = true;
      try {
        await Promise.all([loadDoctor(), loadWorkspace(), loadPairings(), loadOpenclaw()]);
      } catch (error) {
        toast(t('team.loadFailed') + error.message, 'error');
      } finally {
        loading.value = false;
      }
    }

    async function bootstrapWorkspace() {
      bootstrapping.value = true;
      try {
        const data = await API.bootstrapTeamWorkspace();
        doctor.value = data.doctor || doctor.value;
        toast(t('team.bootstrapOk'), 'success');
        await loadWorkspace();
      } catch (error) {
        toast(t('team.bootstrapFailed') + error.message, 'error');
      } finally {
        bootstrapping.value = false;
      }
    }

    async function distillMemory() {
      distilling.value = true;
      try {
        await API.distillMemory(true);
        toast(t('team.distillOk'), 'success');
        await loadDoctor();
      } catch (error) {
        toast(t('team.distillFailed') + error.message, 'error');
      } finally {
        distilling.value = false;
      }
    }

    async function approvePairing(row) {
      try {
        await API.approveChannelPairing(row.channel, row.code);
        toast(t('team.pairingApproved'), 'success');
        await loadPairings();
      } catch (error) {
        toast(t('team.pairingFailed') + error.message, 'error');
      }
    }

    async function importOpenclaw() {
      try {
        const data = await API.importOpenclawChannels();
        const imported = data.imported || {};
        toast(`${t('team.openclawImported')} (${Object.keys(imported).length})`, 'success');
        await loadOpenclaw();
        await loadDoctor();
      } catch (error) {
        toast(t('team.openclawFailed') + error.message, 'error');
      }
    }

    onMounted(refreshAll);

    return {
      t,
      loading,
      doctor,
      workspaceFiles,
      pendingPairings,
      openclawReport,
      bootstrapping,
      distilling,
      failCount,
      warnCount,
      statusClass,
      refreshAll,
      bootstrapWorkspace,
      distillMemory,
      approvePairing,
      importOpenclaw,
    };
  },
  template: `
    <div class="gov-view team-console">
      <header class="gov-header">
        <div class="gov-title-row">
          <h1 class="gov-title">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="24" height="24">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
              <path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
            </svg>
            {{ t('team.title') }}
          </h1>
          <button class="mx-btn mx-btn-ghost" @click="refreshAll">{{ t('common.refresh') }}</button>
        </div>
        <p class="mx-muted">{{ t('team.subtitle') }}</p>
      </header>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>{{ t('common.loading') }}</span></div>

      <div v-else class="mx-card-grid mx-card-grid--compact">
        <section class="gov-panel" style="grid-column: 1 / -1;">
          <div class="gov-panel-head">
            <h2>{{ t('team.doctorTitle') }}</h2>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
              <button class="mx-btn mx-btn-primary" @click="bootstrapWorkspace" :disabled="bootstrapping">
                {{ bootstrapping ? t('common.loading') : t('team.bootstrap') }}
              </button>
              <button class="mx-btn mx-btn--ghost" @click="distillMemory" :disabled="distilling">
                {{ distilling ? t('common.loading') : t('team.distill') }}
              </button>
            </div>
          </div>
          <p class="mx-muted">
            {{ t('team.doctorSummary') }}
            <span v-if="doctor.ready" style="color:var(--success)">{{ t('team.ready') }}</span>
            <span v-else>{{ failCount }} fail / {{ warnCount }} warn</span>
          </p>
          <div class="mx-table-wrap">
            <table class="mx-table">
              <thead><tr><th>{{ t('team.colCheck') }}</th><th>{{ t('team.colStatus') }}</th><th>{{ t('team.colDetail') }}</th></tr></thead>
              <tbody>
                <tr v-for="item in doctor.checks" :key="item.id">
                  <td>{{ item.name }}</td>
                  <td><span :class="statusClass(item.status)">{{ item.status }}</span></td>
                  <td>
                    <div>{{ item.detail }}</div>
                    <div v-if="item.fix_hint" class="mx-muted">{{ item.fix_hint }}</div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="gov-panel">
          <div class="gov-panel-head"><h2>{{ t('team.workspaceTitle') }}</h2></div>
          <ul style="list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:8px;">
            <li v-for="file in workspaceFiles" :key="file.name" style="display:flex;justify-content:space-between;gap:8px;">
              <span>{{ file.name }}</span>
              <span class="mx-muted">{{ file.exists ? t('team.fileReady') : t('team.fileMissing') }}</span>
            </li>
          </ul>
          <p class="mx-muted" style="margin-top:12px;">{{ t('team.workspaceHint') }}</p>
        </section>

        <section class="gov-panel">
          <div class="gov-panel-head"><h2>{{ t('team.pairingTitle') }}</h2></div>
          <p v-if="!pendingPairings.length" class="mx-muted">{{ t('team.noPairings') }}</p>
          <div v-else class="mx-table-wrap">
            <table class="mx-table">
              <thead><tr><th>Channel</th><th>User</th><th>Code</th><th></th></tr></thead>
              <tbody>
                <tr v-for="row in pendingPairings" :key="row.channel + row.user_id">
                  <td>{{ row.channel }}</td>
                  <td>{{ row.user_id }}</td>
                  <td><code>{{ row.code }}</code></td>
                  <td><button class="mx-btn mx-btn--sm" @click="approvePairing(row)">{{ t('team.approve') }}</button></td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="gov-panel" style="grid-column: 1 / -1;" v-if="openclawReport">
          <div class="gov-panel-head">
            <h2>{{ t('team.openclawTitle') }}</h2>
            <button class="mx-btn mx-btn--ghost" @click="importOpenclaw">{{ t('team.openclawImport') }}</button>
          </div>
          <p class="mx-muted">
            {{ t('team.openclawHint') }}
            supported={{ (openclawReport.config?.channel_compatibility?.supported || []).join(', ') || '—' }}
          </p>
        </section>
      </div>
    </div>
  `,
};
