#!/usr/bin/env python3
"""
PF67 UI Patch Script v2 - Corrected for actual file structure
Run from /volume1/docker/pf67:
    python3 patch_ui_v2.py
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent / "app" / "pf67"

# =============================================================================
# FILE 1: templates/flows.html (corrected routes)
# =============================================================================
FLOWS_HTML = '''{% extends "base.html" %}
{% block title %}Flows{% endblock %}
{% block content %}
<div class="page-header">
    <h1>Flows</h1>
    <a href="{{ url_for('views.create_flow') }}" class="btn btn-primary">+ New Flow</a>
</div>

<div class="filter-bar">
    <input type="text" id="flowSearch" class="form-control" placeholder="Filter flows..." onkeyup="filterFlows()">
</div>

<table class="data-table" id="flowsTable">
    <thead>
        <tr>
            <th>Name</th>
            <th>Protocol</th>
            <th>Status</th>
            <th>Started</th>
            <th>Current Step</th>
            <th>Actions</th>
        </tr>
    </thead>
    <tbody>
        {% for flow in flows %}
        <tr>
            <td><a href="{{ url_for('views.flow_detail', flow_id=flow.id) }}">{{ flow.name }}</a></td>
            <td>{{ flow.protocol.name if flow.protocol else '-' }}</td>
            <td><span class="status-badge status-{{ flow.status|lower }}">{{ flow.status }}</span></td>
            <td>{{ flow.started_at.strftime('%Y-%m-%d %H:%M') if flow.started_at else '-' }}</td>
            <td>
                {% set current = flow.steps|selectattr('status', 'equalto', 'pending')|first %}
                {{ current.name if current else '-' }}
            </td>
            <td class="actions">
                <a href="{{ url_for('views.edit_flow', flow_id=flow.id) }}" class="btn btn-sm">Edit</a>
                <form method="POST" action="{{ url_for('views.delete_flow', flow_id=flow.id) }}" style="display:inline;" onsubmit="return confirm('Delete this flow?')">
                    <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                </form>
            </td>
        </tr>
        {% else %}
        <tr>
            <td colspan="6" class="empty-state">No flows yet. Create your first one!</td>
        </tr>
        {% endfor %}
    </tbody>
</table>

<script>
function filterFlows() {
    const input = document.getElementById('flowSearch');
    const filter = input.value.toLowerCase();
    const table = document.getElementById('flowsTable');
    const rows = table.getElementsByTagName('tr');
    
    for (let i = 1; i < rows.length; i++) {
        const cells = rows[i].getElementsByTagName('td');
        let match = false;
        for (let j = 0; j < cells.length; j++) {
            if (cells[j] && cells[j].textContent.toLowerCase().includes(filter)) {
                match = true;
                break;
            }
        }
        rows[i].style.display = match ? '' : 'none';
    }
}
</script>
{% endblock %}
'''

# =============================================================================
# FILE 2: static/style.css (your actual path, not static/css/style.css)
# =============================================================================
STYLE_CSS = ''':root {
    --primary: #3b82f6;
    --primary-hover: #2563eb;
    --danger: #ef4444;
    --danger-hover: #dc2626;
    --success: #22c55e;
    --warning: #f59e0b;
    --bg: #0f172a;
    --bg-card: #1e293b;
    --bg-input: #334155;
    --text: #f1f5f9;
    --text-muted: #94a3b8;
    --border: #475569;
    --radius: 8px;
    --shadow: 0 4px 6px -1px rgba(0,0,0,0.3);
}

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
    min-height: 100vh;
}

/* Layout */
.container {
    max-width: 1200px;
    margin: 0 auto;
    padding: 1rem;
}

/* Navigation */
nav {
    background: var(--bg-card);
    padding: 1rem;
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    z-index: 100;
}

nav ul {
    list-style: none;
    display: flex;
    gap: 1rem;
    flex-wrap: wrap;
    align-items: center;
}

nav a {
    color: var(--text);
    text-decoration: none;
    padding: 0.5rem 1rem;
    border-radius: var(--radius);
    transition: background 0.2s;
}

nav a:hover, nav a.active {
    background: var(--primary);
}

/* Page Header */
.page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 1.5rem;
    flex-wrap: wrap;
    gap: 1rem;
}

.page-header h1 {
    font-size: 1.75rem;
    font-weight: 600;
}

/* Buttons */
.btn {
    display: inline-block;
    padding: 0.5rem 1rem;
    background: var(--bg-input);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    cursor: pointer;
    text-decoration: none;
    font-size: 0.875rem;
    transition: all 0.2s;
}

.btn:hover {
    background: var(--border);
}

.btn-primary {
    background: var(--primary);
    border-color: var(--primary);
}

.btn-primary:hover {
    background: var(--primary-hover);
}

.btn-danger {
    background: var(--danger);
    border-color: var(--danger);
}

.btn-danger:hover {
    background: var(--danger-hover);
}

.btn-sm {
    padding: 0.25rem 0.5rem;
    font-size: 0.75rem;
}

/* Forms */
.form-group {
    margin-bottom: 1rem;
}

.form-group label {
    display: block;
    margin-bottom: 0.5rem;
    font-weight: 500;
}

.form-control {
    width: 100%;
    padding: 0.75rem;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    color: var(--text);
    font-size: 1rem;
}

.form-control:focus {
    outline: none;
    border-color: var(--primary);
}

select.form-control {
    appearance: none;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='[w3.org](http://www.w3.org/2000/svg)' width='12' height='12' fill='%2394a3b8' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10l-5 5z'/%3E%3C/svg%3E");
    background-repeat: no-repeat;
    background-position: right 0.75rem center;
    padding-right: 2.5rem;
}

/* Filter Bar */
.filter-bar {
    margin-bottom: 1rem;
}

.filter-bar input {
    max-width: 300px;
}

/* Tables */
.data-table {
    width: 100%;
    border-collapse: collapse;
    background: var(--bg-card);
    border-radius: var(--radius);
    overflow: hidden;
    box-shadow: var(--shadow);
}

.data-table th,
.data-table td {
    padding: 0.75rem 1rem;
    text-align: left;
    border-bottom: 1px solid var(--border);
}

.data-table th {
    background: var(--bg-input);
    font-weight: 600;
    font-size: 0.875rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.data-table tr:hover {
    background: rgba(59, 130, 246, 0.1);
}

.data-table a {
    color: var(--primary);
}

.data-table .actions {
    white-space: nowrap;
}

.empty-state {
    text-align: center;
    color: var(--text-muted);
    padding: 2rem !important;
}

/* Status Badges */
.status-badge {
    display: inline-block;
    padding: 0.25rem 0.75rem;
    border-radius: 9999px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
}

.status-active, .status-running {
    background: rgba(34, 197, 94, 0.2);
    color: var(--success);
}

.status-pending, .status-scheduled {
    background: rgba(245, 158, 11, 0.2);
    color: var(--warning);
}

.status-completed, .status-done {
    background: rgba(59, 130, 246, 0.2);
    color: var(--primary);
}

.status-paused, .status-stopped {
    background: rgba(239, 68, 68, 0.2);
    color: var(--danger);
}

/* Cards */
.card {
    background: var(--bg-card);
    border-radius: var(--radius);
    padding: 1.5rem;
    box-shadow: var(--shadow);
    margin-bottom: 1rem;
}

.card h2, .card h3 {
    margin-bottom: 1rem;
}

/* Dashboard Grid */
.dashboard-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 1rem;
}

/* Alerts */
.alert {
    padding: 1rem;
    border-radius: var(--radius);
    margin-bottom: 1rem;
}

.alert-success {
    background: rgba(34, 197, 94, 0.2);
    border: 1px solid var(--success);
    color: var(--success);
}

.alert-error, .alert-danger {
    background: rgba(239, 68, 68, 0.2);
    border: 1px solid var(--danger);
    color: var(--danger);
}

.alert-warning {
    background: rgba(245, 158, 11, 0.2);
    border: 1px solid var(--warning);
    color: var(--warning);
}

.alert-info {
    background: rgba(59, 130, 246, 0.2);
    border: 1px solid var(--primary);
    color: var(--primary);
}

/* Mobile Responsive */
@media (max-width: 768px) {
    nav ul {
        justify-content: center;
    }
    
    .page-header {
        flex-direction: column;
        align-items: flex-start;
    }
    
    .data-table {
        display: block;
        overflow-x: auto;
        -webkit-overflow-scrolling: touch;
    }
    
    .filter-bar input {
        max-width: 100%;
    }
    
    .dashboard-grid {
        grid-template-columns: 1fr;
    }
}

/* Fix scroll trap on mobile */
html, body {
    overflow-x: hidden;
    -webkit-overflow-scrolling: touch;
}

.container {
    overflow: visible;
}
'''

# =============================================================================
# FILE 3: templates/base.html (corrected for your routes)
# =============================================================================
BASE_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}PF67{% endblock %} - ProtocolFlow</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
    <nav>
        <ul>
            <li><a href="{{ url_for('views.dashboard') }}" {% if request.endpoint == 'views.dashboard' %}class="active"{% endif %}>Dashboard</a></li>
            <li><a href="{{ url_for('views.protocols') }}" {% if request.endpoint and 'protocol' in request.endpoint %}class="active"{% endif %}>Protocols</a></li>
            <li><a href="{{ url_for('views.flows') }}" {% if request.endpoint and 'flow' in request.endpoint %}class="active"{% endif %}>Flows</a></li>
        </ul>
    </nav>
    
    <main class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                <div class="alert alert-{{ category }}">{{ message }}</div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        {% block content %}{% endblock %}
    </main>
</body>
</html>
'''

# =============================================================================
# PATCH LOGIC
# =============================================================================
def write_file(relative_path, content):
    """Write content to file, creating directories if needed."""
    full_path = BASE_DIR / relative_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Backup existing file
    if full_path.exists():
        backup_path = full_path.with_suffix(full_path.suffix + '.bak')
        print(f"  Backing up: {relative_path} -> {backup_path.name}")
        backup_path.write_text(full_path.read_text())
    
    full_path.write_text(content)
    print(f"  Written: {relative_path}")

def main():
    print("=" * 60)
    print("PF67 UI Patch Script v2")
    print("=" * 60)
    
    if not BASE_DIR.exists():
        print(f"\nERROR: Cannot find {BASE_DIR}")
        print("Make sure you run this script from /volume1/docker/pf67")
        return 1
    
    print(f"\nTarget directory: {BASE_DIR}")
    print("\nApplying patches...\n")
    
    write_file("templates/flows.html", FLOWS_HTML)
    write_file("static/style.css", STYLE_CSS)  # Corrected path
    write_file("templates/base.html", BASE_HTML)
    
    print("\n" + "=" * 60)
    print("DONE! Patches applied successfully.")
    print("=" * 60)
    print("\nNext steps:")
    print("  1. Restart your container:  docker restart <container_name>")
    print("  2. Hard refresh browser: Ctrl+Shift+R (or Cmd+Shift+R on Mac)")
    print("  3. Test the UI")
    print("  4. If working, push to GitHub:")
    print("       cd /volume1/docker/pf67")
    print("       git add -A")
    print('       git commit -m "UI modernization v2"')
    print("       git push")
    print("\nBackup files created with .bak extension if you need to revert.")
    
    return 0

if __name__ == "__main__":
    exit(main())
