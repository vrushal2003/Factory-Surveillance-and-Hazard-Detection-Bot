"""
sensors.py — reads all sensors for Factory Bot.

Hardware (as confirmed):
  - Temp/Humidity : DHT11, data pin -> GPIO4 (BCM)
  - Gas           : MQ-2, analog out -> ADS1115 channel A0 (I2C)
  - Ultrasonic    : HC-SR04, TRIG -> GPIO23, ECHO -> GPIO24
  - LiDAR         : USB-connected, /dev/ttyUSB0 @ 115200 baud
                    (change LIDAR_PORT below if yours differs, or if the
                    Pi enumerates it as ttyUSB1 etc. when other USB-serial
                    devices are plugged in — check with 'ls /dev/ttyUSB*')

Runs in MOCK mode automatically if:
  - not running on a Pi (no RPi.GPIO / smbus available), or
  - a specific sensor library/board fails to init

Mock mode returns realistic-looking randomized values so the dashboard
and alert logic can be developed/tested off-Pi.
"""

import time
import random
import threading

IS_PI = True
try:
    import RPi.GPIO as GPIO
except Exception:
    IS_PI = False

DHT_PIN = 4          # BCM GPIO4
TRIG_PIN = 23
ECHO_PIN = 24
LIDAR_PORT = "/dev/ttyUSB0"
LIDAR_BAUD = 115200

# ---------------------------------------------------------------------
# DHT11
# ---------------------------------------------------------------------
DHT_AVAILABLE = False
if IS_PI:
    try:
        import adafruit_dht
        import board
        _dht_device = adafruit_dht.DHT11(board.D4)
        DHT_AVAILABLE = True
    except Exception as e:
        print(f"[sensors] DHT11 init failed, using mock: {e}")

# ---------------------------------------------------------------------
# MQ-2 gas sensor via ADS1115 (I2C ADC)
# ---------------------------------------------------------------------
ADS_AVAILABLE = False
if IS_PI:
    try:
        import busio
        import adafruit_ads1x15.ads1115 as ADS
        from adafruit_ads1x15.analog_in import AnalogIn
        i2c = busio.I2C(board.SCL, board.SDA)
        ads = ADS.ADS1115(i2c)
        gas_channel = AnalogIn(ads, ADS.P0)  # MQ-2 -> A0
        ADS_AVAILABLE = True
    except Exception as e:
        print(f"[sensors] ADS1115/MQ-2 init failed, using mock: {e}")

# ---------------------------------------------------------------------
# Ultrasonic (HC-SR04)
# ---------------------------------------------------------------------
ULTRASONIC_AVAILABLE = False
if IS_PI:
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(TRIG_PIN, GPIO.OUT)
        GPIO.setup(ECHO_PIN, GPIO.IN)
        GPIO.output(TRIG_PIN, False)
        time.sleep(0.2)
        ULTRASONIC_AVAILABLE = True
    except Exception as e:
        print(f"[sensors] Ultrasonic init failed, using mock: {e}")

# ---------------------------------------------------------------------
# LiDAR (TF-Luna, USB-serial)
# ---------------------------------------------------------------------
LIDAR_AVAILABLE = False
_lidar_serial = None
if IS_PI:
    try:
        import serial
        _lidar_serial = serial.Serial(LIDAR_PORT, LIDAR_BAUD, timeout=1)
        LIDAR_AVAILABLE = True
    except Exception as e:
        print(f"[sensors] LiDAR init failed, using mock: {e}")


_lock = threading.Lock()
_last_good = {"temp_c": 24.0, "humidity": 45.0, "gas": 300, "ultrasonic_cm": 50.0, "lidar_cm": 100.0}


def read_dht11():
    if not DHT_AVAILABLE:
        return round(random.uniform(20, 30), 1), round(random.uniform(35, 60), 1)
    try:
        temp_c = _dht_device.temperature
        humidity = _dht_device.humidity
        if temp_c is not None and humidity is not None:
            _last_good["temp_c"], _last_good["humidity"] = temp_c, humidity
            return temp_c, humidity
    except RuntimeError:
        # DHT11 drops a read fairly often — not an error, just retry next cycle
        pass
    except Exception as e:
        print(f"[sensors] DHT11 read error: {e}")
    return _last_good["temp_c"], _last_good["humidity"]


def read_gas():
    if not ADS_AVAILABLE:
        return random.randint(200, 500)
    try:
        raw = gas_channel.value  # 0-65535
        _last_good["gas"] = raw
        return raw
    except Exception as e:
        print(f"[sensors] MQ-2 read error: {e}")
        return _last_good["gas"]


def read_ultrasonic():
    if not ULTRASONIC_AVAILABLE:
        return round(random.uniform(10, 200), 1)
    try:
        GPIO.output(TRIG_PIN, True)
        time.sleep(0.00001)
        GPIO.output(TRIG_PIN, False)

        timeout_start = time.time()
        while GPIO.input(ECHO_PIN) == 0:
            pulse_start = time.time()
            if pulse_start - timeout_start > 0.02:
                return _last_good["ultrasonic_cm"]

        while GPIO.input(ECHO_PIN) == 1:
            pulse_end = time.time()
            if pulse_end - pulse_start > 0.02:
                return _last_good["ultrasonic_cm"]

        distance_cm = (pulse_end - pulse_start) * 17150
        distance_cm = round(distance_cm, 1)
        if 2 <= distance_cm <= 400:
            _last_good["ultrasonic_cm"] = distance_cm
        return _last_good["ultrasonic_cm"]
    except Exception as e:
        print(f"[sensors] Ultrasonic read error: {e}")
        return _last_good["ultrasonic_cm"]


def read_lidar():
    if not LIDAR_AVAILABLE:
        return round(random.uniform(20, 300), 1)
    try:
        # TF-Luna frame: 0x59 0x59 distLow distHigh strLow strHigh tempLow tempHigh checksum
        if _lidar_serial.in_waiting >= 9:
            data = _lidar_serial.read(9)
            if data[0] == 0x59 and data[1] == 0x59:
                dist_cm = data[2] + data[3] * 256
                _last_good["lidar_cm"] = dist_cm
        return _last_good["lidar_cm"]
    except Exception as e:
        print(f"[sensors] LiDAR read error: {e}")
        return _last_good["lidar_cm"]


def read_all():
    """Returns a single dict snapshot of every sensor — call this once per loop tick."""
    with _lock:
        temp_c, humidity = read_dht11()
        return {
            "temp_c": round(temp_c, 1) if temp_c is not None else None,
            "humidity": round(humidity, 1) if humidity is not None else None,
            "gas": read_gas(),
            "ultrasonic_cm": read_ultrasonic(),
            "lidar_cm": read_lidar(),
            "timestamp": time.time(),
        }


def cleanup():
    if IS_PI:
        try:
            GPIO.cleanup()
        except Exception:
            pass
    if _lidar_serial is not None:
        try:
            _lidar_serial.close()
        except Exception:
            pass
