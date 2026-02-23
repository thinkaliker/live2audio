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
    
    youtube_url = build_youtube_url(video_id)
    
    def generate():
        # Use yt-dlp to get the audio URL and ffmpeg to transcode it to mp3 on the fly
        # This ensures compatibility with most players
        command = [
            'yt-dlp',
            '-f', 'bestaudio',
            '--quiet',
            '--no-warnings',
            '-o', '-',
            youtube_url
        ]
        
        # We pipe yt-dlp to ffmpeg to ensure we are sending a consistent mp3 stream
        ffmpeg_command = [
            'ffmpeg',
            '-i', 'pipe:0',
            '-f', 'mp3',
            '-acodec', 'libmp3lame',
            '-ab', '128k',
            'pipe:1'
        ]
        
        ytdlp_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ffmpeg_process = subprocess.Popen(ffmpeg_command, stdin=ytdlp_process.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        try:
            while True:
                chunk = ffmpeg_process.stdout.read(4096)
                if not chunk:
                    # Check if either process failed
                    if ffmpeg_process.poll() is not None and ffmpeg_process.returncode != 0:
                        stderr = ffmpeg_process.stderr.read().decode()
                        print(f"FFmpeg error: {stderr}")
                    if ytdlp_process.poll() is not None and ytdlp_process.returncode != 0:
                        stderr = ytdlp_process.stderr.read().decode()
                        print(f"yt-dlp error: {stderr}")
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

    return Response(generate(), mimetype="audio/mpeg")

@app.route('/ping')
def ping():
    return jsonify({'status': 'ok', 'message': 'pong'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
