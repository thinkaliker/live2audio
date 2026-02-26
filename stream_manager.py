import subprocess
import os
import time
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, Response, request, jsonify, redirect, render_template_string

app = Flask(__name__)

# Server metadata
START_TIME = datetime.now()
ERROR_LOG = deque(maxlen=10)
LAST_STREAM = {"name": "None", "time": "Never"}
VIDEO_ID_MAP = {}  # Map video_id to station name

def build_youtube_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"

def get_available_streams():
    streams = []
    m3u_path = "youtube.m3u"
    VIDEO_ID_MAP.clear()
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
                        # Example: http://localhost:5000/stream.mp3?v=jfKfPfyJRdk
                        vid_id = "Unknown"
                        if "?v=" in url_line:
                            vid_id = url_line.split("?v=")[1].split("&")[0]
                        
                        VIDEO_ID_MAP[vid_id] = name
                        
                        # Extract logo
                        logo = ""
                        if 'tvg-logo="' in info:
                            logo = info.split('tvg-logo="')[1].split('"')[0]
                        streams.append({"name": name, "url": url_line, "logo": logo})
        except Exception as e:
            ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - M3U Parse Error: {str(e)}")
    return streams

@app.before_request
def log_request():
    print(f"Incoming: {request.method} {request.path}", flush=True)

@app.errorhandler(404)
def page_not_found(e):
    print(f"404 ERROR: {request.path}", flush=True)
    ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - 404: {request.path}")
    return "Not Found", 404

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
                <h1>Live2Audio</h1>
                <p style="color: #94a3b8">Premium YouTube-to-Jellyfin Audio Streamer</p>
                <span class="badge badge-success">● SYSTEM ONLINE</span>
            </header>

            <div class="stats-grid">
                <div class="stat-card">
                    <span class="stat-value">{{ uptime }}</span>
                    <span class="stat-label">Uptime</span>
                </div>
                <div class="stat-card">
                    <span class="stat-value">{{ stream_count }}</span>
                    <span class="stat-label">Total Stations</span>
                </div>
                <div class="stat-card">
                    <span class="stat-value" style="font-size: 1.1rem;">{{ last_stream_name }}</span>
                    <span class="stat-label">Recently Played</span>
                </div>
            </div>

            <section>
                <h2>Configured Stations (M3U)</h2>
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
    </body>
    </html>
    """
    return render_template_string(
        html, 
        uptime=uptime, 
        stream_count=len(streams), 
        streams=streams, 
        errors=list(ERROR_LOG),
        last_stream_name=LAST_STREAM["name"]
    )

@app.route('/stream.mp3', methods=['GET', 'HEAD'])
def stream_audio():
    video_id = request.args.get('v')
    if not video_id:
        return "Missing video ID", 400
    
    # Update "Recently Played"
    station_name = VIDEO_ID_MAP.get(video_id, f"ID: {video_id}")
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
    # Redirect to YouTube's high quality thumbnail
    return redirect(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'message': 'pong'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
