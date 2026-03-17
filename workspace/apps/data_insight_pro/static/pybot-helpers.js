// PyBot App Helpers — DO NOT overwrite this file
// These helpers are always available in app.js

async function apiCall(endpoint, options = {}) {
    const base = window.location.origin;
    const resp = await fetch(base + endpoint, {
        headers: { 'Content-Type': 'application/json' },
        ...options
    });
    return resp.json();
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
