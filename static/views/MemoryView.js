import { ref, computed, onMounted, watch } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';
import HelpTip from '/static/components/HelpTip.js';

export default {
  name: 'MemoryView',
  components: { HelpTip },
  setup() {
    const loading = ref(true);
    const activeTab = ref('overview');
    const data = ref({
      long_term: { content: '', last_distill: null, line_count: 0 },
      journals: [],
      archives: [],
      garden: [],
      stats: { journal_count: 0, archive_count: 0, garden_count: 0, memory_lines: 0, vector_count: 0 },
    });
    const expandedJournal = ref(null);
    const expandedGarden = ref(null);

    // Record Management State
    const records = ref([]);
    const recordsLoading = ref(false);
    const filterStatus = ref('active'); // 'active' | 'forgotten'
    const filterModality = ref('fact'); // 'fact' | 'reflection' | 'insight'
    const editingRecordId = ref(null);
    const editingContent = ref('');

    async function loadData() {
      loading.value = true;
      try {
        data.value = await API.getMemoryOverview();
      } catch (e) {
        toast('Failed to load memory data: ' + e.message, 'error');
      }
      loading.value = false;
    }

    async function loadRecords() {
      recordsLoading.value = true;
      try {
        const res = await API.listMemoryRecords(filterModality.value, filterStatus.value);
        records.value = res.records || [];
      } catch (e) {
        toast('Failed to load memory records: ' + e.message, 'error');
      } finally {
        recordsLoading.value = false;
      }
    }

    function startEdit(record) {
      editingRecordId.value = record.id;
      editingContent.value = record.content;
    }

    function cancelEdit() {
      editingRecordId.value = null;
      editingContent.value = '';
    }

    async function saveEdit(recordId) {
      if (!editingContent.value.trim()) return;
      try {
        await API.updateMemoryRecord(recordId, editingContent.value);
        toast('Memory updated successfully', 'success');
        editingRecordId.value = null;
        editingContent.value = '';
        await Promise.all([loadRecords(), loadData()]);
      } catch (e) {
        toast('Failed to update memory: ' + e.message, 'error');
      }
    }

    async function deleteRecord(recordId) {
      if (!confirm('Are you sure you want to permanently delete this memory record?')) return;
      try {
        await API.deleteMemoryRecord(recordId);
        toast('Memory deleted successfully', 'success');
        await Promise.all([loadRecords(), loadData()]);
      } catch (e) {
        toast('Failed to delete memory: ' + e.message, 'error');
      }
    }

    async function feedbackRecord(recordId, signal) {
      try {
        await API.feedbackMemoryRecord(recordId, signal);
        toast('Feedback applied successfully', 'success');
        await Promise.all([loadRecords(), loadData()]);
      } catch (e) {
        toast('Failed to apply feedback: ' + e.message, 'error');
      }
    }

    const memoryCategories = computed(() => {
      const content = data.value.long_term.content || '';
      const categories = {};
      let currentCat = 'uncategorized';
      for (const line of content.split('\n')) {
        if (line.startsWith('## ')) {
          currentCat = line.replace('## ', '').trim();
          if (!categories[currentCat]) categories[currentCat] = [];
        } else if (line.startsWith('- ') && currentCat) {
          if (!categories[currentCat]) categories[currentCat] = [];
          categories[currentCat].push(line.replace('- ', '').trim());
        }
      }
      return categories;
    });

    const pipelineStages = computed(() => {
      const s = data.value.stats;
      return [
        { label: t('memory.pipelineConversations'), icon: '💬', count: '-', desc: t('memory.pipelineConvDesc') },
        { label: t('memory.pipelineJournal'), icon: '📝', count: s.journal_count, desc: t('memory.pipelineJournalDesc') },
        { label: t('memory.pipelineDistill'), icon: '🧪', count: s.memory_lines, desc: t('memory.pipelineDistillDesc') },
        { label: t('memory.pipelineArchive'), icon: '📦', count: s.archive_count, desc: t('memory.pipelineArchiveDesc') },
      ];
    });

    function toggleJournal(date) {
      expandedJournal.value = expandedJournal.value === date ? null : date;
    }
    function toggleGarden(name) {
      expandedGarden.value = expandedGarden.value === name ? null : name;
    }
    function formatSize(bytes) {
      if (bytes < 1024) return bytes + ' B';
      return (bytes / 1024).toFixed(1) + ' KB';
    }

    watch(activeTab, (newTab) => {
      if (newTab === 'records') {
        loadRecords();
      }
    });

    watch([filterStatus, filterModality], () => {
      if (activeTab.value === 'records') {
        loadRecords();
      }
    });

    onMounted(loadData);

    return {
      loading, activeTab, data, expandedJournal, expandedGarden,
      memoryCategories, pipelineStages,
      toggleJournal, toggleGarden, formatSize, loadData, t,
      records, recordsLoading, filterStatus, filterModality,
      editingRecordId, editingContent,
      loadRecords, startEdit, cancelEdit, saveEdit, deleteRecord, feedbackRecord,
    };
  },
  template: `
<div class="mem-view">
  <HelpTip page="memory" />
  <div class="mem-header">
    <div>
      <h2 class="mem-title">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a2 2 0 0 1-2 2h-4a2 2 0 0 1-2-2v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z"/><line x1="9" y1="21" x2="15" y2="21"/></svg>
        {{ t('memory.title') }}
      </h2>
      <p class="mem-subtitle">{{ t('memory.subtitle') }}</p>
    </div>
    <button class="mem-refresh" @click="loadData" :disabled="loading">↻ {{ t('memory.refresh') }}</button>
  </div>

  <div v-if="loading" class="mem-loading">{{ t('memory.loading') }}</div>

  <template v-else>
    <div class="mem-pipeline">
      <div v-for="(stage, i) in pipelineStages" :key="i" class="mem-stage">
        <div class="mem-stage-icon">{{ stage.icon }}</div>
        <div class="mem-stage-count">{{ stage.count }}</div>
        <div class="mem-stage-label">{{ stage.label }}</div>
        <div class="mem-stage-desc">{{ stage.desc }}</div>
        <div v-if="i < pipelineStages.length - 1" class="mem-stage-arrow">→</div>
      </div>
    </div>

    <div class="mem-stats-bar">
      <div class="mem-stat-chip"><span class="mem-stat-n">{{ data.stats.memory_lines }}</span> {{ t('memory.memoryEntries') }}</div>
      <div class="mem-stat-chip"><span class="mem-stat-n">{{ data.stats.journal_count }}</span> {{ t('memory.journals') }}</div>
      <div class="mem-stat-chip"><span class="mem-stat-n">{{ data.stats.archive_count }}</span> {{ t('memory.archives') }}</div>
      <div class="mem-stat-chip"><span class="mem-stat-n">{{ data.stats.garden_count }}</span> {{ t('memory.gardenNotes') }}</div>
      <div class="mem-stat-chip" v-if="data.stats.vector_count"><span class="mem-stat-n">{{ data.stats.vector_count }}</span> {{ t('memory.vectors') }}</div>
    </div>

    <div v-if="data.components && data.components.length" class="mem-components">
      <h3 class="mem-comp-title">{{ t('memory.componentsTitle') || 'Memory Components' }}</h3>
      <div class="mem-comp-grid">
        <div v-for="comp in data.components" :key="comp.name" class="mem-comp-card" :class="'mem-comp--' + comp.status">
          <div class="mem-comp-header">
            <span class="mem-comp-name">{{ comp.name_zh || comp.name }}</span>
            <span class="mem-comp-status" :class="'status-' + comp.status">{{ comp.status === 'active' ? (t('memory.compActive') || 'Active') : (t('memory.compIdle') || 'Idle') }}</span>
          </div>
          <div class="mem-comp-meta">
            <span v-if="comp.entries != null">{{ comp.entries }} {{ t('memory.compEntries') || 'entries' }}</span>
            <span v-if="comp.size_kb != null">{{ comp.size_kb }} KB</span>
          </div>
        </div>
      </div>
    </div>

    <div class="mem-tabs">
      <button :class="['mem-tab', activeTab==='overview' && 'active']" @click="activeTab='overview'">{{ t('memory.tabOverview') }}</button>
      <button :class="['mem-tab', activeTab==='records' && 'active']" @click="activeTab='records'">{{ t('memory.tabManage') }}</button>
      <button :class="['mem-tab', activeTab==='journals' && 'active']" @click="activeTab='journals'">{{ t('memory.tabJournals') }} ({{ data.journals.length }})</button>
      <button :class="['mem-tab', activeTab==='garden' && 'active']" @click="activeTab='garden'">{{ t('memory.tabGarden') }} ({{ data.garden.length }})</button>
    </div>

    <div v-if="activeTab==='overview'" class="mem-section">
      <div v-if="data.long_term.last_distill" class="mem-distill-time">{{ t('memory.lastDistilled') }}: {{ data.long_term.last_distill }}</div>
      <div v-if="Object.keys(memoryCategories).length" class="mem-categories">
        <div v-for="(items, cat) in memoryCategories" :key="cat" class="mem-cat-card">
          <div class="mem-cat-header">
            <span class="mem-cat-name">{{ cat }}</span>
            <span class="mem-cat-badge">{{ items.length }}</span>
          </div>
          <ul class="mem-cat-list">
            <li v-for="(item, j) in items" :key="j">{{ item }}</li>
          </ul>
        </div>
      </div>
      <div v-else-if="data.long_term.content" class="mem-raw-content">
        <pre>{{ data.long_term.content }}</pre>
      </div>
      <div v-else class="mem-empty">{{ t('memory.noLongTerm') }}</div>
    </div>

    <div v-if="activeTab==='records'" class="mem-section">
      <div style="display:flex;gap:12px;margin-bottom:16px;align-items:center;flex-wrap:wrap;">
        <select v-model="filterModality" class="mx-input" style="width:140px;height:36px;padding:4px 8px;">
          <option value="fact">Fact</option>
          <option value="reflection">Reflection</option>
          <option value="insight">Insight</option>
        </select>
        <select v-model="filterStatus" class="mx-input" style="width:140px;height:36px;padding:4px 8px;">
          <option value="active">{{ t('memory.statusActive') }}</option>
          <option value="forgotten">{{ t('memory.statusForgotten') }}</option>
        </select>
        <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="loadRecords">{{ t('memory.refresh') }}</button>
      </div>

      <div v-if="recordsLoading" class="mem-loading">{{ t('memory.loading') }}</div>
      <div v-else-if="!records.length" class="mem-empty">{{ t('memory.noRecords') }}</div>
      <div v-else class="mx-table-wrap">
        <table class="mx-table">
          <thead>
            <tr>
              <th style="width:50%;">{{ t('memory.colContent') }}</th>
              <th>{{ t('memory.colImportance') }}</th>
              <th>{{ t('memory.colRecallCount') }}</th>
              <th>{{ t('memory.colActions') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="record in records" :key="record.id">
              <td>
                <div v-if="editingRecordId === record.id">
                  <textarea v-model="editingContent" class="mx-input" style="width:100%;min-height:60px;font-family:inherit;padding:8px;"></textarea>
                  <div style="display:flex;gap:8px;margin-top:8px;">
                    <button class="mx-btn mx-btn--sm mx-btn-primary" @click="saveEdit(record.id)">{{ t('memory.btnSave') }}</button>
                    <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="cancelEdit">{{ t('memory.btnCancel') }}</button>
                  </div>
                </div>
                <div v-else style="white-space:pre-wrap;word-break:break-word;">
                  {{ record.content }}
                </div>
              </td>
              <td>{{ (record.importance || 0).toFixed(2) }}</td>
              <td>{{ record.recall_count || 0 }}</td>
              <td>
                <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">
                  <button v-if="editingRecordId !== record.id" class="mx-btn mx-btn--sm mx-btn--ghost" @click="startEdit(record)">{{ t('memory.btnEdit') }}</button>
                  <button v-if="editingRecordId !== record.id" class="mx-btn mx-btn--sm mx-btn-icon--danger" @click="deleteRecord(record.id)">{{ t('memory.btnDelete') }}</button>
                  
                  <template v-if="editingRecordId !== record.id && record.status === 'active'">
                    <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="feedbackRecord(record.id, 'positive')" title="Increase importance">👍</button>
                    <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="feedbackRecord(record.id, 'negative')" title="Decrease importance">👎</button>
                    <button class="mx-btn mx-btn--sm mx-btn--ghost" @click="feedbackRecord(record.id, 'disproved')" title="Mark as disproved/forgotten">🚫</button>
                  </template>
                  <template v-if="editingRecordId !== record.id && record.status === 'forgotten'">
                    <button class="mx-btn mx-btn--sm mx-btn-primary" @click="feedbackRecord(record.id, 'reconsolidated')">{{ t('memory.feedbackReconsolidated') }}</button>
                  </template>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="activeTab==='journals'" class="mem-section">
      <div v-if="!data.journals.length" class="mem-empty">{{ t('memory.noJournals') }}</div>
      <div v-for="j in data.journals" :key="j.date" class="mem-journal-card" @click="toggleJournal(j.date)">
        <div class="mem-journal-header">
          <span class="mem-journal-date">📝 {{ j.date }}</span>
          <span class="mem-journal-size">{{ formatSize(j.size) }}</span>
          <span class="mem-journal-toggle">{{ expandedJournal === j.date ? '▼' : '▶' }}</span>
        </div>
        <div v-show="expandedJournal === j.date" class="mem-journal-body" @click.stop>
          <pre>{{ j.content }}</pre>
        </div>
      </div>
    </div>

    <div v-if="activeTab==='garden'" class="mem-section">
      <div v-if="!data.garden.length" class="mem-empty">{{ t('memory.noGarden') }}</div>
      <div v-for="g in data.garden" :key="g.name" class="mem-garden-card" @click="toggleGarden(g.name)">
        <div class="mem-garden-header">
          <span class="mem-garden-name">🌿 {{ g.name }}</span>
          <span class="mem-garden-size">{{ formatSize(g.size) }}</span>
          <span class="mem-garden-toggle">{{ expandedGarden === g.name ? '▼' : '▶' }}</span>
        </div>
        <div v-show="expandedGarden === g.name" class="mem-garden-body" @click.stop>
          <pre>{{ g.preview }}</pre>
        </div>
      </div>
    </div>
  </template>
</div>
  `,
};
