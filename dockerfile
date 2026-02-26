FROM python:3.11-alpine

# Combine package installation and pip cleanup
# nodejs is used as the JavaScript runtime for yt-dlp
RUN apk add --no-cache ffmpeg curl nodejs && \
    pip install --no-cache-dir flask yt-dlp gunicorn

WORKDIR /app
COPY stream_manager.py .

EXPOSE 5000

# Use gunicorn with threads for multi-user concurrency
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--threads", "4", "stream_manager:app"]
