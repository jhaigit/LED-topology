// LTP Controller Web Interface JavaScript

// CSRF + session handling: wrap fetch once so every same-origin mutating
// request (apiCall and the direct fetch() call sites alike) carries the
// CSRF token. A 401 (anonymous viewer, or a login that expired while the
// page sat open) opens the in-page login modal and *holds* the request:
// once the user logs in it is replayed with the fresh CSRF token and the
// caller sees the replayed response, so the interaction completes instead
// of being lost to a page navigation. Cancelling hands back the 401.
const ltpSession = (function() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    let csrfToken = meta ? meta.content : '';
    const origFetch = window.fetch.bind(window);
    const pending = [];  // {input, init, resolve, response}
    const principalMeta = document.querySelector('meta[name="ltp-principal"]');
    let principal = principalMeta ? principalMeta.content : '';

    function setCsrf(token) {
        csrfToken = token || '';
        if (meta) meta.content = csrfToken;
        document.querySelectorAll('input[name="_csrf"]').forEach(i => { i.value = csrfToken; });
    }

    function isSameOrigin(url) {
        return !/^[a-z]+:\/\//i.test(url) || url.startsWith(window.location.origin);
    }

    window.fetch = function(input, init) {
        init = init || {};
        const method = (init.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
        const url = typeof input === 'string' ? input : input.url;
        if (csrfToken && isSameOrigin(url) && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
            const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
            headers.set('X-CSRF-Token', csrfToken);
            init.headers = headers;
        }
        return origFetch(input, init).then(response => {
            if (response.status !== 401 || window.location.pathname.startsWith('/login')) {
                return response;
            }
            if (!document.getElementById('loginModal')) {
                // No modal on this page: fall back to the full login form.
                window.location = '/login?next=' + encodeURIComponent(window.location.pathname);
                return response;
            }
            const msg = principal
                ? 'Your session has expired. Log in to continue where you left off.'
                : 'Logging in is required for that action.';
            return new Promise(resolve => {
                pending.push({ input, init, resolve, response });
                ltpLoginOpen(msg);
            });
        });
    };

    function replayPending() {
        const items = pending.splice(0);
        // Re-enter window.fetch so the fresh CSRF token is applied (and so a
        // second expiry mid-replay would be handled the same way).
        items.forEach(p => p.resolve(window.fetch(p.input, p.init)));
    }

    function cancelPending() {
        const items = pending.splice(0);
        items.forEach(p => p.resolve(p.response));
    }

    // Log in from the modal without leaving the page.
    async function submitLogin(form) {
        const msg = document.getElementById('login-modal-msg');
        const submit = form.querySelector('button[type="submit"]');
        submit.disabled = true;
        try {
            const resp = await origFetch(form.action, {
                method: 'POST',
                body: new FormData(form),
                headers: { 'Accept': 'application/json' },
            });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || data.status !== 'ok') {
                msg.textContent = data.error || 'Login failed.';
                msg.style.display = '';
                return;
            }
            setCsrf(data.csrf);
            setPrincipal(data.principal.name, data.principal.role);
            form.querySelector('input[name="password"]').value = '';
            ltpLoginClose();
            hideBanner();
            replayPending();
            if (typeof showToast === 'function') showToast(`Logged in as ${data.principal.name}`, 'success');
        } catch (err) {
            msg.textContent = 'Login failed: ' + err.message;
            msg.style.display = '';
        } finally {
            submit.disabled = false;
        }
    }

    // --- nav + banner state -------------------------------------------

    function setPrincipal(name, role) {
        principal = name || '';
        const nav = document.getElementById('nav-auth');
        if (!nav) return;
        if (principal) {
            nav.innerHTML = `<form method="post" action="/logout" class="logout-form" title="Logged in as ${escapeHtml(name)} (${escapeHtml(role || '')})">
                <input type="hidden" name="_csrf" value="${escapeHtml(csrfToken)}">
                <button type="submit" class="btn btn-sm">${escapeHtml(name)} &middot; Log out</button></form>`;
        } else {
            nav.innerHTML = `<a href="/login?next=${encodeURIComponent(window.location.pathname)}" class="btn btn-sm btn-primary" title="Log in for operator/admin actions" onclick="return ltpLoginOpen()">Log in</a>`;
        }
    }

    function escapeHtml(v) {
        return String(v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function showBanner(text, buttonText, onClick, expired) {
        const banner = document.getElementById('session-banner');
        if (!banner) return;
        document.getElementById('session-banner-msg').textContent = text;
        const btn = document.getElementById('session-banner-btn');
        btn.textContent = buttonText;
        btn.onclick = onClick;
        banner.classList.toggle('expired', !!expired);
        banner.hidden = false;
    }

    function hideBanner() {
        const banner = document.getElementById('session-banner');
        if (banner) banner.hidden = true;
    }

    // --- session monitor ------------------------------------------------
    // Notice an expired (or expiring) login before the user starts an
    // interaction that would only fail at the end. Runs on a slow timer and
    // whenever the tab regains focus (a page left open overnight).

    let monitorTimer = null;

    async function check() {
        let data;
        try {
            data = await (await origFetch('/api/session', { cache: 'no-store' })).json();
        } catch (e) {
            return;  // controller unreachable; other polls will complain
        }
        if (!data.auth_enabled) return;
        if (principal && !data.authenticated) {
            setPrincipal('', '');
            showBanner('Your session has expired. Log in again to make changes.',
                       'Log in', () => ltpLoginOpen('Your session has expired.'), true);
            return;
        }
        if (data.authenticated) {
            if (data.name !== principal) setPrincipal(data.name, data.role);  // logged in elsewhere
            if (data.expires_in <= data.warn_below) {
                const mins = Math.max(1, Math.round(data.expires_in / 60));
                showBanner(`Your session expires in about ${mins} minute${mins === 1 ? '' : 's'} of inactivity.`,
                           'Stay logged in', keepAlive, false);
            } else {
                hideBanner();
            }
        }
    }

    async function keepAlive() {
        const resp = await window.fetch('/api/session/refresh', { method: 'POST' });
        if (resp.ok) hideBanner();
    }

    function startMonitor() {
        if (!document.getElementById('loginModal')) return;  // auth disabled
        check();
        monitorTimer = setInterval(check, 60000);
        document.addEventListener('visibilitychange', () => {
            if (document.visibilityState === 'visible') check();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', startMonitor);
    } else {
        startMonitor();
    }

    return { setCsrf, submitLogin, cancelPending, check, get principal() { return principal; } };
})();

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

// Login dialog (the full /login page is the no-JS fallback)
function ltpLoginOpen(message) {
    const modal = document.getElementById('loginModal');
    if (!modal) return true;  // no modal on this page: follow the link to /login
    const msg = document.getElementById('login-modal-msg');
    msg.textContent = message || '';
    msg.style.display = message ? '' : 'none';
    if (modal.style.display !== 'flex') {
        modal.style.display = 'flex';
        document.getElementById('login-username').focus();
    }
    return false;
}

function ltpLoginClose() {
    document.getElementById('loginModal').style.display = 'none';
    // Requests parked on this login get their original 401 back so the
    // caller's own error handling runs.
    ltpSession.cancelPending();
}

function ltpLoginSubmit(event) {
    event.preventDefault();
    ltpSession.submitLogin(event.target);
    return false;
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
