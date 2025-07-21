FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y ffmpeg yt-dlp icecast2 && \
    rm -rf /var/lib/apt/lists/*

COPY icecast.xml /etc/icecast2/icecast.xml

WORKDIR /app
COPY stream_manager.py .

RUN pip install flask

EXPOSE 8000 5000

ENV ICECAST_URL="icecast://source:hackme@localhost:8000/stream"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
