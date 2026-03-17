import { h, defineComponent, markRaw } from 'vue';
import { VueFlow, Handle, Position } from '@vue-flow/core';
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
  },
  emits: ['node-click', 'pane-click', 'connect', 'drop-node'],
  components: { VueFlow, Background, Controls, MiniMap },
  setup(props, { emit }) {
    function onNodeClick(event) {
      emit('node-click', event.node);
    }
    function onPaneClick() {
      emit('pane-click');
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
    return { onNodeClick, onPaneClick, onConnect, onDragOver, onDrop, nodeTypes, minimapNodeColor };
  },
  template: `
    <div class="wb-canvas-container" @dragover="onDragOver" @drop="onDrop">
      <VueFlow
        :id="flowId"
        :nodes="nodes"
        :edges="edges"
        :node-types="nodeTypes"
        :default-edge-options="{ type: 'smoothstep', animated: true }"
        :snap-to-grid="true"
        :snap-grid="[20, 20]"
        :min-zoom="0.15"
        :max-zoom="3"
        :fit-view-on-init="true"
        :delete-key-code="'Delete'"
        @node-click="onNodeClick"
        @pane-click="onPaneClick"
        @connect="onConnect"
      >
        <Background variant="dots" :gap="20" :size="1.5" />
        <Controls position="bottom-right" />
        <MiniMap position="bottom-left" :pannable="true" :zoomable="true" :node-color="minimapNodeColor" />
      </VueFlow>
    </div>
  `
};
