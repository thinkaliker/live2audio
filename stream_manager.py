import subprocess
from flask import Flask, Response, request, jsonify

app = Flask(__name__)

def build_youtube_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"

@app.route('/stream.mp3')
def stream_audio():
    video_id = request.args.get('v')
    if not video_id:
        return "Missing video ID", 400
    
    # Handle HEAD requests for player probing without starting the stream
    if request.method == 'HEAD':
        return Response(mimetype="audio/mpeg")
    
    youtube_url = build_youtube_url(video_id)
    
    def generate():
        # Use yt-dlp to get the audio URL and ffmpeg to transcode it to mp3 on the fly
        command = [
            'yt-dlp',
            '-f', 'bestaudio',
            '--quiet',
            '--no-warnings',
            '-o', '-',
            youtube_url
        ]
        
        # Optimize ffmpeg for streaming:
        # -re: read input at native frame rate (important for live streams)
        # -loglevel error: reduce noise
        ffmpeg_command = [
            'ffmpeg',
            '-re',
            '-i', 'pipe:0',
            '-f', 'mp3',
            '-acodec', 'libmp3lame',
            '-ab', '128k',
            '-loglevel', 'error',
            'pipe:1'
        ]
        
        # Use DEVNULL for stderr to avoid pipe-fill deadlock
        ytdlp_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=ytdlp_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        
        try:
            while True:
                chunk = ffmpeg_process.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        except Exception as e:
            print(f"Streaming error: {e}")
        finally:
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
