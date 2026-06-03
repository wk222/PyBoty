import { ref, computed, onMounted, watch, nextTick } from 'vue';
import { API } from '/static/api/index.js?v=20260531-3';
import { toast } from '/static/stores/global.js?v=20260531-3';

const ALLOWED_FILES = ['SOUL.md', 'IDENTITY.md', 'TEAM.md', 'RULES.md', 'MEMORY.md', 'SCHEDULE.md'];

export default {
  name: 'IdeView',
  props: {
    workspaceId: { type: String, default: 'default' },
    initialFile: { type: String, default: '' },
    threadId: { type: String, default: '' },
  },
  emits: ['file-opened'],
  setup(props, { emit }) {
    const loadingFiles = ref(false);
    const files = ref(['SOUL.md', 'IDENTITY.md', 'TEAM.md', 'RULES.md', 'MEMORY.md', 'SCHEDULE.md']);
    const activeFile = ref('SOUL.md');
    const editorContent = ref('');
    const originalContent = ref('');
    const isSaving = ref(false);

    // AI Assistant Side Panel
    const messages = ref([]);
    const inputText = ref('');
    const isSending = ref(false);
    const selectedModel = ref('Gemini 3.5 Flash');
    const selectedCanvas = ref('balanced');
    const enableGraph = ref(true);
    const streamingContent = ref('');
    const isStreaming = ref(false);
    const chatContainer = ref(null);

    // Simulated Console / Terminal
    const terminalLogs = ref([
      'Welcome to PyBot IDE Interactive Console [Version 3.5-T3]',
      'Initializing secure host pseudoterminal loop...',
      'SQLite MemoryEngine [WAL=Normal] loaded. Graph-Lite schema activated.',
      'Terminal stdout stream ready on WebSocket gateway. Status: OK.',
      'Active session connected: dev-key. Mode: [Assistant]'
    ]);
    const terminalInput = ref('');

    // Available agents for reference
    const agents = ref([]);
    const activeFileIcon = computed(() => {
      if (activeFile.value.endsWith('.md')) return '📝';
      return '📄';
    });

    const isContentModified = computed(() => {
      return editorContent.value !== originalContent.value;
    });

    // ─── File Ops ──────────────────────────────────────────────────────────
    async function fetchFiles() {
      loadingFiles.value = true;
      try {
        const res = await API.listWorkspaceFiles(props.workspaceId);
        if (res && res.files) {
          const names = Array.isArray(res.files) ? res.files : Object.keys(res.files);
          files.value = names.filter((f) => ALLOWED_FILES.includes(f));
          if (files.value.length === 0) {
            files.value = [...ALLOWED_FILES];
          }
        }
      } catch (e) {
        console.warn('Failed to load dynamic file list, using fallbacks:', e);
      }
      loadingFiles.value = false;
    }

    async function selectFile(filename, { force = false } = {}) {
      if (!force && isContentModified.value) {
        if (!confirm('当前文件有未保存的修改，确定要切换吗？')) {
          return;
        }
      }
      activeFile.value = filename;
      try {
        const res = await API.getWorkspaceFile(filename, props.workspaceId);
        editorContent.value = res.content || '';
        originalContent.value = res.content || '';
        addTerminalLog(`[IDE] Loaded ${filename} @ workspace/${props.workspaceId}`);
        emit('file-opened', filename);
      } catch (e) {
        toast('加载文件失败: ' + e.message, 'error');
        addTerminalLog(`[ERROR] Failed to load ${filename}: ${e.message}`);
      }
    }

    async function saveFile() {
      if (!isContentModified.value) return;
      isSaving.value = true;
      try {
        await API.updateWorkspaceFile(activeFile.value, editorContent.value, props.workspaceId);
        originalContent.value = editorContent.value;
        toast('文件保存并同步成功', 'success');
        addTerminalLog(`[IDE] Saved ${activeFile.value} @ workspace/${props.workspaceId}`);
      } catch (e) {
        toast('保存文件失败: ' + e.message, 'error');
        addTerminalLog(`[ERROR] Failed to save ${activeFile.value}: ${e.message}`);
      }
      isSaving.value = false;
    }

    async function reloadWorkspace() {
      messages.value = [];
      await fetchFiles();
      await selectFile(activeFile.value || 'SOUL.md', { force: true });
    }

    // ─── Terminal logs ───────────────────────────────────────────────────────
    function addTerminalLog(text) {
      const hh = String(new Date().getHours()).padStart(2, '0');
      const mm = String(new Date().getMinutes()).padStart(2, '0');
      const ss = String(new Date().getSeconds()).padStart(2, '0');
      terminalLogs.value.push(`[${hh}:${mm}:${ss}] ${text}`);
      nextTick(() => {
        const el = document.getElementById('ide-term-box');
        if (el) el.scrollTop = el.scrollHeight;
      });
    }

    function handleTerminalSubmit() {
      const cmd = terminalInput.value.trim();
      if (!cmd) return;
      addTerminalLog(`$ ${cmd}`);
      terminalInput.value = '';

      // Mock CLI execution inside IDE Console
      if (cmd === 'clear') {
        terminalLogs.value = ['Console cleared.'];
      } else if (cmd === 'help') {
        terminalLogs.value.push(
          'Available commands:',
          '  help            Show this help list',
          '  clear           Clear the terminal console',
          '  memory stats    Read MemoryEngine metrics from SQLite',
          '  graph links     List all Graph-Lite relations'
        );
      } else if (cmd === 'memory stats') {
        addTerminalLog('[MemoryEngine] Reading sqlite metadata...');
        API.getMemoryOverview().then(res => {
          addTerminalLog(`  Active facts: ${res.stats?.memory_lines || 0}`);
          addTerminalLog(`  Journals indexed: ${res.stats?.journal_count || 0}`);
          addTerminalLog(`  Embedding vector count: ${res.stats?.vector_count || 0}`);
        }).catch(err => {
          addTerminalLog(`[ERROR] Stats load failed: ${err.message}`);
        });
      } else if (cmd === 'graph links') {
        addTerminalLog('[Graph-Lite] Querying links index...');
        addTerminalLog('  Connected edges: active. Bidirectional traverser: enabled.');
      } else {
        addTerminalLog(`Command not recognized: '${cmd}'. Type 'help' for a list of commands.`);
      }
    }

    // ─── Assistant Dialogue ──────────────────────────────────────────────────
    function scrollChat() {
      nextTick(() => {
        if (chatContainer.value) {
          chatContainer.value.scrollTop = chatContainer.value.scrollHeight;
        }
      });
    }

    async function sendMsg() {
      const text = inputText.value.trim();
      if (!text || isSending.value) return;

      messages.value.push({ role: 'user', content: text, ts: Date.now() / 1000 });
      inputText.value = '';
      isSending.value = true;
      isStreaming.value = true;
      streamingContent.value = '';
      scrollChat();

      addTerminalLog(`[AI] Assistant query sent: "${text.substring(0, 30)}..."`);

      try {
        let threadId = props.threadId;
        if (!threadId) {
          const res = await API.listConversations();
          const prefix = `ws:${props.workspaceId}:`;
          const scoped = (res.conversations || []).filter((c) =>
            String(c.thread_id || '').startsWith(prefix)
          );
          threadId = scoped.length > 0 ? scoped[0].thread_id : `${prefix}session-${Date.now().toString(36)}`;
        }

        const response = await API.chatStream(threadId, text);
        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          
          // Parse Server-Sent Events (SSE)
          const lines = chunk.split('\n');
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.substring(6));
                if (data.type === 'token') {
                  streamingContent.value += data.content;
                  scrollChat();
                } else if (data.type === 'step') {
                  addTerminalLog(`[AI STEP] ${data.content}`);
                } else if (data.type === 'event') {
                  // Real-time Event Bus Streaming to IDE Output Console
                  const payloadStr = data.payload ? JSON.stringify(data.payload) : '';
                  addTerminalLog(`[EVENT:${data.event_type}] ${data.source || 'system'} -> ${payloadStr.substring(0, 100)}`);
                }
              } catch (err) {
                // ignore invalid partial JSON
              }
            }
          }
        }

        messages.value.push({
          role: 'assistant',
          content: streamingContent.value || 'No response returned.',
          ts: Date.now() / 1000
        });
        addTerminalLog(`[AI] Streaming complete. Total tokens synced.`);
      } catch (e) {
        messages.value.push({
          role: 'assistant',
          content: 'Error: ' + e.message,
          ts: Date.now() / 1000
        });
        addTerminalLog(`[ERROR] AI stream failed: ${e.message}`);
      } finally {
        isSending.value = false;
        isStreaming.value = false;
        streamingContent.value = '';
        scrollChat();
      }
    }

    watch(() => props.workspaceId, () => {
      reloadWorkspace();
    });

    watch(() => props.initialFile, (file) => {
      if (file) selectFile(file);
    });

    watch(() => props.threadId, async (threadId) => {
      if (!threadId) {
        messages.value = [];
        return;
      }
      try {
        const res = await API.getHistory(threadId);
        messages.value = (res.messages || []).map((m) => ({
          role: m.role,
          content: m.content,
          ts: m.timestamp || Date.now() / 1000,
        }));
        scrollChat();
      } catch (e) {
        messages.value = [];
      }
    });

    onMounted(async () => {
      await reloadWorkspace();
      if (props.initialFile) {
        await selectFile(props.initialFile, { force: true });
      }
      try {
        const res = await API.listAgents();
        agents.value = res.agents || [];
      } catch (e) {
        console.warn('Failed to load agents list:', e);
      }
    });

    return {
      loadingFiles, files, activeFile, editorContent, originalContent, isSaving,
      messages, inputText, isSending, selectedModel, selectedCanvas, enableGraph,
      streamingContent, isStreaming, chatContainer, terminalLogs, terminalInput,
      agents, activeFileIcon, isContentModified,
      selectFile, saveFile, handleTerminalSubmit, sendMsg
    };
  },
  template: `
    <div class="ide-layout">
      <!-- Center Column: Visual Document Editor & Console -->
      <div class="ide-column-center">
        <!-- Editor Header -->
        <div class="ide-editor-header">
          <div class="ide-editor-tab">
            <span style="margin-right:6px;">{{ activeFileIcon }}</span>
            <span style="font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:500;">{{ activeFile }}</span>
            <span v-if="isContentModified" class="ide-dirty-dot" style="margin-left:8px;position:static;display:inline-block;"></span>
          </div>
          <button 
            :class="['ide-save-btn', isContentModified && 'ide-save-btn--dirty']" 
            @click="saveFile" 
            :disabled="!isContentModified || isSaving"
          >
            {{ isSaving ? 'Saving...' : 'Save & Sync' }}
          </button>
        </div>

        <!-- Editor Area -->
        <div class="ide-editor-container">
          <textarea 
            class="ide-code-textarea" 
            v-model="editorContent"
            placeholder="// Type file modifications here..."
            spellcheck="false"
          ></textarea>
        </div>

        <!-- Bottom Simulated Console -->
        <div class="ide-console-panel">
          <div class="ide-console-header">
            <svg class="ide-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>
            </svg>
            <span style="font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#858585;">IDE Output Console</span>
          </div>
          <div id="ide-term-box" class="ide-term-output">
            <div v-for="(log, i) in terminalLogs" :key="i" class="ide-term-line">{{ log }}</div>
          </div>
          <form class="ide-term-input-form" @submit.prevent="handleTerminalSubmit">
            <span class="ide-term-prompt">$</span>
            <input 
              type="text" 
              class="ide-term-input" 
              v-model="terminalInput" 
              placeholder="Type command ('help', 'memory stats', 'graph links', 'clear')..." 
            />
          </form>
        </div>
      </div>

      <!-- Right Column: Integrated IDE AI Assistant -->
      <div class="ide-column-right">
        <div class="ide-assistant-header">
          <svg class="ide-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          <span style="font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:1px;color:#858585;">AI Copilot</span>
        </div>

        <!-- Chat History -->
        <div class="ide-chat-history" ref="chatContainer">
          <div v-if="messages.length === 0" class="ide-chat-welcome">
            <div class="ide-welcome-icon">🧠</div>
            <div class="ide-welcome-title">PyBot Unified Copilot</div>
            <div class="ide-welcome-desc">我已深度集成工作区。通过底部的 Graph-Lite 关联引擎与自适应遗忘策略，我可以访问并跨越分析以下文档与任务逻辑。</div>
          </div>
          <div 
            v-for="(msg, i) in messages" 
            :key="i" 
            :class="['ide-chat-msg', msg.role === 'user' ? 'ide-chat-msg--user' : 'ide-chat-msg--assistant']"
          >
            <div class="ide-msg-avatar">{{ msg.role === 'user' ? 'U' : 'AI' }}</div>
            <div class="ide-msg-content" v-html="msg.content"></div>
          </div>
          <div v-if="isStreaming" class="ide-chat-msg ide-chat-msg--assistant">
            <div class="ide-msg-avatar">AI</div>
            <div class="ide-msg-content" v-html="streamingContent"></div>
          </div>
        </div>

        <!-- Float Chat Controls -->
        <div class="ide-chat-controls">
          <div class="ide-control-row">
            <!-- Model Pill -->
            <div class="ide-pill">
              <span class="ide-pill-dot"></span>
              {{ selectedModel }}
            </div>
            <!-- Canvas Pill -->
            <div class="ide-pill" style="border-color:#3c3c3c;color:#a855f7;">
              🧬 {{ selectedCanvas }}
            </div>
            <!-- Graph Pill -->
            <div 
              class="ide-pill" 
              :style="{ borderColor: enableGraph ? '#3bb373' : '#444', color: enableGraph ? '#3bb373' : '#777' }"
              @click="enableGraph = !enableGraph"
            >
              🕸️ Graph-Lite
            </div>
          </div>

          <!-- Message input -->
          <div class="ide-input-box">
            <textarea 
              class="ide-input-textarea"
              v-model="inputText"
              placeholder="Ask Copilot (e.g. '如何修改 SOUL.md' or '长期记忆指标')..."
              @keydown.enter.exact.prevent="sendMsg"
            ></textarea>
            <button class="ide-send-btn" @click="sendMsg" :disabled="!inputText.trim() || isSending">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                <line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  `
};
