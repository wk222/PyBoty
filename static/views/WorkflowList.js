import { ref, onMounted, watch, nextTick } from 'vue';
import { useRouter } from 'vue-router';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';
import DagViewer from '/static/components/DagViewer.js';

const NEW_SPEC_TEMPLATE = `name: new_workflow
description: ''
tags: []

nodes:
  - id: step1
    type: llm
    label: AI 处理
    prompt: "处理用户输入: \${input.text}"

edges: []
`;

function specToPreviewGraph(text) {
  try {
    let def;
    try { def = jsyaml.load(text); } catch (_) { def = JSON.parse(text); }
    if (!def || !def.nodes) return null;
    let rawNodes = def.nodes;
    if (!Array.isArray(rawNodes)) rawNodes = Object.values(rawNodes);

    const nodes = rawNodes.map(n => ({
      id: n.id || '',
      type: n.type || 'exec',
      label: n.label || n.id || '',
    }));

    const rawEdges = def.edges || [];
    const edges = [];
    for (const e of rawEdges) {
      if (typeof e === 'string') {
        const m = e.match(/^\s*(\S+)\s*->\s*(\S+)(?:\s*\|\s*(.+))?\s*$/);
        if (m) edges.push({ from: m[1], to: m[2], label: m[3] || '' });
      } else if (e && typeof e === 'object') {
        edges.push({
          from: e.from || e.source || '',
          to: e.to || e.target || '',
          label: e.label || e.condition || '',
        });
      }
    }

    if (edges.length === 0 && nodes.length >= 2) {
      for (let i = 0; i < nodes.length - 1; i++) {
        edges.push({ from: nodes[i].id, to: nodes[i + 1].id });
      }
    }
    return { nodes, edges };
  } catch (_) {
    return null;
  }
}

export default {
  name: 'WorkflowList',
  components: { DagViewer },
  setup() {
    const router = useRouter();
    const saved = ref([]);
    const active = ref([]);
    const loading = ref(true);

    const triggerName = ref('');
    const triggerVars = ref('{}');
    const showTrigger = ref(false);

    const graphData = ref(null);
    const graphName = ref('');
    const showGraph = ref(false);

    const editName = ref('');
    const editContent = ref('');
    const editMode = ref('yaml');
    const editPreview = ref(null);
    const showEditor = ref(false);
    const isNew = ref(false);
    const saving = ref(false);

    async function load() {
      loading.value = true;
      try {
        const data = await API.listWorkflows();
        saved.value = data.saved || [];
        active.value = data.active || [];
      } catch (e) { toast('Failed to load workflows', 'error'); }
      finally { loading.value = false; }
    }

    async function trigger() {
      if (!triggerName.value) { toast('Enter workflow name', 'warning'); return; }
      try {
        let vars = {};
        try { vars = JSON.parse(triggerVars.value); } catch (_) {}
        await API.triggerWorkflow(triggerName.value, vars);
        toast('Workflow triggered!', 'success');
        showTrigger.value = false;
      } catch (e) { toast('Trigger failed: ' + e.message, 'error'); }
    }

    function openTrigger(name) {
      triggerName.value = name || '';
      triggerVars.value = '{}';
      showTrigger.value = true;
    }

    async function viewGraph(name) {
      graphName.value = name;
      try {
        const data = await API.getWorkflowGraph(name);
        if (data.error) { toast(data.error, 'error'); return; }
        if (!data.nodes || data.nodes.length === 0) { toast('该工作流没有节点数据', 'warning'); return; }
        graphData.value = data;
        showGraph.value = true;
      } catch (e) { toast('Failed to load graph: ' + e.message, 'error'); }
    }

    async function openEditor(name) {
      editMode.value = 'yaml';
      if (name) {
        isNew.value = false;
        editName.value = name;
        try {
          const data = await API.getWorkflowDefinition(name);
          if (data.spec_content) {
            editContent.value = data.spec_content;
            editMode.value = 'yaml';
          } else {
            editContent.value = JSON.stringify(data.definition, null, 2);
            editMode.value = 'json';
          }
          updatePreview();
        } catch (e) {
          editContent.value = NEW_SPEC_TEMPLATE;
          toast('Could not load definition, starting with template', 'warning');
        }
      } else {
        isNew.value = true;
        editName.value = '';
        editContent.value = NEW_SPEC_TEMPLATE;
        updatePreview();
      }
      showEditor.value = true;
    }

    function updatePreview() {
      editPreview.value = specToPreviewGraph(editContent.value);
    }

    function toggleMode() {
      if (editMode.value === 'yaml') {
        try {
          const def = jsyaml.load(editContent.value);
          editContent.value = JSON.stringify(def, null, 2);
          editMode.value = 'json';
        } catch (e) {
          toast('当前 Workflow Spec 格式有误，无法切换', 'error');
        }
      } else {
        try {
          const def = JSON.parse(editContent.value);
          editContent.value = jsyaml.dump(def, { indent: 2, lineWidth: 120, noRefs: true });
          editMode.value = 'yaml';
        } catch (e) {
          toast('当前 JSON 格式有误，无法切换到 Workflow Spec', 'error');
        }
      }
    }

    async function saveWorkflow() {
      const name = editName.value.trim();
      if (!name) { toast('请输入工作流名称', 'warning'); return; }
      saving.value = true;
      try {
        if (editMode.value === 'yaml') {
          if (isNew.value) {
            await API.createWorkflowSpec(name, editContent.value);
          } else {
            await API.updateWorkflowSpec(name, editContent.value);
          }
        } else {
          let def;
          try { def = JSON.parse(editContent.value); } catch (_) {
            toast('JSON 格式错误', 'error');
            saving.value = false;
            return;
          }
          if (isNew.value) {
            await API.createWorkflow(name, def);
          } else {
            await API.updateWorkflow(name, name, def);
          }
        }
        toast('Workflow saved!', 'success');
        showEditor.value = false;
        await load();
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
      finally { saving.value = false; }
    }

    async function remove(name) {
      if (!confirm(`确认删除工作流 "${name}"?`)) return;
      try {
        await API.deleteWorkflow(name);
        toast('Workflow deleted', 'success');
        await load();
      } catch (e) { toast('Delete failed: ' + e.message, 'error'); }
    }

    function wfName(w) { return typeof w === 'string' ? w : (w.name || w.id || ''); }

    function openBuilder(name) {
      if (name) {
        router.push(`/workflows/builder/${encodeURIComponent(name)}`);
      } else {
        router.push('/workflows/builder');
      }
    }

    onMounted(load);

    return {
      saved, active, loading, triggerName, triggerVars, showTrigger,
      graphData, graphName, showGraph,
      editName, editContent, editMode, editPreview, showEditor, isNew, saving,
      load, trigger, openTrigger, viewGraph, openEditor, updatePreview,
      toggleMode, saveWorkflow, remove, wfName, openBuilder, t,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Workflows</h1>
        <div style="display:flex;gap:8px;">
          <button class="mx-btn mx-btn--primary" @click="openBuilder(null)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            Visual Builder
          </button>
          <button class="mx-btn mx-btn--ghost" @click="openEditor(null)">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
            New (YAML)
          </button>
          <button class="mx-btn mx-btn--ghost" @click="openTrigger('')">Trigger</button>
          <button class="mx-btn mx-btn--ghost" @click="load">Refresh</button>
        </div>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <div v-else>
        <div v-if="saved.length === 0 && active.length === 0" class="mx-empty">
          <p>{{ t('workflows.noWorkflows') }}</p>
        </div>

        <div v-if="saved.length > 0" class="mx-section">
          <h2 class="mx-section-title">已保存 ({{ saved.length }})</h2>
          <div class="mx-table-wrap">
            <table class="mx-table">
              <thead><tr><th>Name</th><th>Description</th><th>Nodes</th><th style="width:260px;">Actions</th></tr></thead>
              <tbody>
                <tr v-for="w in saved" :key="wfName(w)">
                  <td class="mx-table-name">
                    {{ wfName(w) }}
                    <span v-if="w.schedule" title="Scheduled Task" style="margin-left:6px;color:var(--accent);font-size:12px;">⏰</span>
                  </td>
                  <td style="max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-muted);font-size:12px;">{{ w.description || '-' }}</td>
                  <td>{{ w.nodes_count || '-' }}</td>
                  <td>
                    <div style="display:flex;gap:4px;">
                      <button class="mx-btn mx-btn--sm mx-btn--primary" @click="openTrigger(wfName(w))">Run</button>
                      <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="openBuilder(wfName(w))">Visual</button>
                      <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="viewGraph(wfName(w))">Graph</button>
                      <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="openEditor(wfName(w))">YAML</button>
                      <button class="mx-btn mx-btn--sm mx-btn--ghost" style="color:var(--error)" @click="remove(wfName(w))">Del</button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <div v-if="active.length > 0" class="mx-section">
          <h2 class="mx-section-title">运行中 ({{ active.length }})</h2>
          <div class="mx-card-grid mx-card-grid--compact">
            <div v-for="w in active" :key="w.id || w" class="mx-entity-card mx-entity-card--compact" style="display:flex;align-items:center;justify-content:space-between;">
              <div>
                <div class="mx-entity-card-name">{{ w.name || w.id || w }}</div>
                <span v-if="w.status" style="font-size:11px;color:var(--text-muted);">{{ w.status }}</span>
              </div>
              <div style="display:flex;gap:4px;">
                <button class="mx-btn mx-btn--sm mx-btn--primary" @click="openTrigger(w.id || w)">Run</button>
                <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="viewGraph(w.id || w)">Graph</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Trigger Dialog -->
      <teleport to="body">
        <div v-if="showTrigger" class="mx-modal-overlay" @click.self="showTrigger = false">
          <div class="mx-modal" style="max-width:480px;">
            <div class="mx-modal-header">
              <span>运行工作流</span>
              <button class="mx-modal-close" @click="showTrigger = false">&times;</button>
            </div>
            <div class="mx-modal-body">
              <label class="mx-field-label">Workflow Name</label>
              <input v-model="triggerName" class="mx-input" placeholder="workflow_name" />
              <label class="mx-field-label" style="margin-top:12px;">Input Variables (JSON)</label>
              <textarea v-model="triggerVars" class="mx-textarea" rows="4" placeholder='{"key": "value"}'></textarea>
            </div>
            <div class="mx-modal-footer">
              <button class="mx-btn mx-btn--ghost" @click="showTrigger = false">Cancel</button>
              <button class="mx-btn mx-btn--primary" @click="trigger">Run</button>
            </div>
          </div>
        </div>
      </teleport>

      <!-- Graph Viewer -->
      <teleport to="body">
        <div v-if="showGraph" class="mx-modal-overlay" @click.self="showGraph = false">
          <div class="mx-modal" style="max-width:900px;height:80vh;">
            <div class="mx-modal-header">
              <span>Workflow Graph: {{ graphName }}</span>
              <button class="mx-modal-close" @click="showGraph = false">&times;</button>
            </div>
            <div class="mx-modal-body" style="flex:1;overflow:auto;padding:0;">
              <DagViewer :graph="graphData" />
            </div>
          </div>
        </div>
      </teleport>

      <!-- Workflow Spec Editor -->
      <teleport to="body">
        <div v-if="showEditor" class="mx-modal-overlay" @click.self="showEditor = false">
          <div class="mx-modal" style="max-width:1200px;height:90vh;">
            <div class="mx-modal-header">
              <span>{{ isNew ? '新建工作流' : '编辑: ' + editName }}</span>
              <button class="mx-modal-close" @click="showEditor = false">&times;</button>
            </div>
            <div style="padding:8px 20px;display:flex;gap:12px;align-items:flex-end;">
              <div style="flex:1;">
                <label class="mx-field-label">Name</label>
                <input v-model="editName" class="mx-input" :disabled="!isNew" placeholder="workflow_name" />
              </div>
              <button class="mx-btn mx-btn--ghost" @click="toggleMode" style="white-space:nowrap;">
                {{ editMode === 'yaml' ? '切换 JSON' : '切换 Spec' }}
              </button>
            </div>
            <div class="mx-modal-body" style="flex:1;display:flex;gap:0;padding:0;overflow:hidden;">
              <div style="flex:1;display:flex;flex-direction:column;border-right:1px solid var(--border);min-width:0;">
                <div style="padding:6px 12px;font-size:10px;font-weight:700;color:var(--accent);text-transform:uppercase;border-bottom:1px solid var(--border);letter-spacing:1px;display:flex;align-items:center;gap:6px;">
                  <span :style="{color: editMode === 'yaml' ? '#34d399' : '#818cf8'}">{{ editMode === 'yaml' ? 'SPEC' : 'JSON' }}</span>
                  <span style="color:var(--text-muted);font-weight:400;">Workflow Spec</span>
                </div>
                <textarea
                  v-model="editContent"
                  @input="updatePreview"
                  class="modal-editor"
                  style="flex:1;font-size:12.5px;line-height:1.6;tab-size:2;"
                  spellcheck="false"
                ></textarea>
              </div>
              <div style="flex:0.8;display:flex;flex-direction:column;min-width:0;">
                <div style="padding:6px 12px;font-size:10px;font-weight:700;color:var(--text-muted);text-transform:uppercase;border-bottom:1px solid var(--border);letter-spacing:1px;">Preview</div>
                <div style="flex:1;overflow:auto;">
                  <DagViewer v-if="editPreview" :graph="editPreview" />
                  <div v-else style="padding:40px;text-align:center;color:var(--text-muted);font-size:12px;">
                    编辑左侧定义后将自动预览 DAG 图
                  </div>
                </div>
              </div>
            </div>
            <div class="mx-modal-footer">
              <button class="mx-btn mx-btn--ghost" @click="showEditor = false">Cancel</button>
              <button class="mx-btn mx-btn--primary" @click="saveWorkflow" :disabled="saving">
                {{ saving ? 'Saving...' : 'Save' }}
              </button>
            </div>
          </div>
        </div>
      </teleport>
    </div>
  `
};
