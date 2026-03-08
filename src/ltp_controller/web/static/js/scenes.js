// LTP Scenes - save and restore named configurations
window.LtpScenes = (function() {
    let scenes = [];

    async function load() {
        try {
            const resp = await fetch('/api/scenes');
            scenes = await resp.json();
            renderDropdown();
        } catch (err) {
            console.error('Failed to load scenes:', err);
        }
    }

    function renderDropdown() {
        const container = document.getElementById('scenes-dropdown');
        if (!container) return;

        const list = container.querySelector('.scenes-list');
        if (!list) return;

        if (scenes.length === 0) {
            list.innerHTML = '<div class="scene-item scene-empty">No saved scenes</div>';
            return;
        }

        list.innerHTML = scenes.map(s => `
            <div class="scene-item" data-scene-id="${s.id}">
                <span class="scene-name">${s.name}</span>
                <div class="scene-actions">
                    <button class="btn btn-sm btn-success" onclick="LtpScenes.activate('${s.id}')">Load</button>
                    <button class="btn btn-sm btn-danger" onclick="LtpScenes.remove('${s.id}')">X</button>
                </div>
            </div>
        `).join('');
    }

    async function save() {
        const name = prompt('Scene name:');
        if (!name) return;

        try {
            const resp = await fetch('/api/scenes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, description: '' })
            });
            const data = await resp.json();
            if (data.status === 'ok') {
                showToast(`Scene "${name}" saved`, 'success');
                await load();
            } else {
                showToast('Error saving scene: ' + (data.error || 'Unknown'), 'error');
            }
        } catch (err) {
            showToast('Error: ' + err.message, 'error');
        }
    }

    async function activate(sceneId) {
        try {
            const resp = await fetch(`/api/scenes/${sceneId}/activate`, { method: 'POST' });
            const data = await resp.json();
            if (data.status === 'ok') {
                const applied = data.applied;
                showToast(`Scene loaded: ${applied.routes} routes, ${applied.virtual_sources} VS, ${applied.rules} rules`, 'success');
                setTimeout(() => location.reload(), 500);
            } else {
                showToast('Error: ' + (data.error || 'Unknown'), 'error');
            }
        } catch (err) {
            showToast('Error: ' + err.message, 'error');
        }
    }

    async function remove(sceneId) {
        if (!confirm('Delete this scene?')) return;
        try {
            const resp = await fetch(`/api/scenes/${sceneId}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.status === 'ok') {
                showToast('Scene deleted', 'success');
                await load();
            }
        } catch (err) {
            showToast('Error: ' + err.message, 'error');
        }
    }

    function toggleDropdown() {
        const container = document.getElementById('scenes-dropdown');
        if (!container) return;
        const isVisible = container.style.display !== 'none';
        container.style.display = isVisible ? 'none' : 'block';
    }

    // Initialize on DOM ready
    document.addEventListener('DOMContentLoaded', load);

    return {
        load,
        save,
        activate,
        remove,
        toggleDropdown
    };
})();
