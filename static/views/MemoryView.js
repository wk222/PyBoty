import { ref, computed, onMounted } from 'vue';
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

    async function loadData() {
      loading.value = true;
      try {
        data.value = await API.getMemoryOverview();
      } catch (e) {
        toast('Failed to load memory data: ' + e.message, 'error');
      }
      loading.value = false;
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

    onMounted(loadData);

    return {
      loading, activeTab, data, expandedJournal, expandedGarden,
      memoryCategories, pipelineStages,
      toggleJournal, toggleGarden, formatSize, loadData, t,
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

    <div class="mem-tabs">
      <button :class="['mem-tab', activeTab==='overview' && 'active']" @click="activeTab='overview'">{{ t('memory.tabOverview') }}</button>
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
