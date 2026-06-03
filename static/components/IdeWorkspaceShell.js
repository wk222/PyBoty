import { ref, computed, onMounted, watch } from 'vue';
import { API } from '/static/api/index.js?v=20260531-3';
import { toast } from '/static/stores/global.js?v=20260531-3';
import IdeView from '/static/views/IdeView.js?v=20260531-3';

const CONFIG_FILES = ['SOUL.md', 'IDENTITY.md', 'TEAM.md', 'RULES.md', 'MEMORY.md', 'SCHEDULE.md'];
const STORAGE_KEY = 'pybot_active_workspace_id';

export default {
  name: 'IdeWorkspaceShell',
  components: { IdeView },
  setup() {
    const workspaces = ref([]);
    const activeWorkspaceId = ref(localStorage.getItem(STORAGE_KEY) || 'default');
    const expanded = ref({});
    const searchQuery = ref('');
    const loading = ref(true);
    const creating = ref(false);
    const newWorkspaceName = ref('');
    const showCreateForm = ref(false);
    const conversations = ref([]);
    const activeThreadId = ref('');
    const pendingFile = ref('');

    const filteredWorkspaces = computed(() => {
      const q = searchQuery.value.trim().toLowerCase();
      if (!q) return workspaces.value;
      return workspaces.value.filter((ws) =>
        ws.name.toLowerCase().includes(q) || ws.id.toLowerCase().includes(q)
      );
    });

    const activeWorkspace = computed(() =>
      workspaces.value.find((ws) => ws.id === activeWorkspaceId.value) || null
    );

    function threadPrefix(workspaceId) {
      return `ws:${workspaceId}:`;
    }

    function threadsFor(workspaceId) {
      const prefix = threadPrefix(workspaceId);
      return conversations.value.filter((c) => String(c.thread_id || '').startsWith(prefix));
    }

    const agents = ref([]);
    const agentsExpanded = ref(true);

    async function loadAgents() {
      try {
        const res = await API.listAgents();
        agents.value = res.agents || [];
      } catch (e) {
        console.warn('Failed to load agents list:', e);
      }
    }

    async function loadWorkspaces() {
      loading.value = true;
      try {
        const res = await API.listWorkspaces();
        workspaces.value = res.workspaces || [];
        if (res.default_id && !workspaces.value.some((ws) => ws.id === activeWorkspaceId.value)) {
          activeWorkspaceId.value = res.default_id;
        }
        const nextExpanded = { ...expanded.value };
        for (const ws of workspaces.value) {
          if (nextExpanded[ws.id] === undefined) {
            nextExpanded[ws.id] = ws.id === activeWorkspaceId.value;
          }
        }
        expanded.value = nextExpanded;
      } catch (e) {
        toast('加载 workspace 失败: ' + e.message, 'error');
      }
      loading.value = false;
    }

    async function loadConversations() {
      try {
        const res = await API.listConversations();
        conversations.value = res.conversations || [];
      } catch (e) {
        conversations.value = [];
      }
    }

    function selectWorkspace(workspaceId) {
      activeWorkspaceId.value = workspaceId;
      localStorage.setItem(STORAGE_KEY, workspaceId);
      expanded.value = { ...expanded.value, [workspaceId]: true };
      activeThreadId.value = '';
      pendingFile.value = 'SOUL.md';
    }

    function toggleWorkspace(workspaceId) {
      expanded.value = {
        ...expanded.value,
        [workspaceId]: !expanded.value[workspaceId],
      };
      if (expanded.value[workspaceId]) {
        selectWorkspace(workspaceId);
      }
    }

    function openFile(workspaceId, filename) {
      selectWorkspace(workspaceId);
      pendingFile.value = filename;
    }

    async function openThread(workspaceId, threadId) {
      selectWorkspace(workspaceId);
      activeThreadId.value = threadId;
      pendingFile.value = '';
    }

    async function createThread(workspaceId) {
      selectWorkspace(workspaceId);
      const suffix = Math.random().toString(36).slice(2, 10);
      activeThreadId.value = `${threadPrefix(workspaceId)}session-${suffix}`;
      pendingFile.value = '';
    }

    async function createWorkspace() {
      const name = newWorkspaceName.value.trim();
      if (!name) return;
      creating.value = true;
      try {
        const res = await API.createWorkspace(name);
        const ws = res.workspace;
        workspaces.value = [...workspaces.value, ws];
        newWorkspaceName.value = '';
        showCreateForm.value = false;
        selectWorkspace(ws.id);
        toast(`已创建 workspace: ${ws.name}`, 'success');
      } catch (e) {
        toast('创建 workspace 失败: ' + e.message, 'error');
      }
      creating.value = false;
    }

    function onFileOpened() {
      pendingFile.value = '';
    }

    watch(activeWorkspaceId, (id) => {
      localStorage.setItem(STORAGE_KEY, id);
    });

    onMounted(async () => {
      await Promise.all([loadWorkspaces(), loadConversations(), loadAgents()]);
    });

    return {
      workspaces,
      activeWorkspaceId,
      activeWorkspace,
      expanded,
      searchQuery,
      loading,
      creating,
      newWorkspaceName,
      showCreateForm,
      filteredWorkspaces,
      configFiles: CONFIG_FILES,
      activeThreadId,
      pendingFile,
      threadsFor,
      toggleWorkspace,
      selectWorkspace,
      openFile,
      openThread,
      createThread,
      createWorkspace,
      onFileOpened,
      agents,
      agentsExpanded,
    };
  },
  template: `
    <div class="ide-cursor-layout">
      <nav class="ide-rail" aria-label="Workspace tools">
        <button class="ide-rail-btn ide-rail-btn--active" title="Workspaces">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>
            <rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>
          </svg>
        </button>
        <a class="ide-rail-btn" href="/" title="返回控制台">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
            <polyline points="9 22 9 12 15 12 15 22"/>
          </svg>
        </a>
      </nav>

      <aside class="ide-workspaces-panel">
        <div class="ide-ws-search">
          <svg class="ide-ws-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
          <input
            v-model="searchQuery"
            class="ide-ws-search-input"
            placeholder="Search workspaces..."
          />
        </div>

        <div class="ide-ws-header">
          <span>Workspaces</span>
          <button class="ide-ws-new-btn" @click="showCreateForm = !showCreateForm" title="New workspace">+</button>
        </div>

        <form v-if="showCreateForm" class="ide-ws-create" @submit.prevent="createWorkspace">
          <input
            v-model="newWorkspaceName"
            class="ide-ws-create-input"
            placeholder="Workspace name"
          />
          <button class="ide-ws-create-submit" :disabled="creating || !newWorkspaceName.trim()">
            {{ creating ? '...' : 'Create' }}
          </button>
        </form>

        <div class="ide-ws-list">
          <div v-if="loading" class="ide-loading">Loading workspaces...</div>
          <div
            v-for="ws in filteredWorkspaces"
            :key="ws.id"
            class="ide-ws-group"
          >
            <button
              class="ide-ws-row"
              :class="{ 'ide-ws-row--active': activeWorkspaceId === ws.id }"
              @click="toggleWorkspace(ws.id)"
            >
              <span class="ide-ws-chevron" :class="{ 'ide-ws-chevron--open': expanded[ws.id] }">›</span>
              <span class="ide-ws-name">{{ ws.name }}</span>
              <span v-if="ws.is_default" class="ide-ws-badge">default</span>
            </button>

            <div v-if="expanded[ws.id]" class="ide-ws-children">
              <div class="ide-ws-section-label">Config</div>
              <button
                v-for="file in configFiles"
                :key="file"
                class="ide-ws-child"
                :class="{ 'ide-ws-child--active': activeWorkspaceId === ws.id && pendingFile === file }"
                @click="openFile(ws.id, file)"
              >
                <span class="ide-ws-child-icon">📝</span>
                <span>{{ file }}</span>
              </button>

              <div class="ide-ws-section-label">
                <span>Threads</span>
                <button class="ide-ws-inline-btn" @click.stop="createThread(ws.id)">+ New</button>
              </div>
              <button
                v-for="thread in threadsFor(ws.id)"
                :key="thread.thread_id"
                class="ide-ws-child"
                :class="{ 'ide-ws-child--active': activeThreadId === thread.thread_id }"
                @click="openThread(ws.id, thread.thread_id)"
              >
                <span class="ide-ws-child-icon">💬</span>
                <span class="ide-ws-child-title">{{ thread.title || thread.thread_id }}</span>
              </button>
              <div v-if="threadsFor(ws.id).length === 0" class="ide-ws-empty">No threads yet</div>
            </div>
          </div>
        </div>

        <!-- Collapsible Agents Section at the bottom -->
        <div class="ide-ws-agents-section">
          <button class="ide-ws-agents-header" @click="agentsExpanded = !agentsExpanded">
            <span class="ide-ws-chevron" :class="{ 'ide-ws-chevron--open': agentsExpanded }">›</span>
            <span>Active Agents</span>
          </button>
          <div v-if="agentsExpanded" class="ide-ws-agents-list">
            <div v-for="agent in agents" :key="agent.name" class="ide-agent-item">
              <div class="ide-agent-dot" :class="agent.enabled ? 'ide-agent-dot--online' : 'ide-agent-dot--offline'"></div>
              <span class="ide-agent-name">{{ agent.name }}</span>
              <span class="ide-agent-role">{{ agent.role_policy?.policy_type || 'default' }}</span>
            </div>
            <div v-if="agents.length === 0" class="ide-no-agents">No loaded agents found.</div>
          </div>
        </div>
      </aside>

      <IdeView
        :workspace-id="activeWorkspaceId"
        :initial-file="pendingFile"
        :thread-id="activeThreadId"
        @file-opened="onFileOpened"
      />
    </div>
  `,
};
