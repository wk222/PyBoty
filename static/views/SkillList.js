import { ref, onMounted } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { useEntityList } from '/static/composables/useEntityList.js';
import EntityCard from '/static/components/EntityCard.js';

const SKILL_ICON = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/></svg>';

export default {
  name: 'SkillList',
  components: { EntityCard },
  setup() {
    const { items: skills, loading, load, toggle, remove } = useEntityList({
      fetchFn:    () => API.listSkills(),
      mapFn:      (d) => Object.entries(d.skills || {}).map(([key, info]) => ({ key, ...info })),
      toggleFn:   (name, enabled) => API.toggleSkill(name, enabled),
      deleteFn:   (name) => API.deleteSkill(name),
      entityLabel: 'skill',
    });

    const editSkill = ref(null);
    const editFiles = ref([]);
    const editFilePath = ref('');
    const editContent = ref('');
    const showEditor = ref(false);
    const fileLoading = ref(false);

    async function openEditor(skill) {
      editSkill.value = skill;
      editFiles.value = [];
      editFilePath.value = '';
      editContent.value = '';
      showEditor.value = true;
      try {
        const data = await API.listSkillFiles(skill.key);
        editFiles.value = data.files || [];
        if (editFiles.value.length > 0) {
          await openFile(editFiles.value[0].path);
        }
      } catch (e) {
        toast('Could not load skill files', 'warning');
      }
    }

    async function openFile(path) {
      if (!editSkill.value) return;
      fileLoading.value = true;
      editFilePath.value = path;
      try {
        const data = await API.getSkillFile(editSkill.value.key, path);
        editContent.value = data.content || '';
      } catch (e) {
        editContent.value = '// Failed to load file';
        toast('Load failed', 'error');
      }
      fileLoading.value = false;
    }

    async function saveFile() {
      if (!editSkill.value || !editFilePath.value) return;
      try {
        await API.updateSkillFile(editSkill.value.key, editFilePath.value, editContent.value);
        toast('File saved', 'success');
      } catch (e) { toast('Save failed: ' + e.message, 'error'); }
    }

    function fileIcon(path) {
      if (path.endsWith('.py')) return '\u{1F40D}';
      if (path.endsWith('.json')) return '\u{1F4CB}';
      if (path.endsWith('.md')) return '\u{1F4DD}';
      if (path.endsWith('.yaml') || path.endsWith('.yml')) return '\u2699';
      return '\u{1F4C4}';
    }

    return {
      skills, loading, editSkill, editFiles, editFilePath, editContent,
      showEditor, fileLoading,
      load, toggle, remove, openEditor, openFile, saveFile, fileIcon, SKILL_ICON,
    };
  },
  template: `
    <div class="mx-page">
      <div class="mx-page-header">
        <h1 class="mx-page-title">Skills</h1>
        <button class="mx-btn mx-btn--ghost" @click="load">Refresh</button>
      </div>

      <div v-if="loading" class="mx-loading"><div class="mx-spinner"></div><span>Loading...</span></div>

      <div v-else-if="skills.length === 0" class="mx-empty">
        <p>No skills installed yet.</p>
      </div>

      <div v-else class="mx-card-grid">
        <EntityCard
          v-for="s in skills" :key="s.key"
          :name="s.name || s.key"
          :description="s.description"
          :icon="SKILL_ICON"
          gradient="linear-gradient(135deg,#10b981,#34d399)"
          :disabled="!s.enabled"
          :toggleable="true"
          :enabled="s.enabled"
          :deletable="true"
          @toggle="toggle(s.key, $event)"
          @delete="remove(s.key)"
        >
          <template #actions>
            <button class="mx-btn-icon" @click="openEditor(s)" title="Edit files">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            </button>
          </template>
          <div class="mx-entity-card-meta">
            <span class="mx-tag">v{{ s.version || '1.0' }}</span>
            <span class="mx-tag">{{ s.author || 'system' }}</span>
            <span class="mx-tag" :style="{ color: s.enabled ? 'var(--success)' : 'var(--error)' }">
              {{ s.enabled ? 'Enabled' : 'Disabled' }}
            </span>
          </div>
          <div v-if="s.capabilities && s.capabilities.length" class="mx-entity-card-caps">
            <span v-for="c in s.capabilities" :key="c" class="mx-cap-tag">{{ c }}</span>
          </div>
        </EntityCard>
      </div>

      <!-- Skill File Editor -->
      <teleport to="body">
        <div v-if="showEditor" class="mx-modal-overlay" @click.self="showEditor = false">
          <div class="mx-modal" style="max-width:1000px;height:85vh;">
            <div class="mx-modal-header">
              <span>Edit Skill: {{ editSkill?.name || editSkill?.key }}</span>
              <button class="mx-modal-close" @click="showEditor = false">&times;</button>
            </div>
            <div class="mx-modal-body" style="flex:1;display:flex;gap:0;padding:0;overflow:hidden;">
              <div style="width:200px;border-right:1px solid var(--border);overflow-y:auto;flex-shrink:0;">
                <div style="padding:8px 12px;font-size:10px;font-weight:600;color:var(--text-muted);text-transform:uppercase;border-bottom:1px solid var(--border);">Files</div>
                <div v-if="editFiles.length === 0" style="padding:12px;color:var(--text-muted);font-size:11px;">No files found</div>
                <div v-for="f in editFiles" :key="f.path"
                     class="mx-file-item" :class="{ active: f.path === editFilePath }"
                     @click="openFile(f.path)">
                  <span>{{ fileIcon(f.path) }}</span>
                  <span class="mx-file-item-name">{{ f.path }}</span>
                </div>
              </div>
              <div style="flex:1;display:flex;flex-direction:column;">
                <div style="padding:6px 12px;font-size:11px;color:var(--text-secondary);border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;">
                  <span>{{ editFilePath || 'Select a file' }}</span>
                  <button v-if="editFilePath" class="mx-btn mx-btn--sm mx-btn--primary" @click="saveFile">Save</button>
                </div>
                <div v-if="fileLoading" class="mx-loading" style="flex:1;"><div class="mx-spinner"></div></div>
                <textarea v-else v-model="editContent" class="modal-editor" style="flex:1;font-size:12px;"></textarea>
              </div>
            </div>
          </div>
        </div>
      </teleport>
    </div>
  `
};
