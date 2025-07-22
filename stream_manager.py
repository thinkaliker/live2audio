from flask import Flask, request, jsonify
import subprocess
import threading
import signal
import os

app = Flask(__name__)

# Global variables to manage the streaming process
stream_process = None
lock = threading.Lock()

# Load Icecast URL from environment variable
ICECAST_URL = os.environ.get('ICECAST_URL')

if not ICECAST_URL:
    raise ValueError("Missing required environment variable: ICECAST_URL")

def build_youtube_url(video_id):
    return f"https://www.youtube.com/watch?v={video_id}"

def start_stream(video_id, stream_type):
    global stream_process

    with lock:
        stop_stream()

        youtube_url = build_youtube_url(video_id)
        print(f"Starting {stream_type} stream: {youtube_url}")

        command = [
            'bash', '-c',
            f'yt-dlp -f bestaudio -o - "{youtube_url}" | ffmpeg -i pipe:0 '
            f'-vn -acodec libmp3lame -ab 128k -content_type audio/mpeg '
            f'-f mp3 "{ICECAST_URL}"'
        ]

        stream_process = subprocess.Popen(
            command,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN)
        )

def stop_stream():
    global stream_process

    with lock:
        if stream_process and stream_process.poll() is None:
            print("Stopping current stream.")
            stream_process.terminate()
            stream_process.wait()
            stream_process = None

@app.route('/control', methods=['GET'])
def control_stream():
    action = request.args.get('action')
    video_id = request.args.get('stream')
    stream_type = request.args.get('type', 'video')

    if action not in ['start', 'stop']:
        return jsonify({'status': 'error', 'message': 'Invalid action.'}), 400

    if action == 'start':
        if not video_id:
            return jsonify({'status': 'error', 'message': 'Missing YouTube video ID.'}), 400

        threading.Thread(target=start_stream, args=(video_id, stream_type)).start()
        return jsonify({'status': 'ok', 'message': f'Started {stream_type} stream: {video_id}'})

    elif action == 'stop':
        threading.Thread(target=stop_stream).start()
        return jsonify({'status': 'ok', 'message': 'Stopped current stream.'})

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({'status': 'ok', 'message': 'pong'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
