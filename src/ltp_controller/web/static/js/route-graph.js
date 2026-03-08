// LTP Route Graph - visual node-graph view for routes
window.LtpRouteGraph = (function() {
    let canvas, ctx;
    let sources = [], sinks = [], routes = [];
    let nodePositions = {};  // Stored positions keyed by device id
    let selectedCable = null;
    let hoveredCable = null;
    let selectedNode = null;
    let dragTarget = null;
    let dragOffset = { x: 0, y: 0 };
    let dragMoved = false;
    let pendingCable = null;  // For drag-to-connect
    let storageKey = 'ltp-route-graph-positions';

    const NODE_W = 160;
    const NODE_H = 36;
    const PORT_R = 6;

    // opts.storageKey: localStorage key for positions (default: 'ltp-route-graph-positions')
    function init(canvasEl, srcData, sinkData, routeData, opts) {
        canvas = canvasEl;
        ctx = canvas.getContext('2d');
        sources = srcData;
        sinks = sinkData;
        routes = routeData;
        if (opts && opts.storageKey) storageKey = opts.storageKey;

        // Size the canvas first so autoLayout has correct dimensions
        resizeCanvas();

        // Load saved positions or auto-layout
        const saved = localStorage.getItem(storageKey);
        if (saved) {
            try { nodePositions = JSON.parse(saved); } catch (e) { nodePositions = {}; }
        }
        autoLayout();

        canvas.addEventListener('mousedown', onMouseDown);
        canvas.addEventListener('mousemove', onMouseMove);
        canvas.addEventListener('mouseup', onMouseUp);
        canvas.addEventListener('dblclick', onDblClick);
        canvas.addEventListener('contextmenu', onContextMenu);

        window.addEventListener('resize', resizeCanvas);
        draw();
    }

    function resizeCanvas() {
        const container = canvas.parentElement;
        canvas.width = container.clientWidth;
        canvas.height = Math.max(400, (Math.max(sources.length, sinks.length) + 1) * 50 + 40);
        draw();
    }

    function autoLayout() {
        const pad = 20;
        const spacing = 50;

        sources.forEach((s, i) => {
            if (!nodePositions[s.id]) {
                nodePositions[s.id] = { x: pad, y: pad + i * spacing };
            }
        });

        sinks.forEach((s, i) => {
            if (!nodePositions[s.id]) {
                nodePositions[s.id] = {
                    x: (canvas ? canvas.width : 600) - NODE_W - pad,
                    y: pad + i * spacing
                };
            }
        });
    }

    function savePositions() {
        localStorage.setItem(storageKey, JSON.stringify(nodePositions));
    }

    function draw() {
        if (!ctx) return;
        const w = canvas.width;
        const h = canvas.height;

        // Clear
        const bgColor = getComputedStyle(document.documentElement).getPropertyValue('--bg-primary').trim() || '#1a1a2e';
        ctx.fillStyle = bgColor;
        ctx.fillRect(0, 0, w, h);

        // Draw cables first (under nodes)
        routes.forEach(route => {
            drawCable(route);
        });

        // Draw pending cable
        if (pendingCable) {
            ctx.beginPath();
            ctx.setLineDash([5, 5]);
            ctx.strokeStyle = '#aaa';
            ctx.lineWidth = 2;
            ctx.moveTo(pendingCable.x1, pendingCable.y1);
            ctx.lineTo(pendingCable.x2, pendingCable.y2);
            ctx.stroke();
            ctx.setLineDash([]);
        }

        // Draw source nodes
        sources.forEach(s => {
            const pos = nodePositions[s.id];
            if (!pos) return;
            drawNode(pos.x, pos.y, s.name, s.online, 'source', s.type === 'virtual', selectedNode === s.id);
            // Output port (right side)
            drawPort(pos.x + NODE_W, pos.y + NODE_H / 2, s.online);
        });

        // Draw sink nodes
        sinks.forEach(s => {
            const pos = nodePositions[s.id];
            if (!pos) return;
            drawNode(pos.x, pos.y, s.name, s.online, 'sink', false, selectedNode === s.id);
            // Input port (left side)
            drawPort(pos.x, pos.y + NODE_H / 2, s.online);
        });
    }

    function drawNode(x, y, name, online, type, isVirtual, isSelected) {
        const borderColor = isSelected ?
            (getComputedStyle(document.documentElement).getPropertyValue('--accent').trim() || '#e94560') :
            online ?
            (getComputedStyle(document.documentElement).getPropertyValue('--success').trim() || '#4caf50') :
            (getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim() || '#a0a0a0');
        const cardColor = getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim() || '#0f3460';
        const textColor = getComputedStyle(document.documentElement).getPropertyValue('--text-primary').trim() || '#eaeaea';

        ctx.fillStyle = cardColor;
        ctx.strokeStyle = borderColor;
        ctx.lineWidth = isSelected ? 2.5 : 1.5;

        // Rounded rect
        const r = 4;
        ctx.beginPath();
        ctx.moveTo(x + r, y);
        ctx.lineTo(x + NODE_W - r, y);
        ctx.quadraticCurveTo(x + NODE_W, y, x + NODE_W, y + r);
        ctx.lineTo(x + NODE_W, y + NODE_H - r);
        ctx.quadraticCurveTo(x + NODE_W, y + NODE_H, x + NODE_W - r, y + NODE_H);
        ctx.lineTo(x + r, y + NODE_H);
        ctx.quadraticCurveTo(x, y + NODE_H, x, y + NODE_H - r);
        ctx.lineTo(x, y + r);
        ctx.quadraticCurveTo(x, y, x + r, y);
        ctx.closePath();
        ctx.fill();
        ctx.stroke();

        // Status dot
        ctx.fillStyle = borderColor;
        ctx.beginPath();
        ctx.arc(x + 14, y + NODE_H / 2, 4, 0, Math.PI * 2);
        ctx.fill();

        // Name
        ctx.fillStyle = textColor;
        ctx.font = '12px -apple-system, sans-serif';
        ctx.textBaseline = 'middle';
        let label = name;
        if (isVirtual) label = 'VS: ' + label;
        if (label.length > 18) label = label.substring(0, 17) + '...';
        ctx.fillText(label, x + 24, y + NODE_H / 2);
    }

    function drawPort(x, y, online) {
        const color = online ?
            (getComputedStyle(document.documentElement).getPropertyValue('--success').trim() || '#4caf50') :
            '#666';
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(x, y, PORT_R, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = '#333';
        ctx.lineWidth = 1;
        ctx.stroke();
    }

    function drawCable(route) {
        const srcPos = nodePositions[route.source_id];
        const sinkPos = nodePositions[route.sink_id];
        if (!srcPos || !sinkPos) return;

        const x1 = srcPos.x + NODE_W;
        const y1 = srcPos.y + NODE_H / 2;
        const x2 = sinkPos.x;
        const y2 = sinkPos.y + NODE_H / 2;

        const isSelected = selectedCable === route.id;
        const isHovered = hoveredCable === route.id;

        let color;
        if (!route.enabled) {
            color = '#666';
        } else if (route.status === 'connected') {
            color = getComputedStyle(document.documentElement).getPropertyValue('--success').trim() || '#4caf50';
        } else if (route.status === 'error') {
            color = getComputedStyle(document.documentElement).getPropertyValue('--error').trim() || '#f44336';
        } else {
            color = getComputedStyle(document.documentElement).getPropertyValue('--warning').trim() || '#ff9800';
        }

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = isSelected ? 3 : (isHovered ? 2.5 : 2);
        if (!route.enabled) ctx.setLineDash([5, 5]);

        // Bezier curve
        const cpx = Math.abs(x2 - x1) * 0.4;
        ctx.moveTo(x1, y1);
        ctx.bezierCurveTo(x1 + cpx, y1, x2 - cpx, y2, x2, y2);
        ctx.stroke();
        ctx.setLineDash([]);

        // Route name label at midpoint
        const mx = (x1 + x2) / 2;
        const my = (y1 + y2) / 2;
        ctx.fillStyle = getComputedStyle(document.documentElement).getPropertyValue('--text-secondary').trim() || '#a0a0a0';
        ctx.font = '10px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillText(route.name, mx, my - 4);
        ctx.textAlign = 'left';
    }

    // Hit testing
    function getNodeAt(mx, my) {
        const all = [...sources, ...sinks];
        for (const item of all) {
            const pos = nodePositions[item.id];
            if (!pos) continue;
            if (mx >= pos.x && mx <= pos.x + NODE_W && my >= pos.y && my <= pos.y + NODE_H) {
                return item;
            }
        }
        return null;
    }

    function getCableAt(mx, my) {
        for (const route of routes) {
            const srcPos = nodePositions[route.source_id];
            const sinkPos = nodePositions[route.sink_id];
            if (!srcPos || !sinkPos) continue;

            const x1 = srcPos.x + NODE_W;
            const y1 = srcPos.y + NODE_H / 2;
            const x2 = sinkPos.x;
            const y2 = sinkPos.y + NODE_H / 2;

            // Check distance to bezier (approximate with line segments)
            for (let t = 0; t <= 1; t += 0.05) {
                const cpx = Math.abs(x2 - x1) * 0.4;
                const px = bezierPoint(x1, x1 + cpx, x2 - cpx, x2, t);
                const py = bezierPoint(y1, y1, y2, y2, t);
                const dist = Math.sqrt((mx - px) ** 2 + (my - py) ** 2);
                if (dist < 8) return route;
            }
        }
        return null;
    }

    function bezierPoint(p0, p1, p2, p3, t) {
        const t2 = t * t;
        const t3 = t2 * t;
        const mt = 1 - t;
        const mt2 = mt * mt;
        const mt3 = mt2 * mt;
        return mt3 * p0 + 3 * mt2 * t * p1 + 3 * mt * t2 * p2 + t3 * p3;
    }

    function getSourcePortAt(mx, my) {
        for (const s of sources) {
            const pos = nodePositions[s.id];
            if (!pos) continue;
            const px = pos.x + NODE_W;
            const py = pos.y + NODE_H / 2;
            if (Math.sqrt((mx - px) ** 2 + (my - py) ** 2) < PORT_R + 4) {
                return s;
            }
        }
        return null;
    }

    function getSinkPortAt(mx, my) {
        for (const s of sinks) {
            const pos = nodePositions[s.id];
            if (!pos) continue;
            const px = pos.x;
            const py = pos.y + NODE_H / 2;
            if (Math.sqrt((mx - px) ** 2 + (my - py) ** 2) < PORT_R + 4) {
                return s;
            }
        }
        return null;
    }

    function getMousePos(e) {
        const rect = canvas.getBoundingClientRect();
        return { x: e.clientX - rect.left, y: e.clientY - rect.top };
    }

    // Event handlers
    function onMouseDown(e) {
        const { x, y } = getMousePos(e);

        // Check if clicking a source port (start cable)
        const srcPort = getSourcePortAt(x, y);
        if (srcPort) {
            const pos = nodePositions[srcPort.id];
            pendingCable = {
                sourceId: srcPort.id,
                x1: pos.x + NODE_W,
                y1: pos.y + NODE_H / 2,
                x2: x,
                y2: y
            };
            return;
        }

        // Check if clicking a cable
        const cable = getCableAt(x, y);
        if (cable) {
            selectedCable = cable.id;
            selectedNode = null;
            draw();
            // Dispatch event for side panel
            canvas.dispatchEvent(new CustomEvent('cable-selected', { detail: cable }));
            return;
        }

        // Check if clicking a node (start drag)
        const node = getNodeAt(x, y);
        if (node) {
            dragTarget = node;
            dragMoved = false;
            const pos = nodePositions[node.id];
            dragOffset = { x: x - pos.x, y: y - pos.y };
            selectedCable = null;
            draw();
            return;
        }

        // Click on empty space - deselect
        selectedCable = null;
        selectedNode = null;
        draw();
        canvas.dispatchEvent(new CustomEvent('cable-selected', { detail: null }));
        canvas.dispatchEvent(new CustomEvent('node-clicked', { detail: null }));
    }

    function onMouseMove(e) {
        const { x, y } = getMousePos(e);

        if (pendingCable) {
            pendingCable.x2 = x;
            pendingCable.y2 = y;
            draw();
            return;
        }

        if (dragTarget) {
            dragMoved = true;
            nodePositions[dragTarget.id] = {
                x: x - dragOffset.x,
                y: y - dragOffset.y
            };
            draw();
            return;
        }

        // Hover detection for cables
        const cable = getCableAt(x, y);
        const newHover = cable ? cable.id : null;
        if (newHover !== hoveredCable) {
            hoveredCable = newHover;
            canvas.style.cursor = hoveredCable ? 'pointer' : 'default';
            draw();
        }

        // Cursor for ports
        if (getSourcePortAt(x, y) || getSinkPortAt(x, y)) {
            canvas.style.cursor = 'crosshair';
        } else if (!hoveredCable && getNodeAt(x, y)) {
            canvas.style.cursor = 'grab';
        }
    }

    function onMouseUp(e) {
        if (dragTarget) {
            const clicked = dragTarget;
            dragTarget = null;
            if (dragMoved) {
                savePositions();
            } else {
                // Click without drag - select the node
                selectedNode = clicked.id;
                selectedCable = null;
                draw();
                canvas.dispatchEvent(new CustomEvent('node-clicked', { detail: clicked }));
            }
            return;
        }

        if (pendingCable) {
            const { x, y } = getMousePos(e);
            const sinkPort = getSinkPortAt(x, y);
            if (sinkPort) {
                // Create route via the existing modal
                canvas.dispatchEvent(new CustomEvent('create-route', {
                    detail: {
                        sourceId: pendingCable.sourceId,
                        sinkId: sinkPort.id
                    }
                }));
            }
            pendingCable = null;
            draw();
        }
    }

    function onDblClick(e) {
        const { x, y } = getMousePos(e);
        const cable = getCableAt(x, y);
        if (cable) {
            canvas.dispatchEvent(new CustomEvent('cable-edit', { detail: cable }));
        }
    }

    function onContextMenu(e) {
        e.preventDefault();
        const { x, y } = getMousePos(e);
        const cable = getCableAt(x, y);
        if (cable) {
            canvas.dispatchEvent(new CustomEvent('cable-context', {
                detail: { route: cable, x: e.clientX, y: e.clientY }
            }));
            return;
        }
        const node = getNodeAt(x, y);
        if (node) {
            canvas.dispatchEvent(new CustomEvent('node-context', {
                detail: { node: node, x: e.clientX, y: e.clientY, canvasX: x, canvasY: y }
            }));
            return;
        }
        // Right-click on empty space
        canvas.dispatchEvent(new CustomEvent('canvas-context', {
            detail: { x: e.clientX, y: e.clientY, canvasX: x, canvasY: y }
        }));
    }

    function updateRouteData(newRoutes) {
        routes = newRoutes;
        draw();
    }

    function resetLayout() {
        nodePositions = {};
        localStorage.removeItem(storageKey);
        autoLayout();
        draw();
    }

    function updateSourceData(newSources) {
        sources = newSources;
        autoLayout();
        draw();
    }

    function updateSinkData(newSinks) {
        sinks = newSinks;
        autoLayout();
        draw();
    }

    function getSelectedCable() {
        return selectedCable;
    }

    function getSelectedNode() {
        return selectedNode;
    }

    return {
        init,
        draw,
        updateRouteData,
        updateSourceData,
        updateSinkData,
        resetLayout,
        resizeCanvas,
        getSelectedCable,
        getSelectedNode
    };
})();
