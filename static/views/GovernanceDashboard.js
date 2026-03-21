import { ref, computed, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';

export default {
  name: 'GovernanceDashboard',
  setup() {
    const approvals = ref([]);
    const loading = ref(false);
    const activeTab = ref('pending');
    const selectedApproval = ref(null);
    const resolveNote = ref('');
    const resolveApprover = ref('admin');
    const resolving = ref(false);

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
        const data = await API.listApprovals();
        const pending = (data.pending || data.approvals || []).map(a => {
          const obj = typeof a === 'object' ? a : {};
          if (!obj.status) obj.status = 'pending';
          return obj;
        });
        const recent = (data.recent || []).map(a => {
          const obj = typeof a === 'object' ? a : {};
          return obj;
        });
        const seen = new Set(pending.map(a => a.approval_id || a.id));
        const merged = [...pending];
        for (const r of recent) {
          const rid = r.approval_id || r.id;
          if (rid && !seen.has(rid)) { merged.push(r); seen.add(rid); }
        }
        approvals.value = merged;
      } catch (e) {
        toast('Failed to load approvals: ' + e.message, 'error');
      } finally {
        loading.value = false;
      }
    }

    async function resolve(id, approved) {
      if (resolving.value) return;
      resolving.value = true;
      try {
        const result = await API.resolveApproval(id, approved, resolveNote.value, resolveApprover.value);
        if (result && result.success === false) {
          toast(result.error || 'Resolve failed', 'error');
          return;
        }
        toast(approved ? 'Approved' : 'Denied', 'success');
        selectedApproval.value = null;
        resolveNote.value = '';
        await loadApprovals();
      } catch (e) {
        toast('Failed: ' + e.message, 'error');
      } finally {
        resolving.value = false;
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
      selectedApproval, resolveNote, resolveApprover, resolving,
      loadApprovals, resolve, selectApproval, closeDetail,
      formatTime, timeSince, riskColor, t,
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
          <router-link to="/governance/policy" class="mx-btn mx-btn-ghost" style="text-decoration:none">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="16" height="16" style="vertical-align:-2px;margin-right:4px">
              <path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
            策略配置
          </router-link>
          <button class="mx-btn mx-btn-ghost" @click="loadApprovals" :title="t('governance.refresh')">↻ {{ t('governance.refresh') }}</button>
        </div>

        <div class="gov-stats-row">
          <div class="gov-stat-card">
            <span class="gov-stat-num">{{ stats.total }}</span>
            <span class="gov-stat-lbl">{{ t('governance.total') }}</span>
          </div>
          <div class="gov-stat-card gov-stat-pending">
            <span class="gov-stat-num">{{ stats.pending }}</span>
            <span class="gov-stat-lbl">{{ t('governance.pending') }}</span>
          </div>
          <div class="gov-stat-card gov-stat-approved">
            <span class="gov-stat-num">{{ stats.approved }}</span>
            <span class="gov-stat-lbl">{{ t('governance.approved') }}</span>
          </div>
          <div class="gov-stat-card gov-stat-denied">
            <span class="gov-stat-num">{{ stats.denied }}</span>
            <span class="gov-stat-lbl">{{ t('governance.denied') }}</span>
          </div>
          <div class="gov-stat-card">
            <span class="gov-stat-num">{{ stats.avgWait }}</span>
            <span class="gov-stat-lbl">{{ t('governance.avgWait') }}</span>
          </div>
        </div>

        <div class="gov-tabs">
          <button v-for="tab in tabs" :key="tab.key"
                  class="gov-tab" :class="{ active: activeTab === tab.key }"
                  @click="activeTab = tab.key">
            <span class="gov-tab-icon">{{ tab.icon }}</span> {{ tab.label }}
            <span v-if="tab.key === 'pending' && stats.pending > 0" class="gov-tab-badge">{{ stats.pending }}</span>
          </button>
        </div>
      </header>

      <div v-if="loading" class="hub-loading"><div class="mx-spinner"></div></div>

      <div v-else-if="filteredApprovals.length === 0" class="hub-empty">
        <p>{{ t('governance.noApprovals') }}</p>
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
          <h3 class="gov-detail-title">{{ t('governance.approvalRequest') }}</h3>
          <div class="gov-detail-grid">
            <div class="gov-detail-field">
              <label>{{ t('governance.action') }}</label>
              <span>{{ selectedApproval.action || selectedApproval.tool_name || '—' }}</span>
            </div>
            <div class="gov-detail-field">
              <label>{{ t('governance.agent') }}</label>
              <span>{{ selectedApproval.agent_name || '—' }}</span>
            </div>
            <div class="gov-detail-field">
              <label>{{ t('governance.status') }}</label>
              <span class="gov-detail-status" :class="'gov-status-' + selectedApproval.status">
                {{ selectedApproval.status }}
              </span>
            </div>
            <div class="gov-detail-field">
              <label>{{ t('governance.risk') }}</label>
              <span :style="{ color: riskColor(selectedApproval.risk_level) }">
                {{ selectedApproval.risk_level || 'unset' }}
              </span>
            </div>
            <div class="gov-detail-field gov-detail-wide" v-if="selectedApproval.reason">
              <label>{{ t('governance.reason') }}</label>
              <span>{{ selectedApproval.reason }}</span>
            </div>
            <div class="gov-detail-field gov-detail-wide" v-if="selectedApproval.context">
              <label>{{ t('governance.context') }}</label>
              <pre class="gov-detail-pre">{{ JSON.stringify(selectedApproval.context, null, 2) }}</pre>
            </div>
          </div>

          <div v-if="selectedApproval.status === 'pending'" class="gov-resolve-section">
            <div class="gov-resolve-input">
              <label>{{ t('governance.note') }}</label>
              <textarea v-model="resolveNote" rows="2" class="hub-input" :placeholder="t('governance.notePlaceholder')"></textarea>
            </div>
            <div class="gov-resolve-actions">
              <button class="mx-btn gov-btn-deny"
                :disabled="resolving"
                @click="resolve(selectedApproval.approval_id || selectedApproval.id, false)">
                {{ resolving ? '...' : '✗' }} {{ t('governance.deny') }}
              </button>
              <button class="mx-btn mx-btn-primary gov-btn-approve"
                :disabled="resolving"
                @click="resolve(selectedApproval.approval_id || selectedApproval.id, true)">
                {{ resolving ? '...' : '✓' }} {{ t('governance.approve') }}
              </button>
            </div>
          </div>

          <div v-else class="gov-resolved-info">
            <span v-if="selectedApproval.resolved_by">{{ t('governance.resolvedBy') }}: {{ selectedApproval.resolved_by }}</span>
            <span v-if="selectedApproval.resolved_at">{{ formatTime(selectedApproval.resolved_at) }}</span>
            <span v-if="selectedApproval.note">{{ t('governance.note') }}: {{ selectedApproval.note }}</span>
          </div>
        </div>
      </div>
    </div>
  `,
};
