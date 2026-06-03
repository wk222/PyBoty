import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useVueFlow } from '@vue-flow/core';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import FlowCanvas from '/static/components/FlowCanvas.js';
import NodePalette from '/static/components/NodePalette.js';
import NodeConfigPanel from '/static/components/NodeConfigPanel.js';
import { NODE_COLORS, NODE_LABELS } from '/static/components/NodePalette.js';

const FLOW_ID = 'builder-flow';

let nodeCounter = 0;
function nextNodeId(type) {
  nodeCounter++;
  return `${type}_${nodeCounter}`;
}

function buildFlowNode(id, type, label, x, y, config = {}) {
  return {
    id,
    type: 'pybot',
    position: { x, y },
    data: {
      label: label || NODE_LABELS[type] || type,
      nodeType: type,
      config: { ...config },
      isBranch: ['condition', 'router', 'question_classifier'].includes(type),
    },
  };
}

function buildFlowEdge(source, target, label = '') {
  return {
    id: `e-${source}-${target}`,
    source,
    target,
    type: 'smoothstep',
    animated: true,
    label: label || undefined,
  };
}

const NODE_W = 160;
const NODE_H = 48;
const GAP_X = 60;
const GAP_Y = 90;

function autoLayout(rawNodes, rawEdges) {
  if (!rawNodes.length) return { nodes: [], edges: rawEdges };
  const adj = {};
  const inDeg = {};
  rawNodes.forEach(n => { adj[n.id] = []; inDeg[n.id] = 0; });
  rawEdges.forEach(e => {
    const src = typeof e === 'object' ? (e.source || e.from) : null;
    const tgt = typeof e === 'object' ? (e.target || e.to) : null;
    if (src && tgt && adj[src]) {
      adj[src].push(tgt);
      if (inDeg[tgt] !== undefined) inDeg[tgt]++;
    }
  });
  const layers = [];
  let queue = rawNodes.filter(n => (inDeg[n.id] || 0) === 0).map(n => n.id);
  if (queue.length === 0 && rawNodes.length > 0) queue = [rawNodes[0].id];
  const visited = new Set();
  while (queue.length > 0) {
    layers.push([...queue]);
    queue.forEach(id => visited.add(id));
    const next = [];
    queue.forEach(id => {
      (adj[id] || []).forEach(to => {
        if (!visited.has(to)) {
          inDeg[to]--;
          if (inDeg[to] <= 0 && !next.includes(to)) next.push(to);
        }
      });
    });
    queue = next;
  }
  rawNodes.forEach(n => { if (!visited.has(n.id)) layers.push([n.id]); });

  const nodeMap = {};
  rawNodes.forEach(n => nodeMap[n.id] = n);
  const positioned = [];
  layers.forEach((layer, li) => {
    const totalW = layer.length * NODE_W + (layer.length - 1) * GAP_X;
    const startX = -totalW / 2 + 400;
    layer.forEach((id, ni) => {
      const orig = nodeMap[id];
      if (orig) {
        positioned.push({
          ...orig,
          position: { x: startX + ni * (NODE_W + GAP_X), y: 60 + li * (NODE_H + GAP_Y) },
        });
      }
    });
  });
  return { nodes: positioned, edges: rawEdges };
}

export default {
  name: 'WorkflowBuilder',
  components: { FlowCanvas, NodePalette, NodeConfigPanel },
  setup() {
    const route = useRoute();
    const router = useRouter();
    const { screenToFlowPosition, project, fitView, zoomIn, zoomOut } = useVueFlow({ id: FLOW_ID });

    const workflowName = ref('');
    const isNew = ref(true);
    const selectedNode = ref(null);
    const saving = ref(false);
    const showYaml = ref(false);
    const yamlPreview = ref('');

    const flowNodes = ref([]);
    const flowEdges = ref([]);

    const showQuickAdd = ref(false);
    const quickAddPos = ref({ x: 0, y: 0 });
    const QUICK_NODES = [
      { type: 'llm', label: 'LLM' },
      { type: 'agent', label: 'Agent' },
      { type: 'code', label: 'Code' },
      { type: 'condition', label: 'Condition' },
      { type: 'http_request', label: 'HTTP' },
      { type: 'tool', label: 'Tool' },
    ];

    const showEmptyHint = computed(() => {
      const actionNodes = flowNodes.value.filter(
        (n) => !['start', 'end'].includes(n.data?.nodeType)
      );
      return actionNodes.length === 0;
    });

    onMounted(async () => {
      const name = route.params.name;
      if (name) {
        isNew.value = false;
        workflowName.value = name;
        await loadWorkflow(name);
      } else {
        isNew.value = true;
        workflowName.value = '';
        const startNode = buildFlowNode('start', 'start', 'Start', 400, 80);
        const endNode = buildFlowNode('end', 'end', 'End', 400, 320);
        const defaultEdge = buildFlowEdge('start', 'end');
        flowNodes.value = [startNode, endNode];
        flowEdges.value = [defaultEdge];
        nextTick(() => fitView({ padding: 0.35, duration: 300 }));
      }
    });

    async function loadWorkflow(name) {
      try {
        const data = await API.getWorkflowDefinition(name);
        let def;
        if (data.spec_content) {
          try { def = jsyaml.load(data.spec_content); } catch { def = data.definition; }
        } else {
          def = data.definition;
        }
        if (!def || !def.nodes) {
          toast('Workflow definition has no nodes', 'warning');
          return;
        }
        importDefinition(def);
      } catch (e) {
        toast('Failed to load workflow: ' + e.message, 'error');
      }
    }

    function importDefinition(def) {
      let rawNodes = def.nodes || [];
      if (!Array.isArray(rawNodes)) rawNodes = Object.values(rawNodes);
      const rawEdges = def.edges || [];

      const builtNodes = rawNodes.map(n => {
        const id = n.id || nextNodeId(n.type || 'exec');
        const config = { ...n };
        delete config.id;
        delete config.type;
        delete config.label;
        delete config.position;
        return buildFlowNode(
          id, n.type || 'exec', n.label || id,
          n.position?.x ?? 0, n.position?.y ?? 0,
          config,
        );
      });

      const builtEdges = [];
      for (const e of rawEdges) {
        if (typeof e === 'string') {
          const m = e.match(/^\s*(\S+)\s*->\s*(\S+)(?:\s*\|\s*(.+))?\s*$/);
          if (m) builtEdges.push(buildFlowEdge(m[1], m[2], m[3] || ''));
        } else if (e && typeof e === 'object') {
          builtEdges.push(buildFlowEdge(
            e.from || e.source || '',
            e.to || e.target || '',
            e.label || e.condition || '',
          ));
        }
      }

      let finalNodes, finalEdges;
      const hasPositions = rawNodes.some(n => n.position && n.position.x !== undefined);
      if (hasPositions) {
        finalNodes = builtNodes;
        finalEdges = builtEdges;
      } else {
        const laid = autoLayout(builtNodes, builtEdges);
        finalNodes = laid.nodes;
        finalEdges = laid.edges;
      }

      flowNodes.value = finalNodes;
      flowEdges.value = finalEdges;
      nextTick(() => fitView({ padding: 0.25, duration: 300 }));

      const maxId = rawNodes.reduce((mx, n) => {
        const m2 = (n.id || '').match(/_(\d+)$/);
        return m2 ? Math.max(mx, parseInt(m2[1], 10)) : mx;
      }, 0);
      nodeCounter = Math.max(nodeCounter, maxId);
    }

    function insertNode(type, label, pos) {
      const id = nextNodeId(type);
      const node = buildFlowNode(id, type, label, pos.x, pos.y);
      flowNodes.value = [...flowNodes.value, node];
      selectedNode.value = node;
      return node;
    }

    function onDropNode(event) {
      const toFlowPos = screenToFlowPosition || project;
      const pos = toFlowPos
        ? toFlowPos({ x: event.clientX, y: event.clientY })
        : { x: event.clientX - 300, y: event.clientY - 100 };
      insertNode(event.type, event.label, pos);
    }

    function onAddNodeFromPalette(data) {
      let cx = 400, cy = 200;
      if (flowNodes.value.length > 0) {
        const maxY = Math.max(...flowNodes.value.map(n => n.position.y));
        cy = maxY + GAP_Y + NODE_H;
      }
      insertNode(data.type, data.label, { x: cx, y: cy });
    }

    function onNodeClick(node) {
      selectedNode.value = node;
      showQuickAdd.value = false;
    }

    function onPaneClick() {
      selectedNode.value = null;
      showQuickAdd.value = false;
    }

    function onPaneDblClick(event) {
      const toFlowPos = screenToFlowPosition || project;
      const pos = toFlowPos
        ? toFlowPos({ x: event.clientX, y: event.clientY })
        : { x: 400, y: 200 };
      quickAddPos.value = pos;
      showQuickAdd.value = true;
    }

    function onQuickAdd(item) {
      insertNode(item.type, item.label, quickAddPos.value);
      showQuickAdd.value = false;
    }

    function onConnect(params) {
      const edge = buildFlowEdge(params.source, params.target);
      flowEdges.value = [...flowEdges.value, edge];
    }

    function onUpdateNode({ id, label, config }) {
      flowNodes.value = flowNodes.value.map((node) => {
        if (node.id !== id) return node;
        return {
          ...node,
          data: {
            ...node.data,
            label: label || node.data.label,
            config: config || node.data.config,
          },
        };
      });
      if (selectedNode.value?.id === id) {
        selectedNode.value = flowNodes.value.find((n) => n.id === id) || null;
      }
    }

    function onDeleteNode(id) {
      flowNodes.value = flowNodes.value.filter((n) => n.id !== id);
      flowEdges.value = flowEdges.value.filter((e) => e.source !== id && e.target !== id);
      if (selectedNode.value?.id === id) selectedNode.value = null;
    }

    function runAutoLayout() {
      const laid = autoLayout(flowNodes.value, flowEdges.value);
      flowNodes.value = laid.nodes;
      flowEdges.value = laid.edges;
      nextTick(() => fitView({ padding: 0.25, duration: 300 }));
      toast('已自动布局', 'success');
    }

    function fitCanvas() {
      fitView({ padding: 0.25, duration: 300 });
    }

    function exportToYaml() {
      const nodes = flowNodes.value;
      const edges = flowEdges.value;
      const defNodes = nodes.map(n => {
        const base = {
          id: n.id,
          type: n.data?.nodeType || 'exec',
          label: n.data?.label || n.id,
          position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
        };
        const cfg = n.data?.config || {};
        for (const [k, v] of Object.entries(cfg)) {
          if (v !== '' && v !== null && v !== undefined) base[k] = v;
        }
        return base;
      });
      const defEdges = edges.map(e => {
        const edge = { from: e.source, to: e.target };
        if (e.label) edge.label = e.label;
        return edge;
      });
      const def = {
        name: workflowName.value || 'untitled',
        description: '',
        tags: [],
        nodes: defNodes,
        edges: defEdges,
      };
      return jsyaml.dump(def, { indent: 2, lineWidth: 120, noRefs: true });
    }

    function toggleYaml() {
      showYaml.value = !showYaml.value;
      if (showYaml.value) {
        yamlPreview.value = exportToYaml();
      }
    }

    function refreshYaml() {
      yamlPreview.value = exportToYaml();
    }

    async function saveWorkflow() {
      const name = workflowName.value.trim();
      if (!name) { toast('Please enter a workflow name', 'warning'); return; }
      saving.value = true;
      try {
        const yaml = exportToYaml();
        if (isNew.value) {
          await API.createWorkflowSpec(name, yaml);
        } else {
          await API.updateWorkflowSpec(name, yaml);
        }
        toast('Workflow saved!', 'success');
        isNew.value = false;
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
      finally { saving.value = false; }
    }

    const showHistory = ref(false);
    const runHistory = ref([]);
    const running = ref(false);

    const showVersions = ref(false);
    const versionList = ref([]);
    const versionMeta = ref({});
    const loadingVersions = ref(false);

    const showSandbox = ref(false);
    const sandboxVars = ref('{\n  "query": "hello"\n}');
    const activeRunId = ref('');
    const selectedRun = ref(null);
    let pollInterval = null;

    onUnmounted(() => {
      if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    });

    function applyTraceToCanvas(runRecord) {
      if (!runRecord) return;
      const execs = runRecord.node_executions || [];
      const execMap = {};
      execs.forEach(rec => {
        execMap[rec.node_id] = rec;
      });

      const updated = flowNodes.value.map(n => {
        const rec = execMap[n.id];
        if (rec) {
          return {
            ...n,
            data: {
              ...n.data,
              status: rec.status,
              elapsed: rec.elapsed_time ? `${rec.elapsed_time.toFixed(2)}s` : null,
              trace: rec,
            }
          };
        } else {
          return {
            ...n,
            data: {
              ...n.data,
              status: null,
              elapsed: null,
              trace: null,
            }
          };
        }
      });
      flowNodes.value = updated;

      if (selectedNode.value) {
        const updatedSelected = updated.find(n => n.id === selectedNode.value.id);
        if (updatedSelected) {
          selectedNode.value = updatedSelected;
        }
      }
    }

    function clearCanvasTrace() {
      activeRunId.value = '';
      selectedRun.value = null;
      const updated = flowNodes.value.map(n => ({
        ...n,
        data: {
          ...n.data,
          status: null,
          elapsed: null,
          trace: null,
        }
      }));
      flowNodes.value = updated;
      if (selectedNode.value) {
        const updatedSelected = updated.find(n => n.id === selectedNode.value.id);
        if (updatedSelected) {
          selectedNode.value = updatedSelected;
        }
      }
    }

    async function selectRun(run) {
      if (!run || !run.run_id) return;
      activeRunId.value = run.run_id;
      try {
        const fullRun = await API.getWorkflowRun(run.run_id);
        selectedRun.value = fullRun;
        applyTraceToCanvas(fullRun);
      } catch (e) {
        toast('Failed to load run trace: ' + e.message, 'error');
      }
    }

    function runWorkflow() {
      const name = workflowName.value.trim();
      if (!name) { toast('Save the workflow first', 'warning'); return; }
      const startNode = flowNodes.value.find(n => n.data?.nodeType === 'start');
      if (startNode && startNode.data?.config?.input_schema) {
        try {
          const schema = typeof startNode.data.config.input_schema === 'string'
            ? JSON.parse(startNode.data.config.input_schema)
            : startNode.data.config.input_schema;
          const defaultVars = {};
          if (schema && typeof schema === 'object') {
            Object.keys(schema).forEach(k => {
              defaultVars[k] = schema[k].default || "";
            });
          }
          sandboxVars.value = JSON.stringify(defaultVars, null, 2);
        } catch { /* ignore */ }
      }
      showSandbox.value = true;
    }

    async function startTestRun() {
      const name = workflowName.value.trim();
      if (!name) { toast('Workflow must have a name', 'warning'); return; }
      
      let vars = {};
      try {
        vars = JSON.parse(sandboxVars.value);
      } catch (e) {
        toast('Variables must be valid JSON', 'warning');
        return;
      }

      showSandbox.value = false;
      running.value = true;
      clearCanvasTrace();

      try {
        const res = await API.triggerWorkflow(name, vars);
        if (res.success) {
          toast('Workflow triggered!', 'success');
          const runId = res.result?.run_id || res.run_id;
          if (runId) {
            activeRunId.value = runId;
            showHistory.value = true;
            await loadHistory();
            startPollingRun(runId);
          } else {
            toast('Workflow finished!', 'success');
            await loadHistory();
          }
        } else {
          toast('Run failed: ' + res.error, 'error');
        }
      } catch (e) {
        toast('Trigger failed: ' + e.message, 'error');
      } finally {
        running.value = false;
      }
    }

    function startPollingRun(runId) {
      if (pollInterval) clearInterval(pollInterval);
      pollInterval = setInterval(async () => {
        try {
          const runRecord = await API.getWorkflowRun(runId);
          selectedRun.value = runRecord;
          applyTraceToCanvas(runRecord);
          
          if (['completed', 'failed', 'error', 'cancelled'].includes(runRecord.status)) {
            clearInterval(pollInterval);
            pollInterval = null;
            toast(`Workflow execution finished with status: ${runRecord.status}`, runRecord.status === 'completed' ? 'success' : 'error');
            await loadHistory();
          }
        } catch (e) {
          clearInterval(pollInterval);
          pollInterval = null;
        }
      }, 1500);
    }

    async function loadHistory() {
      const name = workflowName.value.trim();
      if (!name) return;
      try {
        const data = await API.getWorkflowRuns(name);
        runHistory.value = (data.runs || []).slice(0, 20);
      } catch (_) { runHistory.value = []; }
    }

    function toggleHistory() {
      showHistory.value = !showHistory.value;
      if (showHistory.value) loadHistory();
    }

    function formatDuration(sec) {
      if (!sec || sec < 0.01) return '<0.01s';
      if (sec < 1) return sec.toFixed(2) + 's';
      if (sec < 60) return sec.toFixed(1) + 's';
      return Math.floor(sec / 60) + 'm ' + Math.round(sec % 60) + 's';
    }

    function formatTime(ts) {
      if (!ts) return '';
      return new Date(ts * 1000).toLocaleString();
    }

    function statusColor(s) {
      const map = { completed: '#10b981', failed: '#ef4444', error: '#ef4444', running: '#6366f1', waiting_approval: '#f59e0b' };
      return map[s] || '#6b7280';
    }

    async function loadVersions() {
      const name = workflowName.value.trim();
      if (!name) return;
      loadingVersions.value = true;
      try {
        const data = await API.getWorkflowVersions(name);
        versionList.value = data.commits || [];
        versionMeta.value = data;
      } catch (_) { versionList.value = []; }
      finally { loadingVersions.value = false; }
    }

    function toggleVersions() {
      showVersions.value = !showVersions.value;
      if (showVersions.value) {
        showHistory.value = false;
        loadVersions();
      }
    }

    async function publishWorkflow() {
      const name = workflowName.value.trim();
      if (!name) return;
      try {
        await API.publishWorkflow(name);
        toast('Workflow published!', 'success');
        await loadVersions();
      } catch (e) { toast('Publish failed: ' + e.message, 'error'); }
    }

    async function rollbackToVersion(commitId) {
      const name = workflowName.value.trim();
      if (!name) return;
      try {
        await API.rollbackWorkflow(name, commitId);
        toast('Rolled back to ' + commitId, 'success');
        await loadWorkflow(name);
        await loadVersions();
      } catch (e) { toast('Rollback failed: ' + e.message, 'error'); }
    }

    function goBack() {
      router.push('/workflows');
    }

    function exportJSON() {
      const def = buildDefinitionFromFlow();
      const json = JSON.stringify(def, null, 2);
      const blob = new Blob([json], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = (workflowName.value || 'workflow') + '.json';
      a.click();
      URL.revokeObjectURL(url);
      toast('Workflow exported', 'success');
    }

    function importJSON() {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = '.json,.yaml,.yml';
      input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        try {
          const text = await file.text();
          let def;
          if (file.name.endsWith('.json')) {
            def = JSON.parse(text);
          } else {
            def = jsyaml.load(text);
          }
          if (def && def.nodes) {
            const rawNodes = def.nodes.map((n, i) => buildFlowNode(
              n.id || nextNodeId(n.type),
              n.type,
              n.label || n.id,
              100 + i * (NODE_W + GAP_X),
              100,
              n.config || {},
            ));
            const rawEdges = (def.edges || []).map(e => buildFlowEdge(
              e.from || e.source,
              e.to || e.target,
              e.label || '',
            ));
            const layout = autoLayout(rawNodes, rawEdges);
            flowNodes.value = layout.nodes;
            flowEdges.value = layout.edges;
            if (def.name) workflowName.value = def.name;
            toast('Workflow imported (' + rawNodes.length + ' nodes)', 'success');
          }
        } catch (err) {
          toast('Import failed: ' + err.message, 'error');
        }
      };
      input.click();
    }

    return {
      workflowName, isNew, selectedNode, saving, showYaml, yamlPreview,
      flowNodes, flowEdges, showEmptyHint, showQuickAdd, quickAddPos, QUICK_NODES, showHistory, runHistory, running,
      showVersions, versionList, versionMeta, loadingVersions,
      showSandbox, sandboxVars, activeRunId, selectedRun, selectRun, startTestRun, clearCanvasTrace,
      onDropNode, onAddNodeFromPalette, onNodeClick, onPaneClick, onPaneDblClick, onQuickAdd, onConnect,
      runAutoLayout, fitCanvas, zoomIn, zoomOut,
      onUpdateNode, onDeleteNode,
      toggleYaml, refreshYaml, saveWorkflow, runWorkflow, goBack,
      toggleHistory, toggleVersions, publishWorkflow, rollbackToVersion,
      exportJSON, importJSON,
      formatDuration, formatTime, statusColor,
      FLOW_ID, NODE_COLORS,
    };
  },
  template: `
    <div class="wb-layout">
      <div class="wb-toolbar">
        <div class="wb-toolbar-left">
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="goBack" title="Back to list">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
          </button>
          <span class="wb-toolbar-sep"></span>
          <input
            v-model="workflowName"
            class="wb-toolbar-name-input"
            placeholder="Workflow name..."
            :disabled="!isNew"
          />
        </div>
        <div class="wb-toolbar-right">
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="toggleHistory" :class="{ 'mx-btn--active': showHistory }">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            Runs
          </button>
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="toggleVersions" :class="{ 'mx-btn--active': showVersions }">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20V10M6 20V4M18 20V16"/></svg>
            Versions
          </button>
          <button v-if="!isNew" class="mx-btn mx-btn--ghost mx-btn--sm" @click="publishWorkflow" title="Publish current draft">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
            Publish
          </button>
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="toggleYaml">
            {{ showYaml ? 'Hide Code' : 'Code' }}
          </button>
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="exportJSON" title="Export as JSON">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
            Export
          </button>
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="importJSON" title="Import JSON or YAML">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
            Import
          </button>
          <span class="wb-toolbar-sep"></span>
          <button class="mx-btn mx-btn--ghost mx-btn--sm wb-btn-run" @click="runWorkflow" :disabled="running">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            {{ running ? 'Running...' : 'Test Run' }}
          </button>
          <button class="mx-btn mx-btn--primary mx-btn--sm" @click="saveWorkflow" :disabled="saving">
            {{ saving ? 'Saving...' : 'Save' }}
          </button>
        </div>
      </div>
      <div class="wb-body">
        <NodePalette @add-node="onAddNodeFromPalette" />
        <div class="wb-center">
          <FlowCanvas
            :flow-id="FLOW_ID"
            v-model:nodes="flowNodes"
            v-model:edges="flowEdges"
            :show-empty-hint="showEmptyHint"
            @node-click="onNodeClick"
            @pane-click="onPaneClick"
            @pane-dblclick="onPaneDblClick"
            @connect="onConnect"
            @fit-view="fitCanvas"
            @auto-layout="runAutoLayout"
            @zoom-in="zoomIn"
            @zoom-out="zoomOut"
            @drop-node="onDropNode"
          />
          <div v-if="showQuickAdd" class="wb-quick-add" @click.stop>
            <div class="wb-quick-add-title">快速添加节点</div>
            <div class="wb-quick-add-grid">
              <button
                v-for="item in QUICK_NODES"
                :key="item.type"
                type="button"
                class="wb-quick-add-item"
                @click="onQuickAdd(item)"
              >
                <span class="wb-quick-add-dot" :style="{ background: NODE_COLORS[item.type] || '#64748b' }"></span>
                {{ item.label }}
              </button>
            </div>
          </div>
          <div v-if="showYaml" class="wb-yaml-panel">
            <div class="wb-yaml-header">
              <span>YAML Preview</span>
              <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="refreshYaml">Refresh</button>
            </div>
            <textarea class="wb-yaml-editor" :value="yamlPreview" readonly spellcheck="false"></textarea>
          </div>
        </div>
        <div v-if="showHistory" class="wb-history-panel">
          <div class="wb-history-header">
            <span>Execution History</span>
            <button class="mx-btn mx-btn--ghost mx-btn--xs" style="font-size:10px;padding:2px 6px;margin-left:auto;margin-right:8px;" v-if="activeRunId" @click.stop="clearCanvasTrace">Clear Trace</button>
            <button class="wb-config-close" @click="showHistory = false">&times;</button>
          </div>
          <div class="wb-history-list">
            <div v-if="runHistory.length === 0" class="wb-palette-empty">No execution history</div>
            <div v-for="run in runHistory" :key="run.run_id" class="wb-history-item" :class="{ 'wb-history-item--active': activeRunId === run.run_id }" style="cursor:pointer;" @click="selectRun(run)">
              <div class="wb-history-item-status">
                <span class="wb-history-dot" :style="{ background: statusColor(run.status) }"></span>
                <span class="wb-history-item-id">{{ run.run_id }}</span>
              </div>
              <div class="wb-history-item-meta">
                <span>{{ formatDuration(run.elapsed_time) }}</span>
                <span style="color:var(--text-muted)">{{ run.completed_nodes || 0 }}/{{ run.total_nodes || 0 }} nodes</span>
              </div>
              <div class="wb-history-item-time">{{ formatTime(run.created_at) }}</div>
              <div v-if="run.error" class="wb-history-item-error">{{ run.error }}</div>
            </div>
          </div>
        </div>
        <div v-if="showVersions" class="wb-history-panel">
          <div class="wb-history-header">
            <span>Version History</span>
            <button class="wb-config-close" @click="showVersions = false">&times;</button>
          </div>
          <div v-if="versionMeta.draft_commit_id || versionMeta.published_commit_id" style="padding:8px 12px;font-size:11px;color:var(--text-muted);border-bottom:1px solid var(--border);">
            <div v-if="versionMeta.draft_commit_id">Draft: <code style="color:var(--accent);">{{ versionMeta.draft_commit_id }}</code></div>
            <div v-if="versionMeta.published_commit_id">Published: <code style="color:#10b981;">{{ versionMeta.published_commit_id }}</code></div>
          </div>
          <div class="wb-history-list">
            <div v-if="loadingVersions" class="wb-palette-empty">Loading...</div>
            <div v-else-if="versionList.length === 0" class="wb-palette-empty">No version history</div>
            <div v-for="v in versionList" :key="v.commit_id" class="wb-history-item" style="cursor:pointer;" @click="rollbackToVersion(v.commit_id)">
              <div class="wb-history-item-status">
                <span class="wb-history-dot" :style="{ background: v.is_published ? '#10b981' : v.is_draft ? '#6366f1' : '#6b7280' }"></span>
                <code class="wb-history-item-id">{{ v.commit_id }}</code>
                <span v-if="v.is_published" style="font-size:9px;background:rgba(16,185,129,0.15);color:#10b981;padding:1px 5px;border-radius:4px;margin-left:4px;">published</span>
                <span v-if="v.is_draft" style="font-size:9px;background:rgba(99,102,241,0.15);color:#6366f1;padding:1px 5px;border-radius:4px;margin-left:4px;">draft</span>
              </div>
              <div class="wb-history-item-meta">{{ v.message || '' }}</div>
              <div class="wb-history-item-time">{{ formatTime(v.timestamp) }}</div>
            </div>
          </div>
        </div>
        <NodeConfigPanel
          v-if="!showHistory"
          :node="selectedNode"
          @update-node="onUpdateNode"
          @delete-node="onDeleteNode"
          @close="selectedNode = null"
        />
      </div>

      <!-- Interactive Sandbox Modal -->
      <div v-if="showSandbox" class="wb-sandbox-modal-overlay" @click="showSandbox = false">
        <div class="wb-sandbox-modal" @click.stop>
          <div class="wb-sandbox-header">
            <span class="wb-sandbox-title">Workflow Test Run Sandbox</span>
            <button class="wb-sandbox-close" @click="showSandbox = false">&times;</button>
          </div>
          <div class="wb-sandbox-body">
            <div class="wb-sandbox-field">
              <label class="wb-sandbox-label">Input Variables (JSON)</label>
              <div class="wb-sandbox-desc">Specify starting parameters for this workflow execution.</div>
              <textarea v-model="sandboxVars" class="wb-sandbox-textarea" rows="8" spellcheck="false"></textarea>
            </div>
          </div>
          <div class="wb-sandbox-footer">
            <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="showSandbox = false">Cancel</button>
            <button class="mx-btn mx-btn--primary mx-btn--sm" @click="startTestRun">Run Execution</button>
          </div>
        </div>
      </div>
    </div>
  `
};
