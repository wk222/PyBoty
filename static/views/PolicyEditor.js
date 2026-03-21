import { ref, computed, onMounted, watch } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';

export default {
  name: 'PolicyEditor',
  setup() {
    const policy = ref(null);
    const presets = ref({});
    const loading = ref(true);
    const saving = ref(false);
    const dirty = ref(false);

    const draft = ref({
      mode: 'balanced',
      allow_dynamic_tools: true,
      allow_tool_mutation: true,
      allow_agent_mutation: true,
      allow_agent_delegation: true,
      approval_required_dynamic_tools: false,
      blocked_tools: [],
      blocked_dynamic_tools: [],
      risky_tools: [],
      approval_required_tools: [],
      max_recent_tool_calls: 20,
      stuck_loop_warning_threshold: 3,
      stuck_loop_kill_threshold: 6,
    });

    const newBlockedTool = ref('');
    const newRiskyTool = ref('');
    const newApprovalTool = ref('');

    const modes = [
      { key: 'open', label: 'Open', desc: '最小限制，适合开发调试' },
      { key: 'balanced', label: 'Balanced', desc: '平衡安全与功能，推荐生产使用' },
      { key: 'strict', label: 'Strict', desc: '最严格控制，禁止动态工具和委派' },
    ];

    const switches = [
      { key: 'allow_dynamic_tools', label: '允许动态工具', desc: '允许运行时创建的自定义工具执行' },
      { key: 'allow_tool_mutation', label: '允许工具变更', desc: '允许创建/删除自定义工具' },
      { key: 'allow_agent_mutation', label: '允许智能体变更', desc: '允许创建/删除子智能体' },
      { key: 'allow_agent_delegation', label: '允许委派', desc: '允许将任务委派给子智能体' },
      { key: 'approval_required_dynamic_tools', label: '动态工具需审批', desc: '所有动态工具调用需人工审批' },
    ];

    const sliders = [
      { key: 'max_recent_tool_calls', label: '最大工具调用次数', min: 5, max: 50, step: 1, desc: '单轮对话中工具调用次数限制' },
      { key: 'stuck_loop_warning_threshold', label: '循环警告阈值', min: 1, max: 10, step: 1, desc: '重复工具调用达到此次数时发出警告' },
      { key: 'stuck_loop_kill_threshold', label: '循环终止阈值', min: 2, max: 20, step: 1, desc: '重复工具调用达到此次数时强制终止' },
    ];

    function modeColor(mode) {
      return { open: '#34d399', balanced: '#60a5fa', strict: '#f87171' }[mode] || '#94a3b8';
    }

    async function load() {
      loading.value = true;
      try {
        const data = await API.getGovernancePolicy();
        policy.value = data.policy;
        presets.value = data.presets || {};
        Object.assign(draft.value, data.policy);
        draft.value.blocked_tools = [...(data.policy.blocked_tools || [])];
        draft.value.blocked_dynamic_tools = [...(data.policy.blocked_dynamic_tools || [])];
        draft.value.risky_tools = [...(data.policy.risky_tools || [])];
        draft.value.approval_required_tools = [...(data.policy.approval_required_tools || [])];
        dirty.value = false;
      } catch (e) {
        toast('Failed to load policy: ' + e.message, 'error');
      } finally {
        loading.value = false;
      }
    }

    function applyPreset(mode) {
      const preset = presets.value[mode];
      if (!preset) return;
      Object.assign(draft.value, preset);
      draft.value.blocked_tools = [...(preset.blocked_tools || [])];
      draft.value.blocked_dynamic_tools = [...(preset.blocked_dynamic_tools || [])];
      draft.value.risky_tools = [...(preset.risky_tools || [])];
      draft.value.approval_required_tools = [...(preset.approval_required_tools || [])];
      dirty.value = true;
    }

    function addToList(listKey, inputRef) {
      const val = inputRef.value.trim();
      if (!val) return;
      if (!draft.value[listKey].includes(val)) {
        draft.value[listKey].push(val);
        dirty.value = true;
      }
      inputRef.value = '';
    }

    function removeFromList(listKey, item) {
      draft.value[listKey] = draft.value[listKey].filter(t => t !== item);
      dirty.value = true;
    }

    async function save() {
      saving.value = true;
      try {
        const result = await API.updateGovernancePolicy(draft.value);
        if (result.success) {
          toast(result.message || '策略已保存', 'success');
          policy.value = result.policy;
          dirty.value = false;
        } else {
          toast(result.error || '保存失败', 'error');
        }
      } catch (e) {
        toast('Save failed: ' + e.message, 'error');
      } finally {
        saving.value = false;
      }
    }

    function reset() {
      if (policy.value) {
        Object.assign(draft.value, policy.value);
        draft.value.blocked_tools = [...(policy.value.blocked_tools || [])];
        draft.value.blocked_dynamic_tools = [...(policy.value.blocked_dynamic_tools || [])];
        draft.value.risky_tools = [...(policy.value.risky_tools || [])];
        draft.value.approval_required_tools = [...(policy.value.approval_required_tools || [])];
      }
      dirty.value = false;
    }

    watch(draft, () => { dirty.value = true; }, { deep: true });

    onMounted(load);

    return {
      policy, presets, loading, saving, dirty, draft,
      modes, switches, sliders,
      newBlockedTool, newRiskyTool, newApprovalTool,
      modeColor, load, applyPreset, addToList, removeFromList, save, reset,
    };
  },
  template: `
    <div class="policy-editor">
      <header class="pe-header">
        <div class="pe-title-row">
          <h1 class="pe-title">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="24" height="24">
              <path d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
            治理策略配置
          </h1>
          <div class="pe-actions">
            <button class="mx-btn mx-btn-ghost" @click="load" :disabled="loading">↻ 刷新</button>
            <button class="mx-btn mx-btn-ghost" @click="reset" :disabled="!dirty">重置</button>
            <button class="mx-btn mx-btn-primary" @click="save" :disabled="!dirty || saving">
              {{ saving ? '保存中...' : '保存策略' }}
            </button>
          </div>
        </div>
        <p class="pe-subtitle" v-if="dirty" style="color: var(--c-warning, #fbbf24);">
          有未保存的更改
        </p>
      </header>

      <div v-if="loading" class="hub-loading"><div class="mx-spinner"></div></div>

      <div v-else class="pe-content">
        <!-- Mode Selector -->
        <section class="pe-section">
          <h2 class="pe-section-title">控制模式</h2>
          <div class="pe-mode-grid">
            <div v-for="m in modes" :key="m.key"
                 class="pe-mode-card" :class="{ active: draft.mode === m.key }"
                 @click="draft.mode = m.key; applyPreset(m.key)">
              <div class="pe-mode-indicator" :style="{ background: modeColor(m.key) }"></div>
              <div class="pe-mode-info">
                <span class="pe-mode-label">{{ m.label }}</span>
                <span class="pe-mode-desc">{{ m.desc }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- Permission Switches -->
        <section class="pe-section">
          <h2 class="pe-section-title">权限控制</h2>
          <div class="pe-switch-list">
            <div v-for="sw in switches" :key="sw.key" class="pe-switch-row">
              <div class="pe-switch-info">
                <span class="pe-switch-label">{{ sw.label }}</span>
                <span class="pe-switch-desc">{{ sw.desc }}</span>
              </div>
              <label class="pe-toggle">
                <input type="checkbox" v-model="draft[sw.key]">
                <span class="pe-toggle-track"></span>
              </label>
            </div>
          </div>
        </section>

        <!-- Threshold Sliders -->
        <section class="pe-section">
          <h2 class="pe-section-title">阈值配置</h2>
          <div class="pe-slider-list">
            <div v-for="sl in sliders" :key="sl.key" class="pe-slider-row">
              <div class="pe-slider-info">
                <span class="pe-slider-label">{{ sl.label }}</span>
                <span class="pe-slider-desc">{{ sl.desc }}</span>
              </div>
              <div class="pe-slider-control">
                <input type="range" :min="sl.min" :max="sl.max" :step="sl.step"
                       v-model.number="draft[sl.key]" class="pe-slider">
                <span class="pe-slider-value">{{ draft[sl.key] }}</span>
              </div>
            </div>
          </div>
        </section>

        <!-- Tool Lists -->
        <section class="pe-section">
          <h2 class="pe-section-title">工具规则</h2>

          <!-- Blocked Tools -->
          <div class="pe-list-group">
            <h3 class="pe-list-title">禁用工具 <span class="pe-list-badge">{{ draft.blocked_tools.length }}</span></h3>
            <p class="pe-list-desc">这些工具将完全禁止调用</p>
            <div class="pe-tag-row">
              <span v-for="tool in draft.blocked_tools" :key="tool" class="pe-tag pe-tag-blocked"
                    @click="removeFromList('blocked_tools', tool)">
                {{ tool }} ✕
              </span>
              <span v-if="!draft.blocked_tools.length" class="pe-tag-empty">无</span>
            </div>
            <div class="pe-input-row">
              <input v-model="newBlockedTool" class="hub-input pe-input-sm" placeholder="输入工具名..."
                     @keyup.enter="addToList('blocked_tools', newBlockedTool)">
              <button class="mx-btn mx-btn-ghost pe-btn-add" @click="addToList('blocked_tools', newBlockedTool)">+</button>
            </div>
          </div>

          <!-- Risky Tools -->
          <div class="pe-list-group">
            <h3 class="pe-list-title">高风险工具 <span class="pe-list-badge">{{ draft.risky_tools.length }}</span></h3>
            <p class="pe-list-desc">标记为高风险，在审计日志中突出显示</p>
            <div class="pe-tag-row">
              <span v-for="tool in draft.risky_tools" :key="tool" class="pe-tag pe-tag-risky"
                    @click="removeFromList('risky_tools', tool)">
                {{ tool }} ✕
              </span>
              <span v-if="!draft.risky_tools.length" class="pe-tag-empty">无</span>
            </div>
            <div class="pe-input-row">
              <input v-model="newRiskyTool" class="hub-input pe-input-sm" placeholder="输入工具名..."
                     @keyup.enter="addToList('risky_tools', newRiskyTool)">
              <button class="mx-btn mx-btn-ghost pe-btn-add" @click="addToList('risky_tools', newRiskyTool)">+</button>
            </div>
          </div>

          <!-- Approval Required Tools -->
          <div class="pe-list-group">
            <h3 class="pe-list-title">需审批工具 <span class="pe-list-badge">{{ draft.approval_required_tools.length }}</span></h3>
            <p class="pe-list-desc">调用前需要人工审批通过</p>
            <div class="pe-tag-row">
              <span v-for="tool in draft.approval_required_tools" :key="tool" class="pe-tag pe-tag-approval"
                    @click="removeFromList('approval_required_tools', tool)">
                {{ tool }} ✕
              </span>
              <span v-if="!draft.approval_required_tools.length" class="pe-tag-empty">无</span>
            </div>
            <div class="pe-input-row">
              <input v-model="newApprovalTool" class="hub-input pe-input-sm" placeholder="输入工具名..."
                     @keyup.enter="addToList('approval_required_tools', newApprovalTool)">
              <button class="mx-btn mx-btn-ghost pe-btn-add" @click="addToList('approval_required_tools', newApprovalTool)">+</button>
            </div>
          </div>
        </section>
      </div>
    </div>
  `,
};
