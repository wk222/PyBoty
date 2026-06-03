import { ref, nextTick, onMounted, onUnmounted } from 'vue';
import { t } from '/static/i18n.js';

export default {
  name: 'CliView',
  setup() {
    const inputVal = ref('');
    const history = ref([
      { type: 'system', text: 'PyBot Web Terminal — type /help for commands' },
    ]);
    const cmdHistory = ref([]);
    const historyIndex = ref(-1);
    const inputRef = ref(null);
    const scrollRef = ref(null);
    const executing = ref(false);

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
      nextTick(() => inputRef.value?.focus());
    });

    return { inputVal, history, executing, inputRef, scrollRef, handleInputKey, t };
  },
  template: `
<div class="cli-view">
  <div class="cli-view-header">
    <div class="cli-view-title">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
      </svg>
      <span>PyBot Terminal</span>
    </div>
    <div class="cli-view-hint">{{ t('cli.pageHint') || 'Execute commands, query agents, manage workflows' }}</div>
  </div>
  <div class="cli-view-terminal" ref="scrollRef">
    <div v-for="(line, i) in history" :key="i" :class="'cli-view-line cli-view-line--' + line.type">
      <span v-if="line.type === 'input'" class="cli-view-prompt">$ </span>
      <pre class="cli-view-text">{{ line.text }}</pre>
    </div>
    <div v-if="executing" class="cli-view-line cli-view-line--system">
      <span class="cli-view-spinner"></span> Executing...
    </div>
  </div>
  <div class="cli-view-input-row">
    <span class="cli-view-prompt-sign">$</span>
    <input ref="inputRef" v-model="inputVal" @keydown="handleInputKey"
      class="cli-view-input" :placeholder="t('cli.placeholder') || 'Type a command...'"
      :disabled="executing" autocomplete="off" spellcheck="false" />
  </div>
</div>
  `
};
