"""
app.py — Flask + SocketIO server for Factory Bot.
Run with: python app.py   (inside your venv)
Then open http://<Pi-IP>:5000 on any device on the same WiFi.
"""

import os
import time
import atexit
import secrets
import threading
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, Response, jsonify,
                    send_from_directory, request, session, redirect, url_for)
from flask_socketio import SocketIO
from werkzeug.security import generate_password_hash, check_password_hash

import sensors
import alerts
import bot_control
from camera import camera, RECORDINGS_DIR

app = Flask(__name__)
# Random secret each restart is fine (it just invalidates old sessions on reboot).
# Set a fixed FACTORY_BOT_SECRET env var on the Pi instead if you want sessions
# to survive server restarts.
app.config["SECRET_KEY"] = os.environ.get("FACTORY_BOT_SECRET", secrets.token_hex(32))
app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 60 * 12  # stay logged in 12 hours
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")

SENSOR_LOOP_INTERVAL = 2  # seconds

# ---------------------------------------------------------------------
# Login credentials — change these, or better: set env vars
# FACTORY_BOT_USER / FACTORY_BOT_PASS on the Pi instead of hardcoding here.
# ---------------------------------------------------------------------
LOGIN_USERNAME = os.environ.get("FACTORY_BOT_USER", "admin")
LOGIN_PASSWORD_HASH = generate_password_hash(os.environ.get("FACTORY_BOT_PASS", "changeme123"))


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------
# Background sensor loop -> pushes live data + alerts to all connected dashboards
# ---------------------------------------------------------------------
def sensor_loop():
    while True:
        raw = sensors.read_all()
        # Map our internal sensor field names to the names this dashboard expects
        payload = {
            "temperature": raw.get("temp_c"),
            "humidity": raw.get("humidity"),
            "gas_ppm": raw.get("gas"),
            "distance_cm": raw.get("ultrasonic_cm"),
            "lidar_cm": raw.get("lidar_cm"),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        socketio.emit("sensor_data", payload)

        triggered = alerts.check_thresholds(payload)
        if triggered:
            socketio.emit("alert", {"triggered": triggered, "timestamp": payload["timestamp"]})

        time.sleep(SENSOR_LOOP_INTERVAL)


# ---------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == LOGIN_USERNAME and check_password_hash(LOGIN_PASSWORD_HASH, password):
            session["logged_in"] = True
            session.permanent = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Incorrect username or password")
    return render_template("login.html", error=None)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------
@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/video_feed")
@login_required
def video_feed():
    resp = Response(
        camera.mjpeg_generator(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )
    # Disable all buffering so frames reach the browser immediately
    resp.headers["Cache-Control"]   = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"]          = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/videos")
@login_required
def api_videos():
    files = []
    for name in camera.list_recordings():
        path = os.path.join(RECORDINGS_DIR, name)
        try:
            files.append({
                "name": name,
                "size_mb": round(os.path.getsize(path) / (1024 * 1024), 2),
                "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))),
            })
        except OSError:
            continue
    return jsonify(files)


@app.route("/videos/<path:filename>")
@login_required
def serve_video(filename):
    return send_from_directory(RECORDINGS_DIR, filename)


@app.route("/api/alerts/thresholds", methods=["GET", "POST"])
@login_required
def alert_thresholds():
    if request.method == "POST":
        alerts.update_thresholds(request.get_json(silent=True) or {})
        return jsonify({"status": "updated", **alerts.get_thresholds()})
    return jsonify(alerts.get_thresholds())


@socketio.on("connect")
def handle_connect():
    # Reject socket connections from anyone who hasn't logged in via the browser session
    if not session.get("logged_in"):
        return False  # refuses the connection


@socketio.on("control")
def handle_control(data):
    if not session.get("logged_in"):
        return
    cmd = data.get("command", "stop") if isinstance(data, dict) else str(data)
    speed = data.get("speed") if isinstance(data, dict) else None
    bot_control.command(cmd, speed)


# ---------------------------------------------------------------------
# Cleanup on shutdown
# ---------------------------------------------------------------------
def cleanup():
    camera.stop()
    bot_control.cleanup()
    sensors.cleanup()
    alerts.cleanup()


atexit.register(cleanup)

if __name__ == "__main__":
    camera.start()
    threading.Thread(target=sensor_loop, daemon=True).start()

    print("Factory Bot server starting on http://0.0.0.0:5000")
    print("Access from LAN: http://<Pi-IP-Address>:5000")
    socketio.run(app, host="0.0.0.0", port=5000)
