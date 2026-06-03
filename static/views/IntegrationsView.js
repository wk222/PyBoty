import { computed, onMounted, onUnmounted, ref } from 'vue';

import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';

function pluginStatusLabel(plugin) {
  const runtime = plugin?.runtime || {};
  if (runtime.loaded) return t('integrations.loaded');
  if (runtime.enabled === false) return t('integrations.disabled');
  return t('integrations.registered');
}

export default {
  name: 'IntegrationsView',
  setup() {
    const tab = ref('plugins');
    const loading = ref(true);
    const refreshing = ref(false);
    const plugins = ref([]);
    const defaultPluginDirs = ref([]);
    const discoverDir = ref('');
    const channels = ref([]);
    const clawStatus = ref({ logged_in: false, polling: false, error: '' });
    const clawQr = ref({ qrcode: '', qrcode_img_url: '', status: '' });
    const clawPolling = ref(false);
    let pollTimer = null;

    const wechatClawChannel = computed(() =>
      channels.value.find((item) => item.name === 'wechat_claw') || null,
    );

    async function loadPlugins() {
      const data = await API.listPlugins();
      plugins.value = data.plugins || [];
      defaultPluginDirs.value = data.default_directories || [];
      if (!discoverDir.value && defaultPluginDirs.value.length) {
        discoverDir.value = defaultPluginDirs.value[0];
      }
    }

    async function loadChannels() {
      channels.value = await API.listChannels();
      try {
        clawStatus.value = await API.wechatClawStatus();
      } catch (error) {
        clawStatus.value = { logged_in: false, polling: false, error: error.message };
      }
    }

    async function refreshAll() {
      if (!loading.value) refreshing.value = true;
      try {
        await Promise.all([loadPlugins(), loadChannels()]);
      } catch (error) {
        toast(t('integrations.loadFailed') + error.message, 'error');
      } finally {
        loading.value = false;
        refreshing.value = false;
      }
    }

    async function discoverPlugins() {
      try {
        const payload = {
          reset: false,
          autoload_enabled: true,
        };
        if (discoverDir.value.trim()) {
          payload.directories = [discoverDir.value.trim()];
        }
        const data = await API.discoverPlugins(payload);
        plugins.value = data.plugins || [];
        toast(`${t('integrations.discoverOk')} (${data.discovered?.length || 0})`, 'success');
      } catch (error) {
        toast(t('integrations.discoverFailed') + error.message, 'error');
      }
    }

    async function togglePlugin(plugin, action) {
      const id = plugin.id;
      try {
        if (action === 'enable') await API.enablePlugin(id);
        else if (action === 'disable') await API.disablePlugin(id);
        else if (action === 'unload') await API.unloadPlugin(id);
        await loadPlugins();
        toast(t('integrations.actionOk'), 'success');
      } catch (error) {
        toast(t('integrations.actionFailed') + error.message, 'error');
      }
    }

    function stopClawPoll() {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      clawPolling.value = false;
    }

    async function pollClawOnce() {
      if (!clawQr.value.qrcode) return;
      const data = await API.wechatClawLoginPoll(clawQr.value.qrcode);
      clawQr.value.status = data.status || '';
      if (data.logged_in) {
        stopClawPoll();
        clawStatus.value = { logged_in: true, polling: data.polling ?? true, user_id: data.user_id || '' };
        toast(t('integrations.wechatLoginOk'), 'success');
        await loadChannels();
        return;
      }
      if (data.status === 'expired') {
        stopClawPoll();
        toast(t('integrations.wechatQrExpired'), 'error');
      }
    }

    async function startWechatLogin() {
      stopClawPoll();
      clawQr.value = { qrcode: '', qrcode_img_url: '', status: '' };
      try {
        const data = await API.wechatClawLogin();
        clawQr.value = {
          qrcode: data.qrcode || '',
          qrcode_img_url: data.qrcode_img_url || '',
          status: data.status || 'wait',
        };
        if (data.status === 'already_logged_in') {
          clawStatus.value = { logged_in: true, polling: clawStatus.value.polling };
          toast(t('integrations.wechatAlreadyLoggedIn'), 'success');
          return;
        }
        if (!clawQr.value.qrcode) {
          toast(t('integrations.wechatQrMissing'), 'error');
          return;
        }
        clawPolling.value = true;
        pollTimer = setInterval(() => {
          pollClawOnce().catch((error) => {
            stopClawPoll();
            toast(t('integrations.wechatPollFailed') + error.message, 'error');
          });
        }, 2500);
        await pollClawOnce();
      } catch (error) {
        toast(t('integrations.wechatLoginFailed') + error.message, 'error');
      }
    }

    async function logoutWechat() {
      stopClawPoll();
      try {
        await API.wechatClawLogout();
        clawStatus.value = { logged_in: false, polling: false };
        clawQr.value = { qrcode: '', qrcode_img_url: '', status: '' };
        toast(t('integrations.wechatLogoutOk'), 'success');
        await loadChannels();
      } catch (error) {
        toast(t('integrations.wechatLogoutFailed') + error.message, 'error');
      }
    }

    async function startWechatPolling() {
      try {
        await API.wechatClawStartPolling();
        clawStatus.value = { ...clawStatus.value, polling: true };
        toast(t('integrations.wechatPollingStarted'), 'success');
      } catch (error) {
        toast(t('integrations.wechatPollingFailed') + error.message, 'error');
      }
    }

    onMounted(refreshAll);
    onUnmounted(stopClawPoll);

    return {
      t,
      tab,
      loading,
      refreshing,
      plugins,
      defaultPluginDirs,
      discoverDir,
      channels,
      clawStatus,
      clawQr,
      clawPolling,
      wechatClawChannel,
      pluginStatusLabel,
      refreshAll,
      discoverPlugins,
      togglePlugin,
      startWechatLogin,
      logoutWechat,
      startWechatPolling,
    };
  },
  template: `
    <div class="gov-view">
      <header class="gov-header">
        <div class="gov-title-row">
          <h1 class="gov-title">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="24" height="24">
              <path d="M12 2 2 7l10 5 10-5-10-5Z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>
            </svg>
            {{ t('integrations.title') }}
          </h1>
          <button class="mx-btn mx-btn-ghost" @click="refreshAll" :disabled="refreshing">
            {{ refreshing ? t('common.loading') : t('common.refresh') }}
          </button>
        </div>

        <div class="gcenter-tabs">
          <button class="gcenter-tab" :class="{ active: tab === 'plugins' }" @click="tab = 'plugins'">
            {{ t('integrations.tabPlugins') }}
          </button>
          <button class="gcenter-tab" :class="{ active: tab === 'channels' }" @click="tab = 'channels'">
            {{ t('integrations.tabChannels') }}
          </button>
        </div>
      </header>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>{{ t('common.loading') }}</span></div>

      <div v-else-if="tab === 'plugins'" class="mx-card-grid mx-card-grid--compact">
        <section class="gov-panel" style="grid-column: 1 / -1;">
          <div class="gov-panel-head">
            <h2>{{ t('integrations.pluginsTitle') }}</h2>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
              <input
                v-model="discoverDir"
                class="mx-input"
                style="min-width:280px;"
                :placeholder="t('integrations.discoverDirPlaceholder')"
              />
              <button class="mx-btn mx-btn-primary" @click="discoverPlugins">{{ t('integrations.discover') }}</button>
            </div>
          </div>

          <p v-if="!plugins.length" class="mx-muted">{{ t('integrations.noPlugins') }}</p>

          <div v-else class="mx-table-wrap">
            <table class="mx-table">
              <thead>
                <tr>
                  <th>{{ t('integrations.colName') }}</th>
                  <th>{{ t('integrations.colVersion') }}</th>
                  <th>{{ t('integrations.colStatus') }}</th>
                  <th>{{ t('integrations.colActions') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="plugin in plugins" :key="plugin.id">
                  <td>
                    <strong>{{ plugin.name || plugin.id }}</strong>
                    <div class="mx-muted">{{ plugin.id }}</div>
                  </td>
                  <td>{{ plugin.version || '—' }}</td>
                  <td>{{ pluginStatusLabel(plugin) }}</td>
                  <td style="display:flex;gap:6px;flex-wrap:wrap;">
                    <button
                      v-if="!plugin.runtime?.enabled"
                      class="mx-btn mx-btn--sm"
                      @click="togglePlugin(plugin, 'enable')"
                    >{{ t('integrations.enable') }}</button>
                    <button
                      v-if="plugin.runtime?.enabled"
                      class="mx-btn mx-btn--sm mx-btn--ghost"
                      @click="togglePlugin(plugin, 'disable')"
                    >{{ t('integrations.disable') }}</button>
                    <button
                      v-if="plugin.runtime?.loaded"
                      class="mx-btn mx-btn--sm mx-btn--ghost"
                      @click="togglePlugin(plugin, 'unload')"
                    >{{ t('integrations.unload') }}</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <div v-else class="mx-card-grid mx-card-grid--compact">
        <section class="gov-panel" style="grid-column: 1 / -1;">
          <div class="gov-panel-head">
            <h2>{{ t('integrations.channelsTitle') }}</h2>
          </div>

          <p v-if="!channels.length" class="mx-muted">{{ t('integrations.noChannels') }}</p>

          <div v-else class="mx-table-wrap">
            <table class="mx-table">
              <thead>
                <tr>
                  <th>{{ t('integrations.colName') }}</th>
                  <th>{{ t('integrations.colKind') }}</th>
                  <th>{{ t('integrations.colEnabled') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="channel in channels" :key="channel.name">
                  <td>{{ channel.name }}</td>
                  <td>{{ channel.kind }}</td>
                  <td>{{ channel.enabled ? t('common.enabled') : t('common.disabled') }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="gov-panel" style="grid-column: 1 / -1;">
          <div class="gov-panel-head">
            <h2>{{ t('integrations.wechatClawTitle') }}</h2>
          </div>

          <p v-if="!wechatClawChannel" class="mx-muted">{{ t('integrations.wechatNotRegistered') }}</p>

          <template v-else>
            <div style="display:flex;gap:16px;flex-wrap:wrap;align-items:center;margin-bottom:16px;">
              <span>{{ t('integrations.wechatLoggedIn') }}: <strong>{{ clawStatus.logged_in ? t('common.enabled') : t('common.disabled') }}</strong></span>
              <span>{{ t('integrations.wechatPolling') }}: <strong>{{ clawStatus.polling ? t('common.enabled') : t('common.disabled') }}</strong></span>
              <span v-if="clawStatus.user_id">{{ t('integrations.wechatUser') }}: {{ clawStatus.user_id }}</span>
            </div>

            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px;">
              <button
                v-if="!clawStatus.logged_in"
                class="mx-btn mx-btn-primary"
                @click="startWechatLogin"
                :disabled="clawPolling"
              >{{ clawPolling ? t('integrations.wechatWaitingScan') : t('integrations.wechatLogin') }}</button>
              <button
                v-if="clawStatus.logged_in"
                class="mx-btn mx-btn--ghost"
                @click="startWechatPolling"
              >{{ t('integrations.wechatStartPolling') }}</button>
              <button
                v-if="clawStatus.logged_in"
                class="mx-btn mx-btn--ghost"
                @click="logoutWechat"
              >{{ t('integrations.wechatLogout') }}</button>
            </div>

            <div v-if="clawQr.qrcode_img_url" style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap;">
              <img :src="clawQr.qrcode_img_url" alt="WeChat QR" style="width:220px;height:220px;border-radius:12px;background:#fff;padding:8px;" />
              <div>
                <p>{{ t('integrations.wechatScanHint') }}</p>
                <p class="mx-muted">{{ t('integrations.wechatQrStatus') }}: {{ clawQr.status || 'wait' }}</p>
              </div>
            </div>
          </template>
        </section>
      </div>
    </div>
  `,
};
