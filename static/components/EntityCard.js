/**
 * EntityCard — 通用实体卡片组件
 *
 * 替换各列表页中重复的 mx-entity-card 模板。
 *
 * Props:
 *   name        - 实体名称
 *   description - 描述
 *   icon        - SVG 字符串 (innerHTML)
 *   gradient    - 渐变背景 (e.g. "linear-gradient(135deg,#8b5cf6,#a78bfa)")
 *   disabled    - 是否处于禁用态
 *   toggleable  - 是否显示开关
 *   enabled     - 当前启用状态
 *   deletable   - 是否显示删除按钮
 *
 * Events:
 *   toggle(enabled)  - 开关被切换
 *   delete           - 删除按钮被点击
 */
export default {
  name: 'EntityCard',
  props: {
    name:        { type: String, required: true },
    description: { type: String, default: '' },
    icon:        { type: String, default: '' },
    gradient:    { type: String, default: 'linear-gradient(135deg,#6366f1,#818cf8)' },
    disabled:    { type: Boolean, default: false },
    toggleable:  { type: Boolean, default: false },
    enabled:     { type: Boolean, default: true },
    deletable:   { type: Boolean, default: false },
  },
  emits: ['toggle', 'delete'],
  template: `
    <div class="mx-entity-card" :class="{ 'mx-entity-card--disabled': disabled }">
      <div class="mx-entity-card-header">
        <div class="mx-entity-card-icon" :style="{ background: gradient }">
          <span v-html="icon" style="display:flex;align-items:center;justify-content:center;"></span>
        </div>
        <div class="mx-entity-card-info">
          <div class="mx-entity-card-name">{{ name }}</div>
          <div class="mx-entity-card-desc">{{ description || 'No description' }}</div>
        </div>
        <div class="mx-entity-card-actions">
          <slot name="actions"></slot>
          <label v-if="toggleable" class="mx-toggle" title="Toggle">
            <input type="checkbox" :checked="enabled" @change="$emit('toggle', $event.target.checked)">
            <span class="mx-toggle-slider"></span>
          </label>
          <button v-if="deletable" class="mx-btn-icon mx-btn-icon--danger" title="Delete" @click="$emit('delete')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
          </button>
        </div>
      </div>
      <slot></slot>
    </div>
  `,
};
