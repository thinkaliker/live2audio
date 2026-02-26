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
                        streams.append({"name": name, "url": url_line, "logo": logo})
                        
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

@app.route('/')
def index():
    uptime = str(datetime.now() - START_TIME).split('.')[0]
    streams = get_available_streams()
    
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <meta http-equiv="refresh" content="30">
        <title>Live2Audio Dashboard</title>
        <style>
            :root {
                --primary: #6366f1;
                --bg: #0f172a;
                --card: rgba(30, 41, 59, 0.7);
                --text: #f8fafc;
            }
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif, "Apple Color Emoji", "Segoe UI Emoji";
                background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
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
                backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 24px;
                padding: 40px;
                box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
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
                border: 1px solid rgba(255, 255, 255, 0.1);
                color: #94a3b8;
                padding: 8px 16px;
                border-radius: 12px;
                cursor: pointer;
                font-size: 0.75rem;
                transition: all 0.2s;
            }
            .refresh-btn:hover {
                background: rgba(255, 255, 255, 0.1);
                color: var(--text);
            }
            .refresh-btn:active {
                transform: scale(0.95);
            }
            h1 {
                font-size: 2.5rem;
                margin: 0;
                background: linear-gradient(to right, #818cf8, #c084fc);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }
            .stats-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 40px;
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
                align-items: center;
                background: rgba(255, 255, 255, 0.03);
                padding: 10px 15px;
                border-radius: 12px;
                transition: all 0.2s;
            }
            .stream-item:hover {
                background: rgba(255, 255, 255, 0.08);
                transform: translateY(-2px);
            }
            .stream-logo {
                width: 40px;
                height: 40px;
                border-radius: 8px;
                margin-right: 12px;
                object-fit: cover;
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
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <button class="refresh-btn" onclick="refreshM3U()" id="refreshBtn">↻ Refresh List</button>
                <h1>Live2Audio</h1>
                <p style="color: #94a3b8">Premium YouTube-to-Jellyfin Audio Streamer</p>
                <span class="badge badge-success">● SYSTEM ONLINE</span>
            </header>

            <div class="stats-grid">
                <div class="stat-card">
                    <span class="stat-value">{{ uptime }}</span>
                    <span class="stat-label">System Uptime</span>
                </div>
                <div class="stat-card">
                    <span class="stat-value" style="font-size: 1.1rem;">{{ last_stream_name }}</span>
                    <span class="stat-label">Recently Played</span>
                </div>
            </div>

            <section>
                <h2>Station List Configuration</h2>
                {% if streams %}
                <div class="stream-list">
                    {% for stream in streams %}
                    <div class="stream-item">
                        {% if stream.logo %}
                        <img src="{{ stream.logo }}" class="stream-logo" alt="Logo">
                        {% else %}
                        <div class="stream-logo" style="background:#334155"></div>
                        {% endif %}
                        <span>{{ stream.name }}</span>
                    </div>
                    {% endfor %}
                </div>
                {% else %}
                <p style="color: #64748b; font-style: italic;">No stations found in youtube.m3u.</p>
                {% endif %}
            </section>

            <section>
                <h2>Recent Session Errors</h2>
                {% if errors %}
                <div class="error-log">
                    {% for error in errors %}
                    <div>{{ error }}</div>
                    {% endfor %}
                </div>
                {% else %}
                <p style="color: #64748b; font-style: italic;">No errors reported in this session.</p>
                {% endif %}
            </section>
        </div>
        <script>
            async function refreshM3U() {
                const btn = document.getElementById('refreshBtn');
                const originalText = btn.innerText;
                btn.innerText = '⌛ Refreshing...';
                btn.disabled = true;
                
                try {
                    const response = await fetch('/refresh_m3u', { method: 'POST' });
                    if (response.ok) {
                        btn.innerText = '✅ Updated';
                        setTimeout(() => { location.reload(); }, 500);
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
        last_stream_name=LAST_STREAM["name"]
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
    return send_from_directory(CACHE_DIR, f"{video_id}.jpg")

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
