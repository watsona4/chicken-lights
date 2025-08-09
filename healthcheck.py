#!/usr/bin/env python3
import os, sys, time
from pathlib import Path
import paho.mqtt.client as mqtt


def fail(msg):
    print(msg)
    sys.exit(1)


phase_path = Path("/tmp/phase")
phase = phase_path.read_text().strip() if phase_path.exists() else "unset"

if phase == "sleep":
    nw_path = Path("/tmp/next_wake")
    if not nw_path.exists():
        fail("sleep without next_wake")
    try:
        next_wake = int(nw_path.read_text().strip())
    except Exception as e:
        fail(f"bad next_wake: {e}")
    if time.time() > next_wake + 600:
        fail("overslept")
elif phase in ("active", "unset"):
    tick_path = Path("/tmp/last_tick")
    if not tick_path.exists():
        fail(f"{phase} without last_tick")
    try:
        last_tick = int(tick_path.read_text().strip())
    except Exception as e:
        fail(f"bad last_tick: {e}")
    if time.time() - last_tick > 180:
        fail("stale tick")
elif phase == "idle":
    pass
else:
    fail(f"unknown phase: {phase}")

host = os.getenv("MQTT_HOST", "")
port = int(os.getenv("MQTT_PORT", "1883"))
user = os.getenv("MQTT_USERNAME", "")
pwd = os.getenv("MQTT_PASSWORD", "")
if not host:
    fail("no MQTT_HOST")

c = mqtt.Client()
if user:
    c.username_pw_set(user, pwd)
try:
    c.connect(host, port, 10)
    c.disconnect()
except Exception as e:
    fail(f"broker unreachable: {e}")

sys.exit(0)
