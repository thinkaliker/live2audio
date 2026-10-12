// Live2Audio dashboard. One Alpine component on <body> holds the page state and
// index.html renders from it; this file is loaded before Alpine so 'alpine:init' fires here.

const LIGHT_SCHEME = window.matchMedia('(prefers-color-scheme: light)');

// DLNA targets are device UDNs unless they look like an IP or URL from the manual field
function castTargetPayload(target) {
    return target.includes('://') || target.includes('.') ? { manual_location: target } : { udn: target };
}

function postJSON(url, body) {
    return fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
}

function groupOf(stream) {
    return stream.tvg_id || 'Other';
}

const BLANK_STATION = { editing: false, oldUrl: '', url: '', name: '', id: '', group: '' };

document.addEventListener('alpine:init', () => {
    Alpine.data('dashboard', () => ({
        serverId: '',
        online: true,
        uptime: '',
        liveCount: 0,
        errors: [],
        streams: [],
        loaded: false,
        // { id, name, logo, castTarget?, castingTo? }, mirrored to sessionStorage
        nowPlaying: null,
        volume: 0.5,
        prevVolume: null,
        theme: 'auto',
        m3uStatus: null,
        form: { ...BLANK_STATION },
        saving: false,
        deleting: false,
        cast: { stream: null, devices: [], note: '', noteError: false, scanning: false, busy: null, manual: '' },
        reorder: { items: [], status: 'loading', dragIdx: null, overIdx: null },
        reorderStatus: null,

        init() {
            const { serverId, uptime, liveCount } = this.$el.dataset;
            Object.assign(this, { serverId, uptime, liveCount: Number(liveCount) });

            // A different server id means the service restarted, so saved playback is stale
            if (sessionStorage.getItem('server_id') !== serverId) sessionStorage.removeItem('nowPlaying');
            sessionStorage.setItem('server_id', serverId);
            this.nowPlaying = JSON.parse(sessionStorage.getItem('nowPlaying'));
            this.$watch('nowPlaying', np => {
                if (np) sessionStorage.setItem('nowPlaying', JSON.stringify(np));
                else sessionStorage.removeItem('nowPlaying');
            });

            // The <audio> element applies the volume itself via x-effect
            const savedVolume = parseFloat(localStorage.getItem('userVolume'));
            if (!Number.isNaN(savedVolume)) this.volume = savedVolume;
            this.$watch('volume', v => localStorage.setItem('userVolume', v));

            const storedTheme = localStorage.getItem('theme');
            if (storedTheme === 'light' || storedTheme === 'dark') this.theme = storedTheme;
            this.$watch('theme', theme => {
                if (theme === 'auto') localStorage.removeItem('theme');
                else localStorage.setItem('theme', theme);
                this.applyTheme();
            });
            LIGHT_SCHEME.addEventListener('change', () => this.applyTheme());

            updateFaviconBadge(this.liveCount);
            this.$watch('liveCount', updateFaviconBadge);

            this.refresh();
            setInterval(() => this.refresh(), 10000);
        },

        async refresh() {
            try {
                const response = await fetch('/api/stats');
                if (!response.ok) throw new Error('Offline');
                const data = await response.json();
                this.online = true;
                this.uptime = data.uptime;
                this.liveCount = data.live_count;
                this.errors = data.errors || [];

                if (data.server_id && data.server_id !== this.serverId) {
                    console.log("Server restart detected, clearing stale playback state.");
                    this.serverId = data.server_id;
                    sessionStorage.setItem('server_id', data.server_id);
                    this.endPlayback();
                }

                // An empty list once stations have loaded is a transient server-side
                // failure, so keep showing the last good list
                if (data.streams.length || !this.streams.length) this.streams = data.streams;
                this.loaded = true;
            } catch (e) {
                console.error("Dashboard update failed", e);
                this.online = false;
            }
        },

        // Stations grouped by tvg_id, in playlist order
        get groups() {
            const groups = new Map();
            for (const stream of this.streams) {
                const name = groupOf(stream);
                if (!groups.has(name)) groups.set(name, { name, streams: [] });
                groups.get(name).streams.push(stream);
            }
            return [...groups.values()];
        },

        isPlaying(stream) {
            return this.nowPlaying?.id === stream.id;
        },

        dotClass(stream) {
            const live = stream.listeners > 0 && stream.availability === 'available';
            return `avail-${stream.availability || 'checking'}${live ? ' live' : ''}`;
        },

        dotTitle(stream) {
            return stream.listeners > 0 ? `${stream.listeners} listening` : (stream.availability || 'checking');
        },

        subText(stream) {
            return stream.id + (stream.listeners > 0 ? ` • ${stream.listeners} listening` : '');
        },

        // Names wider than their tile scroll once when first shown, then on hover
        marquee(el) {
            const overflow = el.scrollWidth - el.parentElement.clientWidth;
            if (overflow <= 0) return;
            el.style.setProperty('--marquee-offset', `-${overflow}px`);
            el.classList.add('init-play');
            el.addEventListener('animationend', () => el.classList.remove('init-play'), { once: true });
        },

        applyTheme() {
            const dark = this.theme === 'dark' || (this.theme === 'auto' && !LIGHT_SCHEME.matches);
            document.documentElement.classList.toggle('dark', dark);
        },

        cycleTheme() {
            this.theme = { auto: 'light', light: 'dark', dark: 'auto' }[this.theme];
        },

        // ── Playback ──────────────────────────────────────────────────────────

        togglePlay(stream) {
            if (this.isPlaying(stream)) return this.stop();
            const audio = this.$refs.audio;
            audio.src = `/stream.mp3?v=${stream.id}`;
            audio.play().catch(e => console.log("Play failed:", e));
            this.nowPlaying = { id: stream.id, name: stream.name, logo: stream.logo };
            this.refresh();
        },

        async stop() {
            const target = this.nowPlaying?.castTarget;
            if (target) {
                try {
                    await postJSON('/api/dlna/stop', castTargetPayload(target));
                } catch (e) {
                    console.error("Failed to stop DLNA:", e);
                }
            }
            this.endPlayback();
            this.refresh();
        },

        endPlayback() {
            this.nowPlaying = null;
            const audio = this.$refs.audio;
            audio.pause();
            audio.removeAttribute('src');
            audio.load();
        },

        toggleMute() {
            if (this.volume > 0) {
                this.prevVolume = this.volume;
                this.volume = 0;
            } else {
                this.volume = this.prevVolume || 0.5;
            }
        },

        // ── Casting ───────────────────────────────────────────────────────────

        openCast(stream) {
            this.cast.stream = stream;
            this.$refs.castDialog.showModal();
            this.loadDevices('/api/dlna/devices', 'Searching for devices...', 'No DLNA devices found. Try refreshing.', 'Failed to load devices.');
        },

        async scanDevices() {
            this.cast.scanning = true;
            await this.loadDevices('/api/dlna/refresh', 'Scanning for devices...', 'No DLNA devices found.', 'Scan failed.', 'POST');
            this.cast.scanning = false;
        },

        async loadDevices(url, loadingNote, emptyNote, errorNote, method = 'GET') {
            Object.assign(this.cast, { devices: [], note: loadingNote, noteError: false });
            try {
                const devices = await (await fetch(url, { method })).json();
                Object.assign(this.cast, { devices, note: devices.length ? '' : emptyNote });
            } catch (e) {
                Object.assign(this.cast, { note: errorNote, noteError: true });
            }
        },

        castManual() {
            const target = this.cast.manual.trim();
            if (!target) return alert('Please enter an IP or URL');
            this.castTo(target);
        },

        async castTo(target) {
            const stream = this.cast.stream;
            this.cast.busy = target;
            try {
                const response = await postJSON('/api/dlna/cast', { video_id: stream.id, ...castTargetPayload(target) });
                const result = await response.json();
                if (!result.success) return alert('Cast failed: ' + result.message);
                this.endPlayback();
                this.nowPlaying = {
                    id: stream.id, name: stream.name, logo: stream.logo,
                    castTarget: target, castingTo: result.device_name || 'Device'
                };
                this.$refs.castDialog.close();
            } catch (e) {
                console.error('Cast Error:', e);
                alert('Request failed: ' + e.message);
            } finally {
                this.cast.busy = null;
            }
        },

        // ── Stations ──────────────────────────────────────────────────────────

        openStation(stream = null) {
            this.form = stream
                ? { editing: true, oldUrl: stream.url, url: stream.url, name: stream.name, id: stream.tvg_id, group: stream.group }
                : { ...BLANK_STATION };
            this.$refs.stationDialog.showModal();
        },

        async saveStation() {
            const f = this.form;
            if (!f.url || !f.name) return alert('URL and Name are required');
            // Blank optional fields fall back to their placeholder values
            const body = {
                url: f.url, name: f.name,
                id: f.id || this.$refs.stationId.placeholder,
                group: f.group || this.$refs.stationGroup.placeholder
            };
            if (f.oldUrl) body.old_url = f.oldUrl;

            this.saving = true;
            try {
                const response = await postJSON(f.oldUrl ? '/edit_station' : '/add_station', body);
                if (!response.ok) return alert('Error: ' + (await response.json()).message);
                this.$refs.stationDialog.close();
                this.refresh();
            } catch (e) {
                alert('Request failed');
            } finally {
                this.saving = false;
            }
        },

        async deleteStation() {
            this.deleting = true;
            try {
                const response = await postJSON('/delete_station', { url: this.form.oldUrl });
                if (!response.ok) return alert('Error: ' + (await response.json()).message);
                this.$refs.deleteDialog.close();
                this.$refs.stationDialog.close();
                this.refresh();
            } catch (e) {
                alert('Request failed');
            } finally {
                this.deleting = false;
            }
        },

        // Show a result on a button for a moment, then restore its label
        flash(key, label, ms, then) {
            this[key] = label;
            setTimeout(() => {
                this[key] = null;
                then?.();
            }, ms);
        },

        async refreshM3U() {
            this.m3uStatus = '⌛ Refreshing...';
            try {
                const response = await fetch('/refresh_m3u', { method: 'POST' });
                if (response.ok) this.flash('m3uStatus', '✅ Updated', 1000, () => this.refresh());
                else this.flash('m3uStatus', '❌ Error', 2000);
            } catch (e) {
                this.flash('m3uStatus', '❌ Failed', 2000);
            }
        },

        // ── Reorder ───────────────────────────────────────────────────────────

        // Fetched fresh on open: the server drops any station missing from the saved order
        async openReorder() {
            this.reorder = { items: [], status: 'loading', dragIdx: null, overIdx: null };
            this.$refs.reorderDialog.showModal();
            try {
                const data = await (await fetch('/api/stats')).json();
                Object.assign(this.reorder, { items: data.streams || [], status: 'ready' });
            } catch (e) {
                this.reorder.status = 'error';
            }
        },

        // A group label goes above the first station of each run of the same group
        startsGroup(i) {
            const items = this.reorder.items;
            return i === 0 || groupOf(items[i - 1]) !== groupOf(items[i]);
        },

        dropAt(i) {
            const from = this.reorder.dragIdx;
            this.reorder.overIdx = this.reorder.dragIdx = null;
            if (from === null || from === i) return;
            const items = this.reorder.items;
            items.splice(i, 0, items.splice(from, 1)[0]);
        },

        async saveOrder() {
            this.reorderStatus = 'Saving…';
            try {
                const response = await postJSON('/reorder_stations', { order: this.reorder.items.map(s => s.id) });
                const result = await response.json();
                if (result.status === 'success') {
                    this.flash('reorderStatus', '✅ Saved', 800, () => {
                        this.$refs.reorderDialog.close();
                        this.refresh();
                    });
                } else {
                    this.flash('reorderStatus', '❌ Error', 2000);
                }
            } catch (e) {
                this.flash('reorderStatus', '❌ Failed', 2000);
            }
        }
    }));
});

function updateFaviconBadge(count) {
    const canvas = document.createElement('canvas');
    canvas.width = 64;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    const img = new Image();
    img.src = '/favicon_base.png';
    img.onload = () => {
        ctx.clearRect(0, 0, 64, 64);
        ctx.drawImage(img, 0, 0, 64, 64);
        if (count > 0) {
            // Drawing the badge
            ctx.fillStyle = '#ef4444'; // Bright red for noticeability
            ctx.beginPath();
            ctx.arc(48, 16, 14, 0, 2 * Math.PI);
            ctx.fill();

            ctx.fillStyle = 'white';
            ctx.font = 'bold 18px Inter, system-ui, sans-serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(count, 48, 16);
        }
        const link = document.getElementById('dynamic-favicon');
        if (link) {
            link.href = canvas.toDataURL('image/png');
        }
    };
}
