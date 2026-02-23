FROM python:3.11-alpine

# Combine package installation and pip cleanup to minimize layers
RUN apk add --no-cache ffmpeg curl && \
    pip install --no-cache-dir flask yt-dlp

WORKDIR /app
COPY stream_manager.py .

EXPOSE 5000

CMD ["python", "stream_manager.py"]
