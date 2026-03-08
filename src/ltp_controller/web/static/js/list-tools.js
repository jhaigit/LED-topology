// LTP List Tools - reusable search, filter, sort, and bulk select for list pages
window.LtpListTools = (function() {

    /**
     * Initialize a search bar for filtering device cards or table rows.
     * @param {string} containerId - ID of the container holding cards/rows
     * @param {Object} options
     * @param {string} options.type - 'cards' or 'table'
     * @param {string} options.placeholder - Search input placeholder
     */
    function initSearch(containerId, options = {}) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const type = options.type || 'cards';
        const placeholder = options.placeholder || 'Search...';

        // Create toolbar
        const toolbar = document.createElement('div');
        toolbar.className = 'list-toolbar';
        toolbar.innerHTML = `
            <div class="search-box">
                <input type="text" class="search-input" placeholder="${placeholder}"
                       aria-label="Search">
            </div>
        `;

        // Insert before the container
        container.parentNode.insertBefore(toolbar, container);

        const input = toolbar.querySelector('.search-input');
        input.addEventListener('input', function() {
            const term = this.value.toLowerCase();
            if (type === 'cards') {
                filterCards(container, term);
            } else {
                filterTableRows(container, term);
            }
        });

        return toolbar;
    }

    function filterCards(container, term) {
        const cards = container.querySelectorAll('.device-card, .rule-card');
        let visible = 0;
        cards.forEach(card => {
            const text = card.textContent.toLowerCase();
            const match = !term || text.includes(term);
            card.style.display = match ? '' : 'none';
            if (match) visible++;
        });
        // Show/hide empty state
        let emptyMsg = container.querySelector('.filter-empty');
        if (visible === 0 && term) {
            if (!emptyMsg) {
                emptyMsg = document.createElement('div');
                emptyMsg.className = 'filter-empty empty-state';
                emptyMsg.textContent = 'No items match your search.';
                container.appendChild(emptyMsg);
            }
            emptyMsg.style.display = '';
        } else if (emptyMsg) {
            emptyMsg.style.display = 'none';
        }
    }

    function filterTableRows(container, term) {
        const rows = container.querySelectorAll('tbody tr');
        let visible = 0;
        rows.forEach(row => {
            const text = row.textContent.toLowerCase();
            const match = !term || text.includes(term);
            row.style.display = match ? '' : 'none';
            if (match) visible++;
        });
    }

    /**
     * Initialize bulk select checkboxes for a table.
     * @param {string} tableId - ID of the table element
     * @param {Object} actions - Map of action name to handler function
     *   e.g. { 'Enable': (ids) => ..., 'Disable': (ids) => ..., 'Delete': (ids) => ... }
     */
    function initBulkSelect(tableId, actions) {
        const table = document.getElementById(tableId);
        if (!table) return;

        // Add select-all checkbox to header
        const headerRow = table.querySelector('thead tr');
        if (!headerRow) return;

        const th = document.createElement('th');
        th.style.width = '40px';
        th.innerHTML = '<input type="checkbox" class="bulk-select-all" aria-label="Select all">';
        headerRow.insertBefore(th, headerRow.firstChild);

        // Add checkboxes to each row
        const rows = table.querySelectorAll('tbody tr');
        rows.forEach(row => {
            const td = document.createElement('td');
            const id = row.getAttribute('data-route-id') || row.getAttribute('data-rule-id') || '';
            td.innerHTML = `<input type="checkbox" class="bulk-select-item" data-id="${id}" aria-label="Select row">`;
            row.insertBefore(td, row.firstChild);
        });

        // Create bulk actions bar (hidden by default)
        const bar = document.createElement('div');
        bar.className = 'bulk-actions-bar';
        bar.style.display = 'none';

        let barHTML = '<span class="bulk-count">0 selected</span><div class="bulk-buttons">';
        for (const [label, handler] of Object.entries(actions)) {
            const cls = label.toLowerCase() === 'delete' ? 'btn-danger' :
                        label.toLowerCase() === 'enable' ? 'btn-success' :
                        label.toLowerCase() === 'disable' ? 'btn-warning' : '';
            barHTML += `<button class="btn btn-sm ${cls}" data-action="${label}">${label} Selected</button>`;
        }
        barHTML += '</div>';
        bar.innerHTML = barHTML;

        table.parentNode.insertBefore(bar, table);

        // Select all handler
        const selectAll = table.querySelector('.bulk-select-all');
        selectAll.addEventListener('change', function() {
            const checkboxes = table.querySelectorAll('.bulk-select-item');
            checkboxes.forEach(cb => {
                // Only select visible rows
                if (cb.closest('tr').style.display !== 'none') {
                    cb.checked = this.checked;
                }
            });
            updateBulkBar(table, bar);
        });

        // Individual checkbox handlers
        table.addEventListener('change', function(e) {
            if (e.target.classList.contains('bulk-select-item')) {
                updateBulkBar(table, bar);
            }
        });

        // Action button handlers
        bar.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', function() {
                const actionName = this.getAttribute('data-action');
                const handler = actions[actionName];
                if (handler) {
                    const ids = getSelectedIds(table);
                    if (ids.length > 0) {
                        handler(ids);
                    }
                }
            });
        });
    }

    function getSelectedIds(table) {
        const checked = table.querySelectorAll('.bulk-select-item:checked');
        return Array.from(checked).map(cb => cb.getAttribute('data-id')).filter(id => id);
    }

    function updateBulkBar(table, bar) {
        const ids = getSelectedIds(table);
        const count = ids.length;
        bar.style.display = count > 0 ? 'flex' : 'none';
        const countEl = bar.querySelector('.bulk-count');
        if (countEl) countEl.textContent = count + ' selected';
    }

    return {
        initSearch,
        initBulkSelect
    };
})();
