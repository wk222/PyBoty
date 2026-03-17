import { reactive } from 'vue';

export const store = reactive({
  agents: [],
  tools: [],
  skills: {},
  workflows: { saved: [], active: [] },
  apps: [],
  capabilities: null,
  toasts: [],
  loading: {},
});

let _toastId = 0;

export function toast(message, type = 'info', duration = 3000) {
  const id = ++_toastId;
  store.toasts.push({ id, message, type });
  if (duration > 0) {
    setTimeout(() => {
      const idx = store.toasts.findIndex(t => t.id === id);
      if (idx !== -1) store.toasts.splice(idx, 1);
    }, duration);
  }
  return id;
}

export function removeToast(id) {
  const idx = store.toasts.findIndex(t => t.id === id);
  if (idx !== -1) store.toasts.splice(idx, 1);
}

export function setLoading(key, val) {
  store.loading[key] = val;
}

export function isLoading(key) {
  return !!store.loading[key];
}
