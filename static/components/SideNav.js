import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { t } from '/static/i18n.js';

const PRIMARY_LINKS = [
  {
    key: 'chat',
    to: '/chat',
    icon: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  },
  {
    key: 'governance',
    to: '/governance',
    icon: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  },
  {
    key: 'ecosystem',
    to: '/ecosystem',
    icon: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  },
];

const GOVERNANCE_LINKS = [
  {
    key: 'schedules',
    to: '/schedules',
    icon: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  },
  {
    key: 'debug',
    to: '/debug',
    icon: '<path d="M12 20h.01"/><path d="M8.56 15.69a5 5 0 0 1 6.88 0"/><path d="M5.12 12.25a9 9 0 0 1 13.76 0"/><path d="M1.67 8.81a13 13 0 0 1 20.66 0"/>',
  },
  {
    key: 'settings',
    to: '/settings',
    icon: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  },
];

const ECOSYSTEM_LINKS = [
  {
    key: 'apps',
    to: '/apps',
    icon: '<rect x="2" y="2" width="9" height="9" rx="2"/><rect x="13" y="2" width="9" height="9" rx="2"/><rect x="2" y="13" width="9" height="9" rx="2"/><rect x="13" y="13" width="9" height="9" rx="2"/>',
  },
  {
    key: 'workflows',
    to: '/workflows',
    icon: '<circle cx="12" cy="5" r="3"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="6" cy="19" r="3"/><circle cx="18" cy="19" r="3"/><line x1="12" y1="12" x2="6" y2="16"/><line x1="12" y1="12" x2="18" y2="16"/>',
  },
  {
    key: 'skills',
    to: '/skills',
    icon: '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
  },
  {
    key: 'tools',
    to: '/tools',
    icon: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  },
  {
    key: 'agents',
    to: '/agents',
    icon: '<rect x="3" y="11" width="18" height="11" rx="2"/><circle cx="12" cy="5" r="4"/><line x1="8" y1="16" x2="8" y2="16.01"/><line x1="16" y1="16" x2="16" y2="16.01"/>',
  },
  {
    key: 'hub',
    to: '/hub',
    icon: '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>',
  },
  {
    key: 'system',
    to: '/system',
    icon: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v3"/><path d="M12 18v3"/><path d="M3 12h3"/><path d="M18 12h3"/>',
  },
];

export default {
  name: 'SideNav',
  setup() {
    const route = useRoute();

    function n(key) {
      return t(`nav.${key}`);
    }

    const governanceOpen = computed(() =>
      ['/governance', ...GOVERNANCE_LINKS.map((item) => item.to)].some((path) =>
        path === '/' ? route.path === path : route.path.startsWith(path),
      ),
    );
    const ecosystemOpen = computed(() =>
      ['/ecosystem', ...ECOSYSTEM_LINKS.map((item) => item.to)].some((path) => route.path.startsWith(path)),
    );

    return {
      n,
      primaryLinks: PRIMARY_LINKS,
      governanceLinks: GOVERNANCE_LINKS,
      ecosystemLinks: ECOSYSTEM_LINKS,
      governanceOpen,
      ecosystemOpen,
    };
  },
  template: `
    <nav class="mx-sidenav">
      <div style="padding:8px 0 4px;">
        <router-link
          v-for="link in primaryLinks"
          :key="link.key"
          :to="link.to"
          class="mx-nav-item"
          active-class="active"
          :title="n(link.key)"
        >
          <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" v-html="link.icon"></svg>
          <span class="mx-nav-label">{{ n(link.key) }}</span>
        </router-link>
      </div>

      <details class="mx-nav-group" :open="governanceOpen" style="padding-top:6px;">
        <summary class="mx-nav-group-title" style="cursor:pointer;list-style:none;font-size:11px;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:var(--text-muted);padding:6px 14px 8px;">
          {{ n('governance') }}
        </summary>
        <router-link
          v-for="link in governanceLinks"
          :key="link.key"
          :to="link.to"
          class="mx-nav-item mx-nav-item--nested"
          style="padding-left:24px;"
          active-class="active"
          :title="n(link.key)"
        >
          <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" v-html="link.icon"></svg>
          <span class="mx-nav-label">{{ n(link.key) }}</span>
        </router-link>
      </details>

      <details class="mx-nav-group" :open="ecosystemOpen" style="padding-top:6px;">
        <summary class="mx-nav-group-title" style="cursor:pointer;list-style:none;font-size:11px;font-weight:800;letter-spacing:0.1em;text-transform:uppercase;color:var(--text-muted);padding:6px 14px 8px;">
          {{ n('ecosystemAssets') }}
        </summary>
        <router-link
          v-for="link in ecosystemLinks"
          :key="link.key"
          :to="link.to"
          class="mx-nav-item mx-nav-item--nested"
          style="padding-left:24px;"
          active-class="active"
          :title="n(link.key)"
        >
          <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" v-html="link.icon"></svg>
          <span class="mx-nav-label">{{ n(link.key) }}</span>
        </router-link>
      </details>
    </nav>
  `,
};
