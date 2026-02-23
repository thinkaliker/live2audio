# live2audio

A simple proxy to stream YouTube audio directly into Jellyfin (or any media player) without needing an intermediate Icecast server.

## Why this is better
The previous Icecast-based method required a persistent connection and a running Icecast instance. This new version is **on-demand**: it only starts downloading and transcoding the YouTube audio when you actually click "Play" in your player.

## Setup

1. **Start the service**:
   ```bash
   docker-compose up -d
   ```

2. **Add to Jellyfin**:
   - Go to **Dashboard** -> **Live TV**.
   - Click the `+` next to **Tuner Devices**.
   - Select **M3U Tuner** as the type.
   - For the **File or URL**, create a local file (e.g., `youtube.m3u`) or use a Data URI.

### M3U Example
Create a file called `youtube.m3u` and add your favorite streams:

```text
#EXTM3U
#EXTINF:-1, Lofi Girl - Radio
http://YOUR_SERVER_IP:5000/stream.mp3?v=jfKfPfyJRdk
#EXTINF:-1, Synthwave Radio
http://YOUR_SERVER_IP:5000/stream.mp3?v=4xDzrJKXOOY
```

Replace `YOUR_SERVER_IP` with the IP of the machine running this docker container, and `v=` with any YouTube Video ID.

## How it works
- **Flask**: Receives the request from Jellyfin.
- **yt-dlp**: Fetches the best audio stream from YouTube.
- **ffmpeg**: Transcodes the stream to MP3 in real-time.
- **Direct Streaming**: The audio data is piped directly to the HTTP response, meaning zero disk space is used.
