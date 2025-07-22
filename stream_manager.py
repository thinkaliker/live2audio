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

def start_stream(youtube_url):
    global stream_process

    with lock:
        # Stop existing stream if running
        stop_stream()

        # Start new streaming process
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
            stream_process.terminate()
            stream_process.wait()
            stream_process = None

@app.route('/ping', methods=['GET'])
def return_pong():
    return "pong", 400


@app.route('/control', methods=['GET'])
def control_stream():
    action = request.args.get('action')
    stream_url = request.args.get('stream')

    if action not in ['start', 'stop']:
        return jsonify({'status': 'error', 'message': 'Invalid action.'}), 400

    if action == 'start':
        if not stream_url:
            return jsonify({'status': 'error', 'message': 'Missing stream URL.'}), 400
        threading.Thread(target=start_stream, args=(stream_url,)).start()
        return jsonify({'status': 'ok', 'message': f'Started streaming: {stream_url}'})

    elif action == 'stop':
        threading.Thread(target=stop_stream).start()
        return jsonify({'status': 'ok', 'message': 'Stopped current stream.'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
