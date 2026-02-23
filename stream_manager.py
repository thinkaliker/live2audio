import subprocess
from flask import Flask, Response, request, jsonify

app = Flask(__name__)

def build_youtube_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"

@app.route('/stream.mp3', methods=['GET', 'HEAD'])
def stream_audio():
    video_id = request.args.get('v')
    if not video_id:
        return "Missing video ID", 400
    
    # Handle HEAD requests for player probing without starting the stream
    if request.method == 'HEAD':
        return Response(mimetype="audio/mpeg")
    
    print(f"--- New stream request: {video_id} ---")
    
    def generate():
        print(f"Starting yt-dlp for {video_id}...")
        # Use yt-dlp to get the audio URL and ffmpeg to transcode it to mp3 on the fly
        command = [
            'yt-dlp',
            '-f', 'bestaudio',
            '--quiet',
            '--no-warnings',
            '--no-playlist',
            '-o', '-',
            youtube_url
        ]
        
        # Optimize ffmpeg for streaming:
        # -flush_packets 1: send data immediately
        # -fflags nobuffer: reduce internal buffering
        # -loglevel error: reduce noise
        ffmpeg_command = [
            'ffmpeg',
            '-i', 'pipe:0',
            '-f', 'mp3',
            '-acodec', 'libmp3lame',
            '-ab', '128k',
            '-flush_packets', '1',
            '-fflags', 'nobuffer',
            '-loglevel', 'error',
            'pipe:1'
        ]
        
        # Use bufsize=0 to minimize pipe latency
        ytdlp_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=ytdlp_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        
        print("Subprocesses started, streaming beginning...")
        try:
            while True:
                chunk = ffmpeg_process.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        except Exception as e:
            print(f"Streaming error for {video_id}: {e}")
        finally:
            print(f"Cleaning up stream {video_id}...")
            ffmpeg_process.terminate()
            ytdlp_process.terminate()
            try:
                ffmpeg_process.wait(timeout=2)
                ytdlp_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                ffmpeg_process.kill()
                ytdlp_process.kill()

    return Response(
        generate(), 
        mimetype="audio/mpeg",
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
            'Accept-Ranges': 'none',
            'Access-Control-Allow-Origin': '*'
        }
    )

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'message': 'pong'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
