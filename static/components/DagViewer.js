import { ref, watch, onMounted, nextTick } from 'vue';

const NODE_COLORS = {
  start: '#34d399', end: '#f87171',
  exec: '#fb923c', tool: '#fbbf24', tool_call: '#fbbf24',
  llm: '#818cf8', llm_call: '#818cf8',
  agent: '#ec4899', debate: '#f43f5e', consensus: '#8b5cf6', supervisor: '#10b981',
  code: '#a78bfa',
  approve: '#f472b6', wait_input: '#f472b6',
  condition: '#f87171', router: '#fb7185',
  parallel: '#60a5fa', foreach: '#34d399',
  subflow: '#c084fc', sub_workflow: '#c084fc',
  transform: '#a78bfa', merge: '#94a3b8',
  delay: '#fcd34d',
  api_call: '#38bdf8', set_var: '#94a3b8', log: '#64748b',
  return: '#6ee7b7',
};

const NODE_W = 160, NODE_H = 48, GAP_X = 60, GAP_Y = 70;

function normalizeEdge(e) {
  return {
    from: e.from || e.source || '',
    to: e.to || e.target || '',
    label: e.label || e.condition || '',
  };
}

function layoutNodes(graph) {
  if (!graph || !graph.nodes) return { positioned: [], edges: [] };
  const nodes = graph.nodes || [];
  const edges = (graph.edges || []).map(normalizeEdge);
  const adj = {};
  const inDeg = {};
  nodes.forEach(n => { adj[n.id] = []; inDeg[n.id] = 0; });
  edges.forEach(e => {
    if (adj[e.from]) adj[e.from].push(e.to);
    if (inDeg[e.to] !== undefined) inDeg[e.to]++;
  });
  const layers = [];
  let queue = nodes.filter(n => (inDeg[n.id] || 0) === 0).map(n => n.id);
  if (queue.length === 0 && nodes.length > 0) queue = [nodes[0].id];
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
  nodes.forEach(n => { if (!visited.has(n.id)) layers.push([n.id]); });

  const nodeMap = {};
  nodes.forEach(n => nodeMap[n.id] = n);

  const positioned = [];
  layers.forEach((layer, li) => {
    const totalW = layer.length * NODE_W + (layer.length - 1) * GAP_X;
    const startX = -totalW / 2;
    layer.forEach((id, ni) => {
      positioned.push({
        ...nodeMap[id],
        x: startX + ni * (NODE_W + GAP_X),
        y: li * (NODE_H + GAP_Y),
        w: NODE_W, h: NODE_H,
      });
    });
  });

  return { positioned, edges };
}

export default {
  name: 'DagViewer',
  props: { graph: Object },
  setup(props) {
    const svgRef = ref(null);
    const viewBox = ref('-300 -20 600 400');
    const laid = ref({ positioned: [], edges: [] });

    function reLayout() {
      laid.value = layoutNodes(props.graph);
      if (laid.value.positioned.length > 0) {
        const ps = laid.value.positioned;
        const minX = Math.min(...ps.map(p => p.x)) - 30;
        const maxX = Math.max(...ps.map(p => p.x + p.w)) + 30;
        const minY = -20;
        const maxY = Math.max(...ps.map(p => p.y + p.h)) + 40;
        viewBox.value = `${minX} ${minY} ${maxX - minX} ${maxY - minY}`;
      }
    }

    watch(() => props.graph, reLayout, { deep: true, immediate: true });

    function nodeCenter(id) {
      const n = laid.value.positioned.find(p => p.id === id);
      if (!n) return { x: 0, y: 0 };
      return { x: n.x + n.w / 2, y: n.y + n.h / 2 };
    }

    function color(type) { return NODE_COLORS[type] || '#64748b'; }

    return { svgRef, viewBox, laid, nodeCenter, color };
  },
  template: `
    <svg ref="svgRef" :viewBox="viewBox" class="mx-dag-svg" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <marker id="arrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
          <path d="M0,0 L8,3 L0,6" fill="var(--text-muted)" />
        </marker>
      </defs>
      <g v-for="e in laid.edges" :key="e.from+'-'+e.to">
        <line :x1="nodeCenter(e.from).x" :y1="nodeCenter(e.from).y + 24"
              :x2="nodeCenter(e.to).x"   :y2="nodeCenter(e.to).y - 24"
              stroke="var(--border-active)" stroke-width="1.5" marker-end="url(#arrow)" />
        <text v-if="e.label"
              :x="(nodeCenter(e.from).x + nodeCenter(e.to).x)/2"
              :y="(nodeCenter(e.from).y + nodeCenter(e.to).y)/2"
              font-size="9" fill="var(--text-muted)" text-anchor="middle">{{ e.label }}</text>
      </g>
      <g v-for="n in laid.positioned" :key="n.id">
        <rect :x="n.x" :y="n.y" :width="n.w" :height="n.h" rx="8"
              :fill="color(n.type)" fill-opacity="0.15"
              :stroke="color(n.type)" stroke-width="1.5" />
        <text :x="n.x + n.w/2" :y="n.y + 18" font-size="11" font-weight="600"
              :fill="color(n.type)" text-anchor="middle">{{ n.label || n.id }}</text>
        <text :x="n.x + n.w/2" :y="n.y + 34" font-size="9"
              fill="var(--text-muted)" text-anchor="middle">{{ n.type }}</text>
      </g>
      <text v-if="laid.positioned.length === 0" x="0" y="60" font-size="13"
            fill="var(--text-muted)" text-anchor="middle">No graph data</text>
    </svg>
  `
};
