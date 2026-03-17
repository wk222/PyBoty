/**
 * useEntityList — 通用实体列表 composable
 *
 * 消除 AgentList / ToolList / SkillList / AppList 中重复的
 * loading / load / toggle / remove 样板代码。
 *
 * 用法:
 *   const { items, loading, load, toggle, remove } = useEntityList({
 *     fetchFn:  () => API.listTools(),
 *     mapFn:    (data) => data.tools || [],
 *     toggleFn: (name, enabled) => API.toggleTool(name, enabled),   // optional
 *     deleteFn: (name) => API.deleteTool(name),                     // optional
 *     entityLabel: 'tool',
 *   });
 */
import { ref, onMounted } from 'vue';
import { toast } from '/static/stores/global.js';

export function useEntityList({
  fetchFn,
  mapFn,
  toggleFn = null,
  deleteFn = null,
  entityLabel = 'item',
}) {
  const items = ref([]);
  const loading = ref(true);

  async function load() {
    loading.value = true;
    try {
      const data = await fetchFn();
      items.value = mapFn(data);
    } catch (e) {
      toast(`Failed to load ${entityLabel}s`, 'error');
    } finally {
      loading.value = false;
    }
  }

  async function toggle(name, enabled) {
    if (!toggleFn) return;
    try {
      await toggleFn(name, enabled);
      await load();
    } catch (e) {
      toast('Toggle failed', 'error');
    }
  }

  async function remove(name) {
    if (!confirm(`Delete ${entityLabel} "${name}"?`)) return;
    if (!deleteFn) return;
    try {
      await deleteFn(name);
      await load();
      toast(`${entityLabel} deleted`, 'success');
    } catch (e) {
      toast('Delete failed', 'error');
    }
  }

  onMounted(load);

  return { items, loading, load, toggle, remove };
}
