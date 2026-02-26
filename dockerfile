FROM python:3.11-alpine

# Combine package installation and pip cleanup
# nodejs is used as the JavaScript runtime for yt-dlp
RUN apk add --no-cache ffmpeg curl nodejs && \
    pip install --no-cache-dir flask yt-dlp

WORKDIR /app
COPY stream_manager.py .

EXPOSE 5000

CMD ["python", "stream_manager.py"]
