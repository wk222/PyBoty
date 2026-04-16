import { ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { useRouter } from 'vue-router';
import { t, locale } from '/static/i18n.js';

const COMMANDS = [
  { id: 'chat', icon: '💬', route: '/chat', category: 'navigate' },
  { id: 'dashboard', icon: '📊', route: '/dashboard', category: 'navigate' },
  { id: 'ecosystem', icon: '📦', route: '/ecosystem', category: 'navigate' },
  { id: 'memory', icon: '🧠', route: '/memory', category: 'navigate' },
  { id: 'tracing', icon: '🔍', route: '/tracing', category: 'navigate' },
  { id: 'workflows', icon: '⚡', route: '/workflows', category: 'navigate' },
  { id: 'workflow_new', icon: '✨', route: '/workflows/builder', category: 'action' },
  { id: 'agents', icon: '🤖', route: '/agents', category: 'navigate' },
  { id: 'tools', icon: '🔧', route: '/tools', category: 'navigate' },
  { id: 'skills', icon: '📚', route: '/skills', category: 'navigate' },
  { id: 'apps', icon: '📱', route: '/apps', category: 'navigate' },
  { id: 'hub', icon: '🏪', route: '/hub', category: 'navigate' },
  { id: 'governance', icon: '🛡️', route: '/governance', category: 'navigate' },
  { id: 'settings', icon: '⚙️', route: '/settings', category: 'navigate' },
  { id: 'system', icon: '🗺️', route: '/system', category: 'navigate' },
  { id: 'debug', icon: '🐛', route: '/debug', category: 'navigate' },
];

export default {
  name: 'CommandPalette',
  setup() {
    const router = useRouter();
    const open = ref(false);
    const query = ref('');
    const selectedIndex = ref(0);
    const inputRef = ref(null);

    function getLabel(cmd) {
      const labels = {
        en: {
          chat: 'Go to Chat', dashboard: 'Open Dashboard', ecosystem: 'Browse Ecosystem',
          memory: 'View Memory System', tracing: 'Open Tracing Timeline',
          workflows: 'Browse Workflows', workflow_new: 'Create New Workflow',
          agents: 'Manage Agents', tools: 'Manage Tools', skills: 'Browse Skills',
          apps: 'Manage Apps', hub: 'Open Hub Marketplace', governance: 'Governance Center',
          settings: 'Open Settings', system: 'System Map', debug: 'Debug Panel',
        },
        zh: {
          chat: '打开对话', dashboard: '打开仪表盘', ecosystem: '浏览生态资产',
          memory: '查看记忆系统', tracing: '打开追踪时间线',
          workflows: '浏览工作流', workflow_new: '创建新工作流',
          agents: '管理智能体', tools: '管理工具', skills: '浏览技能',
          apps: '管理应用', hub: '打开市场', governance: '治理中心',
          settings: '打开设置', system: '系统地图', debug: '调试面板',
        },
      };
      return (labels[locale.value] || labels.en)[cmd.id] || cmd.id;
    }

    function getCategoryLabel(cat) {
      if (locale.value === 'zh') {
        return cat === 'navigate' ? '导航' : '操作';
      }
      return cat === 'navigate' ? 'Navigate' : 'Action';
    }

    const filtered = computed(() => {
      const q = query.value.toLowerCase().trim();
      if (!q) return COMMANDS;
      return COMMANDS.filter(cmd => {
        const label = getLabel(cmd).toLowerCase();
        return label.includes(q) || cmd.id.includes(q);
      });
    });

    watch(filtered, () => {
      selectedIndex.value = 0;
    });

    function toggle() {
      open.value = !open.value;
      if (open.value) {
        query.value = '';
        selectedIndex.value = 0;
        setTimeout(() => inputRef.value?.focus(), 50);
      }
    }

    function handleKeydown(e) {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        toggle();
        return;
      }
      if (e.key === 'Escape' && open.value) {
        open.value = false;
        return;
      }
      if (!open.value) return;

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        selectedIndex.value = Math.min(selectedIndex.value + 1, filtered.value.length - 1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        selectedIndex.value = Math.max(selectedIndex.value - 1, 0);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        const cmd = filtered.value[selectedIndex.value];
        if (cmd) execute(cmd);
      }
    }

    function execute(cmd) {
      open.value = false;
      router.push(cmd.route);
    }

    onMounted(() => {
      document.addEventListener('keydown', handleKeydown);
    });
    onUnmounted(() => {
      document.removeEventListener('keydown', handleKeydown);
    });

    return { open, query, selectedIndex, filtered, inputRef, toggle, execute, getLabel, getCategoryLabel };
  },
  template: `
<teleport to="body">
  <div v-if="open" class="cmd-overlay" @click.self="open = false">
    <div class="cmd-palette">
      <div class="cmd-input-row">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input ref="inputRef" v-model="query" class="cmd-input"
          :placeholder="'Type a command...'" @keydown.stop />
        <kbd class="cmd-kbd">ESC</kbd>
      </div>
      <div class="cmd-list" v-if="filtered.length">
        <div v-for="(cmd, i) in filtered" :key="cmd.id"
          :class="['cmd-item', i === selectedIndex && 'cmd-item--active']"
          @click="execute(cmd)"
          @mouseenter="selectedIndex = i">
          <span class="cmd-item-icon">{{ cmd.icon }}</span>
          <span class="cmd-item-label">{{ getLabel(cmd) }}</span>
          <span class="cmd-item-cat">{{ getCategoryLabel(cmd.category) }}</span>
        </div>
      </div>
      <div v-else class="cmd-empty">No matching commands</div>
      <div class="cmd-footer">
        <span><kbd>↑↓</kbd> navigate</span>
        <span><kbd>↵</kbd> select</span>
        <span><kbd>esc</kbd> close</span>
      </div>
    </div>
  </div>
</teleport>
  `
};
