FROM python:3.11-alpine

WORKDIR /app

# Combine installation of system dependencies and python requirements
# We install build tools to compile lxml, then remove them to keep image size small
COPY requirements.txt .
RUN apk add --no-cache ffmpeg curl nodejs \
    libxml2 libxslt build-base libxml2-dev libxslt-dev && \
    pip install --no-cache-dir -r requirements.txt && \
    apk del build-base libxml2-dev libxslt-dev

# Copy the rest of the application files
COPY . .

EXPOSE 5000

# Use gunicorn with threads for multi-user concurrency
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--threads", "4", "stream_manager:app"]
