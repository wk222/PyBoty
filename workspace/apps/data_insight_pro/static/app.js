let currentCharts = [];

async function showView(viewId) {
    document.querySelectorAll('.view-content').forEach(v => v.classList.add('d-none'));
    document.getElementById(`view-${viewId}`).classList.remove('d-none');
    
    document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
    event.target.classList.add('active');

    if (viewId === 'dashboard') {
        loadDashboard();
    } else if (viewId === 'tables') {
        loadTableList();
    }
}

async function loadDashboard() {
    toggleLoading(true);
    const result = await apiCall('/api/apps/data_insight_pro/api', {
        method: 'POST',
        body: JSON.stringify({ action: 'list_tables', payload: {} })
    });
    
    if (result.tables) {
        document.getElementById('stat-table-count').innerText = result.tables.length;
        const list = document.getElementById('recent-tables-list');
        list.innerHTML = result.tables.map(t => `
            <li class="list-group-item d-flex justify-content-between align-items-center">
                ${t}
                <button class="btn btn-sm btn-outline-primary" onclick="goToTable('${t}')">查看</button>
            </li>
        `).join('');
    }
    toggleLoading(false);
}

function goToTable(tableName) {
    showView('tables');
    document.getElementById('table-selector').value = tableName;
    loadTableData();
}

async function loadTableList() {
    const result = await apiCall('/api/apps/data_insight_pro/api', {
        method: 'POST',
        body: JSON.stringify({ action: 'list_tables', payload: {} })
    });
    
    const selector = document.getElementById('table-selector');
    const currentVal = selector.value;
    selector.innerHTML = '<option value="">选择一个表...</option>' + 
        result.tables.map(t => `<option value="${t}">${t}</option>`).join('');
    selector.value = currentVal;
}

async function loadTableData() {
    const tableName = document.getElementById('table-selector').value;
    if (!tableName) return;

    toggleLoading(true);
    
    // Load Preview
    const previewResult = await apiCall('/api/apps/data_insight_pro/api', {
        method: 'POST',
        body: JSON.stringify({ action: 'get_table_preview', payload: { table_name: tableName } })
    });
    
    renderTable('preview-thead', 'preview-tbody', previewResult);

    // Load Stats & Charts
    const statsResult = await apiCall('/api/apps/data_insight_pro/api', {
        method: 'POST',
        body: JSON.stringify({ action: 'get_column_stats', payload: { table_name: tableName } })
    });
    
    renderCharts(statsResult.stats);
    
    toggleLoading(false);
}

function renderTable(theadId, tbodyId, result) {
    const thead = document.getElementById(theadId);
    const tbody = document.getElementById(tbodyId);
    
    if (result.error) {
        tbody.innerHTML = `<tr><td colspan="100" class="text-danger">Error: ${result.error}</td></tr>`;
        return;
    }

    thead.innerHTML = result.columns.map(c => `<th>${c}</th>`).join('');
    tbody.innerHTML = result.data.map(row => `
        <tr>${result.columns.map(c => `<td>${row[c]}</td>`).join('')}</tr>
    `).join('');
}

function renderCharts(stats) {
    const container = document.getElementById('charts-container');
    container.innerHTML = '';
    
    // Clear old charts
    currentCharts.forEach(c => c.destroy());
    currentCharts = [];

    for (const [col, info] of Object.entries(stats)) {
        const colId = `chart-${col.replace(/[^a-zA-Z0-9]/g, '_')}`;
        const card = document.createElement('div');
        card.className = 'col-md-6 chart-card';
        card.innerHTML = `
            <div class="card h-100">
                <div class="card-body">
                    <h5 class="card-title">${col} <small class="text-muted">(${info.type})</small></h5>
                    <canvas id="${colId}"></canvas>
                    ${info.type === 'numeric' ? `
                        <div class="mt-2 small">
                            Min: ${info.min.toFixed(2)} | Max: ${info.max.toFixed(2)} | Mean: ${info.mean.toFixed(2)}
                        </div>
                    ` : ''}
                </div>
            </div>
        `;
        container.appendChild(card);

        const ctx = document.getElementById(colId).getContext('2d');
        let chart;

        if (info.type === 'numeric') {
            // Simple bar for numeric? Or boxplot? Let's do a simple summary for now
            // In a real app, we'd calculate a histogram in the backend
            chart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: ['Min', 'Mean', 'Max'],
                    datasets: [{
                        label: col,
                        data: [info.min, info.mean, info.max],
                        backgroundColor: 'rgba(54, 162, 235, 0.5)'
                    }]
                }
            });
        } else {
            const labels = Object.keys(info.top_values);
            const data = Object.values(info.top_values);
            chart = new Chart(ctx, {
                type: 'pie',
                data: {
                    labels: labels,
                    datasets: [{
                        data: data,
                        backgroundColor: [
                            '#ff6384', '#36a2eb', '#ffce56', '#4bc0c0', '#9966ff', '#ff9f40'
                        ]
                    }]
                },
                options: { responsive: true, maintainAspectRatio: false }
            });
        }
        currentCharts.push(chart);
    }
}

async function runSqlQuery() {
    const sql = document.getElementById('sql-query').value;
    if (!sql) return;

    toggleLoading(true);
    const result = await apiCall('/api/apps/data_insight_pro/api', {
        method: 'POST',
        body: JSON.stringify({ action: 'execute_sql', payload: { sql: sql } })
    });
    
    document.getElementById('sql-results-container').classList.remove('d-none');
    renderTable('sql-thead', 'sql-tbody', result);
    toggleLoading(false);
}

async function importData() {
    const filePath = document.getElementById('import-file-path').value;
    const tableName = document.getElementById('import-table-name').value;
    
    if (!filePath || !tableName) {
        alert('请提供完整的文件路径和表名');
        return;
    }

    toggleLoading(true);
    const result = await apiCall('/api/apps/data_insight_pro/api', {
        method: 'POST',
        body: JSON.stringify({ 
            action: 'import_file', 
            payload: { file_path: filePath, table_name: tableName } 
        })
    });
    
    toggleLoading(false);
    
    if (result.success) {
        alert('导入成功！');
        loadDashboard();
        showView('dashboard');
    } else {
        alert('导入失败: ' + result.error);
    }
}

function toggleLoading(show) {
    document.getElementById('loading-spinner').classList.toggle('d-none', !show);
}

// Initial load
window.onload = () => loadDashboard();
