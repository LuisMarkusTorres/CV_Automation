#!/bin/bash

if ! docker ps --format '{{.Names}}' | grep -q '^cv-automation$'; then
    curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -H "Content-Type: application/json; charset=utf-8" \
        -d "{
            \"chat_id\": \"${CHAT_ID}\",
            \"text\": \"🚨 The cv-automation container has terminated or stopped 🚨\"
        }"
fi

# Set up w/ a cron job
# Cron job setting: 
# */15 * * * * /opt/cv-automation/docker_alarm.sh > /opt/cv-automation/cron_logs.txt 2>&1