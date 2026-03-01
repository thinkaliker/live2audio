import subprocess
import os
import time
import threading
import socket
import html
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, Response, request, jsonify, redirect, render_template_string
try:
    import upnpclient
except ImportError:
    upnpclient = None

app = Flask(__name__)

# Server metadata
START_TIME = datetime.now()
ERROR_LOG = deque(maxlen=10)
LOG_LOCK = threading.Lock()
LAST_STREAM = {"name": "None", "time": "Never"}
VIDEO_ID_MAP = {}  # Map video_id to station name
ACTIVE_STREAMS = {}  # Map video_id to count of active listeners
STREAMS_LOCK = threading.Lock()

# DLNA Discovery
DLNA_DEVICES = []
DEVICES_LOCK = threading.Lock()

CACHE_DIR = "cache"
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR, exist_ok=True)

def get_server_ip():
    """Get the local IP address of the server."""
    # Allow environment variable override
    env_ip = os.getenv('SERVER_IP')
    if env_ip:
        return env_ip

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def discover_dlna_devices():
    """Discover DLNA clients on the network."""
    if not upnpclient:
        print("upnpclient not installed. DLNA discovery skipped.", flush=True)
        return

    global DLNA_DEVICES
    print("Starting DLNA discovery...", flush=True)
    try:
        devices = upnpclient.discover()
        new_devices = []
        for d in devices:
            # Look for devices with AVTransport service (RenderingControl is also common)
            try:
                if any("AVTransport" in s.service_id for s in d.services):
                    new_devices.append({
                        "name": d.friendly_name,
                        "location": d.location,
                        "udn": d.udn
                    })
            except Exception:
                continue
        
        with DEVICES_LOCK:
            DLNA_DEVICES = new_devices
        print(f"Discovered {len(DLNA_DEVICES)} DLNA devices.", flush=True)
    except Exception as e:
        print(f"DLNA discovery failed: {e}", flush=True)

def start_discovery_thread():
    threading.Thread(target=discover_dlna_devices, daemon=True).start()

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
            import re
            with open(m3u_path, 'r') as f:
                content = f.read()
                lines = content.split('\n')
                for i in range(len(lines)):
                    if lines[i].startswith('#EXTINF:'):
                        info = lines[i]
                        url_line = lines[i+1].strip() if i+1 < len(lines) else ""
                        name = info.split(',')[-1].strip()
                        
                        # Extract metadata using regex
                        tvg_id_match = re.search(r'tvg-id="([^"]*)"', info)
                        group_match = re.search(r'group-title="([^"]*)"', info)
                        
                        tvg_id = tvg_id_match.group(1) if tvg_id_match else "Manual"
                        group = group_match.group(1) if group_match else "YouTube Radio"

                        # Extract video ID from URL
                        vid_id = "Unknown"
                        if "?v=" in url_line:
                            vid_id = url_line.split("?v=")[1].split("&")[0]
                        elif "youtu.be/" in url_line:
                            vid_id = url_line.split("youtu.be/")[1].split("?")[0]
                        
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
                            "tvg_id": tvg_id,
                            "group": group,
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

@app.route('/edit_station', methods=['POST'])
def edit_station():
    data = request.json
    old_url = data.get('old_url', '').strip()
    new_url = data.get('url', '').strip()
    new_name = data.get('name', '').strip()
    new_tvg_id = data.get('id', 'Manual').strip()
    new_group = data.get('group', 'YouTube Radio').strip()

    if not old_url or not new_url or not new_name:
        return jsonify({"status": "error", "message": "Original URL, New URL, and Name are required"}), 400

    # Extract new video ID
    new_vid_id = None
    if "?v=" in new_url:
        new_vid_id = new_url.split("?v=")[1].split("&")[0]
    elif "youtu.be/" in new_url:
        new_vid_id = new_url.split("youtu.be/")[1].split("?")[0]
    else:
        new_vid_id = new_url

    # Construct new stream URL
    # Use lowercase localhost to match what's already in the file or just use relative if possible? 
    # The existing code uses http://localhost:5000/stream.mp3?v=...
    new_stream_url = f"http://localhost:5000/stream.mp3?v={new_vid_id}"

    m3u_path = "youtube.m3u"
    try:
        with open(m3u_path, 'r') as f:
            lines = f.readlines()

        new_lines = []
        i = 0
        found = False
        while i < len(lines):
            line = lines[i]
            if line.startswith('#EXTINF:'):
                next_line = lines[i+1].strip() if i+1 < len(lines) else ""
                # Check if this is the station we want to edit by matching the old stream URL
                if next_line == old_url:
                    found = True
                    # Update this entry
                    new_lines.append(f'#EXTINF:-1 tvg-id="{new_tvg_id}" tvg-logo="http://localhost:5000/thumbnail.jpg?v={new_vid_id}" group-title="{new_group}", {new_name}\n')
                    new_lines.append(f'{new_stream_url}\n')
                    i += 2
                    continue
            new_lines.append(line)
            i += 1

        if not found:
            return jsonify({"status": "error", "message": "Original station not found"}), 404

        with open(m3u_path, 'w') as f:
            f.writelines(new_lines)

        get_available_streams()
        return jsonify({"status": "success", "message": f"Updated {new_name}"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/dlna/devices')
def get_dlna_devices():
    with DEVICES_LOCK:
        return jsonify(DLNA_DEVICES)

@app.route('/api/dlna/refresh', methods=['POST'])
def refresh_dlna_devices():
    discover_dlna_devices()
    with DEVICES_LOCK:
        return jsonify(DLNA_DEVICES)

@app.route('/api/dlna/cast', methods=['POST'])
def cast_to_dlna():
    if not upnpclient:
        return jsonify({"success": False, "message": "upnpclient not installed"}), 500

    data = request.json
    udn = data.get('udn')
    manual_location = data.get('manual_location', '').strip()
    video_id = data.get('video_id')
    
    if not (udn or manual_location) or not video_id:
        return jsonify({"success": False, "message": "Missing device UDN/IP or video ID"}), 400

    # Construct the absolute stream URL
    server_ip = get_server_ip()
    stream_url = f"http://{server_ip}:5000/stream.mp3?v={video_id}"
    
    def perform_cast(device, url, vid, name):
        try:
            print(f"[{vid}] Background cast starting for {device.friendly_name}...", flush=True)
            av_transport = next((s for s in device.services if "AVTransport" in s.service_id), None)
            if not av_transport:
                print(f"[{vid}] Error: AVTransport not found on {device.friendly_name}", flush=True)
                return
            
            # Generate DIDL-Lite Metadata
            escaped_name = html.escape(name)
            metadata = f"""<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">
                <item id="1" parentID="0" restricted="1">
                    <dc:title>{escaped_name}</dc:title>
                    <upnp:class>object.item.audioItem.musicTrack</upnp:class>
                    <dc:creator>Live2Audio</dc:creator>
                    <upnp:artist>Live2Audio</upnp:artist>
                    <res protocolInfo="http-get:*:audio/mpeg:*">{url}</res>
                </item>
            </DIDL-Lite>"""

            # 1. Stop if needed
            try:
                print(f"[{vid}] Sending Stop to {device.friendly_name}...", flush=True)
                av_transport.Stop(InstanceID=0)
            except Exception as e:
                print(f"[{vid}] Stop (optional) failed: {e}", flush=True)
            
            # 2. Set URI with Metadata
            print(f"[{vid}] Setting URI to {url} with metadata...", flush=True)
            av_transport.SetAVTransportURI(
                InstanceID=0,
                CurrentURI=url,
                CurrentURIMetaData=metadata
            )
            
            # 3. Play
            print(f"[{vid}] Sending Play command...", flush=True)
            av_transport.Play(InstanceID=0, Speed='1')
            print(f"[{vid}] Cast successful on {device.friendly_name}", flush=True)
        except Exception as e:
            print(f"[{vid}] Background cast failed: {e}", flush=True)
            with LOG_LOCK:
                ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - Cast Error: {str(e)[:50]}")

    try:
        target_device = None
        
        if udn:
            print(f"[{video_id}] Casting to discovered device {udn}", flush=True)
            # Find the location from our cache first to avoid re-discovery
            location = None
            cached_name = "DLNA Device"
            with DEVICES_LOCK:
                for d in DLNA_DEVICES:
                    if d['udn'] == udn:
                        location = d['location']
                        cached_name = d.get('name', 'DLNA Device')
                        break
            
            if location:
                try:
                    target_device = upnpclient.Device(location)
                except Exception as e:
                    print(f"Failed to connect to cached location {location}: {e}", flush=True)
            
            # Fallback to discovery if cache fails or UDN not found
            if not target_device:
                devices = upnpclient.discover()
                target_device = next((d for d in devices if d.udn == udn), None)
        elif manual_location:
            print(f"[{video_id}] Casting to manual location {manual_location}", flush=True)
            if manual_location.startswith('http'):
                try:
                    target_device = upnpclient.Device(manual_location)
                except Exception as e:
                    print(f"Failed to load manual XML {manual_location}: {e}", flush=True)
            else:
                # Optimized IP probing
                ports = [8080, 49152, 49153, 5000, 80]
                for port in ports:
                    url = f"http://{manual_location}:{port}/description.xml"
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(0.3) # Faster timeout for probe
                            if s.connect_ex((manual_location, port)) == 0:
                                try:
                                    target_device = upnpclient.Device(url)
                                    if target_device: break
                                except: pass
                    except: continue
                
                if not target_device:
                    try:
                        url = f"http://{manual_location}:80/device.xml"
                        target_device = upnpclient.Device(url)
                    except: pass
        
        if not target_device:
            # If we were casting via IP, at least we tried. 
            # Return 404 but try to be helpful
            return jsonify({"success": False, "message": f"Device at {manual_location or udn} not found or unreachable"}), 404
            
        # Get friendly name safely
        try:
            device_name = getattr(target_device, 'friendly_name', 'DLNA Device')
        except:
            device_name = 'DLNA Device'

        station_name = VIDEO_ID_MAP.get(video_id, "Unknown Station")

        # Start the actual UPnP command sequence in a background thread
        threading.Thread(target=perform_cast, args=(target_device, stream_url, video_id, station_name), daemon=True).start()
        
        return jsonify({
            "success": True, 
            "message": f"Cast initiated to {device_name}", 
            "device_name": device_name,
            "udn": getattr(target_device, 'udn', manual_location)
        })
    except Exception as e:
        print(f"Casting failed: {e}", flush=True)
        return jsonify({"success": False, "message": str(e)}), 500

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

@app.route('/api/dlna/stop', methods=['POST'])
def stop_dlna():
    if not upnpclient:
        return jsonify({"success": False, "message": "upnpclient not installed"}), 500

    data = request.json
    udn = data.get('udn')
    manual_location = data.get('manual_location', '').strip()
    
    if not (udn or manual_location):
        return jsonify({"success": False, "message": "Missing device UDN/IP"}), 400

    try:
        target_device = None
        if udn:
            devices = upnpclient.discover()
            target_device = next((d for d in devices if d.udn == udn), None)
        elif manual_location:
            if manual_location.startswith('http'):
                target_device = upnpclient.Device(manual_location)
            else:
                ports = [8080, 49152, 49153, 5000, 80]
                for port in ports:
                    try:
                        url = f"http://{manual_location}:{port}/description.xml"
                        target_device = upnpclient.Device(url)
                        if target_device: break
                    except: continue
        
        if not target_device:
            return jsonify({"success": False, "message": "Device not found"}), 404
            
        av_transport = next((s for s in target_device.services if "AVTransport" in s.service_id), None)
        if av_transport:
            av_transport.Stop(InstanceID=0)
            return jsonify({"success": True, "message": "Stopped DLNA playback"})
        return jsonify({"success": False, "message": "AVTransport not found"}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

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
        <link rel="icon" id="dynamic-favicon" href="/favicon_base.png">
        <style>
            :root {
                --primary: #94a3b8;
                --bg: #020617;
                --card: #0f172a;
                --text: #f1f5f9;
                --border: rgba(255, 255, 255, 0.08);
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
                max-width: 1000px;
                width: 100%;
                background: var(--card);
                border: 1px solid var(--border);
                border-radius: 20px;
                padding: 30px;
                box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
            }
            header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                flex-wrap: wrap;
                gap: 20px;
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }
            .header-left {
                display: flex;
                align-items: center;
                gap: 15px;
            }
            .header-center {
                display: flex;
                align-items: center;
                gap: 20px;
                background: rgba(0, 0, 0, 0.2);
                padding: 8px 20px;
                border-radius: 100px;
                border: 1px solid var(--border);
            }
            .header-right {
                display: flex;
                align-items: center;
                gap: 10px;
            }
            .refresh-btn {
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
                background: var(--primary);
                color: white;
                border: none;
            }
            .add-btn:hover {
                filter: brightness(1.2);
            }
            .error-btn {
                background: rgba(239, 68, 68, 0.1);
                border: 1px solid rgba(239, 68, 68, 0.2);
                color: #ef4444;
            }
            .error-btn:hover {
                background: rgba(239, 68, 68, 0.2);
                color: #fca5a5;
            }
            .error-list-modal {
                max-height: 300px;
                overflow-y: auto;
                background: rgba(0, 0, 0, 0.2);
                border-radius: 12px;
                padding: 15px;
                font-family: monospace;
                font-size: 0.8rem;
                color: #fca5a5;
                margin-top: 15px;
            }
            .error-list-modal div {
                padding: 4px 0;
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            }
            h1 {
                font-size: 1.5rem;
                margin: 0;
                color: var(--primary);
                white-space: nowrap;
            }
            .mini-stat {
                display: flex;
                flex-direction: column;
                align-items: center;
                min-width: 80px;
            }
            .mini-stat-value {
                font-size: 0.9rem;
                font-weight: 600;
                color: var(--text);
            }
            .mini-stat-label {
                font-size: 0.65rem;
                color: #64748b;
                text-transform: uppercase;
                letter-spacing: 0.05em;
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
                display: none;
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

            /* Playback Bar Styles */
            .playback-bar {
                position: fixed;
                bottom: 24px;
                left: 50%;
                transform: translateX(-50%);
                width: calc(100% - 48px);
                max-width: 500px;
                background: rgba(30, 41, 59, 0.7);
                backdrop-filter: blur(12px);
                -webkit-backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 16px;
                padding: 12px 16px;
                display: none;
                align-items: center;
                justify-content: space-between;
                z-index: 1000;
                box-shadow: 0 10px 30px rgba(0,0,0,0.4);
                animation: slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1);
            }
            @keyframes slideUp {
                from { transform: translate(-50%, 100%); opacity: 0; }
                to { transform: translate(-50%, 0); opacity: 1; }
            }
            .playback-content {
                display: flex;
                align-items: center;
                gap: 12px;
                flex: 1;
            }
            .playback-logo-small {
                width: 36px;
                height: 36px;
                border-radius: 8px;
                object-fit: cover;
            }
            .playback-info-text {
                display: flex;
                flex-direction: column;
            }
            .playback-name {
                font-size: 0.9rem;
                font-weight: 600;
                color: white;
            }
            .playback-status {
                font-size: 0.7rem;
                color: #94a3b8;
            }
            .stop-btn {
                background: rgba(239, 68, 68, 0.2) !important;
                color: #fca5a5 !important;
                border: 1px solid rgba(239, 68, 68, 0.3) !important;
                padding: 6px 14px !important;
                font-size: 0.75rem !important;
                border-radius: 8px;
                cursor: pointer;
                transition: all 0.2s;
            }
            .stop-btn:hover {
                background: rgba(239, 68, 68, 0.3) !important;
                color: white !important;
            }

            /* Volume Slider Styles */
            .volume-container {
                display: flex;
                align-items: center;
                gap: 8px;
                margin: 0 16px;
                min-width: 120px;
            }
            .volume-slider {
                -webkit-appearance: none;
                width: 100%;
                height: 4px;
                border-radius: 2px;
                background: rgba(255, 255, 255, 0.1);
                outline: none;
            }
            .volume-slider::-webkit-slider-thumb {
                -webkit-appearance: none;
                appearance: none;
                width: 12px;
                height: 12px;
                border-radius: 50%;
                background: var(--primary);
                cursor: pointer;
                border: none;
            }
            .volume-slider::-moz-range-thumb {
                width: 12px;
                height: 12px;
                border-radius: 50%;
                background: var(--primary);
                cursor: pointer;
                border: none;
            }
            .volume-icon {
                color: #94a3b8;
                font-size: 0.8rem;
            }
            /* DLNA Modal List */
            .device-list {
                display: flex;
                flex-direction: column;
                gap: 10px;
                margin-top: 15px;
                max-height: 250px;
                overflow-y: auto;
            }
            .device-item {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid var(--border);
                padding: 12px;
                border-radius: 12px;
                display: flex;
                justify-content: space-between;
                align-items: center;
                cursor: pointer;
                transition: all 0.2s;
            }
            .device-item:hover {
                background: rgba(255, 255, 255, 0.1);
                border-color: var(--primary);
            }
            .device-info {
                display: flex;
                flex-direction: column;
            }
            .device-name {
                font-size: 0.9rem;
                font-weight: 500;
                color: white;
            }
            .device-location {
                font-size: 0.7rem;
                color: #64748b;
            }
            .cast-icon {
                color: var(--primary);
                font-size: 1.2rem;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div class="header-left">
                    <h1>Live2Audio</h1>
                    <span class="badge badge-success" id="system-status-badge">● ONLINE</span>
                </div>
                
                <div class="header-center">
                    <div class="mini-stat">
                        <span class="mini-stat-value" id="uptime-val">{{ uptime }}</span>
                        <span class="mini-stat-label">Uptime</span>
                    </div>
                    <div class="mini-stat">
                        <span class="mini-stat-value" id="live-count-val">{{ live_count }}</span>
                        <span class="mini-stat-label">Live</span>
                    </div>
                </div>

                <div class="header-right">
                    <button class="refresh-btn error-btn" onclick="openErrorModal()" id="errorBtn" style="display: {% if errors %}flex{% else %}none{% endif %};">⚠ Errors</button>
                    <button class="refresh-btn add-btn" onclick="openModal()">+ Add Station</button>
                    <button class="refresh-btn" onclick="refreshM3U()" id="refreshBtn">↻ Refresh</button>
                </div>
            </header>

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
                                <button class="action-link" id="play-btn-{{ stream.id }}" onclick="togglePlayer('{{ stream.id }}')">▶ Play</button>
                                <button class="action-link" onclick="openCastModal('{{ stream.id }}', '{{ stream.name }}')">📺 Cast</button>
                                <button class="action-link" onclick="openEditModal('{{ stream.id }}', '{{ stream.name }}', '{{ stream.url }}', '{{ stream.group }}', '{{ stream.tvg_id }}')">✎ Edit</button>
                                <a href="https://www.youtube.com/watch?v={{ stream.id }}" class="action-link" target="_blank">↗ YT</a>
                            </div>
                        </div>
                        {% endfor %}
                    </div>
                    {% else %}
                    <p style="color: #64748b; font-style: italic;">No stations found in youtube.m3u.</p>
                    {% endif %}
                </div>
            </section>

        </div>

        <!-- Playback Bar -->
        <div id="playback-bar" class="playback-bar">
            <audio id="main-audio" preload="none"></audio>
            <div class="playback-content">
                <img id="playback-logo" src="" class="playback-logo-small" alt="" onerror="this.style.background='#334155'">
                <div class="playback-info-text">
                    <span id="playback-station-name" class="playback-name">Station Name</span>
                    <span id="playback-status" class="playback-status">Currently Playing</span>
                </div>
            </div>
            <div class="volume-container">
                <span class="volume-icon">Vol</span>
                <input type="range" class="volume-slider" id="volume-control" min="0" max="1" step="0.01" value="0.5">
            </div>
            <button class="stop-btn" onclick="stopAllAudio()">Stop</button>
        </div>

        <!-- Station Modal -->
        <div class="modal-overlay" id="modalOverlay">
            <div class="modal">
                <h3 id="modalTitle">Add New Station</h3>
                <input type="hidden" id="oldStationUrl">
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
                    <button class="btn btn-primary" onclick="handleStationSubmit()" id="submitBtn">Save Station</button>
                </div>
            </div>
        </div>

        <!-- Error Modal -->
        <div class="modal-overlay" id="errorModalOverlay">
            <div class="modal">
                <h3 style="color: #ef4444;">Session Error Log</h3>
                <div id="error-log-container-modal" class="error-list-modal">
                    <!-- Errors will be populated here -->
                </div>
                <div class="modal-actions" style="margin-top: 20px;">
                    <button class="btn btn-secondary" onclick="closeErrorModal()">Close</button>
                </div>
            </div>
        </div>

        <!-- Cast Modal -->
        <div class="modal-overlay" id="castModalOverlay">
            <div class="modal">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                    <h3 id="castModalTitle" style="margin: 0;">Cast to Device</h3>
                    <button class="refresh-btn" onclick="refreshDevices()" id="refreshDevicesBtn">↻ Refresh</button>
                </div>
                <p id="castStationName" style="font-size: 0.9rem; color: #94a3b8; margin-bottom: 15px;"></p>
                
                <div class="form-group" style="margin-bottom: 15px;">
                    <label>Manual Device entry (IP or Location URL)</label>
                    <div style="display: flex; gap: 8px;">
                        <input type="text" id="manualDeviceIp" placeholder="192.168.1.100" style="flex: 1;">
                        <button class="btn btn-primary" onclick="castToManualIp()" style="padding: 8px 12px; font-size: 0.8rem;">Cast to IP</button>
                    </div>
                </div>

                <div id="device-list" class="device-list">
                    <!-- Devices will be populated here -->
                    <p style="color: #64748b; font-style: italic; text-align: center;">Searching for devices...</p>
                </div>
                <div class="modal-actions" style="margin-top: 25px;">
                    <button class="btn btn-secondary" onclick="closeCastModal()">Cancel</button>
                </div>
            </div>
        </div>

        <script>
            let currentCastStreamId = null;

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
                        list.innerHTML = '<p style="color: #64748b; font-style: italic; text-align: center;">No DLNA devices found. Try refreshing.</p>';
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
                    list.innerHTML = '<p style="color: #ef4444; font-style: italic; text-align: center;">Failed to load devices.</p>';
                }
            }

            async function refreshDevices() {
                const btn = document.getElementById('refreshDevicesBtn');
                const list = document.getElementById('device-list');
                const originalText = btn.innerText;
                
                btn.innerText = '⌛ Scanning...';
                btn.disabled = true;
                list.innerHTML = '<p style="color: #64748b; font-style: italic; text-align: center;">Scanning for devices...</p>';
                
                try {
                    const response = await fetch('/api/dlna/refresh', { method: 'POST' });
                    const devices = await response.json();
                    
                    if (devices.length === 0) {
                        list.innerHTML = '<p style="color: #64748b; font-style: italic; text-align: center;">No DLNA devices found.</p>';
                    } else {
                        loadDevices();
                    }
                } catch (e) {
                    list.innerHTML = '<p style="color: #ef4444; font-style: italic; text-align: center;">Scan failed.</p>';
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
                        document.querySelectorAll('[id^="play-btn-"]').forEach(btn => {
                            btn.innerText = btn.id === `play-btn-${currentCastStreamId}` ? "⏹ Stop" : "▶ Play";
                        });

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

            async function stopAllAudio() {
                const audio = document.getElementById('main-audio');
                audio.pause();
                audio.src = "";
                audio.load();
                
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

                document.getElementById('playback-bar').style.display = 'none';
                sessionStorage.removeItem('isPlaying');
                sessionStorage.removeItem('isCasting');
                sessionStorage.removeItem('castDeviceTarget');
                
                // Reset all play buttons
                document.querySelectorAll('[id^="play-btn-"]').forEach(btn => {
                    btn.innerText = "▶ Play";
                });

                // Clear session storage
                sessionStorage.removeItem('isPlaying');
                sessionStorage.removeItem('isCasting');
                sessionStorage.removeItem('castDeviceTarget');
                sessionStorage.removeItem('stationName');
                sessionStorage.removeItem('stationLogo');
                sessionStorage.removeItem('castingTo');

                // Update live count immediately
                updateDashboard();
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
                document.querySelectorAll('[id^="play-btn-"]').forEach(btn => {
                    btn.innerText = btn.id === `play-btn-${id}` ? "⏹ Stop" : "▶ Play";
                });

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
                setTimeout(() => {
                    document.querySelectorAll('[id^="play-btn-"]').forEach(btn => {
                        if (btn.id === `play-btn-${isPlaying}`) {
                            btn.innerText = "⏹ Stop";
                        }
                    });
                }, 500);
            }

            // Global volume control listener
            document.addEventListener('DOMContentLoaded', () => {
                restoreUIState();
                const volControl = document.getElementById('volume-control');
                volControl.addEventListener('input', (e) => {
                    const vol = e.target.value;
                    document.getElementById('main-audio').volume = vol;
                    localStorage.setItem('userVolume', vol);
                });

                // Load saved volume
                const savedVol = localStorage.getItem('userVolume');
                if (savedVol !== null) {
                    volControl.value = savedVol;
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
                document.getElementById('modalOverlay').style.display = 'flex';
            }

            function openEditModal(id, name, url, group, tvg_id) {
                document.getElementById('modalTitle').innerText = "Edit Station";
                document.getElementById('oldStationUrl').value = url;
                document.getElementById('stationUrl').value = url;
                document.getElementById('stationName').value = name;
                document.getElementById('stationId').value = tvg_id;
                document.getElementById('stationGroup').value = group;
                document.getElementById('modalOverlay').style.display = 'flex';
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
                    
                    const container = document.getElementById('stream-list-container');
                    if (data.streams.length > 0) {
                        const playingId = sessionStorage.getItem('isPlaying');
                        let html = '<div class="stream-list">';
                        data.streams.forEach(stream => {
                            const isThisPlaying = stream.id === playingId;
                            html += `
                            <div class="stream-item">
                                <img src="${stream.logo || ''}" class="stream-logo" alt="Logo" onerror="this.style.background='#334155'">
                                <div class="stream-info">
                                    <div style="display: flex; align-items: center;">
                                        ${stream.listeners > 0 ? '<span class="pulse"></span>' : ''}
                                        <span class="station-name-text" style="font-weight: 500;">${stream.name}</span>
                                    </div>
                                    <span style="font-size: 0.7rem; color: #64748b;">ID: ${stream.id} ${stream.listeners > 0 ? '• ' + stream.listeners + ' listening' : ''}</span>
                                </div>
                                <div class="stream-actions">
                                    <button class="action-link" id="play-btn-${stream.id}" onclick="togglePlayer('${stream.id}')">${isThisPlaying ? '⏹ Stop' : '▶ Play'}</button>
                                    <button class="action-link" onclick="openCastModal('${stream.id}', '${stream.name}')">📺 Cast</button>
                                    <button class="action-link" onclick="openEditModal('${stream.id}', '${stream.name}', '${stream.url}', '${stream.group}', '${stream.tvg_id}')">✎ Edit</button>
                                    <a href="https://www.youtube.com/watch?v=${stream.id}" class="action-link" target="_blank">↗ YT</a>
                                </div>
                            </div>`;
                        });
                        html += '</div>';
                        container.innerHTML = html;
                    } else {
                        container.innerHTML = '<p style="color: #64748b; font-style: italic;">No stations found in youtube.m3u.</p>';
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
                        errorContainer.innerHTML = '<p style="color: #64748b; font-style: italic; text-align: center;">No errors reported in this session.</p>';
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
                const id = document.getElementById('stationId').value;
                const group = document.getElementById('stationGroup').value;
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
    request_id = f"{video_id}_{int(time.time())}_{request.remote_addr[-4:]}"
    print(f"--- Stream Request Start: {request_id} ---", flush=True)
    
    def generate():
        # Increment listener count
        with STREAMS_LOCK:
            ACTIVE_STREAMS[video_id] = ACTIVE_STREAMS.get(video_id, 0) + 1
            current_listeners = ACTIVE_STREAMS[video_id]
        print(f"[{request_id}] Listener IN (Total: {current_listeners})", flush=True)
            
        ffmpeg_process = None 
        try:
            # 1. Get the direct audio URL from YouTube
            print(f"[{request_id}] Fetching YouTube URL...", flush=True)
            url_command = ['yt-dlp', '-g', '-f', 'ba/b', youtube_url]
            url_proc = subprocess.run(url_command, capture_output=True, text=True)
            
            if url_proc.returncode != 0:
                print(f"[{request_id}] yt-dlp error: {url_proc.stderr}", flush=True)
                with LOG_LOCK:
                    ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - yt-dlp Error: {video_id}")
                return

            direct_url = url_proc.stdout.strip()

            # 2. Stream using FFmpeg
            ffmpeg_command = [
                'ffmpeg', '-i', direct_url, '-f', 'mp3', '-acodec', 'libmp3lame',
                '-ab', '128k', '-flush_packets', '1', '-fflags', 'nobuffer',
                '-loglevel', 'error', 'pipe:1'
            ]
            
            ffmpeg_process = subprocess.Popen(
                ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
            )
            
            while True:
                chunk = ffmpeg_process.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
                
        except GeneratorExit:
            print(f"[{request_id}] Browser disconnected.", flush=True)
        except Exception as e:
            print(f"[{request_id}] Error: {e}", flush=True)
            with LOG_LOCK:
                ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - Stream Error: {str(e)[:50]}")
        finally:
            # Decrement listener count
            with STREAMS_LOCK:
                ACTIVE_STREAMS[video_id] = max(0, ACTIVE_STREAMS.get(video_id, 0) - 1)
                current_listeners = ACTIVE_STREAMS[video_id]
            print(f"[{request_id}] Listener OUT (Total: {current_listeners})", flush=True)
            if ffmpeg_process: 
                ffmpeg_process.terminate()
                try:
                    ffmpeg_process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    ffmpeg_process.kill()
                except:
                    ffmpeg_process.kill()

    return Response(
        generate(), 
        mimetype="audio/mpeg",
        headers={
            'Cache-Control': 'no-cache',
            'Accept-Ranges': 'none',
            'Access-Control-Allow-Origin': '*',
            'icy-name': station_name,
            'icy-description': 'Live2Audio YouTube Stream',
            'icy-url': build_youtube_url(video_id),
            'icy-genre': 'YouTube Radio'
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

@app.route('/favicon_base.png')
def get_favicon_base():
    from flask import send_file
    if os.path.exists('favicon_base.png'):
        return send_file('favicon_base.png')
    return "Not Found", 404

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'message': 'pong'})

if __name__ == '__main__':
    # Pre-populate map when running locally
    get_available_streams()
    start_discovery_thread()
    app.run(host='0.0.0.0', port=5000)
else:
    # Pre-populate map when running under Gunicorn
    get_available_streams()
    start_discovery_thread()
