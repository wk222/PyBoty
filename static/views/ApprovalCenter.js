import { ref, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';

function ensureDraft(drafts, approvalId) {
  if (!drafts.value[approvalId]) {
    drafts.value[approvalId] = { approver: '', note: '' };
  }
  return drafts.value[approvalId];
}

export default {
  name: 'ApprovalCenter',
  props: {
    embedded: {
      type: Boolean,
      default: false,
    },
  },
  setup() {
    const approvals = ref([]);
    const recentApprovals = ref([]);
    const counts = ref({ pending: 0, approved: 0, rejected: 0 });
    const drafts = ref({});
    const loading = ref(true);
    const resolving = ref({});

    async function load() {
      loading.value = true;
      try {
        const data = await API.listApprovals();
        approvals.value = data.pending || data.approvals || [];
        recentApprovals.value = data.recent || [];
        counts.value = data.counts || { pending: approvals.value.length, approved: 0, rejected: 0 };
        approvals.value.forEach((approval) => ensureDraft(drafts, approval.approval_id));
      } catch (e) {
        toast('Failed to load approvals', 'error');
      } finally {
        loading.value = false;
      }
    }

    async function resolve(approvalId, approved) {
      if (resolving.value[approvalId]) return;
      resolving.value[approvalId] = true;
      const draft = ensureDraft(drafts, approvalId);
      try {
        const result = await API.resolveApproval(approvalId, approved, draft.note || '', draft.approver || '');
        if (result && result.success === false) {
          toast(result.error || 'Resolve failed', 'error');
          return;
        }
        toast(`Approval ${approved ? 'Granted' : 'Rejected'}`, 'success');
        await load();
        window.dispatchEvent(new CustomEvent('pybot:governance-changed'));
      } catch (e) {
        toast('Failed to resolve approval: ' + e.message, 'error');
      } finally {
        resolving.value[approvalId] = false;
      }
    }

    function formatTime(ts) {
      if (!ts) return '';
      return new Date(ts * 1000).toLocaleString();
    }

    function statusLabel(status) {
      if (status === 'approved') return 'Approved';
      if (status === 'rejected') return 'Rejected';
      return 'Pending';
    }

    function statusColor(status) {
      if (status === 'approved') return '#10b981';
      if (status === 'rejected') return '#ef4444';
      return '#f59e0b';
    }

    onMounted(load);

    return {
      approvals,
      recentApprovals,
      counts,
      drafts,
      loading,
      resolving,
      load,
      resolve,
      formatTime,
      statusLabel,
      statusColor,
      t,
    };
  },
  template: `
    <div :class="embedded ? '' : 'mx-page'">
      <div v-if="!embedded" class="mx-page-header">
        <div>
          <h1 class="mx-page-title">{{ t('governance.title') }}</h1>
          <div style="font-size:12px;color:var(--text-muted);margin-top:6px;">
            {{ t('governance.pending') }} {{ counts.pending }} · {{ t('governance.approved') }} {{ counts.approved }} · {{ t('governance.rejected') }} {{ counts.rejected }}
          </div>
        </div>
        <button class="mx-btn mx-btn--ghost" @click="load">{{ t('common.refresh') }}</button>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>{{ t('common.loading') }}</span></div>

      <div v-else style="display:flex;flex-direction:column;gap:24px;">
        <section>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h2 style="font-size:18px;font-weight:700;">{{ t('governance.pendingApprovals') }}</h2>
            <span class="mx-badge mx-badge--waiting">{{ approvals.length }} {{ t('governance.open') }}</span>
          </div>

          <div v-if="approvals.length === 0" class="mx-empty">
            <p>{{ t('governance.noPending') }}</p>
            <p style="font-size:12px;margin-top:8px;">{{ t('governance.noPendingHint') }}</p>
          </div>

          <div v-else class="mx-card-grid">
            <div v-for="appr in approvals" :key="appr.approval_id" class="mx-entity-card" style="display:flex;flex-direction:column;gap:12px;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                  <div class="mx-entity-card-name" style="font-size:16px;">{{ appr.summary || appr.kind }}</div>
                  <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">{{ appr.scope }}</div>
                </div>
                <span class="mx-badge mx-badge--waiting">{{ t('governance.waiting') }}</span>
              </div>

              <div style="background:var(--bg-hover);padding:12px;border-radius:6px;font-size:13px;line-height:1.5;border:1px solid var(--border);">
                <strong>{{ t('governance.prompt') }}:</strong><br/>
                {{ appr.prompt }}
              </div>

              <input
                v-model="drafts[appr.approval_id].approver"
                class="mx-input"
                :placeholder="t('governance.approverPlaceholder')"
              />

              <textarea
                v-model="drafts[appr.approval_id].note"
                class="mx-textarea"
                rows="3"
                :placeholder="t('governance.notePlaceholder')"
              ></textarea>

              <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="font-size:11px;color:var(--text-muted);">{{ formatTime(appr.created_at) }}</div>
                <div style="display:flex;gap:8px;">
                  <button class="mx-btn mx-btn--ghost" style="color:var(--error);"
                    :disabled="resolving[appr.approval_id]"
                    @click="resolve(appr.approval_id, false)">
                    {{ resolving[appr.approval_id] ? '...' : t('governance.reject') }}
                  </button>
                  <button class="mx-btn mx-btn--primary" style="background:#10b981;border-color:#10b981;"
                    :disabled="resolving[appr.approval_id]"
                    @click="resolve(appr.approval_id, true)">
                    {{ resolving[appr.approval_id] ? '...' : t('governance.approve') }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h2 style="font-size:18px;font-weight:700;">{{ t('governance.recentDecisions') }}</h2>
            <span style="font-size:12px;color:var(--text-muted);">{{ t('governance.last') }} {{ recentApprovals.length }}</span>
          </div>

          <div v-if="recentApprovals.length === 0" class="mx-empty">
            <p>{{ t('governance.noHistory') }}</p>
          </div>

          <div v-else class="mx-card-grid">
            <div v-for="appr in recentApprovals" :key="appr.approval_id" class="mx-entity-card" style="display:flex;flex-direction:column;gap:10px;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                  <div class="mx-entity-card-name" style="font-size:15px;">{{ appr.summary || appr.kind }}</div>
                  <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">{{ appr.scope }}</div>
                </div>
                <span
                  class="mx-badge"
                  :style="{ color: statusColor(appr.status), borderColor: statusColor(appr.status), background: 'transparent' }"
                >
                  {{ statusLabel(appr.status) }}
                </span>
              </div>

              <div style="font-size:12px;color:var(--text-muted);line-height:1.6;">
                <div>{{ t('governance.created') }}: {{ formatTime(appr.created_at) }}</div>
                <div>{{ t('governance.resolved') }}: {{ formatTime(appr.resolved_at) || t('governance.notResolved') }}</div>
                <div>{{ t('governance.approver') }}: {{ appr.resolved_by || 'N/A' }}</div>
              </div>

              <div v-if="appr.resolution_note" style="background:var(--bg-hover);padding:12px;border-radius:6px;border:1px solid var(--border);font-size:13px;">
                <strong>{{ t('governance.note') }}:</strong><br/>
                {{ appr.resolution_note }}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  `,
};
