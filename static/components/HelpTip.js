import { ref, computed, onMounted } from 'vue';
import { locale } from '/static/i18n.js';

const STORAGE_KEY = 'pybot_help_dismissed';

const TIPS = {
  en: {
    chat: { title: 'Chat Tips', items: [
      'Type "/" to open the slash command menu',
      'Switch mode with the pill button in the header',
      'Click the canvas selector to adjust resource strategy',
      'Shift+Enter for multi-line input',
    ]},
    dashboard: { title: 'Dashboard Tips', items: [
      'Click any stat card to navigate to its manager',
      'Expand LLM Configuration to change models on the fly',
      'Cost tracking updates in real-time',
    ]},
    memory: { title: 'Memory Tips', items: [
      'Memory distills automatically as conversations accumulate',
      'The pipeline shows how conversations flow to long-term memory',
      'Click journal entries to expand and review daily digests',
    ]},
    workflows: { title: 'Workflow Tips', items: [
      'Drag nodes from the palette to build visual pipelines',
      'Use data_source nodes to pull data from APIs or databases',
      'Export/Import JSON to share workflow definitions',
    ]},
    tracing: { title: 'Tracing Tips', items: [
      'Filter events by type using the chips at the top',
      'Auto-refresh keeps the timeline up to date',
      'Click an event to see its full payload',
    ]},
    ecosystem: { title: 'Ecosystem Tips', items: [
      'Use the search bar to filter across all asset types',
      'Click "Open Dedicated Manager" for detailed editing',
      'All assets persist across sessions automatically',
    ]},
    governance: { title: 'Governance Tips', items: [
      'Pending approvals appear when risky tools are invoked',
      'Policy tab lets you configure agent control presets',
      'Gateway tab manages device pairings and channel routes',
    ]},
  },
  zh: {
    chat: { title: '对话提示', items: [
      '输入 "/" 打开斜杠命令菜单',
      '点击头部的模式切换按钮切换运行模式',
      '点击画布选择器调整资源策略',
      'Shift+Enter 多行输入',
    ]},
    dashboard: { title: '仪表盘提示', items: [
      '点击任何统计卡片可跳转到对应管理页',
      '展开 LLM 配置可以实时切换模型',
      '成本追踪实时更新',
    ]},
    memory: { title: '记忆提示', items: [
      '记忆会随着对话积累自动蒸馏',
      '流水线图展示对话如何流转为长期记忆',
      '点击日记条目可展开查看每日摘要',
    ]},
    workflows: { title: '工作流提示', items: [
      '从节点面板拖拽节点构建可视化流水线',
      '使用 data_source 节点从 API 或数据库拉取数据',
      '导出/导入 JSON 分享工作流定义',
    ]},
    tracing: { title: '追踪提示', items: [
      '使用顶部标签按事件类型过滤',
      '自动刷新保持时间线最新',
      '点击事件查看完整载荷',
    ]},
    ecosystem: { title: '生态提示', items: [
      '使用搜索栏跨所有资产类型过滤',
      '点击"打开专门管理页"进行详细编辑',
      '所有资产自动跨会话持久化',
    ]},
    governance: { title: '治理提示', items: [
      '调用高风险工具时会出现待审批项',
      '策略标签页可配置智能体控制预设',
      '网关标签页管理设备配对和渠道路由',
    ]},
  },
};

export default {
  name: 'HelpTip',
  props: {
    page: { type: String, required: true },
  },
  setup(props) {
    const dismissed = ref(false);

    const tip = computed(() => {
      const lang = TIPS[locale.value] || TIPS.en;
      return lang[props.page] || null;
    });

    onMounted(() => {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      if (stored[props.page]) dismissed.value = true;
    });

    function dismiss() {
      dismissed.value = true;
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      stored[props.page] = true;
      localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));
    }

    return { tip, dismissed, dismiss };
  },
  template: `
<div v-if="tip && !dismissed" class="help-tip">
  <div class="help-tip-header">
    <span class="help-tip-icon">💡</span>
    <span class="help-tip-title">{{ tip.title }}</span>
    <button class="help-tip-close" @click="dismiss" title="Dismiss">×</button>
  </div>
  <ul class="help-tip-list">
    <li v-for="(item, i) in tip.items" :key="i">{{ item }}</li>
  </ul>
</div>
  `
};
