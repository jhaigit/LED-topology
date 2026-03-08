// LTP LED Canvas - canvas-based LED preview renderer
window.LtpLedCanvas = (function() {

    class LedCanvas {
        constructor(canvasElement, width, height) {
            this.canvas = canvasElement;
            this.ctx = canvasElement.getContext('2d');
            this.ledWidth = width;
            this.ledHeight = height || 1;
            this.pixels = null;
            this.frameCount = 0;
            this.lastFpsTime = Date.now();
            this.fps = 0;
            this._rafId = null;
            this._dirty = false;

            this.resize();
        }

        resize() {
            const container = this.canvas.parentElement;
            const availW = container.clientWidth - 16;
            const gap = 1;

            // Calculate LED pixel size
            if (this.ledHeight === 1) {
                // 1D strip
                this.ledSize = Math.max(3, Math.min(Math.floor(availW / this.ledWidth), 20));
            } else {
                // 2D matrix
                const maxH = 400;
                this.ledSize = Math.max(3, Math.min(
                    Math.floor(availW / this.ledWidth),
                    Math.floor(maxH / this.ledHeight),
                    16
                ));
            }

            this.canvas.width = this.ledWidth * (this.ledSize + gap);
            this.canvas.height = this.ledHeight * (this.ledSize + gap);
            this._dirty = true;
            this._scheduleRender();
        }

        render(pixels) {
            this.pixels = pixels;
            this._dirty = true;
            this.frameCount++;

            // FPS tracking
            const now = Date.now();
            if (now - this.lastFpsTime >= 1000) {
                this.fps = Math.round(this.frameCount * 1000 / (now - this.lastFpsTime));
                this.frameCount = 0;
                this.lastFpsTime = now;
            }

            this._scheduleRender();
        }

        _scheduleRender() {
            if (this._rafId) return;
            this._rafId = requestAnimationFrame(() => {
                this._rafId = null;
                this._draw();
            });
        }

        _draw() {
            if (!this._dirty || !this.pixels) return;
            this._dirty = false;

            const ctx = this.ctx;
            const size = this.ledSize;
            const gap = 1;

            ctx.fillStyle = '#111';
            ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);

            for (let y = 0; y < this.ledHeight; y++) {
                for (let x = 0; x < this.ledWidth; x++) {
                    const idx = y * this.ledWidth + x;
                    const px = this.pixels[idx];
                    if (!px) continue;

                    const [r, g, b] = px;
                    ctx.fillStyle = `rgb(${r},${g},${b})`;
                    ctx.fillRect(
                        x * (size + gap),
                        y * (size + gap),
                        size,
                        size
                    );
                }
            }
        }

        destroy() {
            if (this._rafId) {
                cancelAnimationFrame(this._rafId);
                this._rafId = null;
            }
        }
    }

    function enterFullscreen(element) {
        if (element.requestFullscreen) {
            element.requestFullscreen();
        } else if (element.webkitRequestFullscreen) {
            element.webkitRequestFullscreen();
        }
    }

    return {
        LedCanvas,
        enterFullscreen
    };
})();
