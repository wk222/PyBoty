import { ref, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

function ensureDraft(drafts, approvalId) {
  if (!drafts.value[approvalId]) {
    drafts.value[approvalId] = { approver: '', note: '' };
  }
  return drafts.value[approvalId];
}

export default {
  name: 'ApprovalCenter',
  setup() {
    const approvals = ref([]);
    const recentApprovals = ref([]);
    const counts = ref({ pending: 0, approved: 0, rejected: 0 });
    const drafts = ref({});
    const loading = ref(true);

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
      const draft = ensureDraft(drafts, approvalId);
      try {
        await API.resolveApproval(approvalId, approved, draft.note || '', draft.approver || '');
        toast(`Approval ${approved ? 'Granted' : 'Rejected'}`, 'success');
        await load();
      } catch (e) {
        toast('Failed to resolve approval: ' + e.message, 'error');
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
      load,
      resolve,
      formatTime,
      statusLabel,
      statusColor,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <div>
          <h1 class="mx-page-title">Approval Center</h1>
          <div style="font-size:12px;color:var(--text-muted);margin-top:6px;">
            Pending {{ counts.pending }} · Approved {{ counts.approved }} · Rejected {{ counts.rejected }}
          </div>
        </div>
        <button class="mx-btn mx-btn--ghost" @click="load">Refresh</button>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <div v-else style="display:flex;flex-direction:column;gap:24px;">
        <section>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h2 style="font-size:18px;font-weight:700;">Pending Approvals</h2>
            <span class="mx-badge mx-badge--waiting">{{ approvals.length }} open</span>
          </div>

          <div v-if="approvals.length === 0" class="mx-empty">
            <p>No pending approvals.</p>
            <p style="font-size:12px;margin-top:8px;">Workflow gates and risky tool calls will appear here.</p>
          </div>

          <div v-else class="mx-card-grid">
            <div v-for="appr in approvals" :key="appr.approval_id" class="mx-entity-card" style="display:flex;flex-direction:column;gap:12px;">
              <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                <div>
                  <div class="mx-entity-card-name" style="font-size:16px;">{{ appr.summary || appr.kind }}</div>
                  <div style="font-size:12px;color:var(--text-muted);margin-top:4px;">{{ appr.scope }}</div>
                </div>
                <span class="mx-badge mx-badge--waiting">Waiting</span>
              </div>

              <div style="background:var(--bg-hover);padding:12px;border-radius:6px;font-size:13px;line-height:1.5;border:1px solid var(--border);">
                <strong>Prompt:</strong><br/>
                {{ appr.prompt }}
              </div>

              <input
                v-model="drafts[appr.approval_id].approver"
                class="mx-input"
                placeholder="Approver name (optional)"
              />

              <textarea
                v-model="drafts[appr.approval_id].note"
                class="mx-textarea"
                rows="3"
                placeholder="Approval note (optional)"
              ></textarea>

              <div style="display:flex;justify-content:space-between;align-items:center;">
                <div style="font-size:11px;color:var(--text-muted);">{{ formatTime(appr.created_at) }}</div>
                <div style="display:flex;gap:8px;">
                  <button class="mx-btn mx-btn--ghost" style="color:var(--error);" @click="resolve(appr.approval_id, false)">Reject</button>
                  <button class="mx-btn mx-btn--primary" style="background:#10b981;border-color:#10b981;" @click="resolve(appr.approval_id, true)">Approve</button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h2 style="font-size:18px;font-weight:700;">Recent Decisions</h2>
            <span style="font-size:12px;color:var(--text-muted);">Last {{ recentApprovals.length }}</span>
          </div>

          <div v-if="recentApprovals.length === 0" class="mx-empty">
            <p>No approval history yet.</p>
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
                <div>Created: {{ formatTime(appr.created_at) }}</div>
                <div>Resolved: {{ formatTime(appr.resolved_at) || 'Not resolved' }}</div>
                <div>Approver: {{ appr.resolved_by || 'N/A' }}</div>
              </div>

              <div v-if="appr.resolution_note" style="background:var(--bg-hover);padding:12px;border-radius:6px;border:1px solid var(--border);font-size:13px;">
                <strong>Note:</strong><br/>
                {{ appr.resolution_note }}
              </div>
            </div>
          </div>
        </section>
      </div>
    </div>
  `,
};
