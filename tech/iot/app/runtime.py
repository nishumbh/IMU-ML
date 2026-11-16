"""Runtime helpers, with no hardware imports at module import time."""


class Clock:
    """Extend wrapping ticks_ms to elapsed ms; service at least once per half-wrap."""

    def __init__(self):
        import time
        self.time = time
        self.last = time.ticks_ms()
        self.elapsed = 0

    def now_ms(self):
        current = self.time.ticks_ms()
        self.elapsed += self.time.ticks_diff(current, self.last)
        self.last = current
        return self.elapsed

    def raw_ticks(self):
        return self.time.ticks_ms()

    def idle(self):
        self.time.sleep_ms(1)


def close_all(*resources):
    # Try every cleanup; caller preserves an active acquisition exception or
    # raises this first cleanup error on an otherwise normal stop.
    first_error = None
    for resource in resources:
        if resource is not None:
            try:
                resource.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc
    return first_error


class Sensor:
    def __init__(self, config):
        from machine import Pin, I2C
        from .mpu6050 import MPU6050
        pins = config["pins"]
        self.imu = None
        self.i2c = I2C(0, sda=Pin(pins["i2c_sda"]),
                       scl=Pin(pins["i2c_scl"]), freq=400000,
                       timeout=10000)
        try:
            self.imu = MPU6050(self.i2c, config["imu_address"])
        except BaseException:
            self.close()
            raise

    def read(self):
        return self.imu.read()

    def close(self):
        try:
            if self.imu is not None:
                self.imu.close()
        finally:
            # Some ESP32 builds expose no I2C.deinit().
            deinit = getattr(self.i2c, "deinit", None)
            if deinit is not None:
                deinit()
