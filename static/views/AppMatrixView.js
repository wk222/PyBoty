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
const NODE_TYPE_LABELS = {
  en: { app: 'App', workflow: 'Workflow', agent: 'Agent', tool: 'Tool', external: 'External' },
  zh: { app: '应用', workflow: '工作流', agent: '智能体', tool: '工具', external: '外部服务' },
};
const STATUS_COLORS = {
  active: '#10b981', inactive: '#6b7280', error: '#ef4444', pending: '#f59e0b',
};
const STATUS_LABELS = {
  en: { active: 'Active', inactive: 'Inactive', error: 'Error', pending: 'Pending' },
  zh: { active: '活跃', inactive: '未激活', error: '异常', pending: '待处理' },
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

    function typeLabel(type) {
      const labels = NODE_TYPE_LABELS[locale.value] || NODE_TYPE_LABELS.en;
      return labels[type] || type;
    }
    function statusLabel(status) {
      const labels = STATUS_LABELS[locale.value] || STATUS_LABELS.en;
      return labels[status] || status;
    }

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
        const tp = node.node_type || 'app';
        if (!groups[tp]) groups[tp] = [];
        groups[tp].push(node);
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
          toast(t('appMatrix.register') + ' OK', 'success');
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
      t, typeLabel, statusLabel,
    };
  },
  template: `
<div class="am-view">
  <HelpTip page="appMatrix" />
  <div class="am-header">
    <div>
      <h2 class="am-title">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
          <rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/>
          <rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/>
        </svg>
        {{ t('appMatrix.title') }}
      </h2>
      <p class="am-subtitle">{{ t('appMatrix.subtitle') }}</p>
    </div>
    <div class="am-actions">
      <button class="am-btn" @click="showAddNode = !showAddNode">{{ t('appMatrix.addNode') }}</button>
      <button class="am-btn am-btn--ghost" @click="loadTopology" :disabled="loading">{{ t('appMatrix.refresh') }}</button>
    </div>
  </div>

  <div v-if="loading" class="am-loading">{{ t('appMatrix.loading') }}</div>

  <template v-else>
    <div class="am-stats">
      <div class="am-stat-chip"><span class="am-stat-n">{{ topology.stats.total_nodes || 0 }}</span> {{ t('appMatrix.nodes') }}</div>
      <div class="am-stat-chip"><span class="am-stat-n">{{ topology.stats.total_edges || 0 }}</span> {{ t('appMatrix.bindings') }}</div>
      <div class="am-stat-chip"><span class="am-stat-n">{{ topology.stats.total_pipelines || 0 }}</span> {{ t('appMatrix.pipelines') }}</div>
      <div class="am-stat-chip" v-if="topology.data_bus">
        <span class="am-stat-n">{{ topology.data_bus.channel_count || 0 }}</span> {{ t('appMatrix.dataChannels') }}
      </div>
    </div>

    <div v-if="showAddNode" class="am-add-form">
      <div class="am-form-row">
        <input v-model="newNode.name" :placeholder="t('appMatrix.nodeName')" class="am-input" />
        <select v-model="newNode.node_type" class="am-input" style="width:120px;">
          <option value="app">{{ typeLabel('app') }}</option>
          <option value="workflow">{{ typeLabel('workflow') }}</option>
          <option value="agent">{{ typeLabel('agent') }}</option>
          <option value="tool">{{ typeLabel('tool') }}</option>
          <option value="external">{{ typeLabel('external') }}</option>
        </select>
        <input v-model="newNode.domain" :placeholder="t('appMatrix.domain')" class="am-input" style="width:120px;" />
      </div>
      <div class="am-form-row">
        <input v-model="newNode.description" :placeholder="t('appMatrix.description')" class="am-input" style="flex:1;" />
        <button class="am-btn" @click="addNode">{{ t('appMatrix.register') }}</button>
        <button class="am-btn am-btn--ghost" @click="showAddNode = false">{{ t('appMatrix.cancel') }}</button>
      </div>
    </div>

    <div class="am-topology">
      <div v-if="topology.nodes.length === 0" class="am-empty">
        <div style="font-size:2.5rem;margin-bottom:12px;">🧩</div>
        <p style="font-weight:600;font-size:1.05rem;margin-bottom:8px;">{{ t('appMatrix.emptyTitle') }}</p>
        <p style="color:var(--text-muted);font-size:.85rem;max-width:480px;margin:0 auto;">
          {{ t('appMatrix.emptyHint') }}
        </p>
      </div>

      <div v-for="(nodes, type) in nodesByType" :key="type" class="am-type-group">
        <div class="am-type-header">
          <span class="am-type-icon">{{ NODE_ICONS[type] || '📦' }}</span>
          <span class="am-type-label">{{ typeLabel(type) }}</span>
          <span class="am-type-count">{{ nodes.length }}</span>
        </div>
        <div class="am-node-grid">
          <div v-for="node in nodes" :key="node.node_id"
            :class="['am-node', selectedNode?.node_id === node.node_id && 'am-node--selected']"
            :style="{ borderColor: NODE_COLORS[type] + '60' }"
            @click="selectNode(node)">
            <div class="am-node-status" :style="{ background: STATUS_COLORS[node.status] || '#6b7280' }" :title="statusLabel(node.status)"></div>
            <div class="am-node-name">{{ node.name }}</div>
            <div class="am-node-domain" v-if="node.domain">{{ node.domain }}</div>
            <div class="am-node-desc" v-if="node.description">{{ node.description }}</div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="selectedNode" class="am-detail">
      <h3 class="am-detail-title">{{ NODE_ICONS[selectedNode.node_type] }} {{ selectedNode.name }}</h3>
      <div class="am-detail-meta">
        <span>{{ t('appMatrix.type') }}: {{ typeLabel(selectedNode.node_type) }}</span>
        <span>{{ t('appMatrix.status') }}: {{ statusLabel(selectedNode.status) }}</span>
        <span v-if="selectedNode.domain">{{ t('appMatrix.domain') }}: {{ selectedNode.domain }}</span>
      </div>
      <p v-if="selectedNode.description" class="am-detail-desc">{{ selectedNode.description }}</p>

      <div v-if="getNodeEdges(selectedNode.node_id).length" class="am-detail-edges">
        <h4>{{ t('appMatrix.dataBindings') }}</h4>
        <div v-for="(edge, i) in getNodeEdges(selectedNode.node_id)" :key="i" class="am-edge-item">
          <span>{{ edge.source_node }}</span>
          <span class="am-edge-arrow">→</span>
          <span>{{ edge.target_node }}</span>
          <span v-if="edge.description" class="am-edge-desc">{{ edge.description }}</span>
        </div>
      </div>
    </div>

    <div v-if="topology.pipelines.length" class="am-pipelines">
      <h3 class="am-section-title">{{ t('appMatrix.pipelinesSection') }}</h3>
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

    <div v-if="topology.data_bus && topology.data_bus.channels?.length" class="am-data-bus">
      <h3 class="am-section-title">{{ t('appMatrix.sharedDataChannels') }}</h3>
      <div v-for="ch in topology.data_bus.channels" :key="ch.name" class="am-channel-card">
        <div class="am-channel-name">{{ ch.name }}</div>
        <div class="am-channel-meta">
          <span>{{ ch.entry_count }} {{ t('appMatrix.entries') }}</span>
          <span>{{ ch.subscriber_count }} {{ t('appMatrix.subscribers') }}</span>
        </div>
        <div v-if="ch.description" class="am-channel-desc">{{ ch.description }}</div>
      </div>
    </div>
  </template>
</div>
  `
};
