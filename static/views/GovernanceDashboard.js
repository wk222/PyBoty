import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';
import ApprovalCenter from '/static/views/ApprovalCenter.js';
import PolicyEditor from '/static/views/PolicyEditor.js';

function formatTimestamp(value) {
  if (!value) return '—';
  if (typeof value === 'number') {
    return new Date(value * 1000).toLocaleString();
  }
  return new Date(value).toLocaleString();
}

function routeTargetSummary(route) {
  if (!route || typeof route !== 'object') return '—';
  if (route.target === 'workflow') {
    return `workflow:${route.workflow_name || 'unnamed'}`;
  }
  return `agent:${route.mode || 'assistant'}`;
}

function routeConditionSummary(route) {
  if (!route || typeof route !== 'object') return '—';
  const parts = [];
  if (route.channel) parts.push(`#${route.channel}`);
  if (route.starts_with) parts.push(`starts:${route.starts_with}`);
  if (route.contains) parts.push(`contains:${route.contains}`);
  if (route.user_pattern) parts.push(`user:${route.user_pattern}`);
  return parts.join(' · ') || 'default';
}

function pairingLabel(pairing) {
  if (!pairing || typeof pairing !== 'object') return 'unknown-device';
  return pairing.device_label || pairing.device_name || pairing.device_id || pairing.request_id || 'unknown-device';
}

export default {
  name: 'GovernanceDashboard',
  components: {
    ApprovalCenter,
    PolicyEditor,
  },
  setup() {
    const route = useRoute();
    const router = useRouter();

    const loading = ref(true);
    const refreshing = ref(false);
    const viewTab = ref('approvals');
    const pairingBusy = ref({});
    const center = ref({
      approvals: { pending: [], recent: [], counts: { pending: 0, approved: 0, rejected: 0 } },
      policy: { policy: { mode: 'balanced' }, presets: {} },
      options: {},
      gateway: {
        status: {
          ws_enabled: false,
          auth_mode: 'none',
          supported_channels: [],
          presence_count: 0,
          pending_pairings: 0,
          approved_pairings: 0,
          session_count: 0,
          route_count: 0,
        },
        pairings: { pending: [], approved: [] },
        routes: [],
      },
    });

    const tabs = [
      { key: 'approvals', label: () => t('governance.tabApprovals') },
      { key: 'policy', label: () => t('governance.tabPolicy') },
      { key: 'gateway', label: () => t('governance.tabGateway') },
    ];

    const approvalCounts = computed(() => center.value.approvals?.counts || {});
    const policyMode = computed(() => center.value.policy?.policy?.mode || 'balanced');
    const gatewayStatus = computed(() => center.value.gateway?.status || {});
    const pendingPairings = computed(() => center.value.gateway?.pairings?.pending || []);
    const approvedPairings = computed(() => center.value.gateway?.pairings?.approved || []);
    const routes = computed(() => center.value.gateway?.routes || []);

    function syncTabFromRoute() {
      const panel = typeof route.query.panel === 'string' ? route.query.panel : '';
      if (route.path === '/governance/policy') {
        viewTab.value = 'policy';
        return;
      }
      if (route.path === '/approvals') {
        viewTab.value = 'approvals';
        return;
      }
      if (panel === 'policy' || panel === 'gateway' || panel === 'approvals') {
        viewTab.value = panel;
        return;
      }
      viewTab.value = 'approvals';
    }

    async function loadCenter() {
      if (!loading.value) {
        refreshing.value = true;
      }
      try {
        center.value = await API.getGovernanceCenter();
      } catch (error) {
        toast('Failed to load governance center: ' + error.message, 'error');
      } finally {
        loading.value = false;
        refreshing.value = false;
      }
    }

    async function openTab(tab) {
      viewTab.value = tab;
      const path = tab === 'policy' ? '/governance/policy' : '/governance';
      const query = tab === 'gateway' ? { panel: 'gateway' } : {};
      await router.replace({ path, query });
    }

    async function resolvePairing(deviceId, approved) {
      if (pairingBusy.value[deviceId]) return;
      pairingBusy.value[deviceId] = true;
      try {
        if (approved) {
          await API.approveGatewayPairing(deviceId);
        } else {
          await API.rejectGatewayPairing(deviceId);
        }
        toast(approved ? 'Device pairing approved' : 'Device pairing rejected', 'success');
        await loadCenter();
      } catch (error) {
        toast('Failed to update pairing: ' + error.message, 'error');
      } finally {
        pairingBusy.value[deviceId] = false;
      }
    }

    const onGovernanceChanged = () => {
      loadCenter();
    };

    watch(() => [route.path, route.query.panel], syncTabFromRoute, { immediate: true });

    onMounted(async () => {
      window.addEventListener('pybot:governance-changed', onGovernanceChanged);
      await loadCenter();
    });

    onUnmounted(() => {
      window.removeEventListener('pybot:governance-changed', onGovernanceChanged);
    });

    return {
      tabs,
      t,
      loading,
      refreshing,
      viewTab,
      approvalCounts,
      policyMode,
      gatewayStatus,
      pendingPairings,
      approvedPairings,
      routes,
      openTab,
      loadCenter,
      resolvePairing,
      pairingBusy,
      formatTimestamp,
      routeTargetSummary,
      routeConditionSummary,
      pairingLabel,
    };
  },
  template: `
    <div class="gov-view">
      <header class="gov-header">
        <div class="gov-title-row">
          <h1 class="gov-title">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="24" height="24">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
            </svg>
            {{ t('governance.title') }}
          </h1>
          <button class="mx-btn mx-btn-ghost" @click="loadCenter" :disabled="refreshing">
            {{ refreshing ? t('common.loading') : t('governance.refresh') }}
          </button>
        </div>

        <div class="gov-stats-row">
          <div class="gov-stat-card gov-stat-pending">
            <span class="gov-stat-num">{{ approvalCounts.pending || 0 }}</span>
            <span class="gov-stat-lbl">{{ t('governance.pending') }}</span>
          </div>
          <div class="gov-stat-card gov-stat-approved">
            <span class="gov-stat-num">{{ approvalCounts.approved || 0 }}</span>
            <span class="gov-stat-lbl">{{ t('governance.approved') }}</span>
          </div>
          <div class="gov-stat-card gov-stat-denied">
            <span class="gov-stat-num">{{ approvalCounts.rejected || 0 }}</span>
            <span class="gov-stat-lbl">{{ t('governance.rejected') }}</span>
          </div>
          <div class="gov-stat-card">
            <span class="gov-stat-num">{{ policyMode }}</span>
            <span class="gov-stat-lbl">{{ t('governance.currentMode') }}</span>
          </div>
          <div class="gov-stat-card">
            <span class="gov-stat-num">{{ gatewayStatus.pending_pairings || 0 }}</span>
            <span class="gov-stat-lbl">{{ t('governance.pendingPairings') }}</span>
          </div>
          <div class="gov-stat-card">
            <span class="gov-stat-num">{{ gatewayStatus.route_count || 0 }}</span>
            <span class="gov-stat-lbl">{{ t('governance.channelRoutes') }}</span>
          </div>
        </div>

        <div class="gcenter-tabs">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            class="gcenter-tab"
            :class="{ active: viewTab === tab.key }"
            @click="openTab(tab.key)"
          >
            {{ tab.label() }}
          </button>
        </div>
      </header>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>{{ t('common.loading') }}</span></div>

      <div v-else style="display:flex;flex-direction:column;gap:24px;">
        <ApprovalCenter v-if="viewTab === 'approvals'" embedded />

        <div v-else-if="viewTab === 'policy'" class="gcenter-stack">
          <div class="gcenter-panel">
            <div class="gcenter-panel-title">{{ t('governance.title') }}</div>
            <div class="gcenter-metrics">
              <div class="gcenter-metric">
                <span class="gcenter-metric-label">{{ t('governance.currentMode') }}</span>
                <span class="gcenter-metric-value">{{ policyMode }}</span>
              </div>
              <div class="gcenter-metric">
                <span class="gcenter-metric-label">{{ t('governance.pendingPairings') }}</span>
                <span class="gcenter-metric-value">{{ gatewayStatus.pending_pairings || 0 }}</span>
              </div>
              <div class="gcenter-metric">
                <span class="gcenter-metric-label">{{ t('governance.channelRoutes') }}</span>
                <span class="gcenter-metric-value">{{ gatewayStatus.route_count || 0 }}</span>
              </div>
            </div>
          </div>
          <PolicyEditor embedded />
        </div>

        <div v-else class="gcenter-stack">
          <div class="gcenter-grid">
            <div class="gcenter-panel">
              <div class="gcenter-panel-title">{{ t('governance.gatewayStatus') }}</div>
              <div class="gcenter-metrics">
                <div class="gcenter-metric">
                  <span class="gcenter-metric-label">{{ t('governance.wsEnabled') }}</span>
                  <span class="gcenter-metric-value">{{ gatewayStatus.ws_enabled ? 'on' : 'off' }}</span>
                </div>
                <div class="gcenter-metric">
                  <span class="gcenter-metric-label">{{ t('governance.authMode') }}</span>
                  <span class="gcenter-metric-value">{{ gatewayStatus.auth_mode || 'none' }}</span>
                </div>
                <div class="gcenter-metric">
                  <span class="gcenter-metric-label">{{ t('governance.supportedChannels') }}</span>
                  <span class="gcenter-metric-value">{{ (gatewayStatus.supported_channels || []).length }}</span>
                </div>
                <div class="gcenter-metric">
                  <span class="gcenter-metric-label">{{ t('governance.sessionCount') }}</span>
                  <span class="gcenter-metric-value">{{ gatewayStatus.session_count || 0 }}</span>
                </div>
                <div class="gcenter-metric">
                  <span class="gcenter-metric-label">{{ t('governance.approvedDevices') }}</span>
                  <span class="gcenter-metric-value">{{ gatewayStatus.approved_pairings || 0 }}</span>
                </div>
              </div>
              <div class="gcenter-chip-row" v-if="(gatewayStatus.supported_channels || []).length">
                <span v-for="channel in gatewayStatus.supported_channels" :key="channel" class="gcenter-chip">
                  {{ channel }}
                </span>
              </div>
            </div>

            <div class="gcenter-panel">
              <div class="gcenter-panel-title">{{ t('governance.pairings') }}</div>
              <div v-if="pendingPairings.length === 0" class="mx-empty">
                <p>{{ t('governance.noPairings') }}</p>
              </div>
              <div v-else class="gcenter-list">
                <div v-for="pairing in pendingPairings" :key="pairing.request_id || pairing.device_id" class="gcenter-list-item">
                  <div class="gcenter-list-main">
                    <div class="gcenter-list-title">{{ pairingLabel(pairing) }}</div>
                    <div class="gcenter-list-meta">
                      <span>{{ pairing.device_id || pairing.request_id }}</span>
                      <span>{{ formatTimestamp(pairing.created_at || pairing.requested_at) }}</span>
                    </div>
                  </div>
                  <div style="display:flex;gap:8px;">
                    <button
                      class="mx-btn mx-btn-ghost"
                      :disabled="pairingBusy[pairing.device_id || pairing.request_id]"
                      @click="resolvePairing(pairing.device_id || pairing.request_id, false)"
                    >
                      {{ t('governance.rejectDevice') }}
                    </button>
                    <button
                      class="mx-btn mx-btn--primary"
                      :disabled="pairingBusy[pairing.device_id || pairing.request_id]"
                      @click="resolvePairing(pairing.device_id || pairing.request_id, true)"
                    >
                      {{ t('governance.approveDevice') }}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="gcenter-panel">
            <div class="gcenter-panel-title">{{ t('governance.channelRoutes') }}</div>
            <div v-if="routes.length === 0" class="mx-empty">
              <p>{{ t('governance.noRoutes') }}</p>
            </div>
            <div v-else class="gcenter-list">
              <div v-for="routeDef in routes" :key="routeDef.name || routeDef.thread_template" class="gcenter-list-item">
                <div class="gcenter-list-main">
                  <div class="gcenter-list-title">{{ routeDef.name || 'unnamed-route' }}</div>
                  <div class="gcenter-list-meta">
                    <span>{{ t('governance.routeTarget') }}: {{ routeTargetSummary(routeDef) }}</span>
                    <span>{{ t('governance.routeWhen') }}: {{ routeConditionSummary(routeDef) }}</span>
                  </div>
                </div>
                <span class="gcenter-chip" :style="{ opacity: routeDef.enabled === false ? 0.65 : 1 }">
                  {{ routeDef.enabled === false ? 'disabled' : 'active' }}
                </span>
              </div>
            </div>
          </div>

          <div v-if="approvedPairings.length" class="gcenter-panel">
            <div class="gcenter-panel-title">{{ t('governance.approvedDevices') }}</div>
            <div class="gcenter-list">
              <div v-for="pairing in approvedPairings.slice(0, 8)" :key="pairing.request_id || pairing.device_id" class="gcenter-list-item">
                <div class="gcenter-list-main">
                  <div class="gcenter-list-title">{{ pairingLabel(pairing) }}</div>
                  <div class="gcenter-list-meta">
                    <span>{{ pairing.device_id || pairing.request_id }}</span>
                    <span>{{ formatTimestamp(pairing.approved_at || pairing.resolved_at || pairing.created_at) }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
};
