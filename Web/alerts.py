"""
alerts.py — threshold monitoring + notifications for Factory Bot.

Fill in EMAIL_SENDER / EMAIL_APP_PASSWORD / EMAIL_RECEIVER below before running.
Use a Gmail "App Password" (Google Account -> Security -> 2-Step Verification
-> App Passwords), not your normal Gmail password.

Thresholds are adjustable at runtime from the dashboard's "Alert Thresholds"
panel (GET/POST /api/alerts/thresholds) — see get_thresholds()/update_thresholds().
"""

import time
import smtplib
import threading
from email.mime.text import MIMEText

IS_PI = True
try:
    import RPi.GPIO as GPIO
except Exception:
    IS_PI = False

# ---------------------------------------------------------------------
# Fill these in
# ---------------------------------------------------------------------
EMAIL_SENDER = "your_bot_gmail@gmail.com"
EMAIL_APP_PASSWORD = "xxxx xxxx xxxx xxxx"   # 16-char Gmail app password
EMAIL_RECEIVER = "you@example.com"

# ---------------------------------------------------------------------
# Thresholds — adjustable at runtime from the dashboard.
# distance_min applies to both ultrasonic and lidar readings.
# ---------------------------------------------------------------------
_thresholds = {
    "temperature_max": 60.0,
    "humidity_max": 85.0,
    "gas_ppm_max": 300.0,
    "distance_min": 15.0,
}
_thresholds_lock = threading.Lock()

ALERT_COOLDOWN_SECONDS = 5 * 60   # one email per issue per 5 min, doesn't affect dashboard log

SIREN_PIN = 17
LED_PIN = 27

if IS_PI:
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(SIREN_PIN, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(LED_PIN, GPIO.OUT, initial=GPIO.LOW)
    except Exception as e:
        print(f"[alerts] GPIO setup for siren/LED failed: {e}")

_last_sent = {}  # alert_key -> timestamp
_lock = threading.Lock()


def get_thresholds():
    with _thresholds_lock:
        return dict(_thresholds)


def update_thresholds(new_values: dict):
    with _thresholds_lock:
        for key in ("temperature_max", "humidity_max", "gas_ppm_max", "distance_min"):
            if key in new_values and new_values[key] is not None:
                try:
                    _thresholds[key] = float(new_values[key])
                except (TypeError, ValueError):
                    pass
        return dict(_thresholds)


def _send_email(subject, body):
    if EMAIL_SENDER.startswith("your_bot_gmail") or "xxxx" in EMAIL_APP_PASSWORD:
        print(f"[alerts] Email not configured — would have sent: {subject} | {body}")
        return
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())
        print(f"[alerts] Email sent: {subject}")
    except Exception as e:
        print(f"[alerts] Email send failed: {e}")


def _throttled(key):
    """Returns True if enough time has passed since the last EMAIL of this type."""
    now = time.time()
    with _lock:
        last = _last_sent.get(key, 0)
        if now - last >= ALERT_COOLDOWN_SECONDS:
            _last_sent[key] = now
            return True
        return False


def _set_siren(state: bool):
    if IS_PI:
        try:
            GPIO.output(SIREN_PIN, GPIO.HIGH if state else GPIO.LOW)
        except Exception:
            pass


def _set_led(state: bool):
    if IS_PI:
        try:
            GPIO.output(LED_PIN, GPIO.HIGH if state else GPIO.LOW)
        except Exception:
            pass


def check_thresholds(readings: dict):
    """
    Call once per sensor loop tick with a dict containing:
    temperature, humidity, gas_ppm, distance_cm, lidar_cm

    Returns a list of triggered dicts: {"sensor": str, "value": num, "limit": num}
    — this is exactly the shape the dashboard's alert log expects.
    """
    t = get_thresholds()
    triggered = []
    danger = False

    temperature = readings.get("temperature")
    humidity = readings.get("humidity")
    gas_ppm = readings.get("gas_ppm")
    distance_cm = readings.get("distance_cm")
    lidar_cm = readings.get("lidar_cm")

    if temperature is not None and temperature > t["temperature_max"]:
        triggered.append({"sensor": "temperature", "value": temperature, "limit": t["temperature_max"]})
        danger = True
        if _throttled("temp"):
            _send_email("Factory Bot: High Temperature Alert",
                        f"Temperature reached {temperature}°C (threshold {t['temperature_max']}°C).")

    if humidity is not None and humidity > t["humidity_max"]:
        triggered.append({"sensor": "humidity", "value": humidity, "limit": t["humidity_max"]})
        if _throttled("humidity"):
            _send_email("Factory Bot: High Humidity Alert",
                        f"Humidity reached {humidity}% (threshold {t['humidity_max']}%).")

    if gas_ppm is not None and gas_ppm > t["gas_ppm_max"]:
        triggered.append({"sensor": "gas_ppm", "value": gas_ppm, "limit": t["gas_ppm_max"]})
        danger = True
        if _throttled("gas"):
            _send_email("Factory Bot: Gas Alert",
                        f"Gas reading hit {gas_ppm}ppm (threshold {t['gas_ppm_max']}ppm). Possible leak.")

    if distance_cm is not None and distance_cm < t["distance_min"]:
        triggered.append({"sensor": "distance_cm", "value": distance_cm, "limit": t["distance_min"]})
        if _throttled("obstacle_us"):
            _send_email("Factory Bot: Obstacle Alert",
                        f"Ultrasonic detected object {distance_cm}cm away.")

    if lidar_cm is not None and lidar_cm < t["distance_min"]:
        triggered.append({"sensor": "lidar_cm", "value": lidar_cm, "limit": t["distance_min"]})
        if _throttled("obstacle_lidar"):
            _send_email("Factory Bot: Obstacle Alert",
                        f"LiDAR detected object {lidar_cm}cm away.")

    _set_siren(danger)
    _set_led(danger)

    return triggered


def cleanup():
    _set_siren(False)
    _set_led(False)
    if IS_PI:
        try:
            GPIO.cleanup([SIREN_PIN, LED_PIN])
        except Exception:
            pass
