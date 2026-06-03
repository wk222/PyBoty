"""Agent-driven app templates: chat, RAG, workflow."""

from __future__ import annotations

from typing import Any

CHAT_APP_CSS = """* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; height: 100vh; display: flex; flex-direction: column; }
.chat-header { padding: 16px 24px; background: #1e293b; border-bottom: 1px solid #334155; display: flex; align-items: center; gap: 12px; }
.chat-header h1 { font-size: 18px; font-weight: 600; }
.chat-header .subtitle { font-size: 12px; color: #94a3b8; }
.chat-messages { flex: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
.msg { max-width: 75%; padding: 12px 16px; border-radius: 16px; font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.msg.user { align-self: flex-end; background: #6366f1; color: white; border-bottom-right-radius: 4px; }
.msg.assistant { align-self: flex-start; background: #1e293b; border: 1px solid #334155; border-bottom-left-radius: 4px; }
.msg.assistant .thinking { color: #94a3b8; font-style: italic; }
.chat-input-area { padding: 16px 24px; background: #1e293b; border-top: 1px solid #334155; display: flex; gap: 12px; }
.chat-input-area input { flex: 1; padding: 12px 16px; background: #0f172a; border: 1px solid #334155; border-radius: 12px; color: #e2e8f0; font-size: 14px; outline: none; }
.chat-input-area input:focus { border-color: #6366f1; }
.chat-input-area button { padding: 12px 24px; background: #6366f1; color: white; border: none; border-radius: 12px; cursor: pointer; font-weight: 600; }
.chat-input-area button:hover { background: #4f46e5; }
.chat-input-area button:disabled { opacity: 0.5; cursor: not-allowed; }
"""

CHAT_APP_JS = """const messagesEl = document.getElementById('messages');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('send');

function addMessage(role, content) {
    const div = document.createElement('div');
    div.className = 'msg ' + role;
    div.textContent = content;
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return div;
}

async function sendMessage() {
    const text = inputEl.value.trim();
    if (!text) return;
    inputEl.value = '';
    sendBtn.disabled = true;
    addMessage('user', text);
    const assistantDiv = addMessage('assistant', '');
    try {
        await agentChat(text, (chunk, full) => {
            assistantDiv.textContent = full;
            messagesEl.scrollTop = messagesEl.scrollHeight;
        });
    } catch (e) {
        assistantDiv.textContent = 'Error: ' + e.message;
    }
    sendBtn.disabled = false;
    inputEl.focus();
}

sendBtn.addEventListener('click', sendMessage);
inputEl.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } });
inputEl.focus();
"""

RAG_APP_CSS = """* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
.rag-container { max-width: 900px; margin: 0 auto; padding: 40px 24px; }
.rag-header { text-align: center; margin-bottom: 32px; }
.rag-header h1 { font-size: 28px; font-weight: 700; margin-bottom: 8px; }
.rag-header p { color: #94a3b8; }
.search-box { display: flex; gap: 12px; margin-bottom: 32px; }
.search-box input { flex: 1; padding: 14px 20px; background: #1e293b; border: 1px solid #334155; border-radius: 12px; color: #e2e8f0; font-size: 16px; outline: none; }
.search-box input:focus { border-color: #6366f1; }
.search-box button { padding: 14px 28px; background: #6366f1; color: white; border: none; border-radius: 12px; cursor: pointer; font-weight: 600; }
.results { display: flex; flex-direction: column; gap: 16px; }
.result-card { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 20px; }
.result-card .score { color: #6366f1; font-size: 12px; font-weight: 600; margin-bottom: 8px; }
.result-card .content { font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
.result-card .source { color: #64748b; font-size: 11px; margin-top: 8px; }
.answer-box { background: #1e293b; border: 2px solid #6366f1; border-radius: 12px; padding: 20px; margin-bottom: 24px; }
.answer-box h3 { color: #6366f1; font-size: 14px; margin-bottom: 8px; }
.answer-box .answer-text { font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
"""

RAG_APP_JS = """const queryInput = document.getElementById('query');
const searchBtn = document.getElementById('search');
const answerBox = document.getElementById('answer-box');
const answerText = document.getElementById('answer-text');
const resultsEl = document.getElementById('results');

async function doSearch() {
    const q = queryInput.value.trim();
    if (!q) return;
    searchBtn.disabled = true;
    resultsEl.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:20px;">Searching...</div>';
    answerBox.style.display = 'none';
    try {
        const knowledgeData = await agentKnowledgeQuery(q);
        const docs = knowledgeData.results || knowledgeData.documents || [];
        resultsEl.innerHTML = '';
        docs.forEach(doc => {
            const card = document.createElement('div');
            card.className = 'result-card';
            card.innerHTML = '<div class="score">Score: ' + (doc.score || 'N/A').toString().slice(0,5) + '</div>'
                + '<div class="content">' + (doc.content || doc.page_content || '') + '</div>'
                + '<div class="source">' + (doc.metadata?.source || '') + '</div>';
            resultsEl.appendChild(card);
        });
        if (docs.length === 0) resultsEl.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:20px;">No results found</div>';

        answerBox.style.display = 'block';
        answerText.textContent = 'Generating answer...';
        await agentChat('Based on my knowledge base, answer this question: ' + q, (chunk, full) => {
            answerText.textContent = full;
        });
    } catch (e) {
        resultsEl.innerHTML = '<div style="color:#ef4444;padding:20px;">Error: ' + e.message + '</div>';
    }
    searchBtn.disabled = false;
}

searchBtn.addEventListener('click', doSearch);
queryInput.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
queryInput.focus();
"""

WORKFLOW_APP_CSS = """* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
.wf-container { max-width: 800px; margin: 0 auto; padding: 40px 24px; }
.wf-header { text-align: center; margin-bottom: 32px; }
.wf-header h1 { font-size: 24px; font-weight: 700; }
.wf-form { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; margin-bottom: 24px; }
.wf-form label { display: block; color: #94a3b8; font-size: 12px; margin-bottom: 4px; }
.wf-form input, .wf-form textarea { width: 100%; padding: 10px 14px; background: #0f172a; border: 1px solid #334155; border-radius: 8px; color: #e2e8f0; margin-bottom: 12px; }
.wf-form button { padding: 12px 24px; background: #6366f1; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; width: 100%; }
.wf-result { background: #1e293b; border: 1px solid #334155; border-radius: 12px; padding: 24px; }
.wf-result h3 { color: #6366f1; margin-bottom: 12px; }
.wf-result pre { font-size: 13px; line-height: 1.5; overflow-x: auto; white-space: pre-wrap; }
.wf-status { display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; margin-bottom: 12px; }
.wf-status.success { background: rgba(16,185,129,0.15); color: #10b981; }
.wf-status.error { background: rgba(239,68,68,0.15); color: #ef4444; }
"""

WORKFLOW_APP_JS = """const runBtn = document.getElementById('run');
const resultEl = document.getElementById('result');
const statusEl = document.getElementById('status');
const outputEl = document.getElementById('output');
const WORKFLOW_NAME = document.body.dataset.workflow || '';

async function runWorkflow() {
    const inputs = {};
    document.querySelectorAll('.wf-input').forEach(el => {
        if (el.value.trim()) inputs[el.name] = el.value.trim();
    });
    runBtn.disabled = true;
    runBtn.textContent = 'Running...';
    resultEl.style.display = 'none';
    try {
        const data = await agentRunWorkflow(WORKFLOW_NAME, inputs);
        resultEl.style.display = 'block';
        if (data.success !== false) {
            statusEl.className = 'wf-status success';
            statusEl.textContent = 'Completed';
            outputEl.textContent = JSON.stringify(data.result || data, null, 2);
        } else {
            statusEl.className = 'wf-status error';
            statusEl.textContent = 'Failed';
            outputEl.textContent = data.error || 'Unknown error';
        }
    } catch (e) {
        resultEl.style.display = 'block';
        statusEl.className = 'wf-status error';
        statusEl.textContent = 'Error';
        outputEl.textContent = e.message;
    }
    runBtn.disabled = false;
    runBtn.textContent = 'Run Workflow';
}

runBtn.addEventListener('click', runWorkflow);
"""


def build_chat_html(name: str, display_name: str, description: str) -> str:
    title = display_name or name
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="static/style.css">
</head>
<body>
    <div class="chat-header">
        <div>
            <h1>{title}</h1>
            <div class="subtitle">{description}</div>
        </div>
    </div>
    <div id="messages" class="chat-messages"></div>
    <div class="chat-input-area">
        <input id="input" type="text" placeholder="Type a message..." autocomplete="off" />
        <button id="send">Send</button>
    </div>
    <script src="static/pybot-helpers.js"></script>
    <script src="static/app.js"></script>
</body>
</html>"""


def build_rag_html(name: str, display_name: str, description: str) -> str:
    title = display_name or name
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="static/style.css">
</head>
<body>
    <div class="rag-container">
        <div class="rag-header">
            <h1>{title}</h1>
            <p>{description}</p>
        </div>
        <div class="search-box">
            <input id="query" type="text" placeholder="Ask anything about the knowledge base..." autocomplete="off" />
            <button id="search">Search</button>
        </div>
        <div id="answer-box" class="answer-box" style="display:none;">
            <h3>AI Answer</h3>
            <div id="answer-text" class="answer-text"></div>
        </div>
        <div id="results" class="results"></div>
    </div>
    <script src="static/pybot-helpers.js"></script>
    <script src="static/app.js"></script>
</body>
</html>"""


def build_workflow_html(name: str, display_name: str, description: str, workflow_name: str) -> str:
    title = display_name or name
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="static/style.css">
</head>
<body data-workflow="{workflow_name}">
    <div class="wf-container">
        <div class="wf-header">
            <h1>{title}</h1>
            <p>{description}</p>
        </div>
        <div class="wf-form">
            <label>Input (JSON or key=value)</label>
            <textarea class="wf-input" name="input" rows="4" placeholder='{{"key": "value"}}'></textarea>
            <button id="run">Run Workflow</button>
        </div>
        <div id="result" class="wf-result" style="display:none;">
            <span id="status" class="wf-status"></span>
            <h3>Output</h3>
            <pre id="output"></pre>
        </div>
    </div>
    <script src="static/pybot-helpers.js"></script>
    <script src="static/app.js"></script>
</body>
</html>"""


APP_TEMPLATES: dict[str, dict[str, Any]] = {
    "chat": {"css": CHAT_APP_CSS, "js": CHAT_APP_JS, "html_builder": build_chat_html},
    "rag": {"css": RAG_APP_CSS, "js": RAG_APP_JS, "html_builder": build_rag_html},
    "workflow": {"css": WORKFLOW_APP_CSS, "js": WORKFLOW_APP_JS, "html_builder": build_workflow_html},
}
