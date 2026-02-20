#!/bin/bash
# TabTriage Backend Starter
cd "$(dirname "$0")/backend"
exec /usr/local/bin/python3.11 main.py >> /tmp/tabtriage.log 2>&1
