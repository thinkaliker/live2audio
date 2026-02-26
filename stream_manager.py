import subprocess
from flask import Flask, Response, request, jsonify, redirect

app = Flask(__name__)

def build_youtube_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"

@app.before_request
def log_request():
    print(f"Incoming: {request.method} {request.path}", flush=True)

@app.errorhandler(404)
def page_not_found(e):
    print(f"404 ERROR: {request.path}", flush=True)
    return "Not Found", 404

@app.route('/')
def index():
    return "OK", 200

@app.route('/stream.mp3', methods=['GET', 'HEAD'])
def stream_audio():
    video_id = request.args.get('v')
    if not video_id:
        return "Missing video ID", 400
    
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
