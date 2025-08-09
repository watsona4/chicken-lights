import asyncio
import json
import logging
import os
import signal
import sys
import time
from types import FrameType

import numpy as np
import paho.mqtt.client as mqtt
import pandas as pd
import suntimes
from colour_system import CS_HDTV
from paho.mqtt.enums import CallbackAPIVersion
from pvlib import atmosphere, location, spectrum
from pathlib import Path

MQTT_HOST: str = str(os.environ.get("MQTT_HOST", ""))
MQTT_PORT: int = int(os.environ.get("MQTT_PORT", 1883))
MQTT_USERNAME: str = str(os.environ.get("MQTT_USERNAME", ""))
MQTT_PASSWORD: str = str(os.environ.get("MQTT_PASSWORD", ""))

DISCOVERY_PREFIX: str = str(os.environ.get("DISCOVERY_PREFIX", "homeassistant"))
BASE_TOPIC: str = str(os.environ.get("BASE_TOPIC", "fake_time"))
LIGHT_CMD_TOPIC: str = str(os.environ.get("CMD_TOPIC", "zigbee2mqtt/Chicken Coop Light/set"))

DEVICE = {
    "identifiers": ["chicken-lights-controller"],
    "name": "Chicken Lights Controller",
    "manufacturer": "custom",
    "model": "pvlib-spectrl2",
}

LATITUDE: float = float(os.environ.get("LATITUDE", 0))
LONGITUDE: float = float(os.environ.get("LONGITUDE", 0))
ALTITUDE: float = float(os.environ.get("ALTITUDE", 0))

TZ: str = str(os.environ.get("TZ", "UTC"))

CLIENT: mqtt.Client = mqtt.Client(CallbackAPIVersion.VERSION2)

logging.basicConfig(format="%(asctime)s [%(levelname)s]: %(message)s", level=logging.DEBUG)


def handler(signum: int, frame: FrameType | None):
    CLIENT.disconnect()
    CLIENT.loop_stop()
    sys.exit(0)


signal.signal(signal.SIGTERM, handler)


async def publish_data():

    CLIENT.enable_logger()

    CLIENT.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    CLIENT.will_set(f"{BASE_TOPIC}/availability", "offline", qos=1, retain=True)

    CLIENT.connect(MQTT_HOST, MQTT_PORT, 60)

    CLIENT.loop_start()

    CLIENT.publish(f"{BASE_TOPIC}/availability", "online", qos=1, retain=True)

    CLIENT.publish(
        f"{DISCOVERY_PREFIX}/sensor/chicken_lights/fake_time/config",
        json.dumps({
            "name": "Chicken Lights Fake Time",
            "unique_id": "chicken_lights_fake_time",
            "icon": "mdi:calendar-clock",
            "device_class": "timestamp",
            "state_topic": BASE_TOPIC,  # publishes ISO 8601 string
            "availability_topic": f"{BASE_TOPIC}/availability",
            "qos": 1,
            "device": DEVICE,
            "json_attributes_topic": f"{BASE_TOPIC}/status",
        }),
        retain=True,
    )

    CLIENT.publish(
        f"{DISCOVERY_PREFIX}/sensor/chicken_lights/phase/config",
        json.dumps({
            "name": "Chicken Lights Phase",
            "unique_id": "chicken_lights_phase",
            "icon": "mdi:state-machine",
            "state_topic": f"{BASE_TOPIC}/phase",  # “sleep”, “active”, “idle”
            "availability_topic": f"{BASE_TOPIC}/availability",
            "qos": 1,
            "device": DEVICE,
        }),
        retain=True,
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
    )

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
    spec = np.array([
        np.interp(lam, spectra["wavelength"], spectra["poa_global"][:, i])
        for i in range(len(times))
    ])

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

    old_day = pd.Timestamp.today().date() - pd.Timedelta(days=1)
    while True:
        logging.info("    old_day: %s", old_day)
        today = pd.Timestamp.today().date()
        logging.info("    today: %s", today)
        logging.info("        today - old_day = %s", today - old_day)
        if today - old_day >= pd.Timedelta(days=1):
            old_day = today
            asyncio.run(publish_data())
        time.sleep(60)


if __name__ == "__main__":

    try:
        sys.exit(main())
    except Exception:
        sys.exit(1)
