"""MPU6050 at AD0=0: +/-4g, +/-500 deg/s, DLPF 44/42 Hz, 50 Hz ODR."""
import math
import struct

from .protocol import valid_sample


class MPU6050:
    def __init__(self, i2c, address=104, sleep_ms=None):
        if sleep_ms is None:
            from time import sleep_ms
        self.i2c = i2c
        self.address = address
        if self._read(0x75, 1)[0] != 0x68:
            raise ValueError("MPU6050 WHO_AM_I mismatch")
        self._write(0x6B, 0x80)  # reset
        sleep_ms(100)
        self._write(0x6B, 0x01)  # awake, PLL X gyro
        sleep_ms(30)
        self._write(0x6C, 0x00)  # all axes enabled
        self._write(0x1A, 0x03)  # gyro output base rate = 1 kHz
        self._write(0x19, 19)    # 1000/(19+1) = 50 Hz
        self._write(0x1B, 0x08)  # FS_SEL=1, 65.5 LSB/(deg/s)
        self._write(0x1C, 0x08)  # AFS_SEL=1, 8192 LSB/g
        sleep_ms(50)
        for reg, expected in ((0x6B, 1), (0x6C, 0), (0x1A, 3),
                              (0x19, 19), (0x1B, 8), (0x1C, 8)):
            if self._read(reg, 1)[0] != expected:
                raise ValueError("MPU6050 configuration readback failed")

    def _read(self, register, length):
        data = self.i2c.readfrom_mem(self.address, register, length)
        if len(data) != length:
            raise OSError("short MPU6050 read")
        return data

    def _write(self, register, value):
        self.i2c.writeto_mem(self.address, register, bytes((value,)))

    def read(self):
        ax, ay, az, temperature, gx, gy, gz = struct.unpack(
            ">7h", self._read(0x3B, 14))
        accel = 9.80665 / 8192
        gyro = math.pi / (180 * 65.5)
        return valid_sample((ax * accel, ay * accel, az * accel,
                             gx * gyro, gy * gyro, gz * gyro))

    def close(self):
        self._write(0x6B, 0x40)  # sensor sleep; I2C lifetime belongs to caller
