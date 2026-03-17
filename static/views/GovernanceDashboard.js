import { ref, computed, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

export default {
  name: 'GovernanceDashboard',
  setup() {
    const approvals = ref([]);
    const loading = ref(false);
    const activeTab = ref('pending');
    const selectedApproval = ref(null);
    const resolveNote = ref('');
    const resolveApprover = ref('admin');

    const tabs = [
      { key: 'pending', label: 'Pending', icon: '⏳' },
      { key: 'approved', label: 'Approved', icon: '✓' },
      { key: 'denied', label: 'Denied', icon: '✗' },
      { key: 'all', label: 'All', icon: '◉' },
    ];

    const filteredApprovals = computed(() => {
      if (activeTab.value === 'all') return approvals.value;
      return approvals.value.filter(a => a.status === activeTab.value);
    });

    const stats = computed(() => {
      const all = approvals.value;
      return {
        total: all.length,
        pending: all.filter(a => a.status === 'pending').length,
        approved: all.filter(a => a.status === 'approved').length,
        denied: all.filter(a => a.status === 'denied').length,
        avgWait: calculateAvgWait(all.filter(a => a.status !== 'pending')),
      };
    });

    function calculateAvgWait(resolved) {
      if (!resolved.length) return '—';
      const waits = resolved.map(a => {
        const created = new Date(a.created_at || a.timestamp || 0).getTime();
        const resolvedAt = new Date(a.resolved_at || Date.now()).getTime();
        return (resolvedAt - created) / 1000;
      }).filter(w => w > 0 && w < 86400 * 30);
      if (!waits.length) return '—';
      const avg = waits.reduce((s, w) => s + w, 0) / waits.length;
      if (avg < 60) return `${Math.round(avg)}s`;
      if (avg < 3600) return `${Math.round(avg / 60)}m`;
      return `${Math.round(avg / 3600)}h`;
    }

    async function loadApprovals() {
      loading.value = true;
      try {
        approvals.value = await API.listApprovals();
      } catch (e) {
        toast.error(`Failed to load approvals: ${e.message}`);
      } finally {
        loading.value = false;
      }
    }

    async function resolve(id, approved) {
      try {
        await API.resolveApproval(id, approved, resolveNote.value, resolveApprover.value);
        toast.success(approved ? 'Approved' : 'Denied');
        selectedApproval.value = null;
        resolveNote.value = '';
        await loadApprovals();
      } catch (e) {
        toast.error(`Failed: ${e.message}`);
      }
    }

    function selectApproval(a) { selectedApproval.value = a; }
    function closeDetail() { selectedApproval.value = null; resolveNote.value = ''; }

    function formatTime(ts) {
      if (!ts) return '';
      const d = new Date(ts);
      return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }
    function timeSince(ts) {
      if (!ts) return '';
      const sec = (Date.now() - new Date(ts).getTime()) / 1000;
      if (sec < 60) return `${Math.round(sec)}s ago`;
      if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
      if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
      return `${Math.round(sec / 86400)}d ago`;
    }
    function riskColor(level) {
      const m = { low: '#34d399', medium: '#fbbf24', high: '#f87171', critical: '#ef4444' };
      return m[level] || '#94a3b8';
    }

    onMounted(loadApprovals);

    return {
      approvals, loading, activeTab, tabs, filteredApprovals, stats,
      selectedApproval, resolveNote, resolveApprover,
      loadApprovals, resolve, selectApproval, closeDetail,
      formatTime, timeSince, riskColor,
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
            Governance Dashboard
          </h1>
          <button class="mx-btn mx-btn-ghost" @click="loadApprovals" title="Refresh">↻ Refresh</button>
        </div>

        <div class="gov-stats-row">
          <div class="gov-stat-card">
            <span class="gov-stat-num">{{ stats.total }}</span>
            <span class="gov-stat-lbl">Total</span>
          </div>
          <div class="gov-stat-card gov-stat-pending">
            <span class="gov-stat-num">{{ stats.pending }}</span>
            <span class="gov-stat-lbl">Pending</span>
          </div>
          <div class="gov-stat-card gov-stat-approved">
            <span class="gov-stat-num">{{ stats.approved }}</span>
            <span class="gov-stat-lbl">Approved</span>
          </div>
          <div class="gov-stat-card gov-stat-denied">
            <span class="gov-stat-num">{{ stats.denied }}</span>
            <span class="gov-stat-lbl">Denied</span>
          </div>
          <div class="gov-stat-card">
            <span class="gov-stat-num">{{ stats.avgWait }}</span>
            <span class="gov-stat-lbl">Avg Wait</span>
          </div>
        </div>

        <div class="gov-tabs">
          <button v-for="t in tabs" :key="t.key"
                  class="gov-tab" :class="{ active: activeTab === t.key }"
                  @click="activeTab = t.key">
            <span class="gov-tab-icon">{{ t.icon }}</span> {{ t.label }}
            <span v-if="t.key === 'pending' && stats.pending > 0" class="gov-tab-badge">{{ stats.pending }}</span>
          </button>
        </div>
      </header>

      <div v-if="loading" class="hub-loading"><div class="mx-spinner"></div></div>

      <div v-else-if="filteredApprovals.length === 0" class="hub-empty">
        <p>No {{ activeTab === 'all' ? '' : activeTab }} approvals</p>
      </div>

      <div v-else class="gov-list">
        <div v-for="a in filteredApprovals" :key="a.approval_id || a.id"
             class="gov-item" :class="'gov-item-' + a.status"
             @click="selectApproval(a)">
          <div class="gov-item-left">
            <div class="gov-item-status" :class="'gov-status-' + a.status">
              {{ a.status === 'pending' ? '⏳' : a.status === 'approved' ? '✓' : '✗' }}
            </div>
            <div class="gov-item-info">
              <span class="gov-item-action">{{ a.action || a.tool_name || 'Unknown Action' }}</span>
              <span class="gov-item-agent" v-if="a.agent_name">by {{ a.agent_name }}</span>
              <span class="gov-item-reason" v-if="a.reason">{{ a.reason }}</span>
            </div>
          </div>
          <div class="gov-item-right">
            <span class="gov-item-risk" v-if="a.risk_level" :style="{ color: riskColor(a.risk_level) }">
              {{ a.risk_level }}
            </span>
            <span class="gov-item-time">{{ timeSince(a.created_at || a.timestamp) }}</span>
          </div>
        </div>
      </div>

      <!-- Detail / Resolve modal -->
      <div v-if="selectedApproval" class="hub-modal-overlay" @click.self="closeDetail">
        <div class="hub-modal gov-modal">
          <button class="hub-modal-close" @click="closeDetail">×</button>
          <h3 class="gov-detail-title">Approval Request</h3>
          <div class="gov-detail-grid">
            <div class="gov-detail-field">
              <label>Action</label>
              <span>{{ selectedApproval.action || selectedApproval.tool_name || '—' }}</span>
            </div>
            <div class="gov-detail-field">
              <label>Agent</label>
              <span>{{ selectedApproval.agent_name || '—' }}</span>
            </div>
            <div class="gov-detail-field">
              <label>Status</label>
              <span class="gov-detail-status" :class="'gov-status-' + selectedApproval.status">
                {{ selectedApproval.status }}
              </span>
            </div>
            <div class="gov-detail-field">
              <label>Risk</label>
              <span :style="{ color: riskColor(selectedApproval.risk_level) }">
                {{ selectedApproval.risk_level || 'unset' }}
              </span>
            </div>
            <div class="gov-detail-field gov-detail-wide" v-if="selectedApproval.reason">
              <label>Reason</label>
              <span>{{ selectedApproval.reason }}</span>
            </div>
            <div class="gov-detail-field gov-detail-wide" v-if="selectedApproval.context">
              <label>Context</label>
              <pre class="gov-detail-pre">{{ JSON.stringify(selectedApproval.context, null, 2) }}</pre>
            </div>
          </div>

          <div v-if="selectedApproval.status === 'pending'" class="gov-resolve-section">
            <div class="gov-resolve-input">
              <label>Note (optional)</label>
              <textarea v-model="resolveNote" rows="2" class="hub-input" placeholder="Add a note..."></textarea>
            </div>
            <div class="gov-resolve-actions">
              <button class="mx-btn gov-btn-deny" @click="resolve(selectedApproval.approval_id || selectedApproval.id, false)">
                ✗ Deny
              </button>
              <button class="mx-btn mx-btn-primary gov-btn-approve" @click="resolve(selectedApproval.approval_id || selectedApproval.id, true)">
                ✓ Approve
              </button>
            </div>
          </div>

          <div v-else class="gov-resolved-info">
            <span v-if="selectedApproval.resolved_by">Resolved by: {{ selectedApproval.resolved_by }}</span>
            <span v-if="selectedApproval.resolved_at">at {{ formatTime(selectedApproval.resolved_at) }}</span>
            <span v-if="selectedApproval.note">Note: {{ selectedApproval.note }}</span>
          </div>
        </div>
      </div>
    </div>
  `,
};
