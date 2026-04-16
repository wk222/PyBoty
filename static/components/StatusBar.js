import { ref, onMounted, onUnmounted } from 'vue';
import { API } from '/static/api/index.js';
import { t, locale } from '/static/i18n.js';

export default {
  name: 'StatusBar',
  setup() {
    const mode = ref('');
    const canvas = ref('');
    const cost = ref(0);
    const tokens = ref(0);
    const llmCalls = ref(0);
    const connected = ref(true);
    let interval = null;

    async function refresh() {
      try {
        const [modesRes, costRes] = await Promise.allSettled([
          API.getSystemModes(),
          API.getCostStats(),
        ]);
        if (modesRes.status === 'fulfilled' && modesRes.value?.current) {
          mode.value = modesRes.value.current.name || '';
        }
        if (costRes.status === 'fulfilled' && costRes.value) {
          cost.value = costRes.value.total_cost_usd || 0;
          tokens.value = costRes.value.total_tokens || 0;
          llmCalls.value = costRes.value.total_llm_calls || 0;
        }
        connected.value = true;
      } catch (_) {
        connected.value = false;
      }
    }

    function modeLabel() {
      if (!mode.value) return '...';
      return t('modes.' + mode.value);
    }

    function shortcutHint() {
      return locale.value === 'zh' ? 'Ctrl+K 命令面板' : 'Ctrl+K Command Palette';
    }

    onMounted(() => {
      refresh();
      interval = setInterval(refresh, 30000);
    });
    onUnmounted(() => {
      clearInterval(interval);
    });

    return { mode, canvas, cost, tokens, llmCalls, connected, modeLabel, shortcutHint };
  },
  template: `
<div class="status-bar">
  <div class="status-bar-left">
    <span class="status-item" :class="connected ? 'status-ok' : 'status-err'">
      <span class="status-dot"></span>
      {{ connected ? 'Connected' : 'Disconnected' }}
    </span>
    <span class="status-sep">|</span>
    <span class="status-item">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
      {{ modeLabel() }}
    </span>
  </div>
  <div class="status-bar-center">
    <span class="status-item status-hint">{{ shortcutHint() }}</span>
  </div>
  <div class="status-bar-right">
    <span class="status-item" v-if="tokens > 0">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
      \${{ cost.toFixed(4) }}
    </span>
    <span class="status-item" v-if="tokens > 0">
      {{ tokens.toLocaleString() }} tokens
    </span>
    <span class="status-item" v-if="llmCalls > 0">
      {{ llmCalls }} calls
    </span>
  </div>
</div>
  `
};
