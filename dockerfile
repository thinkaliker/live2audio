# Get the Deno binary from official image
FROM denoland/deno:alpine AS deno-bin

FROM python:3.11-alpine

# Copy Deno binary
COPY --from=deno-bin /usr/bin/deno /usr/bin/deno

# Combine package installation and pip cleanup
RUN apk add --no-cache ffmpeg curl && \
    pip install --no-cache-dir flask yt-dlp

WORKDIR /app
COPY stream_manager.py .

EXPOSE 5000

CMD ["python", "stream_manager.py"]
