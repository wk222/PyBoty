import { ref, computed, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t, locale } from '/static/i18n.js';
import HelpTip from '/static/components/HelpTip.js';

const NODE_COLORS = {
  app: '#818cf8',
  workflow: '#60a5fa',
  agent: '#34d399',
  tool: '#fbbf24',
  external: '#f472b6',
};
const NODE_ICONS = {
  app: '📱', workflow: '⚡', agent: '🤖', tool: '🔧', external: '🌐',
};
const STATUS_COLORS = {
  active: '#10b981', inactive: '#6b7280', error: '#ef4444', pending: '#f59e0b',
};

export default {
  name: 'AppMatrixView',
  components: { HelpTip },
  setup() {
    const loading = ref(true);
    const topology = ref({ nodes: [], edges: [], pipelines: [], stats: {}, data_bus: null });
    const selectedNode = ref(null);
    const showAddNode = ref(false);
    const newNode = ref({ name: '', node_type: 'app', description: '', domain: '' });

    async function loadTopology() {
      loading.value = true;
      try {
        topology.value = await API.getAppMatrixTopology();
      } catch (e) {
        toast('Failed to load topology: ' + e.message, 'error');
      }
      loading.value = false;
    }

    const nodesByType = computed(() => {
      const groups = {};
      for (const node of topology.value.nodes) {
        const t = node.node_type || 'app';
        if (!groups[t]) groups[t] = [];
        groups[t].push(node);
      }
      return groups;
    });

    function selectNode(node) {
      selectedNode.value = selectedNode.value?.node_id === node.node_id ? null : node;
    }

    function getNodeEdges(nodeId) {
      return topology.value.edges.filter(
        e => e.source_node === nodeId || e.target_node === nodeId
      );
    }

    async function addNode() {
      if (!newNode.value.name.trim()) return;
      try {
        const res = await API.registerAppMatrixNode(newNode.value);
        if (res.success) {
          toast('Node registered', 'success');
          showAddNode.value = false;
          newNode.value = { name: '', node_type: 'app', description: '', domain: '' };
          await loadTopology();
        } else {
          toast('Failed: ' + res.error, 'error');
        }
      } catch (e) {
        toast('Error: ' + e.message, 'error');
      }
    }

    onMounted(loadTopology);

    return {
      loading, topology, selectedNode, showAddNode, newNode,
      nodesByType, selectNode, getNodeEdges, addNode, loadTopology,
      NODE_COLORS, NODE_ICONS, STATUS_COLORS,
    };
  },
  template: `
<div class="am-view">
  <div class="am-header">
    <h2 class="am-title">
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
        <rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/>
        <rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/>
      </svg>
      App Matrix
    </h2>
    <div class="am-actions">
      <button class="am-btn" @click="showAddNode = !showAddNode">+ Add Node</button>
      <button class="am-btn am-btn--ghost" @click="loadTopology" :disabled="loading">Refresh</button>
    </div>
  </div>

  <div v-if="loading" class="am-loading">Loading topology...</div>

  <template v-else>
    <!-- Stats Bar -->
    <div class="am-stats">
      <div class="am-stat-chip"><span class="am-stat-n">{{ topology.stats.total_nodes || 0 }}</span> nodes</div>
      <div class="am-stat-chip"><span class="am-stat-n">{{ topology.stats.total_edges || 0 }}</span> bindings</div>
      <div class="am-stat-chip"><span class="am-stat-n">{{ topology.stats.total_pipelines || 0 }}</span> pipelines</div>
      <div class="am-stat-chip" v-if="topology.data_bus">
        <span class="am-stat-n">{{ topology.data_bus.channel_count || 0 }}</span> data channels
      </div>
    </div>

    <!-- Add Node Form -->
    <div v-if="showAddNode" class="am-add-form">
      <div class="am-form-row">
        <input v-model="newNode.name" placeholder="Node name" class="am-input" />
        <select v-model="newNode.node_type" class="am-input" style="width:120px;">
          <option value="app">App</option>
          <option value="workflow">Workflow</option>
          <option value="agent">Agent</option>
          <option value="tool">Tool</option>
          <option value="external">External</option>
        </select>
        <input v-model="newNode.domain" placeholder="Domain" class="am-input" style="width:120px;" />
      </div>
      <div class="am-form-row">
        <input v-model="newNode.description" placeholder="Description" class="am-input" style="flex:1;" />
        <button class="am-btn" @click="addNode">Register</button>
        <button class="am-btn am-btn--ghost" @click="showAddNode = false">Cancel</button>
      </div>
    </div>

    <!-- Topology Graph -->
    <div class="am-topology">
      <div v-if="topology.nodes.length === 0" class="am-empty">
        <p>No nodes in the App Matrix yet.</p>
        <p style="color:var(--text-muted);font-size:.85rem;">
          Register apps, agents, workflows, and tools as nodes, then create data bindings between them to form an orchestration topology.
        </p>
      </div>

      <div v-for="(nodes, type) in nodesByType" :key="type" class="am-type-group">
        <div class="am-type-header">
          <span class="am-type-icon">{{ NODE_ICONS[type] || '📦' }}</span>
          <span class="am-type-label">{{ type }}</span>
          <span class="am-type-count">{{ nodes.length }}</span>
        </div>
        <div class="am-node-grid">
          <div v-for="node in nodes" :key="node.node_id"
            :class="['am-node', selectedNode?.node_id === node.node_id && 'am-node--selected']"
            :style="{ borderColor: NODE_COLORS[type] + '60' }"
            @click="selectNode(node)">
            <div class="am-node-status" :style="{ background: STATUS_COLORS[node.status] || '#6b7280' }"></div>
            <div class="am-node-name">{{ node.name }}</div>
            <div class="am-node-domain" v-if="node.domain">{{ node.domain }}</div>
            <div class="am-node-desc" v-if="node.description">{{ node.description }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Selected Node Detail -->
    <div v-if="selectedNode" class="am-detail">
      <h3 class="am-detail-title">{{ NODE_ICONS[selectedNode.node_type] }} {{ selectedNode.name }}</h3>
      <div class="am-detail-meta">
        <span>Type: {{ selectedNode.node_type }}</span>
        <span>Status: {{ selectedNode.status }}</span>
        <span v-if="selectedNode.domain">Domain: {{ selectedNode.domain }}</span>
      </div>
      <p v-if="selectedNode.description" class="am-detail-desc">{{ selectedNode.description }}</p>

      <div v-if="getNodeEdges(selectedNode.node_id).length" class="am-detail-edges">
        <h4>Data Bindings</h4>
        <div v-for="(edge, i) in getNodeEdges(selectedNode.node_id)" :key="i" class="am-edge-item">
          <span>{{ edge.source_node }}</span>
          <span class="am-edge-arrow">→</span>
          <span>{{ edge.target_node }}</span>
          <span v-if="edge.description" class="am-edge-desc">{{ edge.description }}</span>
        </div>
      </div>
    </div>

    <!-- Pipelines -->
    <div v-if="topology.pipelines.length" class="am-pipelines">
      <h3 class="am-section-title">Pipelines</h3>
      <div v-for="pipe in topology.pipelines" :key="pipe.name" class="am-pipeline-card">
        <div class="am-pipeline-name">{{ pipe.name }}</div>
        <div class="am-pipeline-steps">
          <span v-for="(step, i) in pipe.steps" :key="i" class="am-pipeline-step">
            {{ step }}
            <span v-if="i < pipe.steps.length - 1" class="am-pipeline-arrow">→</span>
          </span>
        </div>
        <div v-if="pipe.description" class="am-pipeline-desc">{{ pipe.description }}</div>
      </div>
    </div>

    <!-- Data Bus Channels -->
    <div v-if="topology.data_bus && topology.data_bus.channels?.length" class="am-data-bus">
      <h3 class="am-section-title">Shared Data Channels</h3>
      <div v-for="ch in topology.data_bus.channels" :key="ch.name" class="am-channel-card">
        <div class="am-channel-name">{{ ch.name }}</div>
        <div class="am-channel-meta">
          <span>{{ ch.entry_count }} entries</span>
          <span>{{ ch.subscriber_count }} subscribers</span>
        </div>
        <div v-if="ch.description" class="am-channel-desc">{{ ch.description }}</div>
      </div>
    </div>
  </template>
</div>
  `
};
