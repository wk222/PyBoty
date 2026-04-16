import { ref, computed, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { locale } from '/static/i18n.js';

const STORAGE_KEY = 'pybot_onboarding_done';

const STEPS = {
  en: [
    {
      title: 'Welcome to PyBot',
      body: 'PyBot is an agent runtime that improves over time. Tools, skills, workflows, and applications persist across sessions.',
      icon: '👋',
    },
    {
      title: 'Start with Chat',
      body: 'The Chat page is your primary workspace. Ask questions, create tools, build workflows — all through natural language.',
      icon: '💬',
      action: { label: 'Open Chat', route: '/chat' },
    },
    {
      title: 'Configure your LLM',
      body: 'Go to Dashboard → LLM Configuration to set up your API key and model. PyBot supports 10+ LLM providers.',
      icon: '⚙️',
      action: { label: 'Open Dashboard', route: '/dashboard' },
    },
    {
      title: 'Explore the Ecosystem',
      body: 'Browse tools, skills, agents, workflows, and apps in the Ecosystem page. Everything is reusable across sessions.',
      icon: '📦',
      action: { label: 'Open Ecosystem', route: '/ecosystem' },
    },
    {
      title: 'Use Ctrl+K Anytime',
      body: 'Press Ctrl+K to open the Command Palette and quickly navigate to any page or feature. Use keyboard shortcuts for efficiency.',
      icon: '⌨️',
    },
  ],
  zh: [
    {
      title: '欢迎使用 PyBot',
      body: 'PyBot 是一个持续进化的智能体运行时。工具、技能、工作流和应用能跨会话保留。',
      icon: '👋',
    },
    {
      title: '从对话开始',
      body: '对话页面是你的主要工作台。通过自然语言提问、创建工具、构建工作流。',
      icon: '💬',
      action: { label: '打开对话', route: '/chat' },
    },
    {
      title: '配置 LLM',
      body: '在仪表盘 → LLM 配置中设置 API 密钥和模型。PyBot 支持 10+ LLM 供应商。',
      icon: '⚙️',
      action: { label: '打开仪表盘', route: '/dashboard' },
    },
    {
      title: '探索生态',
      body: '在生态页面浏览工具、技能、智能体、工作流和应用。所有资产跨会话可复用。',
      icon: '📦',
      action: { label: '打开生态', route: '/ecosystem' },
    },
    {
      title: '随时 Ctrl+K',
      body: '按 Ctrl+K 打开命令面板，快速跳转到任何页面或功能。善用键盘快捷键提升效率。',
      icon: '⌨️',
    },
  ],
};

export default {
  name: 'OnboardingGuide',
  setup() {
    const router = useRouter();
    const visible = ref(false);
    const step = ref(0);

    const steps = computed(() => STEPS[locale.value] || STEPS.en);
    const current = computed(() => steps.value[step.value]);
    const isLast = computed(() => step.value >= steps.value.length - 1);
    const progress = computed(() => ((step.value + 1) / steps.value.length) * 100);

    function next() {
      if (isLast.value) {
        finish();
      } else {
        step.value++;
      }
    }
    function prev() {
      if (step.value > 0) step.value--;
    }
    function finish() {
      visible.value = false;
      localStorage.setItem(STORAGE_KEY, 'true');
    }
    function skip() {
      finish();
    }
    function goAction() {
      if (current.value?.action) {
        router.push(current.value.action.route);
      }
    }

    onMounted(() => {
      if (!localStorage.getItem(STORAGE_KEY)) {
        visible.value = true;
      }
    });

    return { visible, step, current, isLast, progress, steps, next, prev, skip, finish, goAction };
  },
  template: `
<teleport to="body">
  <div v-if="visible" class="onb-overlay" @click.self="skip">
    <div class="onb-dialog">
      <div class="onb-progress">
        <div class="onb-progress-bar" :style="{ width: progress + '%' }"></div>
      </div>
      <div class="onb-step-counter">{{ step + 1 }} / {{ steps.length }}</div>
      <div class="onb-icon">{{ current.icon }}</div>
      <h2 class="onb-title">{{ current.title }}</h2>
      <p class="onb-body">{{ current.body }}</p>
      <button v-if="current.action" class="onb-action-btn" @click="goAction">
        {{ current.action.label }} →
      </button>
      <div class="onb-footer">
        <button class="onb-skip" @click="skip">Skip</button>
        <div class="onb-nav">
          <button class="onb-prev" @click="prev" :disabled="step === 0">←</button>
          <button class="onb-next" @click="next">{{ isLast ? 'Done' : 'Next →' }}</button>
        </div>
      </div>
    </div>
  </div>
</teleport>
  `
};
