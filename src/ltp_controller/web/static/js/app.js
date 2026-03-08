// LTP Controller Web Interface JavaScript

// Theme management
(function() {
    const saved = localStorage.getItem('ltp-theme');
    if (saved) {
        document.documentElement.setAttribute('data-theme', saved);
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
        document.documentElement.setAttribute('data-theme', 'light');
    }
})();

function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    const next = current === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('ltp-theme', next);
}

// Auto-refresh status every 5 seconds
(function() {
    const statusElements = document.querySelectorAll('[data-auto-refresh]');
    if (statusElements.length > 0) {
        setInterval(() => {
            fetch('/api/status')
                .then(response => response.json())
                .then(data => {
                    // Update status elements if they exist
                    console.log('Status:', data);
                })
                .catch(err => console.error('Status refresh error:', err));
        }, 5000);
    }
})();

// Utility function for API calls
async function apiCall(method, endpoint, data = null) {
    const options = {
        method: method,
        headers: {
            'Content-Type': 'application/json'
        }
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(endpoint, options);
    return response.json();
}

// Toast notifications
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.setAttribute('role', 'status');
    toast.setAttribute('aria-live', 'polite');
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('show');
    }, 100);

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Confirmation dialog (styled modal, returns a Promise<boolean>)
let _confirmResolve = null;

function ltpConfirmClose(result) {
    document.getElementById('confirmModal').style.display = 'none';
    if (_confirmResolve) {
        _confirmResolve(result);
        _confirmResolve = null;
    }
}

function confirmAction(message, {title = 'Confirm', okLabel = 'Delete'} = {}) {
    document.getElementById('confirmTitle').textContent = title;
    document.getElementById('confirmMessage').textContent = message;
    document.getElementById('confirmOkBtn').textContent = okLabel;
    document.getElementById('confirmModal').style.display = 'flex';
    return new Promise(resolve => { _confirmResolve = resolve; });
}

// Save config button handler
(function() {
    const saveBtn = document.getElementById('save-config-btn');
    if (saveBtn) {
        saveBtn.addEventListener('click', async () => {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';

            try {
                const response = await fetch('/api/config/save', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });

                const result = await response.json();

                if (response.ok) {
                    showToast('Configuration saved successfully', 'success');
                } else {
                    showToast(result.error || 'Failed to save configuration', 'error');
                }
            } catch (err) {
                showToast('Error saving configuration: ' + err.message, 'error');
            } finally {
                saveBtn.disabled = false;
                saveBtn.textContent = 'Save Config';
            }
        });
    }
})();
