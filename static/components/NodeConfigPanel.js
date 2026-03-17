import { ref, computed, watch } from 'vue';

const FIELD_DEFS = {
  start:               [{ key: 'description', label: 'Description', type: 'text' }],
  end:                 [{ key: 'description', label: 'Description', type: 'text' }],
  llm:                 [
    { key: 'prompt', label: 'Prompt', type: 'textarea' },
    { key: 'model', label: 'Model', type: 'text', placeholder: 'gpt-4o' },
    { key: 'temperature', label: 'Temperature', type: 'number', min: 0, max: 2, step: 0.1 },
    { key: 'output_var', label: 'Output Variable', type: 'text', placeholder: 'result' },
  ],
  code:                [
    { key: 'language', label: 'Language', type: 'select', options: ['python', 'javascript'] },
    { key: 'source', label: 'Source Code', type: 'code' },
    { key: 'output_var', label: 'Output Variable', type: 'text' },
  ],
  agent:               [
    { key: 'agent_name', label: 'Agent Name', type: 'text' },
    { key: 'prompt', label: 'Instructions', type: 'textarea' },
    { key: 'tools', label: 'Tools (comma-sep)', type: 'text' },
  ],
  tool:                [
    { key: 'tool_name', label: 'Tool Name', type: 'text' },
    { key: 'args', label: 'Arguments (JSON)', type: 'textarea', placeholder: '{}' },
  ],
  exec:                [
    { key: 'command', label: 'Command', type: 'textarea' },
  ],
  http_request:        [
    { key: 'method', label: 'Method', type: 'select', options: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'] },
    { key: 'url', label: 'URL', type: 'text', placeholder: 'https://api.example.com/...' },
    { key: 'headers', label: 'Headers (JSON)', type: 'textarea', placeholder: '{}' },
    { key: 'body', label: 'Body', type: 'textarea' },
    { key: 'output_var', label: 'Output Variable', type: 'text' },
  ],
  condition:           [
    { key: 'expression', label: 'Condition Expression', type: 'textarea', placeholder: '${var} == "value"' },
    { key: 'branches', label: 'Branches (JSON array)', type: 'textarea', placeholder: '["true", "false"]' },
  ],
  router:              [
    { key: 'routes', label: 'Routes (JSON)', type: 'textarea', placeholder: '[{"condition":"...", "target":"..."}]' },
  ],
  question_classifier: [
    { key: 'query', label: 'Query Variable', type: 'text' },
    { key: 'categories', label: 'Categories (JSON)', type: 'textarea', placeholder: '["cat1", "cat2"]' },
  ],
  parallel:            [
    { key: 'description', label: 'Description', type: 'text' },
  ],
  foreach:             [
    { key: 'collection_var', label: 'Collection Variable', type: 'text' },
    { key: 'item_var', label: 'Item Variable', type: 'text', placeholder: 'item' },
  ],
  iteration:           [
    { key: 'collection_var', label: 'Collection Variable', type: 'text' },
    { key: 'item_var', label: 'Item Variable', type: 'text', placeholder: 'item' },
    { key: 'max_iterations', label: 'Max Iterations', type: 'number' },
  ],
  subflow:             [
    { key: 'workflow_name', label: 'Sub-Workflow Name', type: 'text' },
    { key: 'input_vars', label: 'Input Variables (JSON)', type: 'textarea', placeholder: '{}' },
  ],
  transform:           [
    { key: 'expression', label: 'Transform Expression', type: 'textarea' },
    { key: 'output_var', label: 'Output Variable', type: 'text' },
  ],
  merge:               [
    { key: 'strategy', label: 'Merge Strategy', type: 'select', options: ['all', 'any', 'first'] },
  ],
  delay:               [
    { key: 'seconds', label: 'Delay (seconds)', type: 'number', min: 0 },
  ],
  approve:             [
    { key: 'approver', label: 'Approver', type: 'text' },
    { key: 'message', label: 'Approval Message', type: 'textarea' },
  ],
  debate:              [
    { key: 'agents', label: 'Agents (comma-sep)', type: 'text' },
    { key: 'rounds', label: 'Rounds', type: 'number', min: 1, max: 10 },
    { key: 'topic', label: 'Topic', type: 'textarea' },
  ],
  consensus:           [
    { key: 'agents', label: 'Agents (comma-sep)', type: 'text' },
    { key: 'topic', label: 'Topic', type: 'textarea' },
  ],
  supervisor:          [
    { key: 'supervisor_agent', label: 'Supervisor Agent', type: 'text' },
    { key: 'worker_agents', label: 'Worker Agents (comma-sep)', type: 'text' },
    { key: 'task', label: 'Task', type: 'textarea' },
  ],
  variable_assigner:   [
    { key: 'assignments', label: 'Assignments (JSON)', type: 'textarea', placeholder: '{"var_name": "value"}' },
  ],
  list_operator:       [
    { key: 'operation', label: 'Operation', type: 'select', options: ['filter', 'sort', 'map', 'reduce', 'slice', 'unique', 'flatten', 'reverse'] },
    { key: 'input_var', label: 'Input Variable', type: 'text' },
    { key: 'expression', label: 'Expression', type: 'textarea' },
    { key: 'output_var', label: 'Output Variable', type: 'text' },
  ],
  parameter_extractor: [
    { key: 'input_var', label: 'Input Variable', type: 'text' },
    { key: 'parameters', label: 'Parameters (JSON)', type: 'textarea', placeholder: '[{"name":"...", "type":"string", "description":"..."}]' },
  ],
};

export default {
  name: 'NodeConfigPanel',
  props: {
    node: { type: Object, default: null },
  },
  emits: ['update-node', 'delete-node', 'close'],
  setup(props, { emit }) {
    const localConfig = ref({});

    watch(() => props.node, (n) => {
      if (n && n.data) {
        localConfig.value = { ...n.data.config || {} };
      } else {
        localConfig.value = {};
      }
    }, { immediate: true, deep: true });

    const fields = computed(() => {
      if (!props.node) return [];
      const nodeType = props.node.data?.nodeType || 'exec';
      return FIELD_DEFS[nodeType] || [{ key: 'description', label: 'Description', type: 'text' }];
    });

    function updateField(key, value) {
      localConfig.value[key] = value;
      emitUpdate();
    }

    function updateLabel(value) {
      emitUpdate(value);
    }

    function emitUpdate(newLabel) {
      emit('update-node', {
        id: props.node.id,
        label: newLabel !== undefined ? newLabel : props.node.data?.label,
        config: { ...localConfig.value },
      });
    }

    function deleteNode() {
      emit('delete-node', props.node.id);
    }

    function close() {
      emit('close');
    }

    return { localConfig, fields, updateField, updateLabel, deleteNode, close };
  },
  template: `
    <div class="wb-config-panel" v-if="node">
      <div class="wb-config-header">
        <span class="wb-config-title">Node Config</span>
        <button class="wb-config-close" @click="close">&times;</button>
      </div>
      <div class="wb-config-body">
        <div class="wb-config-field">
          <label class="wb-config-label">ID</label>
          <input class="mx-input mx-input--sm" :value="node.id" disabled />
        </div>
        <div class="wb-config-field">
          <label class="wb-config-label">Type</label>
          <input class="mx-input mx-input--sm" :value="node.data?.nodeType" disabled />
        </div>
        <div class="wb-config-field">
          <label class="wb-config-label">Label</label>
          <input
            class="mx-input mx-input--sm"
            :value="node.data?.label || ''"
            @input="updateLabel($event.target.value)"
          />
        </div>
        <hr class="wb-config-divider" />
        <div v-for="f in fields" :key="f.key" class="wb-config-field">
          <label class="wb-config-label">{{ f.label }}</label>
          <input
            v-if="f.type === 'text'"
            class="mx-input mx-input--sm"
            :value="localConfig[f.key] || ''"
            :placeholder="f.placeholder || ''"
            @input="updateField(f.key, $event.target.value)"
          />
          <input
            v-else-if="f.type === 'number'"
            class="mx-input mx-input--sm"
            type="number"
            :value="localConfig[f.key] ?? ''"
            :min="f.min" :max="f.max" :step="f.step || 1"
            @input="updateField(f.key, parseFloat($event.target.value) || 0)"
          />
          <select
            v-else-if="f.type === 'select'"
            class="mx-input mx-input--sm"
            :value="localConfig[f.key] || f.options?.[0] || ''"
            @change="updateField(f.key, $event.target.value)"
          >
            <option v-for="opt in f.options" :key="opt" :value="opt">{{ opt }}</option>
          </select>
          <textarea
            v-else-if="f.type === 'textarea' || f.type === 'code'"
            class="mx-textarea"
            :class="{ 'wb-code-area': f.type === 'code' }"
            rows="4"
            :value="localConfig[f.key] || ''"
            :placeholder="f.placeholder || ''"
            @input="updateField(f.key, $event.target.value)"
            spellcheck="false"
          ></textarea>
        </div>
      </div>
      <div class="wb-config-footer">
        <button class="mx-btn mx-btn--sm" style="color:var(--error)" @click="deleteNode">Delete Node</button>
      </div>
    </div>
    <div class="wb-config-panel wb-config-panel--empty" v-else>
      <div class="wb-config-empty-msg">
        <p>Click a node to configure</p>
        <p style="font-size:11px;color:var(--text-muted)">Or drag nodes from the left palette onto the canvas</p>
      </div>
    </div>
  `
};
