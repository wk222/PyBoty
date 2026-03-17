import { ref, reactive, onMounted, nextTick, watch } from 'vue';
import { API } from '/static/api/index.js';
import { toast } from '/static/stores/global.js';

function escapeHtml(str) {
  if (!str) return '';
  const d = document.createElement('div');
  d.textContent = str;
  return d.innerHTML;
}

function formatContent(text) {
  if (!text) return '';
  let s = escapeHtml(text);
  s = s.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
  s = s.replace(/\n/g, '<br>');
  return s;
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

export default {
  name: 'ChatView',
  setup() {
    const conversations = ref([]);
    const currentThreadId = ref(null);
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

    const filteredConversations = () => {
      if (!convSearch.value.trim()) return conversations.value;
      const q = convSearch.value.toLowerCase();
      return conversations.value.filter(c => (c.title || '').toLowerCase().includes(q));
    };

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
          messages.value = [];
        }
        await loadConversations();
      } catch (e) { toast('Failed to delete', 'error'); }
    }

    async function switchConversation(id) {
      currentThreadId.value = id;
      messages.value = [];
      steps.value = [];
      showSteps.value = false;
      try {
        const data = await API.getHistory(id);
        messages.value = (data.messages || []).map(m => ({
          role: m.role,
          content: m.content,
          html: formatContent(m.content),
          timestamp: formatTime(m.timestamp),
        }));
        await nextTick();
        scrollToBottom();
      } catch (e) { console.error(e); }
      if (inputEl.value) inputEl.value.focus();
    }

    async function sendMessage() {
      if (isSending.value) return;
      const text = inputText.value.trim();
      if (!text || !currentThreadId.value) return;

      isSending.value = true;
      inputText.value = '';
      showSteps.value = true;
      stepsExpanded.value = true;
      stepsFinished.value = false;
      steps.value = [{ icon: '\u{1F680}', text: 'Sending...' }];
      streamingContent.value = '';
      isStreaming.value = true;

      messages.value.push({
        role: 'user', content: text,
        html: formatContent(text),
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
                steps.value.push({ icon: evt.icon || '\u{1F4CB}', text: evt.content });
              } else if (evt.type === 'done') {
                streamingContent.value = '';
                isStreaming.value = false;
                messages.value.push({
                  role: 'assistant', content: evt.content,
                  html: formatContent(evt.content),
                  timestamp: formatTime(Date.now() / 1000),
                });
                stepsFinished.value = true;
                setTimeout(() => { showSteps.value = false; }, 3000);
                const conv = conversations.value.find(c => c.thread_id === currentThreadId.value);
                if (conv && (conv.message_count || 0) === 0) {
                  conv.title = text.substring(0, 30) + (text.length > 30 ? '...' : '');
                }
              } else if (evt.type === 'error') {
                isStreaming.value = false;
                messages.value.push({
                  role: 'assistant', content: evt.content,
                  html: '<span style="color:var(--error)">' + escapeHtml(evt.content) + '</span>',
                  timestamp: formatTime(Date.now() / 1000),
                });
                steps.value.push({ icon: '\u274C', text: evt.content });
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

    function onInputKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
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

    onMounted(async () => {
      await loadConversations();
      if (conversations.value.length > 0) {
        await switchConversation(conversations.value[0].thread_id);
      } else {
        await createConversation();
      }
    });

    return {
      conversations, currentThreadId, messages, inputText, isSending,
      steps, showSteps, stepsExpanded, stepsFinished, streamingContent, isStreaming,
      convSearch, messagesEl, inputEl, fileInput,
      filteredConversations, loadConversations, createConversation,
      deleteConversation, switchConversation, sendMessage, onInputKeydown,
      handleFileUpload, formatTime, escapeHtml, formatBytes,
    };
  },
  template: `
    <div class="chat-layout">
      <!-- Sidebar: Conversations -->
      <aside class="sidebar">
        <div class="sidebar-header">
          <h1>PyBot Chat</h1>
          <div class="subtitle">AI-Powered Conversations</div>
        </div>
        <button class="new-chat-btn" @click="createConversation">
          <span>+</span> New Chat
        </button>
        <div style="padding:0 12px 8px;">
          <input v-model="convSearch" type="text" placeholder="Search conversations..."
            style="width:100%;padding:6px 10px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-primary);font-size:12px;outline:none;" />
        </div>
        <div class="conversation-list">
          <div v-if="filteredConversations().length === 0" style="padding:24px 12px;text-align:center;color:var(--text-muted);font-size:12px;">
            No conversations yet
          </div>
          <div v-for="c in filteredConversations()" :key="c.thread_id"
               class="conv-item" :class="{ active: c.thread_id === currentThreadId }"
               @click="switchConversation(c.thread_id)">
            <div class="conv-icon">&#x1F4AC;</div>
            <div class="conv-info">
              <div class="conv-title">{{ c.title || 'Untitled' }}</div>
              <div class="conv-meta">{{ c.message_count || 0 }} msgs &middot; {{ formatTime(c.last_message_at) }}</div>
            </div>
            <button class="conv-delete" @click.stop="deleteConversation(c.thread_id)" title="Delete">&times;</button>
          </div>
        </div>
      </aside>

      <!-- Main Chat Area -->
      <main class="main">
        <div class="chat-header">
          <div>
            <div class="chat-title">{{ currentThreadId ? (conversations.find(c => c.thread_id === currentThreadId)?.title || currentThreadId) : 'Select or create a conversation' }}</div>
            <div class="chat-thread-id" v-if="currentThreadId">{{ currentThreadId }}</div>
          </div>
        </div>

        <div class="chat-messages" ref="messagesEl">
          <div v-if="messages.length === 0 && !isStreaming" class="empty-state">
            <div class="logo">&#x1F6E0;</div>
            <h2>Welcome to PyBot</h2>
            <p>I can <strong>create tools and agents</strong> autonomously.<br>
            Try: "Create a tool to calculate circle area" or "Create a data analyst agent".</p>
          </div>

          <div v-for="(msg, i) in messages" :key="i" class="message" :class="msg.role">
            <div class="avatar">{{ msg.role === 'user' ? '\\u{1F464}' : '\\u{1F916}' }}</div>
            <div>
              <div class="bubble" v-html="msg.html"></div>
              <div class="timestamp">{{ msg.timestamp }}</div>
            </div>
          </div>

          <div v-if="isStreaming" class="message assistant">
            <div class="avatar">&#x1F916;</div>
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
            <span v-if="stepsFinished" style="color:var(--success)">&#10003;</span>
            <span v-else class="steps-spinner"></span>
            <span>{{ stepsFinished ? 'Done' : 'Running...' }}</span>
            <button class="steps-toggle">{{ stepsExpanded ? '\\u25BC' : '\\u25B6' }}</button>
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
          <div class="input-wrapper">
            <input type="file" ref="fileInput" style="display:none" @change="handleFileUpload" />
            <button class="upload-btn" @click="$refs.fileInput.click()" :disabled="!currentThreadId" title="Upload file">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>
              </svg>
            </button>
            <textarea ref="inputEl" v-model="inputText"
              :disabled="!currentThreadId"
              @keydown="onInputKeydown"
              :placeholder="currentThreadId ? 'Type a message... (Enter to send, Shift+Enter for newline)' : 'Loading conversation...'"
              rows="1"
              @input="e => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 150) + 'px'; }"></textarea>
            <button class="send-btn" @click="sendMessage" :disabled="!currentThreadId || isSending">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
        </div>
      </main>
    </div>
  `
};
