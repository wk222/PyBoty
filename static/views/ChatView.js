import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';
import { t } from '/static/i18n.js';
import SwarmPanel from '/static/components/SwarmPanel.js';
import HelpTip from '/static/components/HelpTip.js';

// ─── Markdown renderer (markdown-it + highlight.js) ──────────────────────────
let _md = null;
function getMarkdown() {
  if (_md) return _md;
  if (window.markdownit && window.hljs) {
    _md = window.markdownit({
      html: false,
      linkify: true,
      typographer: false,
      highlight(str, lang) {
        if (lang && window.hljs.getLanguage(lang)) {
          try { return `<pre class="hljs-block"><code class="hljs language-${lang}">${window.hljs.highlight(str, { language: lang, ignoreIllegals: true }).value}</code></pre>`; }
          catch (_) {}
        }
        return `<pre class="hljs-block"><code>${_md.utils.escapeHtml(str)}</code></pre>`;
      }
    });
  }
  return _md;
}

function renderMarkdown(text) {
  if (!text) return '';
  const md = getMarkdown();
  if (md) return md.render(text);
  // fallback: basic escape + line breaks
  const d = document.createElement('div');
  d.textContent = text;
  return d.innerHTML.replace(/\n/g, '<br>');
}

function escapeHtml(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function formatTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  if (isToday) return `${hh}:${mm}`;
  return `${d.getMonth() + 1}/${String(d.getDate()).padStart(2, '0')} ${hh}:${mm}`;
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const k = 1024, s = ['B', 'KB', 'MB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + s[i];
}

const MODE_META = {
  assistant: { label: 'Assistant', color: '#3b82f6' },
  app_matrix: { label: 'App Matrix', color: '#0891b2' },
  admin: { label: 'Admin', color: '#d97706' },
};

// Canvas 元数据：标签和颜色。详细描述通过 API /api/canvas/profiles 获取。
const CANVAS_META = {
  focused:  { label: '📊 精简', color: '#10b981', desc: '省Token，限制工具深度' },
  balanced: { label: '⚡ 均衡', color: '#818cf8', desc: '默认，标准能力' },
  deep:     { label: '🔮 深度', color: '#a855f7', desc: '全能力，记忆蒸馏' },
};
// Canvas 名称在 core/modes/canvas.py 定义，与此保持一致

const BUDGET_META = {
  low: { color: '#22c55e', pulse: false },
  moderate: { color: '#eab308', pulse: false },
  high: { color: '#f97316', pulse: false },
  critical: { color: '#ef4444', pulse: true },
};

// ─── Slash command definitions ────────────────────────────────────────────────
const SLASH_COMMANDS = [
  { cmd: '/help',      icon: '❓', label: '/help',      hint: '查看所有可用指令' },
  { cmd: '/clear',     icon: '🗑️',  label: '/clear',     hint: '清空当前对话上下文' },
  { cmd: '/skills',    icon: '⚡',  label: '/skills',    hint: '列出所有已启用技能' },
  { cmd: '/tools',     icon: '🔧',  label: '/tools',     hint: '列出所有工具' },
  { cmd: '/memory',    icon: '🧠',  label: '/memory',    hint: '查看长期记忆摘要' },
  { cmd: '/schedule',  icon: '🕐',  label: '/schedule',  hint: '创建定时任务' },
  { cmd: '/search',    icon: '🔍',  label: '/search',    hint: '联网搜索' },
  { cmd: '/image',     icon: '🖼️',  label: '/image',     hint: '生成图片' },
  { cmd: '/code',      icon: '💻',  label: '/code',      hint: '进入代码模式' },
  { cmd: '/translate', icon: '🌐',  label: '/translate', hint: '翻译内容' },
];

// ─── Welcome example cards ────────────────────────────────────────────────────
const EXAMPLE_CARDS = [
  { icon: '📁', color: '#3b82f6', title: '文件管理', text: '查看工作区有哪些文件', bg: 'rgba(59,130,246,0.1)' },
  { icon: '⚡', color: '#f59e0b', title: '技能查询', text: '列出所有已启用的技能', bg: 'rgba(245,158,11,0.1)' },
  { icon: '💻', color: '#10b981', title: '编程助手', text: '帮我写一个 Python 数据分析脚本', bg: 'rgba(16,185,129,0.1)' },
  { icon: '🧠', color: '#8b5cf6', title: '记忆查看', text: '你还记得我们聊过什么吗', bg: 'rgba(139,92,246,0.1)' },
  { icon: '🌐', color: '#06b6d4', title: '联网搜索', text: '搜索今日 AI 最新动态', bg: 'rgba(6,182,212,0.1)' },
  { icon: '🕐', color: '#f43f5e', title: '定时任务', text: '10 分钟后提醒我看邮件', bg: 'rgba(244,63,94,0.1)' },
];

export default {
  name: 'ChatView',
  setup() {
    const conversations = ref([]);
    const currentThreadId = ref(null);
    const currentSessionKey = ref('');
    const sessionStatus = ref(null);
    const modeSwitcherOpen = ref(false);
    const currentCanvas = ref('balanced');
    const canvasOpen = ref(false);
    const messages = ref([]);
    const inputText = ref('');
    const isSending = ref(false);
    const steps = ref([]);
    const showSteps = ref(false);
    const stepsExpanded = ref(true);
    const stepsFinished = ref(false);
    const convSearch = ref('');
    const streamingContent = ref('');
    const isStreaming = ref(false);
    const messagesEl = ref(null);
    const inputEl = ref(null);
    const fileInput = ref(null);
    const swarmPanelVisible = ref(false);
    const traceVisible = ref(false);
    const traceEvents = ref([]);
    const traceLoading = ref(false);

    // slash menu state
    const slashMenuVisible = ref(false);
    const slashQuery = ref('');
    const slashActiveIdx = ref(0);

    let statusPollTimer = null;

    const filteredConversations = () => {
      if (!convSearch.value.trim()) return conversations.value;
      const q = convSearch.value.toLowerCase();
      return conversations.value.filter(c => (c.title || '').toLowerCase().includes(q));
    };

    const filteredSlashCommands = computed(() => {
      const q = slashQuery.value.toLowerCase();
      return SLASH_COMMANDS.filter(c =>
        c.cmd.includes(q) || c.hint.includes(q)
      );
    });

    const traceScope = ref('session');

    async function loadTrace() {
      if (!currentThreadId.value) return;
      traceLoading.value = true;
      try {
        const data = await API.getTrace(currentThreadId.value);
        traceEvents.value = data.events || [];
        traceScope.value = data.scope || 'session';
      } catch (e) { console.error(e); }
      finally { traceLoading.value = false; }
    }

    watch(traceVisible, (v) => { if (v) loadTrace(); });

    async function loadConversations() {
      try {
        const data = await API.listConversations();
        conversations.value = data.conversations || [];
      } catch (e) { console.error(e); }
    }

    async function createConversation() {
      try {
        const data = await API.createConversation();
        currentThreadId.value = data.thread_id;
        await loadConversations();
        await switchConversation(data.thread_id);
      } catch (e) { toast('Failed to create conversation', 'error'); }
    }

    async function deleteConversation(id) {
      if (!confirm('Delete this conversation?')) return;
      try {
        await API.deleteConversation(id);
        if (currentThreadId.value === id) {
          currentThreadId.value = null;
          currentSessionKey.value = '';
          sessionStatus.value = null;
          messages.value = [];
        }
        await loadConversations();
      } catch (e) { toast('Failed to delete', 'error'); }
    }

    async function switchConversation(id) {
      currentThreadId.value = id;
      currentSessionKey.value = '';
      sessionStatus.value = null;
      messages.value = [];
      steps.value = [];
      showSteps.value = false;
      traceEvents.value = [];
      try {
        const data = await API.getHistory(id);
        messages.value = (data.messages || []).map(m => ({
          role: m.role,
          content: m.content,
          html: renderMarkdown(m.content),
          timestamp: formatTime(m.timestamp),
        }));
        await nextTick();
        scrollToBottom();
        applyHljs();
      } catch (e) { console.error(e); }

      await resolveSessionKey(id);
      await loadCanvas(id);
      if (traceVisible.value) await loadTrace();
      if (inputEl.value) inputEl.value.focus();
    }

    async function loadCanvas(threadId) {
      try {
        const data = await API.getCanvas(threadId);
        currentCanvas.value = data.canvas || 'balanced';
      } catch (e) { currentCanvas.value = 'balanced'; }
    }

    async function switchCanvas(canvas) {
      if (!currentThreadId.value) return;
      canvasOpen.value = false;
      try {
        await API.setCanvas(currentThreadId.value, canvas);
        currentCanvas.value = canvas;
        // Clear cached agent on frontend side too
        toast(`画布已切换: ${CANVAS_META[canvas]?.label || canvas}`, 'success');
        // Deep mode: trigger memory distill after the session
        if (canvas === 'deep') {
          toast('深度模式：对话结束后将自动进行记忆蒸馏', 'info');
        }
      } catch (e) {
        toast('画布切换失败: ' + e.message, 'error');
      }
    }

    function closeCanvasSwitcher(e) {
      if (!e.target.closest('.canvas-picker')) canvasOpen.value = false;
    }

    async function resolveSessionKey(threadId) {
      try {
        const data = await API.listSessions();
        const sessions = data.sessions || [];
        const match = sessions.find(s => s.thread_id === threadId);
        if (match && match.session_key) {
          currentSessionKey.value = match.session_key;
          await loadSessionStatus();
        }
      } catch (e) { /* silent */ }
    }

    async function loadSessionStatus() {
      if (!currentSessionKey.value) return;
      try {
        const data = await API.getSessionStatus(currentSessionKey.value);
        sessionStatus.value = data;
      } catch (e) { /* silent */ }
    }

    async function switchMode(mode) {
      if (!currentSessionKey.value) return;
      modeSwitcherOpen.value = false;
      try {
        await API.switchSessionMode(currentSessionKey.value, mode);
        await loadSessionStatus();
        toast(`Mode: ${MODE_META[mode]?.label || mode}`, 'success');
      } catch (e) {
        toast('Mode switch failed: ' + e.message, 'error');
      }
    }

    function closeModeSwitcher(e) {
      if (!e.target.closest('.session-mode-pill')) {
        modeSwitcherOpen.value = false;
      }
    }

    function applyHljs() {
      if (window.hljs) {
        nextTick(() => {
          document.querySelectorAll('.bubble pre code').forEach(el => {
            if (!el.dataset.highlighted) window.hljs.highlightElement(el);
          });
        });
      }
    }

    async function sendMessage(textOverride) {
      if (isSending.value) return;
      const text = (textOverride || inputText.value).trim();
      if (!text || !currentThreadId.value) return;

      isSending.value = true;
      inputText.value = '';
      // reset textarea height
      if (inputEl.value) { inputEl.value.style.height = 'auto'; }
      showSteps.value = true;
      stepsExpanded.value = true;
      stepsFinished.value = false;
      steps.value = [{ icon: '🚀', text: 'Sending...' }];
      streamingContent.value = '';
      isStreaming.value = true;

      messages.value.push({
        role: 'user', content: text,
        html: renderMarkdown(text),
        timestamp: formatTime(Date.now() / 1000),
      });
      await nextTick();
      scrollToBottom();

      try {
        const res = await API.chatStream(currentThreadId.value, text);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const json = line.slice(6).trim();
            if (!json) continue;
            try {
              const evt = JSON.parse(json);
              if (evt.type === 'step') {
                steps.value.push({ icon: evt.icon || '📋', text: evt.content });
              } else if (evt.type === 'done') {
                streamingContent.value = '';
                isStreaming.value = false;
                const toolSteps = steps.value.length > 0 ? [...steps.value] : null;
                messages.value.push({
                  role: 'assistant', content: evt.content,
                  html: renderMarkdown(evt.content),
                  timestamp: formatTime(Date.now() / 1000),
                  toolSteps: toolSteps,
                  _toolsOpen: false,
                });
                stepsFinished.value = true;
                setTimeout(() => { showSteps.value = false; }, 3000);
                const conv = conversations.value.find(c => c.thread_id === currentThreadId.value);
                if (conv && (conv.message_count || 0) === 0) {
                  conv.title = text.substring(0, 30) + (text.length > 30 ? '...' : '');
                }
                if (evt.session_key && !currentSessionKey.value) {
                  currentSessionKey.value = evt.session_key;
                }
                await loadSessionStatus();
                applyHljs();
              } else if (evt.type === 'error') {
                isStreaming.value = false;
                messages.value.push({
                  role: 'assistant', content: evt.content,
                  html: '<span style="color:var(--error)">' + escapeHtml(evt.content) + '</span>',
                  timestamp: formatTime(Date.now() / 1000),
                });
                steps.value.push({ icon: '❌', text: evt.content });
              }
            } catch (_) {}
          }
          await nextTick();
          scrollToBottom();
        }
        await loadConversations();
      } catch (e) {
        isStreaming.value = false;
        messages.value.push({
          role: 'assistant',
          content: 'Network error: ' + e.message,
          html: '<span style="color:var(--error)">Network error: ' + escapeHtml(e.message) + '</span>',
          timestamp: formatTime(Date.now() / 1000),
        });
      } finally {
        isSending.value = false;
        if (inputEl.value) inputEl.value.focus();
      }
    }

    function scrollToBottom() {
      if (messagesEl.value) {
        requestAnimationFrame(() => { messagesEl.value.scrollTop = messagesEl.value.scrollHeight; });
      }
    }

    // ── Slash menu logic ────────────────────────────────────────────────────
    function onInputKeydown(e) {
      if (slashMenuVisible.value) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          slashActiveIdx.value = Math.min(slashActiveIdx.value + 1, filteredSlashCommands.value.length - 1);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          slashActiveIdx.value = Math.max(slashActiveIdx.value - 1, 0);
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          const item = filteredSlashCommands.value[slashActiveIdx.value];
          if (item) selectSlashCommand(item);
          return;
        }
        if (e.key === 'Escape') {
          slashMenuVisible.value = false;
          return;
        }
      }
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    }

    function onInputInput(e) {
      // auto-resize
      e.target.style.height = 'auto';
      e.target.style.height = Math.min(e.target.scrollHeight, 150) + 'px';

      const val = inputText.value;
      if (val.startsWith('/')) {
        slashQuery.value = val.slice(1).toLowerCase();
        slashActiveIdx.value = 0;
        slashMenuVisible.value = filteredSlashCommands.value.length > 0;
      } else {
        slashMenuVisible.value = false;
      }
    }

    function selectSlashCommand(item) {
      inputText.value = item.cmd + ' ';
      slashMenuVisible.value = false;
      if (inputEl.value) inputEl.value.focus();
    }

    function clickExampleCard(card) {
      inputText.value = card.text;
      if (inputEl.value) inputEl.value.focus();
    }

    async function handleFileUpload(e) {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const data = await API.upload(file);
        if (data.success) {
          inputText.value = `I uploaded "${data.filename}", please process it.`;
          toast('File uploaded: ' + data.filename, 'success');
        } else {
          toast('Upload failed', 'error');
        }
      } catch (err) { toast('Upload failed: ' + err.message, 'error'); }
      if (fileInput.value) fileInput.value.value = '';
    }

    const currentMode = () => sessionStatus.value?.mode || 'assistant';
    const currentBudgetLevel = () => sessionStatus.value?.budget?.level || 'low';
    const modeMeta = () => MODE_META[currentMode()] || MODE_META.assistant;
    const budgetMeta = () => BUDGET_META[currentBudgetLevel()] || BUDGET_META.low;

    onMounted(async () => {
      await loadConversations();
      if (conversations.value.length > 0) {
        await switchConversation(conversations.value[0].thread_id);
      } else {
        await createConversation();
      }
      statusPollTimer = setInterval(loadSessionStatus, 30_000);
      document.addEventListener('click', closeModeSwitcher);
      document.addEventListener('click', closeCanvasSwitcher);
    });

    onUnmounted(() => {
      if (statusPollTimer) clearInterval(statusPollTimer);
      document.removeEventListener('click', closeModeSwitcher);
      document.removeEventListener('click', closeCanvasSwitcher);
    });

    const contextBudget = ref(null);
    const showContextBudget = ref(false);

    async function loadContextBudget() {
      if (!currentThreadId.value) return;
      try {
        contextBudget.value = await API.getContextBudget(currentThreadId.value);
        showContextBudget.value = true;
      } catch (_) {
        showContextBudget.value = false;
      }
    }

    async function exportChat(fmt = 'markdown') {
      if (!currentThreadId.value) return;
      try {
        if (fmt === 'json') {
          const data = await API.exportConversation(currentThreadId.value, 'json');
          const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `${currentThreadId.value}.json`;
          a.click();
          URL.revokeObjectURL(url);
        } else {
          const resp = await fetch(`/api/conversations/${currentThreadId.value}/export?fmt=markdown`);
          const text = await resp.text();
          const blob = new Blob([text], { type: 'text/markdown' });
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `${currentThreadId.value}.md`;
          a.click();
          URL.revokeObjectURL(url);
        }
        toast('Exported successfully', 'success');
      } catch (e) {
        toast('Export failed: ' + e.message, 'error');
      }
    }

    return {
      conversations, currentThreadId, currentSessionKey, sessionStatus,
      modeSwitcherOpen, messages, inputText, isSending,
      steps, showSteps, stepsExpanded, stepsFinished, streamingContent, isStreaming,
      convSearch, messagesEl, inputEl, fileInput,
      filteredConversations, loadConversations, createConversation,
      deleteConversation, switchConversation, sendMessage, onInputKeydown, onInputInput,
      handleFileUpload, switchMode,
      currentMode, currentBudgetLevel, modeMeta, budgetMeta,
      MODE_META, BUDGET_META,
      formatTime, escapeHtml, formatBytes, t,
      swarmPanelVisible,
      traceVisible, traceEvents, traceLoading, traceScope, loadTrace,
      slashMenuVisible, slashQuery, slashActiveIdx, filteredSlashCommands,
      selectSlashCommand, clickExampleCard,
      EXAMPLE_CARDS,
      currentCanvas, canvasOpen, switchCanvas, CANVAS_META,
      contextBudget, showContextBudget, loadContextBudget, exportChat,
    };
  },
  components: { SwarmPanel, HelpTip },
  template: `
    <div class="chat-layout">
      <!-- Sidebar: Conversations -->
      <aside class="sidebar">
        <div class="sidebar-header">
          <h1>PyBot Chat</h1>
          <div class="subtitle">AI-Powered Conversations</div>
        </div>
        <button class="new-chat-btn" @click="createConversation">
          <span>+</span> {{ t('chat.newChat') || 'New Chat' }}
        </button>
        <div style="padding:0 12px 8px;">
          <input v-model="convSearch" type="text" :placeholder="t('chat.searchConversations') || 'Search...'"
            style="width:100%;padding:6px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-size:12px;outline:none;" />
        </div>
        <div class="conversation-list">
          <div v-if="filteredConversations().length === 0" style="padding:24px 12px;text-align:center;color:var(--text-muted);font-size:12px;">
            {{ t('chat.noConversations') || 'No conversations' }}
          </div>
          <div v-for="c in filteredConversations()" :key="c.thread_id"
               class="conv-item" :class="{ active: c.thread_id === currentThreadId }"
               @click="switchConversation(c.thread_id)">
            <div class="conv-icon">{{ c.message_count > 20 ? '📚' : c.message_count > 5 ? '💬' : '🗨️' }}</div>
            <div class="conv-info">
              <div class="conv-title">{{ c.title || 'Untitled' }}</div>
              <div class="conv-meta">
                <span>{{ c.message_count || 0 }} msgs</span>
                <span class="conv-dot">·</span>
                <span>{{ formatTime(c.last_message_at) }}</span>
                <span v-if="c.canvas" class="conv-canvas-tag" :style="{ background: (CANVAS_META[c.canvas] || {}).color || 'var(--border)' }">{{ c.canvas }}</span>
              </div>
            </div>
            <button class="conv-delete" @click.stop="deleteConversation(c.thread_id)" title="Delete">×</button>
          </div>
        </div>
      </aside>

      <!-- Main Chat Area -->
      <main class="main">
        <div class="chat-header">
          <div style="flex:1;min-width:0;">
            <div class="chat-title">{{ currentThreadId ? (conversations.find(c => c.thread_id === currentThreadId)?.title || currentThreadId) : 'Select or create a conversation' }}</div>
            <div class="chat-thread-id" v-if="currentThreadId">{{ currentThreadId }}</div>
          </div>

          <!-- Session Spine Bar -->
          <div v-if="currentThreadId" class="session-spine-bar" style="display:flex;align-items:center;gap:12px;flex-shrink:0;">

            <!-- Trace Log Toggle -->
            <button class="mx-btn-icon" @click="traceVisible = !traceVisible"
                    :class="{'active': traceVisible}" :title="t('chat.traceLog') || 'Execution Trace'"
                    style="color: var(--text-muted);">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
                <polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/>
              </svg>
            </button>

            <!-- Context Budget -->
            <button class="mx-btn-icon" @click="loadContextBudget"
                    :title="t('chat.contextBudget') || 'Context Budget'" style="color: var(--text-muted);">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/>
              </svg>
            </button>

            <!-- Export -->
            <button class="mx-btn-icon" @click="exportChat('markdown')"
                    :title="t('chat.export') || 'Export as Markdown'" style="color: var(--text-muted);">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
            </button>

            <!-- Swarm Toggle -->
            <button v-if="currentSessionKey" class="mx-btn-icon" @click="swarmPanelVisible = !swarmPanelVisible"
                    :class="{'active': swarmPanelVisible}" title="Swarm Observability"
                    style="color: var(--text-muted);">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :style="{color: swarmPanelVisible ? 'var(--accent)' : 'inherit'}">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
              </svg>
            </button>

            <!-- Execution Canvas picker -->
            <div class="canvas-picker" style="position:relative;">
              <button
                @click.stop="canvasOpen = !canvasOpen"
                :style="{
                  display:'flex', alignItems:'center', gap:'5px',
                  padding:'3px 8px', borderRadius:'20px',
                  border:'1px solid ' + (CANVAS_META[currentCanvas]?.color || '#818cf8') + '55',
                  background: (CANVAS_META[currentCanvas]?.color || '#818cf8') + '15',
                  color: CANVAS_META[currentCanvas]?.color || '#818cf8',
                  fontSize:'11px', fontWeight:'600', cursor:'pointer',
                  letterSpacing:'0.02em', whiteSpace:'nowrap',
                }"
                :title="CANVAS_META[currentCanvas]?.desc || '切换执行画布'"
              >
                {{ CANVAS_META[currentCanvas]?.label || '⚡ 均衡' }}
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
              </button>
              <div v-if="canvasOpen"
                   style="position:absolute;top:calc(100% + 4px);right:0;min-width:180px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius);box-shadow:0 4px 12px rgba(0,0,0,0.2);z-index:100;overflow:hidden;padding:4px;">
                <div style="padding:5px 10px 3px;font-size:10px;font-weight:700;letter-spacing:0.06em;text-transform:uppercase;color:var(--text-muted);">执行画布</div>
                <button
                  v-for="(meta, key) in CANVAS_META" :key="key"
                  @click="switchCanvas(key)"
                  :style="{
                    display:'flex', alignItems:'flex-start', flexDirection:'column', gap:'2px',
                    width:'100%', padding:'8px 12px',
                    background: currentCanvas === key ? meta.color + '15' : 'transparent',
                    border:'none', cursor:'pointer', textAlign:'left', borderRadius:'6px',
                  }"
                >
                  <div :style="{ fontSize:'12px', fontWeight: currentCanvas === key ? '700' : '500', color: currentCanvas === key ? meta.color : 'var(--text-primary)', display:'flex', alignItems:'center', gap:'6px' }">
                    {{ meta.label }}
                    <span v-if="currentCanvas === key" style="font-size:10px;">✓</span>
                  </div>
                  <div style="font-size:11px;color:var(--text-muted);">{{ meta.desc }}</div>
                </button>
              </div>
            </div>

            <!-- Budget indicator -->
            <div :title="'Context: ' + currentBudgetLevel() + (sessionStatus?.budget ? ' (' + Math.round((sessionStatus.budget.utilization || 0) * 100) + '%)' : '')"
                 style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--text-muted);">
              <span :style="{
                display: 'inline-block',
                width: '8px', height: '8px', borderRadius: '50%',
                background: budgetMeta().color,
                animation: budgetMeta().pulse ? 'pulse 1.5s ease-in-out infinite' : 'none',
                flexShrink: 0,
              }"></span>
              <span style="font-size:10px;letter-spacing:0.02em;">{{ currentBudgetLevel() }}</span>
            </div>

            <!-- Mode pill + switcher -->
            <div class="session-mode-pill" style="position:relative;">
              <button
                @click.stop="modeSwitcherOpen = !modeSwitcherOpen"
                :style="{
                  display:'flex', alignItems:'center', gap:'5px',
                  padding:'3px 8px', borderRadius:'20px',
                  border:'1px solid ' + modeMeta().color + '55',
                  background: modeMeta().color + '18',
                  color: modeMeta().color,
                  fontSize:'11px', fontWeight:'600', cursor:'pointer',
                  letterSpacing:'0.02em', whiteSpace:'nowrap',
                }"
              >
                <span :style="{ width:'6px', height:'6px', borderRadius:'50%', background:modeMeta().color, display:'inline-block', flexShrink:0 }"></span>
                {{ modeMeta().label }}
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="6 9 12 15 18 9"/></svg>
              </button>
              <div v-if="modeSwitcherOpen"
                   style="position:absolute;top:calc(100% + 4px);right:0;min-width:140px;background:var(--bg-secondary);border:1px solid var(--border);border-radius:var(--radius);box-shadow:0 4px 12px rgba(0,0,0,0.2);z-index:100;overflow:hidden;">
                <button
                  v-for="(meta, key) in MODE_META" :key="key"
                  @click="switchMode(key)"
                  :style="{
                    display:'flex', alignItems:'center', gap:'8px',
                    width:'100%', padding:'8px 12px',
                    background: currentMode() === key ? meta.color + '15' : 'transparent',
                    border:'none', color: currentMode() === key ? meta.color : 'var(--text-primary)',
                    fontSize:'12px', fontWeight: currentMode() === key ? '600' : '400',
                    cursor:'pointer', textAlign:'left',
                  }"
                >
                  <span :style="{ width:'7px', height:'7px', borderRadius:'50%', background:meta.color, display:'inline-block', flexShrink:0 }"></span>
                  {{ meta.label }}
                  <span v-if="currentMode() === key" style="margin-left:auto;font-size:10px;">✓</span>
                </button>
              </div>
            </div>

          </div>
        </div>

        <!-- Context Budget Overlay -->
        <div v-if="showContextBudget && contextBudget" class="ctx-budget-overlay" @click.self="showContextBudget = false">
          <div class="ctx-budget-panel">
            <div class="ctx-budget-header">
              <span>Context Window Budget</span>
              <button @click="showContextBudget = false" class="ctx-budget-close">&times;</button>
            </div>
            <div class="ctx-budget-bar">
              <div v-for="cat in contextBudget.categories.filter(c => c.tokens > 0)" :key="cat.name"
                class="ctx-budget-segment"
                :style="{ width: (cat.tokens / contextBudget.max_tokens * 100) + '%', background: cat.color }"
                :title="cat.name + ': ' + cat.tokens + ' tokens'">
              </div>
            </div>
            <div class="ctx-budget-meta">
              <span>{{ contextBudget.used_tokens.toLocaleString() }} / {{ contextBudget.max_tokens.toLocaleString() }} tokens ({{ contextBudget.percentage }}%)</span>
              <span>Canvas: {{ contextBudget.canvas }} | {{ contextBudget.message_count }} messages</span>
            </div>
            <div class="ctx-budget-legend">
              <div v-for="cat in contextBudget.categories" :key="cat.name" class="ctx-budget-legend-item">
                <span class="ctx-budget-dot" :style="{ background: cat.color }"></span>
                <span class="ctx-budget-label">{{ cat.name }}</span>
                <span class="ctx-budget-val">{{ cat.tokens.toLocaleString() }}</span>
              </div>
            </div>
          </div>
        </div>

        <div class="chat-messages" ref="messagesEl">
          <HelpTip page="chat" />
          <!-- Welcome Screen (empty state) -->
          <div v-if="messages.length === 0 && !isStreaming" class="empty-state">
            <div class="logo">🛠️</div>
            <h2>{{ t('chat.welcome') || 'PyBot' }}</h2>
            <p style="color:var(--text-muted);">{{ t('chat.welcomeHint') || '输入消息开始对话，或选择下方示例' }}</p>
            <!-- Example Cards -->
            <div class="example-cards">
              <div v-for="card in EXAMPLE_CARDS" :key="card.cmd"
                   class="example-card" @click="clickExampleCard(card)">
                <div class="example-card-icon" :style="{ background: card.bg }">
                  <span>{{ card.icon }}</span>
                </div>
                <div class="example-card-title">{{ card.title }}</div>
                <div class="example-card-desc">{{ card.text }}</div>
              </div>
            </div>
          </div>

          <div v-for="(msg, i) in messages" :key="i" class="message" :class="[msg.role, msg.isError ? 'error-msg' : '']">
            <div class="avatar">{{ msg.role === 'user' ? '👤' : '🤖' }}</div>
            <div class="msg-body">
              <div v-if="msg.toolSteps && msg.toolSteps.length" class="tool-calls-section">
                <div class="tool-calls-header" @click="msg._toolsOpen = !msg._toolsOpen">
                  <span class="tool-calls-icon">🔧</span>
                  <span>{{ msg.toolSteps.length }} tool call{{ msg.toolSteps.length > 1 ? 's' : '' }}</span>
                  <span class="tool-calls-toggle">{{ msg._toolsOpen ? '▼' : '▶' }}</span>
                </div>
                <div v-show="msg._toolsOpen" class="tool-calls-list">
                  <div v-for="(ts, j) in msg.toolSteps" :key="j" class="tool-call-item">
                    <span class="tool-call-icon">{{ ts.icon || '📋' }}</span>
                    <span class="tool-call-text">{{ ts.text }}</span>
                  </div>
                </div>
              </div>
              <div class="bubble md-content" v-html="msg.html"></div>
              <div class="timestamp">{{ msg.timestamp }}</div>
            </div>
          </div>

          <div v-if="isStreaming" class="message assistant">
            <div class="avatar">🤖</div>
            <div>
              <div class="bubble">
                <div class="thinking"><span></span><span></span><span></span></div>
              </div>
            </div>
          </div>
        </div>

        <!-- Steps Bar -->
        <div class="steps-bar" v-if="showSteps" :class="{ collapsed: !stepsExpanded }">
          <div class="steps-bar-header" @click="stepsExpanded = !stepsExpanded">
            <span v-if="stepsFinished" style="color:var(--success)">✓</span>
            <span v-else class="steps-spinner"></span>
            <span>{{ stepsFinished ? 'Done' : 'Running...' }}</span>
            <button class="steps-toggle">{{ stepsExpanded ? '▼' : '▶' }}</button>
          </div>
          <div class="steps-list" v-show="stepsExpanded">
            <div v-for="(s, i) in steps" :key="i" class="step-item">
              <span class="step-icon">{{ s.icon }}</span>
              <span class="step-text">{{ s.text }}</span>
            </div>
          </div>
        </div>

        <!-- Input Area -->
        <div class="chat-input-area">
          <div class="input-wrapper" style="position:relative;">
            <!-- Slash Command Menu -->
            <div v-if="slashMenuVisible" class="slash-menu">
              <div class="slash-menu-header">指令 · 输入关键词筛选</div>
              <div
                v-for="(item, idx) in filteredSlashCommands" :key="item.cmd"
                class="slash-menu-item" :class="{ active: idx === slashActiveIdx }"
                @mousedown.prevent="selectSlashCommand(item)"
                @mouseover="slashActiveIdx = idx"
              >
                <div class="slash-menu-item-icon">{{ item.icon }}</div>
                <div style="flex:1;min-width:0;">
                  <div class="slash-menu-item-name">{{ item.label }}</div>
                  <div class="slash-menu-item-hint">{{ item.hint }}</div>
                </div>
              </div>
            </div>

            <input type="file" ref="fileInput" style="display:none" @change="handleFileUpload" />
            <button class="upload-btn" @click="$refs.fileInput.click()" :disabled="!currentThreadId" title="上传文件">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </button>
            <textarea ref="inputEl" v-model="inputText"
              :disabled="!currentThreadId"
              @keydown="onInputKeydown"
              @input="onInputInput"
              :placeholder="currentThreadId ? (t('chat.inputPlaceholder') || '输入消息，或 / 使用指令...') : (t('chat.loadingConversation') || 'Loading...')"
              rows="1"></textarea>
            <button class="send-btn" @click="sendMessage()" :disabled="!currentThreadId || isSending">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
        </div>
      </main>
      <SwarmPanel :session-key="currentSessionKey" :visible="swarmPanelVisible" />

      <!-- Trace Overlay -->
      <div v-if="traceVisible" class="trace-overlay" @click.self="traceVisible = false">
        <div class="chat-trace-modal">
          <div class="chat-trace-header">
            <h3>{{ t('chat.traceTitle') || 'Execution Trace Log' }}</h3>
            <div style="display:flex;gap:8px;">
              <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="loadTrace">{{ t('common.refresh') || 'Refresh' }}</button>
              <button class="mx-btn mx-btn--ghost mx-btn--sm" @click="traceVisible = false">×</button>
            </div>
          </div>
          <div class="chat-trace-body">
            <div v-if="traceLoading" class="mx-loading">{{ t('common.loading') || 'Loading...' }}</div>
            <div v-else-if="traceEvents.length === 0" class="chat-trace-empty">
              <div style="font-size:2rem;margin-bottom:12px;">📭</div>
              {{ t('chat.traceEmpty') || 'No trace events found for this conversation.' }}
            </div>
            <div v-if="traceScope === 'global' && traceEvents.length > 0"
              style="padding:8px 12px;background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.25);border-radius:6px;font-size:0.78rem;color:#fbbf24;margin-bottom:8px;">
              {{ t('chat.traceGlobalHint') || 'Showing recent global events (no session-specific trace available for this conversation)' }}
            </div>
            <div v-for="e in traceEvents" :key="e.id" class="chat-trace-event">
              <div class="chat-trace-event-header">
                <span class="chat-trace-event-type" :class="'type-' + e.type">{{ e.type }}</span>
                <span class="chat-trace-event-source">{{ e.source }}</span>
                <span class="chat-trace-event-time">{{ formatTime(e.timestamp) }}</span>
              </div>
              <div class="chat-trace-event-payload">
                <pre><code>{{ JSON.stringify(e.payload, null, 2) }}</code></pre>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  `
};
