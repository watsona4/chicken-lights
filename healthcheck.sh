#!/bin/sh

TOPIC="chicken_lights/healthcheck/status"
BROKER="frigate.home.arpa" # Change this to your MQTT broker address if needed
CHECK_MESSAGE="CHECK"
EXPECTED_RESPONSE="OK"
TIMEOUT=5

# Check if mosquitto_sub is already running for the topic
if ! pgrep -f "mosquitto_sub.*$TOPIC" > /dev/null; then
    # Start mosquitto_sub in the background
    mosquitto_sub -h "$BROKER" -t "$TOPIC" > /tmp/chicken_lights_healthcheck_response.txt &
    sleep 1 # Give mosquitto_sub some time to start
fi

# Publish the CHECK message
mosquitto_pub -h "$BROKER" -t "$TOPIC" -m "$CHECK_MESSAGE"

# Wait for the response
sleep "$TIMEOUT"
RESPONSE=$(tail -n 1 /tmp/chicken_lights_healthcheck_response.txt)

# Check the response and return appropriate status
if [ "$RESPONSE" = "$EXPECTED_RESPONSE" ]; then
    exit 0
else
    exit 1
fi
