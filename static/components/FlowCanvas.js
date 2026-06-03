import { h, defineComponent, markRaw, computed } from 'vue';
import { VueFlow, Handle, Position, applyNodeChanges, applyEdgeChanges } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { MiniMap } from '@vue-flow/minimap';
import { NODE_COLORS, NODE_ICONS } from '/static/components/NodePalette.js';

function nodeColor(type) {
  return NODE_COLORS[type] || '#64748b';
}

const STATUS_STYLES = {
  completed: { icon: '\u2713', color: '#10b981', bg: 'rgba(16,185,129,0.15)' },
  failed:    { icon: '\u2717', color: '#ef4444', bg: 'rgba(239,68,68,0.15)' },
  running:   { icon: '\u25CF', color: '#6366f1', bg: 'rgba(99,102,241,0.15)' },
  skipped:   { icon: '\u2192', color: '#6b7280', bg: 'rgba(107,114,128,0.15)' },
  waiting:   { icon: '\u23F8', color: '#f59e0b', bg: 'rgba(245,158,11,0.15)' },
};

const PyBotNode = defineComponent({
  name: 'PyBotNode',
  inheritAttrs: false,
  props: {
    id: String,
    data: Object,
    selected: Boolean,
    sourcePosition: { type: String, default: 'bottom' },
    targetPosition: { type: String, default: 'top' },
  },
  setup(props) {
    return () => {
      const d = props.data || {};
      const c = nodeColor(d.nodeType || 'exec');
      const isStart = d.nodeType === 'start';
      const isEnd = d.nodeType === 'end';
      const isTerminal = isStart || isEnd;
      const icon = NODE_ICONS[d.nodeType] || '\u25C6';
      const status = d.status;
      const statusStyle = STATUS_STYLES[status];
      const hasErrorOutput = d.config?.on_error === 'continue_error';
      const elapsed = d.elapsed;

      const handles = [];
      if (!isStart) {
        handles.push(h(Handle, { type: 'target', position: Position.Top, id: 'target' }));
      }
      if (!isEnd) {
        handles.push(h(Handle, { type: 'source', position: Position.Bottom, id: 'source' }));
      }
      if (hasErrorOutput && !isEnd) {
        handles.push(h(Handle, {
          type: 'source', position: Position.Right, id: 'error',
          style: { background: '#ef4444', right: '-5px', top: '50%' },
        }));
      }

      const children = [
        ...handles,
        h('div', {
          class: 'pf-node-icon',
          style: { background: c },
        }, icon),
        h('div', { class: 'pf-node-info' }, [
          h('div', { class: 'pf-node-label' }, d.label || props.id),
          h('div', { class: 'pf-node-type' }, d.nodeType || 'exec'),
        ]),
      ];

      if (statusStyle) {
        children.push(h('div', {
          class: 'pf-node-status',
          style: { color: statusStyle.color, background: statusStyle.bg },
        }, [
          h('span', { class: 'pf-node-status-icon' }, statusStyle.icon),
          elapsed ? h('span', { class: 'pf-node-status-time' }, elapsed) : null,
        ]));
      }

      if (d.config?.max_retries > 0) {
        children.push(h('div', { class: 'pf-node-badge', title: `Retry: ${d.config.max_retries}x` }, '\u21BB'));
      }

      return h('div', {
        class: [
          'pf-node',
          isTerminal ? 'pf-node--terminal' : '',
          isStart ? 'pf-node--start' : '',
          isEnd ? 'pf-node--end' : '',
          props.selected ? 'pf-node--selected' : '',
          status === 'failed' ? 'pf-node--error' : '',
          status === 'running' ? 'pf-node--running' : '',
        ].filter(Boolean).join(' '),
      }, children);
    };
  },
});

export const nodeTypes = markRaw({ pybot: PyBotNode });

export function minimapNodeColor(node) {
  return nodeColor(node.data?.nodeType || 'exec');
}

export default {
  name: 'FlowCanvas',
  props: {
    flowId: { type: String, default: 'builder-flow' },
    nodes: { type: Array, default: () => [] },
    edges: { type: Array, default: () => [] },
    showEmptyHint: { type: Boolean, default: false },
  },
  emits: [
    'update:nodes', 'update:edges',
    'node-click', 'pane-click', 'pane-dblclick', 'connect', 'drop-node',
    'fit-view', 'auto-layout', 'zoom-in', 'zoom-out',
  ],
  components: { VueFlow, Background, Controls, MiniMap },
  setup(props, { emit }) {
    const nodeCount = computed(() => props.nodes.length);

    function onNodesChange(changes) {
      emit('update:nodes', applyNodeChanges(changes, props.nodes));
    }
    function onEdgesChange(changes) {
      emit('update:edges', applyEdgeChanges(changes, props.edges));
    }
    function onNodeClick(event) {
      emit('node-click', event.node);
    }
    function onPaneClick(event) {
      emit('pane-click', event);
    }
    function onPaneDblClick(event) {
      emit('pane-dblclick', event);
    }
    function onConnect(params) {
      emit('connect', params);
    }
    function onDragOver(event) {
      event.preventDefault();
      event.dataTransfer.dropEffect = 'move';
    }
    function onDrop(event) {
      event.preventDefault();
      const raw = event.dataTransfer.getData('application/pybot-node');
      if (!raw) return;
      try {
        const data = JSON.parse(raw);
        emit('drop-node', {
          ...data,
          clientX: event.clientX,
          clientY: event.clientY,
        });
      } catch { /* ignore */ }
    }
    return {
      onNodesChange, onEdgesChange, onNodeClick, onPaneClick, onPaneDblClick,
      onConnect, onDragOver, onDrop, nodeTypes, minimapNodeColor, nodeCount,
    };
  },
  template: `
    <div class="wb-canvas-container" @dragover="onDragOver" @drop="onDrop">
      <div class="wb-canvas-toolbar">
        <button type="button" class="wb-canvas-tool" title="适应画布" @click="$emit('fit-view')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/></svg>
          适应
        </button>
        <button type="button" class="wb-canvas-tool" title="自动布局" @click="$emit('auto-layout')">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>
          布局
        </button>
        <span class="wb-canvas-tool-sep"></span>
        <button type="button" class="wb-canvas-tool" title="缩小" @click="$emit('zoom-out')">−</button>
        <button type="button" class="wb-canvas-tool" title="放大" @click="$emit('zoom-in')">+</button>
      </div>

      <div v-if="showEmptyHint" class="wb-canvas-empty">
        <div class="wb-canvas-empty-card">
          <div class="wb-canvas-empty-title">从左侧拖入节点，或双击画布快速添加</div>
          <div class="wb-canvas-empty-hint">滚轮缩放 · 拖拽平移 · 从节点锚点拖线连接</div>
        </div>
      </div>

      <VueFlow
        :id="flowId"
        :nodes="nodes"
        :edges="edges"
        :node-types="nodeTypes"
        :default-edge-options="{ type: 'smoothstep', animated: true }"
        :snap-to-grid="true"
        :snap-grid="[20, 20]"
        :min-zoom="0.15"
        :max-zoom="2.5"
        :fit-view-on-init="true"
        :delete-key-code="'Delete'"
        :pan-on-scroll="true"
        :zoom-on-scroll="true"
        :zoom-on-pinch="true"
        :pan-on-drag="true"
        :selection-on-drag="false"
        :elevate-nodes-on-select="true"
        :nodes-draggable="true"
        :nodes-connectable="true"
        :elements-selectable="true"
        @nodes-change="onNodesChange"
        @edges-change="onEdgesChange"
        @node-click="onNodeClick"
        @pane-click="onPaneClick"
        @pane-double-click="onPaneDblClick"
        @connect="onConnect"
      >
        <Background variant="dots" :gap="24" :size="1.2" />
        <Controls position="bottom-right" />
        <MiniMap position="bottom-left" :pannable="true" :zoomable="true" :node-color="minimapNodeColor" />
      </VueFlow>
    </div>
  `
};
