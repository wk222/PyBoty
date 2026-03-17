export default {
  name: 'StatCard',
  props: {
    title: String,
    value: [String, Number],
    subtitle: String,
    icon: String,
    to: String,
    color: { type: String, default: 'var(--accent)' },
  },
  template: `
    <router-link :to="to || '/'" class="mx-stat-card">
      <div class="mx-stat-icon" :style="{ color: color }">
        <span v-html="icon"></span>
      </div>
      <div class="mx-stat-body">
        <div class="mx-stat-value">{{ value }}</div>
        <div class="mx-stat-title">{{ title }}</div>
        <div class="mx-stat-sub" v-if="subtitle">{{ subtitle }}</div>
      </div>
    </router-link>
  `
};
