#!/bin/bash

# Start Icecast in the background
icecast2 -c /etc/icecast2/icecast.xml &

# Start the Python Flask stream manager
python /app/stream_manager.py
