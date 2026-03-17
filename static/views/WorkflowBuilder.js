import { ref, computed, onMounted, nextTick } from 'vue';
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
    const { screenToFlowPosition, project, addNodes, addEdges, removeNodes, removeEdges,
            getNodes, getEdges, setNodes, setEdges, fitView } = useVueFlow({ id: FLOW_ID });

    const workflowName = ref('');
    const isNew = ref(true);
    const selectedNode = ref(null);
    const saving = ref(false);
    const showYaml = ref(false);
    const yamlPreview = ref('');

    const initialNodes = ref([]);
    const initialEdges = ref([]);

    onMounted(async () => {
      const name = route.params.name;
      if (name) {
        isNew.value = false;
        workflowName.value = name;
        await loadWorkflow(name);
      } else {
        isNew.value = true;
        workflowName.value = '';
        const startNode = buildFlowNode('start', 'start', 'Start', 400, 60);
        const endNode = buildFlowNode('end', 'end', 'End', 400, 300);
        const defaultEdge = buildFlowEdge('start', 'end');
        initialNodes.value = [startNode, endNode];
        initialEdges.value = [defaultEdge];
        nextTick(() => {
          setNodes([startNode, endNode]);
          setEdges([defaultEdge]);
          nextTick(() => fitView({ padding: 0.3 }));
        });
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

      const flowNodes = rawNodes.map(n => {
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

      const flowEdges = [];
      for (const e of rawEdges) {
        if (typeof e === 'string') {
          const m = e.match(/^\s*(\S+)\s*->\s*(\S+)(?:\s*\|\s*(.+))?\s*$/);
          if (m) flowEdges.push(buildFlowEdge(m[1], m[2], m[3] || ''));
        } else if (e && typeof e === 'object') {
          flowEdges.push(buildFlowEdge(
            e.from || e.source || '',
            e.to || e.target || '',
            e.label || e.condition || '',
          ));
        }
      }

      let finalNodes, finalEdges;
      const hasPositions = rawNodes.some(n => n.position && n.position.x !== undefined);
      if (hasPositions) {
        finalNodes = flowNodes;
        finalEdges = flowEdges;
      } else {
        const laid = autoLayout(flowNodes, flowEdges);
        finalNodes = laid.nodes;
        finalEdges = laid.edges;
      }

      initialNodes.value = finalNodes;
      initialEdges.value = finalEdges;
      nextTick(() => {
        setNodes(finalNodes);
        setEdges(finalEdges);
        nextTick(() => fitView({ padding: 0.2 }));
      });

      const maxId = rawNodes.reduce((mx, n) => {
        const m2 = (n.id || '').match(/_(\d+)$/);
        return m2 ? Math.max(mx, parseInt(m2[1], 10)) : mx;
      }, 0);
      nodeCounter = Math.max(nodeCounter, maxId);
    }

    function onDropNode(event) {
      const toFlowPos = screenToFlowPosition || project;
      const pos = toFlowPos
        ? toFlowPos({ x: event.clientX, y: event.clientY })
        : { x: event.clientX - 300, y: event.clientY - 100 };
      const id = nextNodeId(event.type);
      const node = buildFlowNode(id, event.type, event.label, pos.x, pos.y);
      addNodes([node]);
    }

    function onAddNodeFromPalette(data) {
      const nodes = getNodes.value;
      let cx = 400, cy = 200;
      if (nodes.length > 0) {
        const maxY = Math.max(...nodes.map(n => n.position.y));
        cy = maxY + GAP_Y + NODE_H;
      }
      const id = nextNodeId(data.type);
      const node = buildFlowNode(id, data.type, data.label, cx, cy);
      addNodes([node]);
    }

    function onNodeClick(node) {
      selectedNode.value = node;
    }

    function onPaneClick() {
      selectedNode.value = null;
    }

    function onConnect(params) {
      const edge = buildFlowEdge(params.source, params.target);
      addEdges([edge]);
    }

    function onUpdateNode({ id, label, config }) {
      const nodes = getNodes.value;
      const idx = nodes.findIndex(n => n.id === id);
      if (idx === -1) return;
      const node = nodes[idx];
      node.data = {
        ...node.data,
        label: label || node.data.label,
        config: config || node.data.config,
      };
    }

    function onDeleteNode(id) {
      removeNodes([id]);
      if (selectedNode.value?.id === id) selectedNode.value = null;
    }

    function exportToYaml() {
      const nodes = getNodes.value;
      const edges = getEdges.value;
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

    async function runWorkflow() {
      const name = workflowName.value.trim();
      if (!name) { toast('Save the workflow first', 'warning'); return; }
      try {
        await API.triggerWorkflow(name, {});
        toast('Workflow triggered!', 'success');
      } catch (e) { toast('Trigger failed: ' + e.message, 'error'); }
    }

    function goBack() {
      router.push('/workflows');
    }

    return {
      workflowName, isNew, selectedNode, saving, showYaml, yamlPreview,
      initialNodes, initialEdges,
      onDropNode, onAddNodeFromPalette, onNodeClick, onPaneClick, onConnect,
      onUpdateNode, onDeleteNode,
      toggleYaml, refreshYaml, saveWorkflow, runWorkflow, goBack,
      FLOW_ID,
    };
  },
  template: `
    <div class="wb-layout">
      <div class="wb-toolbar">
        <div class="wb-toolbar-left">
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="goBack" title="Back to list">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M19 12H5M12 19l-7-7 7-7"/></svg>
            Back
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
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="toggleYaml">
            {{ showYaml ? 'Hide YAML' : 'YAML' }}
          </button>
          <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="runWorkflow">Run</button>
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
            :nodes="initialNodes"
            :edges="initialEdges"
            @node-click="onNodeClick"
            @pane-click="onPaneClick"
            @connect="onConnect"
            @drop-node="onDropNode"
          />
          <div v-if="showYaml" class="wb-yaml-panel">
            <div class="wb-yaml-header">
              <span>YAML Preview</span>
              <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="refreshYaml">Refresh</button>
            </div>
            <textarea class="wb-yaml-editor" :value="yamlPreview" readonly spellcheck="false"></textarea>
          </div>
        </div>
        <NodeConfigPanel
          :node="selectedNode"
          @update-node="onUpdateNode"
          @delete-node="onDeleteNode"
          @close="selectedNode = null"
        />
      </div>
    </div>
  `
};
