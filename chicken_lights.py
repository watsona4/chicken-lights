import json
import logging
import os
import signal
import socket
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Optional, Tuple

import numpy as np
import paho.mqtt.client as mqtt
import pandas as pd
import suntimes
from paho.mqtt.enums import CallbackAPIVersion
from pvlib import atmosphere, location, spectrum

from colour_system import CS_HDTV

MQTT_HOST: str = str(os.environ.get("MQTT_HOST", "")).strip()
MQTT_PORT: int = int(os.environ.get("MQTT_PORT", 1883))
MQTT_USERNAME: str = str(os.environ.get("MQTT_USERNAME", "")).strip()
MQTT_PASSWORD: str = str(os.environ.get("MQTT_PASSWORD", "")).strip()

DISCOVERY_PREFIX: str = str(os.environ.get("DISCOVERY_PREFIX", "homeassistant")).strip()
BASE_TOPIC: str = str(os.environ.get("BASE_TOPIC", "fake_time")).strip()
LIGHT_CMD_TOPIC: str = str(os.environ.get("LIGHT_CMD_TOPIC", "zigbee2mqtt/Chicken Coop Light/set")).strip()

DEVICE = {
    "identifiers": ["chicken-lights-controller"],
    "name": "Chicken Lights Controller",
    "manufacturer": "custom",
    "model": "pvlib-spectrl2",
}

LATITUDE: float = float(os.environ.get("LATITUDE", 0))
LONGITUDE: float = float(os.environ.get("LONGITUDE", 0))
ALTITUDE: float = float(os.environ.get("ALTITUDE", 0))

TZ: str = str(os.environ.get("TZ", "UTC")).strip()

# Optional: fetch coordinates from remote gpsd
GPSD_HOST: str = str(os.environ.get("GPSD_HOST", "")).strip()
GPSD_PORT: int = int(os.environ.get("GPSD_PORT", 2947))
GPSD_TIMEOUT_S: int = int(os.environ.get("GPSD_TIMEOUT", 5))
GPSD_REFRESH_S: int = int(os.environ.get("GPSD_REFRESH", 900))  # periodic refresh while active

MQTT_KEEPALIVE_S: int = int(os.environ.get("MQTT_KEEPALIVE", 300))
CLIENT_ID: str = str(os.environ.get("MQTT_CLIENT_ID", f"chicken-lights-{socket.gethostname()}")).strip()

CLIENT: mqtt.Client = mqtt.Client(
    callback_api_version=CallbackAPIVersion.VERSION2,
    client_id=CLIENT_ID,
    clean_session=False,
    protocol=mqtt.MQTTv311,
)
CLIENT.reconnect_delay_set(min_delay=1, max_delay=60)

logging.basicConfig(format="%(asctime)s [%(levelname)s]: %(message)s", level=logging.DEBUG)

_connected: bool = False


def get_fix_from_gpsd(host: str, port: int = 2947, timeout_s: int = 5) -> Optional[Tuple[float, float, float]]:
    """Return (lat, lon, alt_m) from gpsd, or None if unavailable."""
    if not host:
        return None

    deadline = time.time() + max(1, timeout_s)
    buf = b""

    try:
        with socket.create_connection((host, port), timeout=2) as s:
            s.settimeout(2)
            s.sendall(b'?WATCH={"enable":true,"json":true}\n')

            while time.time() < deadline:
                try:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    buf += chunk

                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            msg = json.loads(line.decode("utf-8", errors="ignore"))
                        except json.JSONDecodeError:
                            continue

                        if msg.get("class") != "TPV":
                            continue

                        mode = int(msg.get("mode") or 0)
                        lat = msg.get("lat")
                        lon = msg.get("lon")
                        alt = msg.get("alt")

                        if mode >= 2 and lat is not None and lon is not None:
                            alt_m = float(alt) if (alt is not None and mode >= 3) else 0.0
                            return float(lat), float(lon), alt_m

                except socket.timeout:
                    continue

    except Exception as e:
        logging.warning("gpsd lookup failed (%s:%s): %s", host, port, e)

    return None


def on_connect(client, userdata, flags, rc, properties=None):
    global _connected
    if rc == 0:
        _connected = True
        logging.info("MQTT connected (client_id=%s)", CLIENT_ID)

        # Availability + discovery are best re-published on every connect (retained).
        CLIENT.publish(f"{BASE_TOPIC}/availability", "online", qos=1, retain=True)

        CLIENT.publish(
            f"{DISCOVERY_PREFIX}/sensor/chicken_lights/fake_time/config",
            json.dumps({
                "name": "Chicken Lights Fake Time",
                "unique_id": "chicken_lights_fake_time",
                "icon": "mdi:calendar-clock",
                "device_class": "timestamp",
                "state_topic": BASE_TOPIC,
                "availability_topic": f"{BASE_TOPIC}/availability",
                "qos": 1,
                "device": DEVICE,
                "json_attributes_topic": f"{BASE_TOPIC}/status",
            }),
            retain=True,
            qos=1,
        )

        CLIENT.publish(
            f"{DISCOVERY_PREFIX}/sensor/chicken_lights/phase/config",
            json.dumps({
                "name": "Chicken Lights Phase",
                "unique_id": "chicken_lights_phase",
                "icon": "mdi:state-machine",
                "state_topic": f"{BASE_TOPIC}/phase",
                "availability_topic": f"{BASE_TOPIC}/availability",
                "qos": 1,
                "device": DEVICE,
            }),
            retain=True,
            qos=1,
        )

        CLIENT.publish(
            f"{DISCOVERY_PREFIX}/sensor/chicken_lights/brightness/config",
            json.dumps({
                "name": "Chicken Lights Brightness",
                "unique_id": "chicken_lights_brightness",
                "icon": "mdi:brightness-6",
                "state_topic": f"{BASE_TOPIC}/status",
                "value_template": "{{ value_json.brightness|int }}",
                "unit_of_measurement": "/254",
                "availability_topic": f"{BASE_TOPIC}/availability",
                "qos": 1,
                "device": DEVICE,
            }),
            retain=True,
            qos=1,
        )
    else:
        logging.warning("MQTT connect failed rc=%s", rc)


def on_disconnect(client, userdata, rc, properties=None):
    global _connected
    _connected = False
    logging.warning("MQTT disconnected rc=%s", rc)


def handler(signum: int, frame: FrameType | None):
    try:
        CLIENT.publish(f"{BASE_TOPIC}/availability", "offline", qos=1, retain=True)
    except Exception:
        pass
    CLIENT.disconnect()
    CLIENT.loop_stop()
    sys.exit(0)


signal.signal(signal.SIGINT, handler)


def publish_day():
    global LATITUDE, LONGITUDE, ALTITUDE

    # Get a fresh gpsd fix at the start of the day (if configured)
    if GPSD_HOST:
        fix = get_fix_from_gpsd(GPSD_HOST, GPSD_PORT, GPSD_TIMEOUT_S)
        if fix:
            LATITUDE, LONGITUDE, ALTITUDE = fix
            logging.info("Using gpsd fix: lat=%s lon=%s alt=%sm", LATITUDE, LONGITUDE, ALTITUDE)
        else:
            logging.warning("gpsd configured but no fix, using env LAT/LON/ALT")
    last_gpsd_check = time.time()

    today = pd.Timestamp.today(tz=TZ)
    logging.info("Today is %s", today)

    sun = suntimes.SunTimes(LONGITUDE, LATITUDE, ALTITUDE)
    logging.info("Sun: %s", sun)

    sunrise = pd.Timestamp(sun.riselocal(today)).tz_convert(TZ)
    sunset = pd.Timestamp(sun.setlocal(today)).tz_convert(TZ)

    logging.info("Sunrise today: %s", sunrise)
    logging.info("Sunset today: %s", sunset)

    dl = today.replace(month=6, day=21)
    ds = today.replace(month=12, day=21)

    dsp = today.replace(month=8, day=15)

    todayp = (dl - dsp) / 2 * (np.cos(np.pi * (dl - today) / (dl - ds)) + 1) + dsp

    start_time = todayp.replace(hour=0, minute=0, second=0, microsecond=0, nanosecond=0)
    end_time = todayp.replace(hour=23, minute=59, second=59)

    times = pd.date_range(start_time, end_time, freq="1min", tz=TZ)

    loc = location.Location(LATITUDE, LONGITUDE, TZ, ALTITUDE)

    solpos = loc.get_solarposition(times)

    relative_airmass = atmosphere.get_relative_airmass(solpos.apparent_zenith)

    spectra = spectrum.spectrl2(
        apparent_zenith=solpos.apparent_zenith,
        aoi=solpos.apparent_zenith,
        surface_tilt=0,
        ground_albedo=0.2,
        surface_pressure=101300,
        relative_airmass=relative_airmass,
        precipitable_water=0.5,
        ozone=0.31,
        aerosol_turbidity_500nm=0.1,
    )

    lam = np.arange(380.0, 781.0, 5)
    spec = np.array([np.interp(lam, spectra["wavelength"], spectra["poa_global"][:, i]) for i in range(len(times))])

    norms = np.array([np.linalg.norm(v) for v in spec])
    nanmax = np.nanmax(norms)
    logging.info("Max. irradiance: %s", nanmax)
    brights = norms / nanmax

    spec = np.array([CS_HDTV.spec_to_xyz(s) for s in spec])

    df = pd.DataFrame(
        {
            "Fake Time": times,
            "X": spec[:, 0],
            "Y": spec[:, 1],
            "Brightness": brights,
        },
        index=times,
    )

    df.dropna(inplace=True)

    delta_time = df.index[-1] - df.index[0]
    logging.info("Length of fake day: %s", delta_time)

    start_time = sunset - delta_time
    logging.info("Start time: %s", start_time)

    now = pd.Timestamp.now(tz=TZ)
    logging.info("Time right now: %s", now)

    delay = start_time - now
    logging.info("Sleep delay: %s", delay)

    if delay.total_seconds() < 0:
        mins = delay.total_seconds() / 60
        logging.info("Stripping first %d entries", int(np.abs(mins)))
        df = df.tail(-int(np.abs(mins)))
    else:
        logging.info(
            "Now sleeping for %d seconds, will continue at %s",
            delay.total_seconds(),
            now + delay,
        )
        # Announce sleep phase and next wake for healthcheck
        next_wake = (now + delay).timestamp()
        CLIENT.publish(f"{BASE_TOPIC}/phase", "sleep", qos=1, retain=True)
        Path("/tmp/phase").write_text("sleep")
        Path("/tmp/next_wake").write_text(str(int(next_wake)))
        time.sleep(delay.total_seconds())

    CLIENT.publish(f"{BASE_TOPIC}/phase", "active", qos=1, retain=True)
    Path("/tmp/phase").write_text("active")
    Path("/tmp/last_tick").write_text(str(int(time.time())))

    for idx, row in df.iterrows():
        while pd.Timestamp.now().second % 60 != 0:
            time.sleep(0.5)

        # Periodic gpsd refresh while active (does not recompute the schedule;
        # it just updates what we report in status so you can confirm it's correct)
        if GPSD_HOST and (time.time() - last_gpsd_check) >= GPSD_REFRESH_S:
            fix = get_fix_from_gpsd(GPSD_HOST, GPSD_PORT, GPSD_TIMEOUT_S)
            last_gpsd_check = time.time()
            if fix:
                LATITUDE, LONGITUDE, ALTITUDE = fix
                logging.info("Updated gpsd fix: lat=%s lon=%s alt=%sm", LATITUDE, LONGITUDE, ALTITUDE)
            else:
                logging.warning("gpsd refresh failed, keeping previous location")

        CLIENT.publish(
            LIGHT_CMD_TOPIC,
            json.dumps({
                "state": "on",
                "color": {"x": float(f"{row['X']:.4f}"), "y": float(f"{row['Y']:.4f}")},
                "brightness": max(int(row["Brightness"] * 254), 1),
            }),
            qos=1,
        )
        CLIENT.publish(BASE_TOPIC, row["Fake Time"].isoformat(), qos=1)

        CLIENT.publish(
            f"{BASE_TOPIC}/status",
            json.dumps({
                "x": float(f"{row['X']:.4f}"),
                "y": float(f"{row['Y']:.4f}"),
                "brightness": max(int(row["Brightness"] * 254), 1),
                "ts": pd.Timestamp.now(tz=TZ).isoformat(),
                "lat": LATITUDE,
                "lon": LONGITUDE,
                "alt_m": ALTITUDE,
            }),
            qos=1,
        )

        # tick files for healthcheck
        Path("/tmp/last_tick").write_text(str(int(time.time())))

        time.sleep(1)

    CLIENT.publish(LIGHT_CMD_TOPIC, json.dumps({"state": "off"}), qos=1)

    CLIENT.publish(f"{BASE_TOPIC}/phase", "idle", qos=1, retain=True)
    Path("/tmp/phase").write_text("idle")


def main():

    CLIENT.enable_logger()

    CLIENT.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    CLIENT.will_set(f"{BASE_TOPIC}/availability", "offline", qos=1, retain=True)

    CLIENT.on_connect = on_connect
    CLIENT.on_disconnect = on_disconnect

    if not MQTT_HOST:
        logging.error("MQTT_HOST is empty")
        return 2

    CLIENT.connect_async(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE_S)

    CLIENT.loop_start()

    # Wait briefly for the initial connect
    start = time.time()
    while not _connected and time.time() - start < 30:
        time.sleep(0.2)

    old_day = pd.Timestamp.today().date() - pd.Timedelta(days=1)
    while True:
        logging.info("    old_day: %s", old_day)
        today = pd.Timestamp.today().date()
        logging.info("    today: %s", today)
        logging.info("        today - old_day = %s", today - old_day)
        if today - old_day >= pd.Timedelta(days=1):
            old_day = today
            if _connected:
                publish_day()
            else:
                logging.warning("MQTT not connected, skipping day run")
        time.sleep(60)


if __name__ == "__main__":

    try:
        raise SystemExit(main())
    except Exception:
        logging.exception("fatal error")
        raise SystemExit(1)
