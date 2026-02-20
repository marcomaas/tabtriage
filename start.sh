#!/bin/bash
# TabTriage Backend Starter
cd "$(dirname "$0")/backend"

# Strip CLAUDE* env vars to avoid nested session errors in summarizer
unset ${!CLAUDE*}

exec /usr/local/bin/python3.11 main.py >> /tmp/tabtriage.log 2>&1
