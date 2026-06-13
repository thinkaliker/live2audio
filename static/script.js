let currentCastStreamId = null;

function initTheme() {
    const stored = localStorage.getItem('theme');
    if (stored === 'light' || stored === 'dark') {
        document.documentElement.dataset.theme = stored;
    } else {
        delete document.documentElement.dataset.theme;
    }
    updateThemeBtn(stored || 'auto');
}

function updateThemeBtn(mode) {
    const btn = document.getElementById('themeBtn');
    if (!btn) return;
    btn.innerText = { auto: '🕘', light: '☀️', dark: '🌙' }[mode] || '🕘';
}

function updatePlayBtns(activeId) {
    document.querySelectorAll('[id^="play-btn-"]').forEach(btn => {
        const isActive = activeId != null && btn.id === `play-btn-${activeId}`;
        btn.innerText = isActive ? '⏹' : '▶';
        btn.classList.toggle('playing', isActive);
    });
}

function cycleTheme() {
    const current = localStorage.getItem('theme') || 'auto';
    const next = current === 'auto' ? 'light' : current === 'light' ? 'dark' : 'auto';
    if (next === 'auto') {
        localStorage.removeItem('theme');
        delete document.documentElement.dataset.theme;
    } else {
        localStorage.setItem('theme', next);
        document.documentElement.dataset.theme = next;
    }
    updateThemeBtn(next);
}

function safeSetText(id, text) {
    const el = document.getElementById(id);
    if (el) {
        el.innerText = text;
    } else {
        console.warn(`[SafeSetText] Element #${id} not found.`);
    }
}

function openCastModal(streamId, stationName) {
    currentCastStreamId = streamId;
    safeSetText('castStationName', "Target Station: " + stationName);
    document.getElementById('castModalOverlay').style.display = 'flex';
    loadDevices();
}

function closeCastModal() {
    document.getElementById('castModalOverlay').style.display = 'none';
    currentCastStreamId = null;
}

async function loadDevices() {
    const list = document.getElementById('device-list');
    try {
        const response = await fetch('/api/dlna/devices');
        const devices = await response.json();
        
        if (devices.length === 0) {
            list.innerHTML = '<p style="color: var(--text-dim); font-style: italic; text-align: center;">No DLNA devices found. Try refreshing.</p>';
            return;
        }
        
        let html = '';
        devices.forEach(d => {
            html += `
            <div class="device-item" onclick="castToDevice('${d.udn}', event)">
                <div class="device-info">
                    <span class="device-name">${d.name}</span>
                    <span class="device-location">${d.location}</span>
                </div>
                <span class="cast-icon">📺</span>
            </div>`;
        });
        list.innerHTML = html;
    } catch (e) {
        list.innerHTML = '<p style="color: var(--error); font-style: italic; text-align: center;">Failed to load devices.</p>';
    }
}

async function refreshDevices() {
    const btn = document.getElementById('refreshDevicesBtn');
    const list = document.getElementById('device-list');
    const originalText = btn.innerText;
    
    btn.innerText = '⌛ Scanning...';
    btn.disabled = true;
    list.innerHTML = '<p style="color: var(--text-dim); font-style: italic; text-align: center;">Scanning for devices...</p>';
    
    try {
        const response = await fetch('/api/dlna/refresh', { method: 'POST' });
        const devices = await response.json();
        
        if (devices.length === 0) {
            list.innerHTML = '<p style="color: var(--text-dim); font-style: italic; text-align: center;">No DLNA devices found.</p>';
        } else {
            loadDevices();
        }
    } catch (e) {
        list.innerHTML = '<p style="color: var(--error); font-style: italic; text-align: center;">Scan failed.</p>';
    } finally {
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

async function castToDevice(udn, castEvent) {
    if (!currentCastStreamId) return;
    
    const item = castEvent ? castEvent.currentTarget : null;
    const originalBg = item ? item.style.background : '';
    if (item) item.style.background = 'rgba(148, 163, 184, 0.2)';
    
    // Get station metadata BEFORE closing modal
    const castStationElem = document.getElementById('castStationName');
    const stationName = castStationElem ? castStationElem.innerText.replace('Target Station: ', '') : 'Unknown Station';
    
    // Find the logo from the main list for this station
    let logo = '';
    const activeBtn = document.getElementById(`play-btn-${currentCastStreamId}`);
    if (activeBtn) {
        const item = activeBtn.closest('.stream-item');
        const logoElem = item ? item.querySelector('.stream-logo') : null;
        if (logoElem) logo = logoElem.src;
    }

    const payload = { video_id: currentCastStreamId };
    if (typeof udn === 'string' && (udn.includes('://') || udn.includes('.'))) {
        payload.manual_location = udn;
    } else {
        payload.udn = udn;
    }

    try {
        const response = await fetch('/api/dlna/cast', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        
        if (result.success) {
            // Update playback bar for DLNA
            const bar = document.getElementById('playback-bar');
            
            safeSetText('playback-station-name', stationName);
            safeSetText('playback-status', `Casting to ${result.device_name || 'Device'}`);
            const logoPlaying = document.getElementById('playback-logo');
            if (logoPlaying) logoPlaying.src = logo;
            
            if (bar) bar.style.display = 'flex';
            
            // Set session storage
            sessionStorage.setItem('isPlaying', currentCastStreamId);
            sessionStorage.setItem('isCasting', 'true');
            sessionStorage.setItem('castDeviceTarget', udn); 
            sessionStorage.setItem('stationName', stationName);
            sessionStorage.setItem('stationLogo', logo);
            sessionStorage.setItem('castingTo', result.device_name || 'Device');
            
            // Stop any local playback
            const audio = document.getElementById('main-audio');
            if (audio) {
                audio.pause();
                audio.src = "";
            }
            
            // Update local play buttons
            updatePlayBtns(currentCastStreamId);

            closeCastModal();
        } else {
            alert('Cast failed: ' + result.message);
        }
    } catch (e) {
        console.error('Cast Error:', e);
        alert('Request failed: ' + e.message);
    } finally {
        if (item) item.style.background = originalBg;
    }
}

function castToManualIp() {
    const ip = document.getElementById('manualDeviceIp').value.trim();
    if (!ip) return alert('Please enter an IP or URL');
    castToDevice(ip, null);
}
function copyLink(url) {
    navigator.clipboard.writeText(url).then(() => {
        alert('Copied stream URL to clipboard!');
    });
}

function clearPlaybackState() {
    // Clear session storage
    sessionStorage.removeItem('isPlaying');
    sessionStorage.removeItem('isCasting');
    sessionStorage.removeItem('castDeviceTarget');
    sessionStorage.removeItem('stationName');
    sessionStorage.removeItem('stationLogo');
    sessionStorage.removeItem('castingTo');

    // Update UI elements
    const bar = document.getElementById('playback-bar');
    if (bar) bar.style.display = 'none';
    
    const audio = document.getElementById('main-audio');
    if (audio) {
        audio.pause();
        audio.src = "";
        audio.load();
    }

    // Reset all play buttons
    updatePlayBtns(null);

    // Update dynamic favicon and stats
    updateDashboard();
}

async function stopAllAudio() {
    const audio = document.getElementById('main-audio');
    
    // If we were casting, send a stop request to the backend
    const isCasting = sessionStorage.getItem('isCasting');
    const castTarget = sessionStorage.getItem('castDeviceTarget');
    if (isCasting === 'true' && castTarget) {
        const payload = {};
        if (castTarget.includes('.') || castTarget.includes('://')) {
            payload.manual_location = castTarget;
        } else {
            payload.udn = castTarget;
        }
        
        try {
            await fetch('/api/dlna/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        } catch (e) {
            console.error("Failed to stop DLNA:", e);
        }
    }

    clearPlaybackState();
}

function togglePlayer(id) {
    const audio = document.getElementById('main-audio');
    const bar = document.getElementById('playback-bar');
    const currentPlayingId = sessionStorage.getItem('isPlaying');
    
    if (currentPlayingId === id) {
        // Stop if clicking the same one
        stopAllAudio();
        return;
    }

    // New station logic
    audio.pause();
    audio.src = `/stream.mp3?v=${id}`;
    audio.play().catch(e => console.log("Play failed:", e));
    sessionStorage.setItem('isPlaying', id);

    // Update UI state
    updatePlayBtns(id);

    // Update and show playback bar
    const item = document.getElementById(`play-btn-${id}`).closest('.stream-item');
    const nameElem = item.querySelector('.station-name-text');
    const name = nameElem ? nameElem.innerText : 'Unknown Station';
    const logoElem = item.querySelector('.stream-logo');
    const logo = logoElem ? logoElem.src : '';
    
    safeSetText('playback-station-name', name);
    safeSetText('playback-status', 'Currently Playing');
    const logoPlaying = document.getElementById('playback-logo');
    if (logoPlaying) logoPlaying.src = logo;
    if (bar) bar.style.display = 'flex';

    // Persist state
    sessionStorage.setItem('isPlaying', id);
    sessionStorage.setItem('isCasting', 'false');
    sessionStorage.setItem('stationName', name);
    sessionStorage.setItem('stationLogo', logo);

    // Update live count immediately
    updateDashboard();
}

function restoreUIState() {
    const isPlaying = sessionStorage.getItem('isPlaying');
    if (!isPlaying) return;

    const isCasting = sessionStorage.getItem('isCasting') === 'true';
    const name = sessionStorage.getItem('stationName');
    const logo = sessionStorage.getItem('stationLogo');
    const castingTo = sessionStorage.getItem('castingTo');

    const bar = document.getElementById('playback-bar');
    if (bar) {
        safeSetText('playback-station-name', name || 'Station');
        safeSetText('playback-status', isCasting ? `Casting to ${castingTo || 'Device'}` : 'Currently Playing');
        const logoPlaying = document.getElementById('playback-logo');
        if (logoPlaying && logo) logoPlaying.src = logo;
        bar.style.display = 'flex';
    }

    // Update play buttons on the page (they might be re-rendered by updateDashboard)
    setTimeout(() => updatePlayBtns(isPlaying), 500);
}

// Global volume control listener
document.addEventListener('DOMContentLoaded', () => {
    initTheme();
    const currentServerId = SERVER_ID;
    const storedServerId = sessionStorage.getItem('server_id');
    
    if (storedServerId && storedServerId !== currentServerId) {
        console.log("Service restart detected on load, cleaning session storage.");
        clearPlaybackState();
    }
    
    sessionStorage.setItem('server_id', currentServerId);
    restoreUIState();
    const volControl = document.getElementById('volume-control');
    volControl.addEventListener('input', (e) => {
        const vol = e.target.value;
        document.getElementById('main-audio').volume = vol;
        localStorage.setItem('userVolume', vol);
        
        const muteBtn = document.getElementById('mute-btn');
        if (muteBtn) {
            muteBtn.innerText = vol == 0 ? '🔇' : '🔊';
        }
    });

    // Load saved volume
    const savedVol = localStorage.getItem('userVolume');
    if (savedVol !== null) {
        volControl.value = savedVol;
    }
    
    window.toggleMute = function() {
        const audio = document.getElementById('main-audio');
        const volControl = document.getElementById('volume-control');
        const muteBtn = document.getElementById('mute-btn');
        
        if (audio.volume > 0) {
            // Store previous volume and mute
            audio.dataset.prevVol = audio.volume;
            audio.volume = 0;
            volControl.value = 0;
            if (muteBtn) muteBtn.innerText = '🔇';
        } else {
            // Unmute to previous volume or 50%
            const prevVol = audio.dataset.prevVol || 0.5;
            audio.volume = prevVol;
            volControl.value = prevVol;
            if (muteBtn) muteBtn.innerText = '🔊';
        }
        localStorage.setItem('userVolume', audio.volume);
    };

    // Set initial mute icon if saved volume is 0
    if (savedVol !== null && parseFloat(savedVol) === 0) {
        const muteBtn = document.getElementById('mute-btn');
        if (muteBtn) muteBtn.innerText = '🔇';
    }

    // Initial dashboard update
    updateDashboard();
});
function openModal() {
    document.getElementById('modalTitle').innerText = "Add New Station";
    document.getElementById('oldStationUrl').value = "";
    document.getElementById('stationUrl').value = "";
    document.getElementById('stationName').value = "";
    document.getElementById('stationId').value = "";
    document.getElementById('stationGroup').value = "";
    document.getElementById('deleteBtn').style.display = 'none';
    document.getElementById('modalOverlay').style.display = 'flex';
}

function openEditModal(id, name, url, group, tvg_id) {
    document.getElementById('modalTitle').innerText = "Edit Station";
    document.getElementById('oldStationUrl').value = url;
    document.getElementById('stationUrl').value = url;
    document.getElementById('stationName').value = name;
    document.getElementById('stationId').value = tvg_id;
    document.getElementById('stationGroup').value = group;
    document.getElementById('deleteBtn').style.display = 'inline-flex';
    document.getElementById('modalOverlay').style.display = 'flex';
}

function openDeleteConfirmModal() {
    const name = document.getElementById('stationName').value;
    document.getElementById('deleteConfirmText').innerText = `Delete "${name}"? This will remove it from the M3U file and cannot be undone.`;
    document.getElementById('deleteConfirmOverlay').style.display = 'flex';
}

function closeDeleteConfirmModal() {
    document.getElementById('deleteConfirmOverlay').style.display = 'none';
}

async function confirmDeleteStation() {
    const url = document.getElementById('oldStationUrl').value;
    const btn = document.getElementById('confirmDeleteBtn');
    btn.disabled = true;
    btn.innerText = 'Deleting...';

    try {
        const response = await fetch('/delete_station', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        if (response.ok) {
            closeDeleteConfirmModal();
            closeModal();
            updateDashboard();
        } else {
            const err = await response.json();
            alert('Error: ' + err.message);
        }
    } catch (e) {
        alert('Request failed');
    } finally {
        btn.disabled = false;
        btn.innerText = 'Delete';
    }
}

function closeModal() {
    document.getElementById('modalOverlay').style.display = 'none';
}

function openErrorModal() {
    document.getElementById('errorModalOverlay').style.display = 'flex';
}

function closeErrorModal() {
    document.getElementById('errorModalOverlay').style.display = 'none';
}

async function updateDashboard() {
    const badge = document.getElementById('system-status-badge');
    try {
        const response = await fetch('/api/stats');
        if (!response.ok) throw new Error('Offline');
        const data = await response.json();
        
        safeSetText('system-status-badge', '● ONLINE');
        const badge = document.getElementById('system-status-badge');
        if (badge) badge.className = 'badge badge-success';
        
        // Update dynamic favicon badge
        updateFaviconBadge(data.live_count);
        
        safeSetText('uptime-val', data.uptime);
        safeSetText('live-count-val', data.live_count);
        
        // Check for server restart
        const storedServerId = sessionStorage.getItem('server_id');
        if (data.server_id && storedServerId && data.server_id !== storedServerId) {
            console.log("Server restart detected, clearing stale playback state.");
            clearPlaybackState();
        }
        if (data.server_id) {
            sessionStorage.setItem('server_id', data.server_id);
        }

        const container = document.getElementById('stream-list-container');
        if (data.streams.length > 0) {
            const playingId = sessionStorage.getItem('isPlaying');

            // Group by tvg_id preserving order
            const groupMap = {};
            const groupOrder = [];
            data.streams.forEach(stream => {
                const key = stream.tvg_id || 'Other';
                if (!groupMap[key]) { groupMap[key] = []; groupOrder.push(key); }
                groupMap[key].push(stream);
            });

            // Build index of existing stream-item elements keyed by stream id
            const existingItems = {};
            container.querySelectorAll('.stream-item[data-stream-id]').forEach(el => {
                existingItems[el.dataset.streamId] = el;
            });

            // Build/update group containers, patching existing items in place
            const seenIds = new Set();
            const seenGroups = new Set();

            groupOrder.forEach(key => {
                seenGroups.add(key);
                let groupEl = container.querySelector(`.stream-group[data-group="${CSS.escape(key)}"]`);
                if (!groupEl) {
                    groupEl = document.createElement('div');
                    groupEl.className = 'stream-group';
                    groupEl.dataset.group = key;
                    groupEl.innerHTML = `<div class="group-header">${key}</div><div class="stream-list"></div>`;
                    container.appendChild(groupEl);
                }
                const listEl = groupEl.querySelector('.stream-list');

                groupMap[key].forEach((stream, idx) => {
                    seenIds.add(stream.id);
                    const avail = stream.availability || 'checking';
                    const live = stream.listeners > 0 && avail === 'available';
                    const dotTitle = stream.listeners > 0 ? `${stream.listeners} listening` : avail;
                    const subText = `${stream.id}${stream.listeners > 0 ? ' • ' + stream.listeners + ' listening' : ''}`;

                    let item = existingItems[stream.id];
                    if (!item) {
                        // Create new element only when it doesn't exist yet
                        item = document.createElement('div');
                        item.className = 'stream-item';
                        item.dataset.streamId = stream.id;
                        item.innerHTML = `
                            <span class="avail-dot avail-${avail}${live ? ' live' : ''}" title="${dotTitle}"></span>
                            <img src="${stream.logo || ''}" class="stream-logo" alt="Logo" onerror="this.style.background='var(--logo-placeholder)'">
                            <div class="stream-info">
                                <div class="stream-name-wrapper">
                                    <span class="station-name-text">${stream.name}</span>
                                </div>
                                <span class="stream-sub">${subText}</span>
                            </div>
                            <div class="stream-actions">
                                <button class="action-link${stream.id === playingId ? ' playing' : ''}" id="play-btn-${stream.id}" onclick="togglePlayer('${stream.id}')">${stream.id === playingId ? '⏹' : '▶'}</button>
                                <button class="action-link" onclick="openCastModal('${stream.id}', '${stream.name}')">📺</button>
                                <button class="action-link" onclick="openEditModal('${stream.id}', '${stream.name}', '${stream.url}', '${stream.group}', '${stream.tvg_id}')">✎</button>
                                <a href="https://www.youtube.com/watch?v=${stream.id}" class="action-link" target="_blank">↗</a>
                            </div>`;
                        listEl.appendChild(item);

                        // Measure marquee after insert, play once then rely on hover
                        const nameEl = item.querySelector('.station-name-text');
                        const overflow = nameEl.scrollWidth - nameEl.parentElement.clientWidth;
                        if (overflow > 0) {
                            nameEl.style.setProperty('--marquee-offset', `-${overflow}px`);
                            nameEl.classList.add('init-play');
                            nameEl.addEventListener('animationend', () => nameEl.classList.remove('init-play'), { once: true });
                        }
                    } else {
                        // Patch only changed attributes on existing element
                        const dot = item.querySelector('.avail-dot');
                        const dotClass = `avail-dot avail-${avail}${live ? ' live' : ''}`;
                        if (dot.className !== dotClass) dot.className = dotClass;
                        if (dot.title !== dotTitle) dot.title = dotTitle;

                        const sub = item.querySelector('.stream-sub');
                        if (sub.textContent !== subText) sub.textContent = subText;

                        const playBtn = item.querySelector(`#play-btn-${stream.id}`);
                        const isActive = stream.id === playingId;
                        const wantedLabel = isActive ? '⏹' : '▶';
                        if (playBtn) {
                            if (playBtn.innerText !== wantedLabel) playBtn.innerText = wantedLabel;
                            playBtn.classList.toggle('playing', isActive);
                        }

                        // Move into correct group list if it changed groups
                        if (item.parentElement !== listEl) {
                            const ref = listEl.children[idx] || null;
                            listEl.insertBefore(item, ref);
                        } else if (listEl.children[idx] !== item) {
                            // Already in right list but wrong position — insertBefore the element
                            // currently at idx (which shifts down), only if truly misplaced
                            const ref = listEl.children[idx] || null;
                            listEl.insertBefore(item, ref);
                        }
                    }
                });
            });

            // Remove items that no longer exist in data
            Object.entries(existingItems).forEach(([id, el]) => {
                if (!seenIds.has(id)) el.remove();
            });

            // Remove groups that no longer exist
            container.querySelectorAll('.stream-group[data-group]').forEach(el => {
                if (!seenGroups.has(el.dataset.group)) el.remove();
            });

            // Ensure group order matches groupOrder — only move if position is wrong
            groupOrder.forEach((key, idx) => {
                const groupEl = container.querySelector(`.stream-group[data-group="${CSS.escape(key)}"]`);
                if (groupEl && container.children[idx] !== groupEl) {
                    container.insertBefore(groupEl, container.children[idx] || null);
                }
            });
        } else {
            container.innerHTML = '<p style="color: var(--text-dim); font-style: italic;">No stations found in youtube.m3u.</p>';
        }

        // Update errors
        const errorContainer = document.getElementById('error-log-container-modal');
        const errorBtn = document.getElementById('errorBtn');
        if (data.errors && data.errors.length > 0) {
            let errorHtml = '';
            // data.errors is deque(maxlen=10), so it's a list
            data.errors.forEach(err => {
                errorHtml += `<div>${err}</div>`;
            });
            errorContainer.innerHTML = errorHtml;
            errorBtn.style.display = 'flex';
        } else {
            errorContainer.innerHTML = '<p style="color: var(--text-dim); font-style: italic; text-align: center;">No errors reported in this session.</p>';
            errorBtn.style.display = 'none';
        }
    } catch (e) {
        console.error("Dashboard update failed", e);
        badge.innerText = '● SYSTEM OFFLINE';
        badge.className = 'badge badge-error';
    }
}

// Update every 10 seconds
setInterval(updateDashboard, 10000);

async function handleStationSubmit() {
    const oldUrl = document.getElementById('oldStationUrl').value;
    const url = document.getElementById('stationUrl').value;
    const name = document.getElementById('stationName').value;
    const idEl = document.getElementById('stationId');
    const groupEl = document.getElementById('stationGroup');
    const id = idEl.value || idEl.placeholder;
    const group = groupEl.value || groupEl.placeholder;
    const btn = document.getElementById('submitBtn');

    if (!url || !name) return alert('URL and Name are required');

    btn.disabled = true;
    btn.innerText = 'Saving...';

    const endpoint = oldUrl ? '/edit_station' : '/add_station';
    const body = { url, name, id, group };
    if (oldUrl) body.old_url = oldUrl;

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (response.ok) {
            closeModal();
            updateDashboard();
        } else {
            const err = await response.json();
            alert('Error: ' + err.message);
        }
    } catch (e) {
        alert('Request failed');
    } finally {
        btn.disabled = false;
        btn.innerText = 'Save Station';
    }
}

async function refreshM3U() {
    const btn = document.getElementById('refreshBtn');
    const originalText = btn.innerText;
    btn.innerText = '⌛ Refreshing...';
    btn.disabled = true;
    
    try {
        const response = await fetch('/refresh_m3u', { method: 'POST' });
        if (response.ok) {
            btn.innerText = '✅ Updated';
            setTimeout(() => { 
                btn.innerText = originalText;
                btn.disabled = false;
                updateDashboard();
            }, 1000);
        } else {
            btn.innerText = '❌ Error';
            setTimeout(() => { 
                btn.innerText = originalText;
                btn.disabled = false;
            }, 2000);
        }
    } catch (e) {
        btn.innerText = '❌ Failed';
        setTimeout(() => { 
            btn.innerText = originalText;
            btn.disabled = false;
        }, 2000);
    }
}

// ── Reorder Modal ─────────────────────────────────────────────────────────────

let reorderData = [];   // flat array of stream objects in current order
let dragSrcIdx = null;

function openReorderModal() {
    document.getElementById('reorderModalOverlay').style.display = 'flex';
    fetch('/api/stats')
        .then(r => r.json())
        .then(data => {
            reorderData = data.streams || [];
            renderReorderList();
        })
        .catch(() => {
            document.getElementById('reorder-list').innerHTML =
                '<p style="color:#ef4444;text-align:center;">Failed to load stations.</p>';
        });
}

function closeReorderModal() {
    document.getElementById('reorderModalOverlay').style.display = 'none';
    reorderData = [];
    dragSrcIdx = null;
}

function renderReorderList() {
    const list = document.getElementById('reorder-list');
    if (!reorderData.length) {
        list.innerHTML = '<p style="color:var(--text-dim);text-align:center;font-style:italic;">No stations.</p>';
        return;
    }

    let html = '';
    let prevGroup = null;
    reorderData.forEach((stream, idx) => {
        const group = stream.tvg_id || 'Other';
        if (group !== prevGroup) {
            html += `<div class="reorder-group-label">${group}</div>`;
            prevGroup = group;
        }
        html += `
        <div class="reorder-item" draggable="true" data-idx="${idx}">
            <span class="drag-handle">⠿</span>
            <img class="reorder-thumb" src="${stream.logo || ''}" alt="" onerror="this.style.background='var(--logo-placeholder)'">
            <span class="reorder-name">${stream.name}</span>
        </div>`;
    });
    list.innerHTML = html;

    list.querySelectorAll('.reorder-item').forEach(el => {
        el.addEventListener('dragstart', onDragStart);
        el.addEventListener('dragover',  onDragOver);
        el.addEventListener('dragleave', onDragLeave);
        el.addEventListener('drop',      onDrop);
        el.addEventListener('dragend',   onDragEnd);
    });
}

function onDragStart(e) {
    dragSrcIdx = parseInt(e.currentTarget.dataset.idx);
    e.currentTarget.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
}

function onDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    e.currentTarget.classList.add('drag-over');
}

function onDragLeave(e) {
    e.currentTarget.classList.remove('drag-over');
}

function onDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('drag-over');
    const dropIdx = parseInt(e.currentTarget.dataset.idx);
    if (dragSrcIdx === null || dragSrcIdx === dropIdx) return;
    const moved = reorderData.splice(dragSrcIdx, 1)[0];
    reorderData.splice(dropIdx, 0, moved);
    dragSrcIdx = null;
    renderReorderList();
}

function onDragEnd(e) {
    e.currentTarget.classList.remove('dragging');
    dragSrcIdx = null;
}

async function saveOrder() {
    const btn = document.getElementById('saveOrderBtn');
    btn.disabled = true;
    btn.innerText = 'Saving…';
    try {
        const resp = await fetch('/reorder_stations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ order: reorderData.map(s => s.id) })
        });
        const result = await resp.json();
        if (result.status === 'success') {
            btn.innerText = '✅ Saved';
            setTimeout(() => {
                closeReorderModal();
                btn.innerText = 'Save Order';
                btn.disabled = false;
                updateDashboard();
            }, 800);
        } else {
            btn.innerText = '❌ Error';
            setTimeout(() => { btn.innerText = 'Save Order'; btn.disabled = false; }, 2000);
        }
    } catch {
        btn.innerText = '❌ Failed';
        setTimeout(() => { btn.innerText = 'Save Order'; btn.disabled = false; }, 2000);
    }
}

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