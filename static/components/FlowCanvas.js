import { h, defineComponent, markRaw } from 'vue';
import { VueFlow, Handle, Position } from '@vue-flow/core';
import { Background } from '@vue-flow/background';
import { Controls } from '@vue-flow/controls';
import { MiniMap } from '@vue-flow/minimap';
import { NODE_COLORS } from '/static/components/NodePalette.js';

function nodeColor(type) {
  return NODE_COLORS[type] || '#64748b';
}

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

      const handles = [];
      if (!isStart) {
        handles.push(h(Handle, { type: 'target', position: Position.Top }));
      }
      if (!isEnd) {
        handles.push(h(Handle, { type: 'source', position: Position.Bottom }));
      }

      return h('div', {
        class: ['pybot-node-wrapper', props.selected ? 'pybot-node--selected' : ''],
      }, [
        ...handles,
        h('div', { class: 'pybot-node-body', style: `border-color:${c}` }, [
          h('div', { class: 'pybot-node-color-bar', style: `background:${c}` }),
          h('div', { class: 'pybot-node-content' }, [
            h('div', { class: 'pybot-node-label' }, d.label || props.id),
            h('div', { class: 'pybot-node-type' }, d.nodeType || 'exec'),
          ]),
        ]),
      ]);
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
        :snap-grid="[15, 15]"
        :min-zoom="0.2"
        :max-zoom="3"
        :fit-view-on-init="true"
        :delete-key-code="'Delete'"
        @node-click="onNodeClick"
        @pane-click="onPaneClick"
        @connect="onConnect"
      >
        <Background :gap="20" :size="1" />
        <Controls position="bottom-right" />
        <MiniMap position="bottom-left" :pannable="true" :zoomable="true" :node-color="minimapNodeColor" />
      </VueFlow>
    </div>
  `
};
