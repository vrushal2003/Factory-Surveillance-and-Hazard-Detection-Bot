"""
bot_control.py — L298N motor driver control (W/A/S/D style commands).

Wiring (BCM numbering, adjust to match your build):
  Left motor  : IN1 -> GPIO5,  IN2 -> GPIO6,  ENA -> GPIO12 (PWM)
  Right motor : IN3 -> GPIO13, IN4 -> GPIO19, ENB -> GPIO18 (PWM)
"""

import threading

IS_PI = True
try:
    import RPi.GPIO as GPIO
except Exception:
    IS_PI = False

IN1, IN2, ENA = 5, 6, 12
IN3, IN4, ENB = 13, 19, 18
PWM_FREQ = 1000
DEFAULT_SPEED_PERCENT = 70

_pwm_a = None
_pwm_b = None
_lock = threading.Lock()
_current_state = "stop"

if IS_PI:
    try:
        GPIO.setmode(GPIO.BCM)
        for pin in (IN1, IN2, ENA, IN3, IN4, ENB):
            GPIO.setup(pin, GPIO.OUT)
        _pwm_a = GPIO.PWM(ENA, PWM_FREQ)
        _pwm_b = GPIO.PWM(ENB, PWM_FREQ)
        _pwm_a.start(0)
        _pwm_b.start(0)
    except Exception as e:
        print(f"[bot_control] GPIO setup failed, running in MOCK mode: {e}")
        IS_PI = False


def _drive(left_fwd, left_speed, right_fwd, right_speed):
    if not IS_PI:
        return
    with _lock:
        GPIO.output(IN1, GPIO.HIGH if left_fwd else GPIO.LOW)
        GPIO.output(IN2, GPIO.LOW if left_fwd else GPIO.HIGH)
        GPIO.output(IN3, GPIO.HIGH if right_fwd else GPIO.LOW)
        GPIO.output(IN4, GPIO.LOW if right_fwd else GPIO.HIGH)
        _pwm_a.ChangeDutyCycle(left_speed)
        _pwm_b.ChangeDutyCycle(right_speed)


_CMD_ALIASES = {
    "forward": "w", "backward": "s", "left": "a", "right": "d", "stop": "stop",
}


def command(cmd: str, speed: int = None):
    """Accepts 'forward'/'backward'/'left'/'right'/'stop' (dashboard) or
    'w'/'a'/'s'/'d'/'stop' (keyboard shortcuts) — both normalize to the same drive logic.
    speed: 0-100, defaults to DEFAULT_SPEED_PERCENT if not given."""
    global _current_state
    cmd = cmd.lower().strip()
    cmd = _CMD_ALIASES.get(cmd, cmd)
    _current_state = cmd
    speed = DEFAULT_SPEED_PERCENT if speed is None else max(0, min(100, int(speed)))

    if not IS_PI:
        print(f"[bot_control] MOCK drive command: {cmd} @ {speed}%")
        return {"status": "mock", "command": cmd, "speed": speed}

    if cmd == "w":
        _drive(True, speed, True, speed)
    elif cmd == "s":
        _drive(False, speed, False, speed)
    elif cmd == "a":
        _drive(False, speed, True, speed)
    elif cmd == "d":
        _drive(True, speed, False, speed)
    else:  # stop / space
        _drive(True, 0, True, 0)

    return {"status": "ok", "command": cmd, "speed": speed}


def get_state():
    return _current_state


def cleanup():
    if IS_PI:
        try:
            _pwm_a.stop()
            _pwm_b.stop()
            GPIO.cleanup([IN1, IN2, ENA, IN3, IN4, ENB])
        except Exception:
            pass
