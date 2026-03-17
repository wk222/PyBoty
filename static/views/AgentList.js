import { ref, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { useEntityList } from '/static/composables/useEntityList.js';
import EntityCard from '/static/components/EntityCard.js';

const AGENT_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><circle cx="12" cy="5" r="4"/></svg>';

export default {
  name: 'AgentList',
  components: { EntityCard },
  setup() {
    const allTools = ref([]);

    const { items: agents, loading, load: baseLoad, toggle, remove } = useEntityList({
      fetchFn:    () => API.listAgents(),
      mapFn:      (d) => d.agents || [],
      toggleFn:   (name, enabled) => API.toggleAgent(name, enabled),
      deleteFn:   (name) => API.deleteAgent(name),
      entityLabel: 'agent',
    });

    async function load() {
      const [, tRes] = await Promise.all([baseLoad(), API.listTools()]);
      allTools.value = (tRes.tools || []).map(t => t.name);
    }

    async function assignTool(agentName, selectEl) {
      const tool = selectEl.value;
      if (!tool) return;
      try { await API.assignTool(agentName, tool); selectEl.value = ''; await load(); }
      catch (e) { toast('Assign failed', 'error'); }
    }

    async function removeTool(agent, tool) {
      try { await API.removeTool(agent, tool); await load(); }
      catch (e) { toast('Remove failed', 'error'); }
    }

    async function syncTool(agentName, toolName, direction, overwrite = false) {
      try {
        await API.syncAgentTool(agentName, toolName, direction, overwrite);
        toast(
          syncToastMessage(toolName, direction, overwrite),
          'success'
        );
        await load();
      } catch (e) {
        toast(e.message || 'Sync failed', 'error');
      }
    }

    function inventory(agent) {
      return agent.tool_inventory || {
        assigned_global_tools: [],
        local_tools: [],
        missing_assigned_tools: [],
      };
    }

    function assignedGlobalTools(agent) {
      return inventory(agent).assigned_global_tools || [];
    }

    function localTools(agent) {
      return inventory(agent).local_tools || [];
    }

    function syncToastMessage(toolName, direction, overwrite) {
      if (direction === 'to_global') {
        return overwrite
          ? `Synced ${toolName} to global and replaced the shared version`
          : `Synced ${toolName} to the global library`;
      }
      return overwrite
        ? `Pulled ${toolName} from global and replaced the local copy`
        : `Pulled ${toolName} from the global library`;
    }

    function shouldShowPull(tool) {
      return tool.sync_status === 'global_only' || tool.sync_status === 'conflict';
    }

    function shouldShowPush(tool) {
      return tool.sync_status === 'local_only' || tool.sync_status === 'conflict';
    }

    function missingAssignedTools(agent) {
      return inventory(agent).missing_assigned_tools || [];
    }

    function assignableTools(agent) {
      const assigned = new Set(assignedGlobalTools(agent).map(t => t.name));
      return allTools.value.filter(t => !assigned.has(t));
    }

    onMounted(load);

    return {
      agents,
      allTools,
      loading,
      load,
      toggle,
      remove,
      assignTool,
      removeTool,
      syncTool,
      syncToastMessage,
      shouldShowPull,
      shouldShowPush,
      inventory,
      assignedGlobalTools,
      localTools,
      missingAssignedTools,
      assignableTools,
      AGENT_ICON,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Agents</h1>
        <button class="mx-btn mx-btn--ghost" @click="load">Refresh</button>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <div v-else-if="agents.length === 0" class="mx-empty">
        <p>No agents yet. Tell PyBot to create one in Chat.</p>
        <router-link to="/chat" class="mx-btn mx-btn--primary">Go to Chat</router-link>
      </div>

      <div v-else class="mx-card-grid">
        <EntityCard
          v-for="a in agents" :key="a.name"
          :name="a.name"
          :description="a.description || a.role || ''"
          :icon="AGENT_ICON"
          gradient="linear-gradient(135deg,#6366f1,#818cf8)"
          :disabled="a.enabled === false"
          :toggleable="true"
          :enabled="a.enabled !== false"
          :deletable="true"
          @toggle="toggle(a.name, $event)"
          @delete="remove(a.name)"
        >
          <div class="mx-entity-card-meta">
            <span class="mx-tag">Model: {{ a.model || 'default' }}</span>
            <span class="mx-tag">Used: {{ a.usage_count || 0 }}x</span>
            <span class="mx-tag" :style="{ color: a.enabled !== false ? 'var(--success)' : 'var(--error)' }">
              {{ a.enabled !== false ? 'Enabled' : 'Disabled' }}
            </span>
          </div>
          <div class="mx-entity-card-tools">
            <div class="mx-tools-label">Assigned Global Tools</div>
            <div class="mx-tools-chips">
              <span v-if="assignedGlobalTools(a).length === 0" class="mx-text-muted" style="font-size:11px;">None</span>
              <span v-for="tool in assignedGlobalTools(a)" :key="tool.name" class="tool-chip">
                {{ tool.name }}
                <button
                  v-if="shouldShowPull(tool)"
                  class="chip-remove"
                  :title="tool.sync_status === 'conflict' ? 'Replace local copy from global tool' : 'Create local copy from global tool'"
                  @click="syncTool(a.name, tool.name, 'from_global', tool.sync_status === 'conflict')"
                >
                  Pull
                </button>
                <span
                  v-else-if="tool.sync_status === 'in_sync'"
                  class="mx-text-muted"
                  style="font-size:10px; margin-left:6px;"
                >
                  Synced
                </span>
                <button class="chip-remove" @click="removeTool(a.name, tool.name)">&times;</button>
              </span>
            </div>
            <div v-if="missingAssignedTools(a).length > 0" class="mx-tools-chips" style="margin-top:8px;">
              <span v-for="tool in missingAssignedTools(a)" :key="tool.name" class="tool-chip" style="background:rgba(239,68,68,.12); color:var(--error);">
                Missing: {{ tool.name }}
              </span>
            </div>
            <div v-if="assignableTools(a).length > 0" class="tool-assign-row">
              <select class="tool-assign-select" :ref="'sel_' + a.name">
                <option value="">Select tool...</option>
                <option v-for="t in assignableTools(a)" :key="t" :value="t">{{ t }}</option>
              </select>
              <button class="mini-btn" @click="assignTool(a.name, $refs['sel_' + a.name][0] || $refs['sel_' + a.name])">Add</button>
            </div>
            <div class="mx-tools-label" style="margin-top:10px;">Local Agent Tools</div>
            <div class="mx-tools-chips">
              <span v-if="localTools(a).length === 0" class="mx-text-muted" style="font-size:11px;">None</span>
              <span v-for="tool in localTools(a)" :key="tool.name" class="tool-chip" style="background:rgba(59,130,246,.12); color:var(--info);">
                {{ tool.name }}
                <button
                  v-if="shouldShowPush(tool)"
                  class="chip-remove"
                  :title="tool.sync_status === 'conflict' ? 'Replace global tool from local copy' : 'Publish local tool to global library'"
                  @click="syncTool(a.name, tool.name, 'to_global', tool.sync_status === 'conflict')"
                >
                  Push
                </button>
                <button
                  v-if="tool.sync_status === 'conflict'"
                  class="chip-remove"
                  title="Replace local copy from global tool"
                  @click="syncTool(a.name, tool.name, 'from_global', true)"
                >
                  Pull
                </button>
                <span
                  v-if="tool.sync_status === 'in_sync'"
                  class="mx-text-muted"
                  style="font-size:10px; margin-left:6px;"
                >
                  Synced
                </span>
              </span>
            </div>
          </div>
        </EntityCard>
      </div>
    </div>
  `
};
