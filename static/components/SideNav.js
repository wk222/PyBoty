import { ref } from 'vue';
import { t } from '/static/i18n.js';
import { PRIMARY_SURFACES, UTILITY_SURFACES } from '/static/config/navigation.js';

const STORAGE_KEY = 'pybot_sidenav_collapsed';

export default {
  name: 'SideNav',
  setup() {
    const collapsed = ref(localStorage.getItem(STORAGE_KEY) === 'true');

    function n(key) {
      return t(`nav.${key}`);
    }

    function toggleCollapse() {
      collapsed.value = !collapsed.value;
      localStorage.setItem(STORAGE_KEY, collapsed.value);
    }

    return {
      n, collapsed, toggleCollapse,
      primaryLinks: PRIMARY_SURFACES,
      utilityLinks: UTILITY_SURFACES,
    };
  },
  template: `
    <nav :class="['mx-sidenav', collapsed && 'mx-sidenav--collapsed']">
      <button class="mx-nav-collapse-btn" @click="toggleCollapse" :title="collapsed ? 'Expand sidebar' : 'Collapse sidebar'">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline v-if="collapsed" points="9 18 15 12 9 6"/>
          <polyline v-else points="15 18 9 12 15 6"/>
        </svg>
      </button>
      <div style="padding:8px 0 4px;width:100%;">
        <template v-for="link in primaryLinks" :key="link.key">
          <a
            v-if="link.href"
            :href="link.href"
            class="mx-nav-item"
            :title="n(link.key)"
          >
            <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" v-html="link.icon"></svg>
            <span class="mx-nav-label" v-show="!collapsed">{{ n(link.key) }}</span>
          </a>
          <router-link
            v-else
            :to="link.to"
            class="mx-nav-item"
            active-class="active"
            :title="n(link.key)"
          >
            <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" v-html="link.icon"></svg>
            <span class="mx-nav-label" v-show="!collapsed">{{ n(link.key) }}</span>
          </router-link>
        </template>
      </div>

      <div class="mx-nav-spacer"></div>

      <div style="padding:8px 0 0;width:100%;">
        <router-link
          v-for="link in utilityLinks"
          :key="link.key"
          :to="link.to"
          class="mx-nav-item"
          active-class="active"
          :title="n(link.key)"
        >
          <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" v-html="link.icon"></svg>
          <span class="mx-nav-label" v-show="!collapsed">{{ n(link.key) }}</span>
        </router-link>
        <router-link class="mx-nav-item" to="/cli" active-class="active" :title="n('cli') || 'CLI'">
          <svg class="mx-nav-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
          </svg>
          <span class="mx-nav-label" v-show="!collapsed">{{ n('cli') || 'CLI' }}</span>
        </router-link>
      </div>
    </nav>
  `,
};
