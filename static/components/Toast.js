import { store, removeToast } from '/static/stores/global.js';

export default {
  name: 'ToastContainer',
  setup() {
    return { store, removeToast };
  },
  template: `
    <teleport to="body">
      <div class="mx-toast-container">
        <transition-group name="toast">
          <div v-for="t in store.toasts" :key="t.id"
               class="mx-toast" :class="'mx-toast--' + t.type"
               @click="removeToast(t.id)">
            <span class="mx-toast-icon">
              <template v-if="t.type==='success'">&#10003;</template>
              <template v-else-if="t.type==='error'">&#10007;</template>
              <template v-else-if="t.type==='warning'">&#9888;</template>
              <template v-else>&#8505;</template>
            </span>
            <span class="mx-toast-msg">{{ t.message }}</span>
          </div>
        </transition-group>
      </div>
    </teleport>
  `
};
