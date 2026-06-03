import { ref, nextTick, onMounted, onUnmounted } from 'vue';
import { locale } from '/static/i18n.js';

export default {
  name: 'CliPanel',
  setup() {
    const visible = ref(false);
    const inputVal = ref('');
    const history = ref([
      { type: 'system', text: 'PyBot Web Terminal — type /help for commands' },
    ]);
    const cmdHistory = ref([]);
    const historyIndex = ref(-1);
    const inputRef = ref(null);
    const scrollRef = ref(null);
    const executing = ref(false);

    function toggle() {
      visible.value = !visible.value;
      if (visible.value) {
        nextTick(() => inputRef.value?.focus());
      }
    }

    function handleKeydown(e) {
      if (e.key === '`' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        toggle();
        return;
      }
    }

    async function execute() {
      const cmd = inputVal.value.trim();
      if (!cmd) return;

      history.value.push({ type: 'input', text: cmd });
      cmdHistory.value.push(cmd);
      historyIndex.value = cmdHistory.value.length;
      inputVal.value = '';
      executing.value = true;

      try {
        const resp = await fetch('/api/cli/execute', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: cmd }),
        });
        const data = await resp.json();

        if (data.type === 'clear') {
          history.value = [{ type: 'system', text: 'Terminal cleared.' }];
        } else if (data.output) {
          history.value.push({ type: data.type || 'response', text: data.output });
        }
      } catch (err) {
        history.value.push({ type: 'error', text: 'Connection error: ' + err.message });
      }

      executing.value = false;
      nextTick(() => {
        if (scrollRef.value) scrollRef.value.scrollTop = scrollRef.value.scrollHeight;
        inputRef.value?.focus();
      });
    }

    function historyUp() {
      if (historyIndex.value > 0) {
        historyIndex.value--;
        inputVal.value = cmdHistory.value[historyIndex.value];
      }
    }
    function historyDown() {
      if (historyIndex.value < cmdHistory.value.length - 1) {
        historyIndex.value++;
        inputVal.value = cmdHistory.value[historyIndex.value];
      } else {
        historyIndex.value = cmdHistory.value.length;
        inputVal.value = '';
      }
    }

    function handleInputKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        execute();
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        historyUp();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        historyDown();
      }
    }

    onMounted(() => {
      document.addEventListener('keydown', handleKeydown);
    });
    onUnmounted(() => {
      document.removeEventListener('keydown', handleKeydown);
    });

    return { visible, inputVal, history, executing, inputRef, scrollRef, toggle, handleInputKey };
  },
  template: `
<div :class="['cli-panel', visible && 'cli-panel--open']">
  <div class="cli-toggle" @click="toggle" title="Terminal (Ctrl+\`)">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
    </svg>
    <span>Terminal</span>
  </div>
  <div v-if="visible" class="cli-body">
    <div class="cli-header">
      <span class="cli-header-title">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
        PyBot Terminal
      </span>
      <span class="cli-header-hint">Ctrl+\` to toggle</span>
      <button class="cli-close" @click="visible = false">&times;</button>
    </div>
    <div class="cli-output" ref="scrollRef">
      <div v-for="(line, i) in history" :key="i" :class="'cli-line cli-line--' + line.type">
        <span v-if="line.type === 'input'" class="cli-prompt">$ </span>
        <span class="cli-text">{{ line.text }}</span>
      </div>
      <div v-if="executing" class="cli-line cli-line--system cli-executing">
        <span class="cli-spinner"></span> Executing...
      </div>
    </div>
    <div class="cli-input-row">
      <span class="cli-prompt-sign">$</span>
      <input ref="inputRef" v-model="inputVal" @keydown="handleInputKey"
        class="cli-input" placeholder="Type a command or message..."
        :disabled="executing" autocomplete="off" spellcheck="false" />
    </div>
  </div>
</div>
  `
};
