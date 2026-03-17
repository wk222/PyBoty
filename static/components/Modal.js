export default {
  name: 'MxModal',
  props: {
    show: Boolean,
    title: { type: String, default: '' },
    width: { type: String, default: '600px' },
  },
  emits: ['close'],
  template: `
    <teleport to="body">
      <transition name="modal-fade">
        <div v-if="show" class="mx-modal-overlay" @click.self="$emit('close')">
          <div class="mx-modal" :style="{ maxWidth: width }">
            <div class="mx-modal-header" v-if="title">
              <span>{{ title }}</span>
              <button class="mx-modal-close" @click="$emit('close')">&times;</button>
            </div>
            <div class="mx-modal-body">
              <slot />
            </div>
            <div class="mx-modal-footer" v-if="$slots.footer">
              <slot name="footer" />
            </div>
          </div>
        </div>
      </transition>
    </teleport>
  `
};
