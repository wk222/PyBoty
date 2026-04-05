import { t } from '/static/i18n.js';
import { PRIMARY_SURFACES, UTILITY_SURFACES } from '/static/config/navigation.js';

export default {
  name: 'SideNav',
  setup() {
    function n(key) {
      return t(`nav.${key}`);
    }

    return {
      n,
      primaryLinks: PRIMARY_SURFACES,
      utilityLinks: UTILITY_SURFACES,
    };
  },
  template: `
    <nav class="mx-sidenav">
      <div style="padding:8px 0 4px;width:100%;">
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
          <span class="mx-nav-label">{{ n(link.key) }}</span>
        </router-link>
      </div>
    </nav>
  `,
};
