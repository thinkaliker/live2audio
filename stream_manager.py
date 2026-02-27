import subprocess
import os
import time
import threading
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, Response, request, jsonify, redirect, render_template_string

app = Flask(__name__)

# Server metadata
START_TIME = datetime.now()
ERROR_LOG = deque(maxlen=10)
LOG_LOCK = threading.Lock()
LAST_STREAM = {"name": "None", "time": "Never"}
VIDEO_ID_MAP = {}  # Map video_id to station name
ACTIVE_STREAMS = {}  # Map video_id to count of active listeners
STREAMS_LOCK = threading.Lock()

CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR, exist_ok=True)

def cache_thumbnail(video_id):
    """Download thumbnail to local cache if it doesn't exist."""
    cache_path = os.path.join(CACHE_DIR, f"{video_id}.jpg")
    if not os.path.exists(cache_path):
        print(f"Background caching thumbnail for {video_id}...", flush=True)
        url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        subprocess.run(['curl', '-s', '-L', '-o', cache_path, url])

def build_youtube_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"

def get_available_streams():
    global VIDEO_ID_MAP
    streams = []
    temp_map = {}
    m3u_path = "youtube.m3u"
    
    if os.path.exists(m3u_path):
        try:
            with open(m3u_path, 'r') as f:
                content = f.read()
                lines = content.split('\n')
                for i in range(len(lines)):
                    if lines[i].startswith('#EXTINF:'):
                        info = lines[i]
                        url_line = lines[i+1].strip() if i+1 < len(lines) else ""
                        name = info.split(',')[-1].strip()
                        
                        # Extract video ID from URL
                        vid_id = "Unknown"
                        if "?v=" in url_line:
                            vid_id = url_line.split("?v=")[1].split("&")[0]
                        
                        temp_map[vid_id] = name
                        
                        # Always use local thumbnail proxy for dashboard to avoid localhost issues
                        logo = f"/thumbnail.jpg?v={vid_id}"
                        with STREAMS_LOCK:
                            listeners = ACTIVE_STREAMS.get(vid_id, 0)
                        streams.append({
                            "name": name, 
                            "url": url_line, 
                            "logo": logo, 
                            "id": vid_id,
                            "listeners": listeners
                        })
                        
                        # Cache in background
                        threading.Thread(target=cache_thumbnail, args=(vid_id,), daemon=True).start()
            
            # Atomic update of the global map to prevent race conditions
            VIDEO_ID_MAP = temp_map
        except Exception as e:
            with LOG_LOCK:
                ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - M3U Parse Error: {str(e)}")
    return streams

@app.before_request
def log_request():
    print(f"Incoming: {request.method} {request.path}", flush=True)

@app.errorhandler(404)
def page_not_found(e):
    print(f"404 ERROR: {request.path}", flush=True)
    with LOG_LOCK:
        ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - 404: {request.path}")
    return "Not Found", 404

@app.route('/refresh_m3u', methods=['POST'])
def refresh_m3u():
    print("Manual M3U refresh triggered...", flush=True)
    get_available_streams()
    return jsonify({"status": "success", "message": "Station list and thumbnails updated"})

@app.route('/playlist.m3u')
def get_playlist():
    m3u_path = "youtube.m3u"
    if os.path.exists(m3u_path):
        from flask import send_file
        return send_file(m3u_path, mimetype='text/plain')
    return "M3U file not found", 404

@app.route('/add_station', methods=['POST'])
def add_station():
    data = request.json
    url = data.get('url', '').strip()
    name = data.get('name', '').strip()
    tvg_id = data.get('id', 'Manual').strip()
    group = data.get('group', 'YouTube Radio').strip()

    if not url or not name:
        return jsonify({"status": "error", "message": "URL and Name are required"}), 400

    # Extract video ID
    vid_id = None
    if "?v=" in url:
        vid_id = url.split("?v=")[1].split("&")[0]
    elif "youtu.be/" in url:
        vid_id = url.split("youtu.be/")[1].split("?")[0]
    else:
        vid_id = url # Assume raw ID if no URL format detected

    if not vid_id:
        return jsonify({"status": "error", "message": "Could not extract YouTube Video ID"}), 400

    # Sanitize and construct stream URL
    stream_url = f"http://localhost:5000/stream.mp3?v={vid_id}"
    
    # Append to M3U
    m3u_path = "youtube.m3u"
    try:
        with open(m3u_path, 'a') as f:
            f.write(f'\n#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="http://localhost:5000/thumbnail.jpg?v={vid_id}" group-title="{group}", {name}\n')
            f.write(f'{stream_url}\n')
        
        # Trigger refresh
        get_available_streams()
        return jsonify({"status": "success", "message": f"Added {name}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/stats')
def api_stats():
    uptime = str(datetime.now() - START_TIME).split('.')[0]
    streams = get_available_streams()
    with STREAMS_LOCK:
        live_count = sum(1 for count in ACTIVE_STREAMS.values() if count > 0)
    return jsonify({
        "uptime": uptime,
        "live_count": live_count,
        "recently_played": LAST_STREAM["name"],
        "streams": streams,
        "errors": list(ERROR_LOG)
    })

@app.route('/')
def index():
    uptime = str(datetime.now() - START_TIME).split('.')[0]
    streams = get_available_streams()
    
    with STREAMS_LOCK:
        live_count = sum(1 for count in ACTIVE_STREAMS.values() if count > 0)
    
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Live2Audio Dashboard</title>
        <style>
            :root {
                --primary: #6366f1;
                --bg: #0f172a;
                --card: #1e293b;
                --text: #f8fafc;
                --border: rgba(255, 255, 255, 0.1);
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
                background: var(--bg);
                color: var(--text);
                margin: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                min-height: 100vh;
                padding: 20px;
            }
            .container {
                max-width: 900px;
                width: 100%;
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 20px;
                padding: 40px;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
            }
            header {
                text-align: center;
                margin-bottom: 40px;
                position: relative;
            }
            .refresh-btn {
                position: absolute;
                right: 0;
                top: 0;
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid var(--border);
                color: #94a3b8;
                padding: 8px 16px;
                border-radius: 12px;
                cursor: pointer;
                font-size: 0.75rem;
                transition: all 0.2s;
                display: flex;
                align-items: center;
                gap: 6px;
            }
            .refresh-btn:hover {
                background: rgba(255, 255, 255, 0.1);
                color: var(--text);
            }
            .add-btn {
                right: 120px;
                background: var(--primary);
                color: white;
                border: none;
            }
            .add-btn:hover {
                filter: brightness(1.2);
            }
            .refresh-btn:active {
                transform: scale(0.95);
            }
            h1 {
                font-size: 2.2rem;
                margin: 0;
                color: var(--primary);
            }
            /* Modal Styles */
            .modal-overlay {
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                background: rgba(0, 0, 0, 0.8);
                backdrop-filter: blur(4px);
                display: none;
                justify-content: center;
                align-items: center;
                z-index: 1000;
            }
            .modal {
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 20px;
                padding: 30px;
                width: 100%;
                max-width: 450px;
                box-shadow: 0 20px 40px rgba(0,0,0,0.5);
            }
            .modal h3 { margin-top: 0; color: var(--primary); }
            .form-group { margin-bottom: 20px; }
            .form-group label { display: block; font-size: 0.8rem; color: #94a3b8; margin-bottom: 5px; }
            .form-group input {
                width: 100%;
                background: rgba(0,0,0,0.2);
                border: 1px solid var(--border);
                padding: 10px;
                border-radius: 8px;
                color: white;
                box-sizing: border-box;
            }
            .modal-actions { display: flex; gap: 10px; justify-content: flex-end; }
            .btn {
                padding: 10px 20px;
                border-radius: 8px;
                cursor: pointer;
                font-size: 0.9rem;
                border: none;
            }
            .btn-primary { background: var(--primary); color: white; }
            .btn-secondary { background: rgba(255,255,255,0.05); color: #94a3b8; }
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 20px;
                margin-bottom: 40px;
            }
            .pulse {
                width: 8px;
                height: 8px;
                background: #22c55e;
                border-radius: 50%;
                display: inline-block;
                margin-right: 8px;
                box-shadow: 0 0 0 rgba(34, 197, 94, 0.4);
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7); }
                70% { box-shadow: 0 0 0 10px rgba(34, 197, 94, 0); }
                100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
            }
            .player-container {
                width: 100%;
                margin-top: 12px;
                display: none;
            }
            audio {
                width: 100%;
                height: 32px;
                filter: invert(100%) hue-rotate(180deg) brightness(1.5);
            }
            .stat-card {
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.05);
                padding: 20px;
                border-radius: 16px;
                text-align: center;
            }
            .stat-value {
                font-size: 1.5rem;
                font-weight: 600;
                color: #818cf8;
                display: block;
            }
            .stat-label {
                font-size: 0.875rem;
                color: #94a3b8;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }
            section {
                margin-bottom: 30px;
            }
            h2 {
                font-size: 1.25rem;
                border-bottom: 1px solid rgba(255, 255, 255, 0.1);
                padding-bottom: 10px;
                margin-bottom: 15px;
            }
            .stream-list {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
                gap: 15px;
            }
            .stream-item {
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                background: rgba(255, 255, 255, 0.03);
                padding: 15px;
                border-radius: 12px;
                transition: all 0.2s;
            }
            .stream-item:hover {
                background: rgba(255, 255, 255, 0.08);
                transform: translateY(-2px);
            }
            .stream-logo {
                width: 44px;
                height: 44px;
                border-radius: 8px;
                margin-right: 12px;
                object-fit: cover;
            }
            .stream-info {
                flex: 1;
                display: flex;
                flex-direction: column;
            }
            .stream-actions {
                width: 100%;
                display: flex;
                gap: 8px;
                margin-top: 12px;
                padding-top: 12px;
                border-top: 1px solid rgba(255, 255, 255, 0.05);
            }
            .action-link {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                color: #94a3b8;
                padding: 6px 10px;
                border-radius: 8px;
                font-size: 0.7rem;
                text-decoration: none;
                transition: all 0.2s;
                white-space: nowrap;
                cursor: pointer;
            }
            .action-link:hover {
                background: rgba(255, 255, 255, 0.12);
                color: var(--text);
                border-color: var(--primary);
            }
            .error-log {
                background: rgba(239, 68, 68, 0.05);
                border: 1px solid rgba(239, 68, 68, 0.1);
                padding: 15px;
                border-radius: 12px;
                font-family: monospace;
                font-size: 0.875rem;
                color: #fca5a5;
                max-height: 200px;
                overflow-y: auto;
            }
            .badge {
                padding: 4px 10px;
                border-radius: 20px;
                font-size: 0.75rem;
                font-weight: 600;
            }
            .badge-success { background: rgba(34, 197, 94, 0.2); color: #4ade80; }
            .badge-error { background: rgba(239, 68, 68, 0.2); color: #fca5a5; }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <button class="refresh-btn add-btn" onclick="openModal()">+ Add Station</button>
                <button class="refresh-btn" onclick="refreshM3U()" id="refreshBtn">↻ Refresh List</button>
                <h1>Live2Audio</h1>
                <span class="badge badge-success" id="system-status-badge">● SYSTEM ONLINE</span>
            </header>

            <div class="stats-grid">
                <div class="stat-card">
                    <span class="stat-value" id="uptime-val">{{ uptime }}</span>
                    <span class="stat-label">System Uptime</span>
                </div>
                <div class="stat-card">
                    <span class="stat-value" id="live-count-val">{{ live_count }}</span>
                    <span class="stat-label">Live Stations</span>
                </div>
                <div class="stat-card">
                    <span class="stat-value" id="recently-played-val" style="font-size: 1.1rem;">{{ last_stream_name }}</span>
                    <span class="stat-label">Recently Played</span>
                </div>
            </div>

            <section>
                <h2>Station List Configuration</h2>
                <div id="stream-list-container">
                    {% if streams %}
                    <div class="stream-list">
                        {% for stream in streams %}
                        <div class="stream-item">
                            {% if stream.logo %}
                            <img src="{{ stream.logo }}" class="stream-logo" alt="Logo">
                            {% else %}
                            <div class="stream-logo" style="background:#334155"></div>
                            {% endif %}
                            <div class="stream-info">
                                <div style="display: flex; align-items: center;">
                                    {% if stream.listeners > 0 %}<span class="pulse"></span>{% endif %}
                                    <span style="font-weight: 500;">{{ stream.name }}</span>
                                </div>
                                <span style="font-size: 0.7rem; color: #64748b;">ID: {{ stream.id }} {% if stream.listeners > 0 %}• {{ stream.listeners }} listening{% endif %}</span>
                            </div>
                            <div class="stream-actions">
                                <button class="action-link" onclick="togglePlayer('player-{{ stream.id }}')">▶ Play</button>
                                <button class="action-link" onclick="copyLink('http://' + window.location.host + '/stream.mp3?v={{ stream.id }}')">📋 Copy Link</button>
                                <a href="https://www.youtube.com/watch?v={{ stream.id }}" class="action-link" target="_blank">↗ YouTube</a>
                            </div>
                            <div class="player-container" id="player-{{ stream.id }}">
                                <audio controls preload="none">
                                    <source src="/stream.mp3?v={{ stream.id }}" type="audio/mpeg">
                                </audio>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                    {% else %}
                    <p style="color: #64748b; font-style: italic;">No stations found in youtube.m3u.</p>
                    {% endif %}
                </div>
            </section>

            <section>
                <h2>Recent Session Errors</h2>
                <div id="error-log-container">
                    {% if errors %}
                    <div class="error-log">
                        {% for error in errors %}
                        <div>{{ error }}</div>
                        {% endfor %}
                    </div>
                    {% else %}
                    <p style="color: #64748b; font-style: italic;">No errors reported in this session.</p>
                    {% endif %}
                </div>
            </section>
        </div>

        <!-- Add Station Modal -->
        <div class="modal-overlay" id="modalOverlay">
            <div class="modal">
                <h3>Add New Station</h3>
                <div class="form-group">
                    <label>YouTube URL or Video ID</label>
                    <input type="text" id="stationUrl" placeholder="https://youtube.com/watch?v=...">
                </div>
                <div class="form-group">
                    <label>Station Name</label>
                    <input type="text" id="stationName" placeholder="Lofi Radio">
                </div>
                <div class="form-group">
                    <label>Station ID (Optional)</label>
                    <input type="text" id="stationId" placeholder="LofiGirl">
                </div>
                <div class="form-group">
                    <label>Group (Optional)</label>
                    <input type="text" id="stationGroup" placeholder="YouTube Radio">
                </div>
                <div class="modal-actions">
                    <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
                    <button class="btn btn-primary" onclick="submitStation()" id="submitBtn">Add Station</button>
                </div>
            </div>
        </div>

        <script>
            function copyLink(url) {
                navigator.clipboard.writeText(url).then(() => {
                    alert('Copied stream URL to clipboard!');
                });
            }

            function togglePlayer(id) {
                const p = document.getElementById(id);
                const audio = p.querySelector('audio');
                if (p.style.display === 'block') {
                    p.style.display = 'none';
                    audio.pause();
                } else {
                    // Stop any other playing audio
                    document.querySelectorAll('audio').forEach(a => {
                        // Only pause if not the one we want to play
                        if (a.parentElement.id !== id) {
                            a.pause();
                            a.parentElement.style.display = 'none';
                        }
                    });
                    p.style.display = 'block';
                    audio.play();
                }
            }
            function openModal() { document.getElementById('modalOverlay').style.display = 'flex'; }
            function closeModal() { document.getElementById('modalOverlay').style.display = 'none'; }

            async function updateDashboard() {
                const badge = document.getElementById('system-status-badge');
                try {
                    const response = await fetch('/api/stats');
                    if (!response.ok) throw new Error('Offline');
                    const data = await response.json();
                    
                    badge.innerText = '● SYSTEM ONLINE';
                    badge.className = 'badge badge-success';
                    
                    document.getElementById('uptime-val').innerText = data.uptime;
                    document.getElementById('live-count-val').innerText = data.live_count;
                    document.getElementById('recently-played-val').innerText = data.recently_played;
                    
                    // Update streams without stopping audio
                    const container = document.getElementById('stream-list-container');
                    if (data.streams.length > 0) {
                        let html = '<div class="stream-list">';
                        data.streams.forEach(stream => {
                            const isPlaying = document.getElementById(`player-${stream.id}`)?.style.display === 'block';
                            html += `
                            <div class="stream-item">
                                <img src="${stream.logo || ''}" class="stream-logo" alt="Logo" onerror="this.style.background='#334155'">
                                <div class="stream-info">
                                    <div style="display: flex; align-items: center;">
                                        ${stream.listeners > 0 ? '<span class="pulse"></span>' : ''}
                                        <span style="font-weight: 500;">${stream.name}</span>
                                    </div>
                                    <span style="font-size: 0.7rem; color: #64748b;">ID: ${stream.id} ${stream.listeners > 0 ? '• ' + stream.listeners + ' listening' : ''}</span>
                                </div>
                                <div class="stream-actions">
                                    <button class="action-link" onclick="togglePlayer('player-${stream.id}')">▶ Play</button>
                                    <button class="action-link" onclick="copyLink('http://' + window.location.host + '/stream.mp3?v=${stream.id}')">📋 Copy Link</button>
                                    <a href="https://www.youtube.com/watch?v=${stream.id}" class="action-link" target="_blank">↗ YouTube</a>
                                </div>
                                <div class="player-container" id="player-${stream.id}" style="${isPlaying ? 'display: block;' : ''}">
                                    <audio controls preload="none">
                                        <source src="/stream.mp3?v=${stream.id}" type="audio/mpeg">
                                    </audio>
                                </div>
                            </div>`;
                        });
                        html += '</div>';
                        
                        // To preserve audio playback, we should ideally only update the DOM elements that changed
                        // or re-attach the audio element if it was playing.
                        // For simplicity, we check if ANY audio is playing. If so, we are careful.
                        const activeAudio = document.querySelector('audio:not([paused])');
                        const playingId = activeAudio ? activeAudio.parentElement.id : null;
                        const currentTime = activeAudio ? activeAudio.currentTime : 0;

                        container.innerHTML = html;

                        if (playingId) {
                            const newAudio = document.getElementById(playingId).querySelector('audio');
                            newAudio.currentTime = currentTime;
                            newAudio.play().catch(() => {}); // Handle autoplay restrictions
                        }
                    } else {
                        container.innerHTML = '<p style="color: #64748b; font-style: italic;">No stations found in youtube.m3u.</p>';
                    }

                    // Update errors
                    const errorContainer = document.getElementById('error-log-container');
                    if (data.errors.length > 0) {
                        let errorHtml = '<div class="error-log">';
                        data.errors.forEach(err => {
                            errorHtml += `<div>${err}</div>`;
                        });
                        errorHtml += '</div>';
                        errorContainer.innerHTML = errorHtml;
                    } else {
                        errorContainer.innerHTML = '<p style="color: #64748b; font-style: italic;">No errors reported in this session.</p>';
                    }
                } catch (e) {
                    console.error("Dashboard update failed", e);
                    badge.innerText = '● SYSTEM OFFLINE';
                    badge.className = 'badge badge-error';
                }
            }

            // Update every 10 seconds
            setInterval(updateDashboard, 10000);

            async function submitStation() {
                const url = document.getElementById('stationUrl').value;
                const name = document.getElementById('stationName').value;
                const id = document.getElementById('stationId').value;
                const group = document.getElementById('stationGroup').value;
                const btn = document.getElementById('submitBtn');

                if (!url || !name) return alert('URL and Name are required');

                btn.disabled = true;
                btn.innerText = 'Adding...';

                try {
                    const response = await fetch('/add_station', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url, name, id, group })
                    });
                    if (response.ok) {
                        closeModal();
                        updateDashboard();
                    } else {
                        const err = await response.json();
                        alert('Error: ' + err.message);
                    }
                } catch (e) {
                    alert('Failed to connect to server');
                } finally {
                    btn.disabled = false;
                    btn.innerText = 'Add Station';
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
        </script>
    </body>
    </html>
    """
    return render_template_string(
        html, 
        uptime=uptime, 
        streams=streams, 
        errors=list(ERROR_LOG),
        last_stream_name=LAST_STREAM["name"],
        live_count=live_count
    )

@app.route('/stream.mp3', methods=['GET', 'HEAD'])
def stream_audio():
    video_id = request.args.get('v')
    if not video_id:
        return "Missing video ID", 400
    
    # Update "Recently Played" with name lookup
    station_name = VIDEO_ID_MAP.get(video_id)
    if not station_name:
        # Try one refresh if name is unknown
        get_available_streams()
        station_name = VIDEO_ID_MAP.get(video_id, f"ID: {video_id}")

    with LOG_LOCK:
        LAST_STREAM["name"] = station_name
        LAST_STREAM["time"] = datetime.now().strftime('%H:%M:%S')

    # Simple HEAD support
    if request.method == 'HEAD':
        return Response(mimetype="audio/mpeg")
    
    youtube_url = build_youtube_url(video_id)
    print(f"--- Stream Request: {video_id} ---", flush=True)
    
    def generate():
        # Increment listener count
        with STREAMS_LOCK:
            ACTIVE_STREAMS[video_id] = ACTIVE_STREAMS.get(video_id, 0) + 1
            
        ffmpeg_process = None # Initialize ffmpeg_process outside try block
        try:
            # 1. Get the direct audio URL from YouTube
            # Using 'ba/b' as a robust fallback for "format not available" errors
            print(f"Fetching audio URL for {video_id} with format 'ba/b'...", flush=True)
            url_command = ['yt-dlp', '-g', '-f', 'ba/b', youtube_url]
            url_proc = subprocess.run(url_command, capture_output=True, text=True)
            
            if url_proc.returncode != 0:
                print(f"yt-dlp error: {url_proc.stderr}", flush=True)
                with LOG_LOCK:
                    ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - yt-dlp Error: {video_id}")
                return

            direct_url = url_proc.stdout.strip()
            print(f"Streaming from: {direct_url[:50]}...", flush=True)

            # 2. Stream using FFmpeg directly from the URL
            ffmpeg_command = [
                'ffmpeg',
                '-i', direct_url,
                '-f', 'mp3',
                '-acodec', 'libmp3lame',
                '-ab', '128k',
                '-flush_packets', '1',
                '-fflags', 'nobuffer',
                '-loglevel', 'error',
                'pipe:1'
            ]
            
            # Use bufsize=0 and flush=True for real-time visibility
            ffmpeg_process = subprocess.Popen(
                ffmpeg_command, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.DEVNULL,
                bufsize=0
            )
            
            while True:
                chunk = ffmpeg_process.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
                
        except Exception as e:
            print(f"Error: {e}", flush=True)
            with LOG_LOCK:
                ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - Stream Error: {str(e)[:50]}")
        finally:
            # Decrement listener count
            with STREAMS_LOCK:
                ACTIVE_STREAMS[video_id] = max(0, ACTIVE_STREAMS.get(video_id, 0) - 1)
            print(f"Cleaning up {video_id}", flush=True)
            if ffmpeg_process: # Check if ffmpeg_process was successfully created
                ffmpeg_process.terminate()
                try:
                    ffmpeg_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    ffmpeg_process.kill()

    return Response(
        generate(), 
        mimetype="audio/mpeg",
        headers={
            'Cache-Control': 'no-cache',
            'Accept-Ranges': 'none',
            'Access-Control-Allow-Origin': '*'
        }
    )

@app.route('/thumbnail.jpg')
def get_thumbnail():
    video_id = request.args.get('v')
    if not video_id:
        return "Missing video ID", 400
    
    # Check cache first
    cache_path = os.path.join(CACHE_DIR, f"{video_id}.jpg")
    if not os.path.exists(cache_path):
        print(f"Caching thumbnail for {video_id}...", flush=True)
        url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        # Download using curl
        subprocess.run(['curl', '-s', '-L', '-o', cache_path, url])
        
    from flask import send_from_directory
    response = send_from_directory(CACHE_DIR, f"{video_id}.jpg")
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'message': 'pong'})

if __name__ == '__main__':
    # Pre-populate map when running locally
    get_available_streams()
    app.run(host='0.0.0.0', port=5000)
else:
    # Pre-populate map when running under Gunicorn
    get_available_streams()
