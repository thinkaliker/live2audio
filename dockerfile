FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y ffmpeg curl python3-pip && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY stream_manager.py .

RUN pip install --no-cache-dir flask yt-dlp

EXPOSE 5000

CMD ["python", "stream_manager.py"]
