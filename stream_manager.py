import subprocess
import os
import time
import threading
import socket
import html
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, Response, request, jsonify, redirect, render_template
try:
    import upnpclient
except ImportError:
    upnpclient = None

app = Flask(__name__)

# Server metadata
START_TIME = datetime.now()
SERVER_ID = str(int(START_TIME.timestamp()))  # Unique ID for this server instance
ERROR_LOG = deque(maxlen=10)
LOG_LOCK = threading.Lock()
LAST_STREAM = {"name": "None", "time": "Never"}
VIDEO_ID_MAP = {}  # Map video_id to station name
ACTIVE_STREAMS = {}  # Map video_id to count of active listeners
STREAMS_LOCK = threading.Lock()
LAST_GOOD_STREAMS = []  # Cache of last successful parse result

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

PENDING_DOWNLOADS = set()
DOWNLOADS_LOCK = threading.Lock()

STREAM_AVAILABILITY = {}
AVAILABILITY_LOCK = threading.Lock()

def check_stream_availability(video_id):
    youtube_url = build_youtube_url(video_id)
    try:
        result = subprocess.run(
            ['yt-dlp', '-g', '-f', 'ba/b', '--no-playlist', youtube_url],
            capture_output=True, text=True, timeout=30
        )
        status = "available" if result.returncode == 0 else "unavailable"
    except subprocess.TimeoutExpired:
        status = "unavailable"
    except Exception:
        status = "unavailable"
    with AVAILABILITY_LOCK:
        STREAM_AVAILABILITY[video_id] = status
    print(f"[{video_id}] Availability: {status}", flush=True)

def cache_thumbnail(video_id):
    """Download thumbnail to local cache if it doesn't exist."""
    if not video_id or video_id == "Unknown":
        return

    cache_path = os.path.join(CACHE_DIR, f"{video_id}.jpg")
    if os.path.exists(cache_path):
        return

    with DOWNLOADS_LOCK:
        if video_id in PENDING_DOWNLOADS:
            return
        PENDING_DOWNLOADS.add(video_id)

    try:
        print(f"Background caching thumbnail for {video_id}...", flush=True)
        url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        # Run curl with connect timeout and max-time, and fail on 404
        result = subprocess.run(
            ['curl', '-s', '-f', '-L', '--connect-timeout', '5', '--max-time', '10', '-o', cache_path, url],
            capture_output=True
        )
        if result.returncode != 0:
            print(f"Failed to download thumbnail for {video_id}, creating empty placeholder.", flush=True)
            try:
                with open(cache_path, 'w') as placeholder:
                    placeholder.write("")
            except Exception as e:
                print(f"Failed to write placeholder for {video_id}: {e}", flush=True)
    except Exception as e:
        print(f"Error caching thumbnail for {video_id}: {e}", flush=True)
    finally:
        with DOWNLOADS_LOCK:
            PENDING_DOWNLOADS.discard(video_id)


def build_youtube_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"

def get_available_streams():
    global VIDEO_ID_MAP, LAST_GOOD_STREAMS
    streams = []
    temp_map = {}
    m3u_path = "youtube.m3u"

    if not os.path.exists(m3u_path):
        print(f"M3U not found at '{os.path.abspath(m3u_path)}', returning cached streams ({len(LAST_GOOD_STREAMS)})", flush=True)
        return LAST_GOOD_STREAMS

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

                    tvg_id_match = re.search(r'tvg-id="([^"]*)"', info)
                    group_match = re.search(r'group-title="([^"]*)"', info)

                    tvg_id = tvg_id_match.group(1) if tvg_id_match else "Manual"
                    group = group_match.group(1) if group_match else "YouTube Radio"

                    vid_id = "Unknown"
                    if "?v=" in url_line:
                        vid_id = url_line.split("?v=")[1].split("&")[0]
                    elif "youtu.be/" in url_line:
                        vid_id = url_line.split("youtu.be/")[1].split("?")[0]

                    temp_map[vid_id] = name

                    logo = f"/thumbnail.jpg?v={vid_id}"
                    with STREAMS_LOCK:
                        listeners = ACTIVE_STREAMS.get(vid_id, 0)

                    with AVAILABILITY_LOCK:
                        avail_status = STREAM_AVAILABILITY.get(vid_id, "checking")
                        if vid_id not in STREAM_AVAILABILITY and vid_id != "Unknown":
                            STREAM_AVAILABILITY[vid_id] = "checking"
                            try:
                                threading.Thread(target=check_stream_availability, args=(vid_id,), daemon=True).start()
                            except RuntimeError as e:
                                print(f"[{vid_id}] Could not start availability thread: {e}", flush=True)

                    streams.append({
                        "name": name,
                        "url": url_line,
                        "logo": logo,
                        "id": vid_id,
                        "tvg_id": tvg_id,
                        "group": group,
                        "listeners": listeners,
                        "availability": avail_status
                    })

                    if vid_id and vid_id != "Unknown":
                        cache_path = os.path.join(CACHE_DIR, f"{vid_id}.jpg")
                        if not os.path.exists(cache_path):
                            with DOWNLOADS_LOCK:
                                should_start = vid_id not in PENDING_DOWNLOADS
                            if should_start:
                                try:
                                    threading.Thread(target=cache_thumbnail, args=(vid_id,), daemon=True).start()
                                except RuntimeError as e:
                                    print(f"[{vid_id}] Could not start thumbnail thread: {e}", flush=True)

        VIDEO_ID_MAP = temp_map
        if streams:
            LAST_GOOD_STREAMS = streams
    except Exception as e:
        print(f"M3U Parse Error: {e}", flush=True)
        with LOG_LOCK:
            ERROR_LOG.append(f"{datetime.now().strftime('%H:%M:%S')} - M3U Parse Error: {str(e)}")
        if LAST_GOOD_STREAMS:
            print(f"Returning last good streams ({len(LAST_GOOD_STREAMS)} stations)", flush=True)
            return LAST_GOOD_STREAMS
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

@app.route('/reorder_stations', methods=['POST'])
def reorder_stations():
    ordered_ids = request.json.get('order', [])
    m3u_path = "youtube.m3u"
    try:
        with open(m3u_path, 'r') as f:
            lines = f.read().split('\n')

        # Parse existing stations keyed by video_id
        import re
        stations = {}
        i = 0
        while i < len(lines):
            if lines[i].startswith('#EXTINF:'):
                extinf = lines[i]
                url = lines[i + 1].strip() if i + 1 < len(lines) else ''
                vid_id = 'Unknown'
                if '?v=' in url:
                    vid_id = url.split('?v=')[1].split('&')[0]
                elif 'youtu.be/' in url:
                    vid_id = url.split('youtu.be/')[1].split('?')[0]
                if vid_id != 'Unknown':
                    stations[vid_id] = (extinf, url)
                i += 2
            else:
                i += 1

        def get_tvg_id(extinf):
            m = re.search(r'tvg-id="([^"]*)"', extinf)
            return m.group(1) if m else ''

        out = ['#EXTM3U']
        prev_group = None
        for vid_id in ordered_ids:
            if vid_id not in stations:
                continue
            extinf, url = stations[vid_id]
            group = get_tvg_id(extinf)
            if prev_group is not None and group != prev_group:
                out.append('')
            out.append(extinf)
            out.append(url)
            prev_group = group

        with open(m3u_path, 'w') as f:
            f.write('\n'.join(out) + '\n')

        get_available_streams()
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

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

@app.route('/delete_station', methods=['POST'])
def delete_station():
    data = request.json
    url = data.get('url', '').strip()

    if not url:
        return jsonify({"status": "error", "message": "URL is required"}), 400

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
                if next_line == url:
                    found = True
                    i += 2
                    continue
            new_lines.append(line)
            i += 1

        if not found:
            return jsonify({"status": "error", "message": "Station not found"}), 404

        with open(m3u_path, 'w') as f:
            f.writelines(new_lines)

        get_available_streams()
        return jsonify({"status": "success", "message": "Station deleted"})
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
        "server_id": SERVER_ID,
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
    with STREAMS_LOCK:
        live_count = sum(1 for count in ACTIVE_STREAMS.values() if count > 0)
    return render_template(
        'index.html',
        server_id=SERVER_ID,
        uptime=uptime,
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
                    ffmpeg_process.wait() # Reap zombie process
                except:
                    ffmpeg_process.kill()
                    ffmpeg_process.wait()
                    
                if ffmpeg_process.stdout:
                    ffmpeg_process.stdout.close()

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
    app.run(host='0.0.0.0', port=5001)
else:
    # Pre-populate map when running under Gunicorn
    get_available_streams()
    start_discovery_thread()
