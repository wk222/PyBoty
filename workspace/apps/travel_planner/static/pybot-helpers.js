// PyBot App Helpers — DO NOT overwrite this file
// These helpers are always available in app.js

const _BASE = window.location.origin;
const _APP_NAME = (() => {
    const m = window.location.pathname.match(/\/apps\/([^/]+)/);
    return m ? m[1] : '';
})();

async function apiCall(endpoint, options = {}) {
    const isAppAction = !endpoint.startsWith('/') && !endpoint.startsWith('http');
    const url = isAppAction
        ? _BASE + '/api/apps/' + _APP_NAME + '/api'
        : _BASE + endpoint;

    let apiKey = localStorage.getItem('pybot_api_key') || 'dev-key';
    
    const headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey
    };

    const fetchOpts = isAppAction
        ? {
            method: 'POST',
            headers: headers,
            body: JSON.stringify({ action: endpoint, payload: options }),
        }
        : {
            ...options,
            headers: { ...headers, ...(options.headers || {}) },
        };

    let resp = await fetch(url, fetchOpts);
    
    if (resp.status === 401) {
        const newKey = prompt("API Key Required (401 Unauthorized):", apiKey);
        if (newKey && newKey !== apiKey) {
            localStorage.setItem('pybot_api_key', newKey);
            fetchOpts.headers['Authorization'] = 'Bearer ' + newKey;
            resp = await fetch(url, fetchOpts);
        }
    }
    
    const data = await resp.json();
    if (isAppAction && data && data.success && data.result !== undefined) {
        return data.result;
    }
    return data;
}

async function dbQuery(sql) {
    return apiCall('/api/apps/~db/query', {
        method: 'POST',
        body: JSON.stringify({ sql })
    });
}

async function dbWrite(sql, params) {
    return apiCall('/api/apps/~db/write', {
        method: 'POST',
        body: JSON.stringify({ sql, params: params || [] })
    });
}

// --- Agent-Driven Helpers ---

let _agentThreadId = null;

async function agentEnsureThread() {
    if (_agentThreadId) return _agentThreadId;
    const data = await apiCall('/api/conversations', { method: 'POST', body: '{}' });
    _agentThreadId = data.id || data.thread_id;
    return _agentThreadId;
}

async function agentChat(message, onChunk) {
    const threadId = await agentEnsureThread();
    let apiKey = localStorage.getItem('pybot_api_key') || 'dev-key';
    
    let fetchOpts = {
        method: 'POST',
        headers: { 
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + apiKey
        },
        body: JSON.stringify({ thread_id: threadId, message })
    };
    
    let resp = await fetch(_BASE + '/api/chat/stream', fetchOpts);
    
    if (resp.status === 401) {
        const newKey = prompt("API Key Required (401 Unauthorized):", apiKey);
        if (newKey && newKey !== apiKey) {
            localStorage.setItem('pybot_api_key', newKey);
            fetchOpts.headers['Authorization'] = 'Bearer ' + newKey;
            resp = await fetch(_BASE + '/api/chat/stream', fetchOpts);
        }
    }
    
    if (!onChunk) return resp.json();
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let contentFull = '';
    let buffer = '';
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith('data: ')) continue;
            try {
                const evt = JSON.parse(trimmed.slice(6));
                if (evt.type === 'done' || evt.type === 'error') {
                    contentFull = evt.content || '';
                    onChunk(evt.content || '', contentFull);
                }
            } catch (_) {}
        }
    }
    return contentFull;
}

async function agentRunWorkflow(workflowName, inputVars = {}) {
    return apiCall('/api/workflows/trigger', {
        method: 'POST',
        body: JSON.stringify({ name: workflowName, input_vars: inputVars })
    });
}

async function agentSearch(query) {
    return apiCall('/api/search?q=' + encodeURIComponent(query));
}

async function agentKnowledgeQuery(query, collection = 'default', topK = 5) {
    return apiCall('/api/knowledge/search', {
        method: 'POST',
        body: JSON.stringify({ query, collection, top_k: topK })
    });
}

async function agentListTools() {
    return apiCall('/api/tools');
}

async function agentCallTool(toolName, args = {}) {
    let raw = await apiCall('/api/apps/' + _APP_NAME + '/tool/' + encodeURIComponent(toolName) + '/run', {
        method: 'POST',
        body: JSON.stringify(args)
    });
    if (typeof raw === 'string') {
        try { raw = JSON.parse(raw); } catch (_) { /* keep string */ }
    }
    if (raw && typeof raw === 'object' && raw.success === true && raw.result !== undefined) {
        raw = raw.result;
    }
    if (Array.isArray(raw)) {
        Object.defineProperty(raw, 'list', { value: raw, enumerable: false });
        Object.defineProperty(raw, 'data', { value: raw, enumerable: false });
        Object.defineProperty(raw, 'result', { value: raw, enumerable: false });
    }
    return raw;
}
