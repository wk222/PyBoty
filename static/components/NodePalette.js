import { ref, computed, onMounted } from 'vue';
import { API } from '/static/api/index.js';

const CATEGORY_META = {
  control:       { label: 'Control Flow', icon: '⟁' },
  action:        { label: 'Action',       icon: '✦' },
  data:          { label: 'Data',         icon: '⊞' },
  iteration:     { label: 'Iteration',    icon: '↻' },
  collaboration: { label: 'Multi-Agent',  icon: '⊛' },
  flow:          { label: 'Flow',         icon: '◈' },
  other:         { label: 'Other',        icon: '◇' },
};

const NODE_LABELS = {
  start: 'Start', end: 'End', exec: 'Execute', tool: 'Tool',
  llm: 'LLM', code: 'Code', agent: 'Agent', approve: 'Approve',
  condition: 'Condition', router: 'Router', parallel: 'Parallel',
  foreach: 'ForEach', iteration: 'Iteration', subflow: 'Sub-Workflow',
  transform: 'Transform', merge: 'Merge', delay: 'Delay',
  debate: 'Debate', consensus: 'Consensus', supervisor: 'Supervisor',
  http_request: 'HTTP Request', question_classifier: 'Classifier',
  variable_assigner: 'Set Variable', list_operator: 'List Op',
  parameter_extractor: 'Extract Params',
};

const NODE_COLORS = {
  start: '#34d399', end: '#f87171',
  exec: '#fb923c', tool: '#fbbf24', tool_call: '#fbbf24',
  llm: '#818cf8', llm_call: '#818cf8',
  agent: '#ec4899', debate: '#f43f5e', consensus: '#8b5cf6', supervisor: '#10b981',
  code: '#a78bfa',
  approve: '#f472b6', wait_input: '#f472b6',
  condition: '#f87171', router: '#fb7185',
  parallel: '#60a5fa', foreach: '#34d399', iteration: '#34d399',
  subflow: '#c084fc', sub_workflow: '#c084fc',
  transform: '#a78bfa', merge: '#94a3b8',
  delay: '#fcd34d',
  http_request: '#38bdf8', question_classifier: '#fb7185',
  variable_assigner: '#94a3b8', list_operator: '#60a5fa',
  parameter_extractor: '#fbbf24',
};

const NODE_ICONS = {
  start: '\u25B6', end: '\u25A0',
  exec: '\u2699', tool: '\u2692', tool_call: '\u2692',
  llm: '\u2728', llm_call: '\u2728',
  agent: '\u{1F916}', debate: '\u2694', consensus: '\u{1F91D}', supervisor: '\u{1F451}',
  code: '\u{1F4BB}',
  approve: '\u2714', wait_input: '\u2714',
  condition: '\u2747', router: '\u2B95',
  parallel: '\u2261', foreach: '\u21BB', iteration: '\u21BB',
  subflow: '\u25C7', sub_workflow: '\u25C7',
  transform: '\u21C4', merge: '\u222A',
  delay: '\u23F1',
  http_request: '\u{1F310}', question_classifier: '\u{1F3AF}',
  variable_assigner: '\u{1F4DD}', list_operator: '\u{1F4CB}',
  parameter_extractor: '\u{1F50D}',
};

export { NODE_COLORS, NODE_LABELS, NODE_ICONS };

export default {
  name: 'NodePalette',
  emits: ['add-node'],
  setup(_, { emit }) {
    const nodeTypes = ref([]);
    const searchQuery = ref('');
    const collapsedCategories = ref({});

    onMounted(async () => {
      try {
        const data = await API.getNodeTypes();
        nodeTypes.value = data.node_types || [];
      } catch {
        nodeTypes.value = Object.keys(NODE_LABELS).map(t => ({
          type: t,
          category: 'other',
          is_branch: ['condition', 'router', 'question_classifier'].includes(t),
        }));
      }
    });

    const grouped = computed(() => {
      const q = searchQuery.value.toLowerCase();
      const groups = {};
      for (const nt of nodeTypes.value) {
        const label = NODE_LABELS[nt.type] || nt.type;
        if (q && !label.toLowerCase().includes(q) && !nt.type.includes(q)) continue;
        const cat = nt.category || 'other';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(nt);
      }
      return groups;
    });

    function onDragStart(event, nodeType) {
      event.dataTransfer.setData('application/pybot-node', JSON.stringify({
        type: nodeType.type,
        label: NODE_LABELS[nodeType.type] || nodeType.type,
      }));
      event.dataTransfer.effectAllowed = 'move';
    }

    function onClickAdd(nodeType) {
      emit('add-node', {
        type: nodeType.type,
        label: NODE_LABELS[nodeType.type] || nodeType.type,
      });
    }

    function toggleCategory(cat) {
      collapsedCategories.value[cat] = !collapsedCategories.value[cat];
    }

    function catMeta(cat) {
      return CATEGORY_META[cat] || CATEGORY_META.other;
    }

    function nodeColor(type) {
      return NODE_COLORS[type] || '#64748b';
    }

    function nodeLabel(type) {
      return NODE_LABELS[type] || type;
    }

    function nodeIcon(type) {
      return NODE_ICONS[type] || '\u25C6';
    }

    return { grouped, searchQuery, collapsedCategories, onDragStart, onClickAdd, toggleCategory, catMeta, nodeColor, nodeLabel, nodeIcon };
  },
  template: `
    <div class="wb-palette">
      <div class="wb-palette-header">
        <span class="wb-palette-title">Nodes</span>
      </div>
      <div class="wb-palette-search">
        <input v-model="searchQuery" class="mx-input mx-input--sm" placeholder="Search nodes..." />
      </div>
      <div class="wb-palette-list">
        <div v-for="(items, cat) in grouped" :key="cat" class="wb-palette-group">
          <div class="wb-palette-group-header" @click="toggleCategory(cat)">
            <span class="wb-palette-group-icon">{{ catMeta(cat).icon }}</span>
            <span class="wb-palette-group-label">{{ catMeta(cat).label }}</span>
            <span class="wb-palette-group-count">{{ items.length }}</span>
            <span class="wb-palette-group-chevron" :class="{ collapsed: collapsedCategories[cat] }">&#x25BE;</span>
          </div>
          <div v-show="!collapsedCategories[cat]" class="wb-palette-group-items">
            <div
              v-for="nt in items" :key="nt.type"
              class="wb-palette-item"
              draggable="true"
              @dragstart="onDragStart($event, nt)"
              @click="onClickAdd(nt)"
              :title="'Drag or click to add: ' + (nt.type)"
            >
              <span class="wb-palette-item-icon" :style="{ background: nodeColor(nt.type) }">{{ nodeIcon(nt.type) }}</span>
              <span class="wb-palette-item-label">{{ nodeLabel(nt.type) }}</span>
              <span v-if="nt.is_branch" class="wb-palette-item-badge">branch</span>
            </div>
          </div>
        </div>
        <div v-if="Object.keys(grouped).length === 0" class="wb-palette-empty">
          No matching nodes
        </div>
      </div>
    </div>
  `
};
