"""
camera.py — Pi Camera Module (CSI) with low-latency MJPEG streaming.

Key optimisations vs the original:
  - Capture runs in its own thread at full speed (no sleep)
  - Encoding runs in a separate thread so capture is never blocked
  - JPEG quality 55 — looks fine on a 640px wide panel, much smaller payload
  - Stream resolution 480x360 for fast transfer; records at full 640x480
  - IR grayscale conversion for night vision look
  - mjpeg_generator uses an Event so it wakes instantly on new frame
    instead of sleeping a fixed interval
"""

import os
import time
import threading
from datetime import datetime

import cv2
import numpy as np

RECORDINGS_DIR = os.path.join(os.path.dirname(__file__), "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

CHUNK_SECONDS  = 10 * 60
# Full res for recording
REC_W, REC_H   = 640, 480
# Smaller res for live stream (faster encode + transfer = lower latency)
STREAM_W, STREAM_H = 480, 360
FRAME_RATE     = 30        # ask picamera2 for 30 fps
JPEG_QUALITY   = 55        # lower = smaller payload = less lag

try:
    from picamera2 import Picamera2
    PICAMERA2_AVAILABLE = True
except Exception:
    PICAMERA2_AVAILABLE = False


class Camera:
    def __init__(self):
        self._raw_lock   = threading.Lock()   # protects _raw_frame
        self._jpeg_lock  = threading.Lock()   # protects _latest_jpeg
        self._new_frame  = threading.Event()  # signals mjpeg_generator

        self._raw_frame   = None
        self._latest_jpeg = None

        self.running   = False
        self.mock_mode = False
        self.picam2    = None

        self._video_writer      = None
        self._chunk_start_time  = None

        self._init_camera()

    # ── Init ──────────────────────────────────────────────────────────

    def _init_camera(self):
        if not PICAMERA2_AVAILABLE:
            print("[camera] picamera2 not found — MOCK mode.")
            self.mock_mode = True
            return
        try:
            self.picam2 = Picamera2()
            # Use full sensor res for capture; we'll downscale for stream
            config = self.picam2.create_video_configuration(
                main={"size": (REC_W, REC_H), "format": "BGR888"},
                controls={"FrameRate": FRAME_RATE, "AwbEnable": False}
            )
            self.picam2.configure(config)
            self.picam2.start()
            time.sleep(1.5)
            print(f"[camera] Pi Camera started — {REC_W}x{REC_H} @ {FRAME_RATE}fps")
        except Exception as e:
            print(f"[camera] Init failed: {e}")
            if self.picam2:
                try: self.picam2.close()
                except: pass
            self.picam2 = None
            self.mock_mode = True

    # ── Mock frame ────────────────────────────────────────────────────

    def _make_mock_frame(self):
        frame = np.zeros((REC_H, REC_W, 3), dtype=np.uint8)
        frame[:] = (20, 20, 30)
        cv2.putText(frame, "NO CAMERA SIGNAL", (120, 220),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 80, 200), 2)
        cv2.putText(frame, datetime.now().strftime("%Y-%m-%d  %H:%M:%S"), (140, 265),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (160, 160, 160), 1)
        return frame

    # ── Thread 1: capture raw frames as fast as possible ─────────────

    def _capture_loop(self):
        while self.running:
            if self.mock_mode or self.picam2 is None:
                frame = self._make_mock_frame()
                time.sleep(0.033)   # mock at ~30 fps
            else:
                try:
                    frame = self.picam2.capture_array()
                except Exception as e:
                    print(f"[camera] Capture error: {e}")
                    self.mock_mode = True
                    continue

            with self._raw_lock:
                self._raw_frame = frame

    # ── Thread 2: encode latest raw frame → JPEG ──────────────────────

    def _encode_loop(self):
        rec_interval = 1.0 / FRAME_RATE
        while self.running:
            with self._raw_lock:
                frame = self._raw_frame

            if frame is None:
                time.sleep(0.01)
                continue

            # IR grayscale (night vision look)
            gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

            # Timestamp overlay
            ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
            cv2.putText(frame, ts, (8, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 120), 1)

            # --- Recording: full resolution ---
            try:
                self._rotate_chunk_writer()
                if self._video_writer:
                    self._video_writer.write(frame)
            except Exception as e:
                print(f"[camera] Record error: {e}")

            # --- Stream: downscale for speed ---
            small = cv2.resize(frame, (STREAM_W, STREAM_H),
                               interpolation=cv2.INTER_LINEAR)
            ok, jpeg = cv2.imencode(
                ".jpg", small,
                [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY,
                 cv2.IMWRITE_JPEG_OPTIMIZE, 1]
            )
            if ok:
                with self._jpeg_lock:
                    self._latest_jpeg = jpeg.tobytes()
                self._new_frame.set()   # wake up any waiting generators

            # Pace encoding — no need faster than FRAME_RATE
            time.sleep(rec_interval)

    # ── Video chunk management ────────────────────────────────────────

    def _rotate_chunk_writer(self):
        now = time.time()
        if self._video_writer is None or (now - self._chunk_start_time) >= CHUNK_SECONDS:
            if self._video_writer:
                self._video_writer.release()
            fname = datetime.now().strftime("%Y%m%d_%H%M%S") + ".mp4"
            path  = os.path.join(RECORDINGS_DIR, fname)
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self._video_writer = cv2.VideoWriter(
                path, fourcc, FRAME_RATE, (REC_W, REC_H))
            self._chunk_start_time = now
            print(f"[camera] New chunk: {fname}")

    # ── Public API ────────────────────────────────────────────────────

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._capture_loop, daemon=True, name="cam-capture").start()
        threading.Thread(target=self._encode_loop,  daemon=True, name="cam-encode").start()

    def stop(self):
        self.running = False
        if self._video_writer:
            self._video_writer.release()
        if self.picam2:
            try: self.picam2.stop(); self.picam2.close()
            except: pass

    def get_jpeg(self):
        with self._jpeg_lock:
            return self._latest_jpeg

    def mjpeg_generator(self):
        """
        Yields MJPEG frames to Flask's streaming Response.
        Uses an Event so it wakes instantly when a new frame is ready
        instead of sleeping a fixed interval — this is what kills latency.
        """
        while True:
            # Wait up to 1s for a new frame; avoids busy-wait
            self._new_frame.wait(timeout=1.0)
            self._new_frame.clear()
            with self._jpeg_lock:
                frame = self._latest_jpeg
            if frame:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")

    def list_recordings(self):
        files = sorted(os.listdir(RECORDINGS_DIR), reverse=True)
        return [f for f in files if f.endswith(".mp4")]


camera = Camera()
