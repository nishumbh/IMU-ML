#include "imu_mpu6050.h"

#include <Arduino.h>

#include "pod_core.h"

namespace {
constexpr uint8_t REG_SMPLRT_DIV = 0x19;
constexpr uint8_t REG_CONFIG = 0x1A;
constexpr uint8_t REG_GYRO_CONFIG = 0x1B;
constexpr uint8_t REG_ACCEL_CONFIG = 0x1C;
constexpr uint8_t REG_ACCEL_XOUT_H = 0x3B;
constexpr uint8_t REG_PWR_MGMT_1 = 0x6B;
constexpr uint8_t REG_WHO_AM_I = 0x75;
constexpr uint8_t WHO_AM_I_VALUE = 0x68;

bool write_reg(TwoWire &wire, uint8_t address, uint8_t reg, uint8_t value) {
    wire.beginTransmission(address);
    wire.write(reg);
    wire.write(value);
    return wire.endTransmission() == 0;
}

bool read_regs(TwoWire &wire, uint8_t address, uint8_t reg, uint8_t *buf, size_t n) {
    wire.beginTransmission(address);
    wire.write(reg);
    if (wire.endTransmission(false) != 0) return false;
    if (wire.requestFrom(address, n, (bool)true) != n) return false;
    for (size_t i = 0; i < n; ++i) buf[i] = (uint8_t)wire.read();
    return true;
}
}  // namespace

bool imu_init(TwoWire &wire, uint8_t address) {
    uint8_t who = 0;
    if (!read_regs(wire, address, REG_WHO_AM_I, &who, 1) || who != WHO_AM_I_VALUE) return false;
    if (!write_reg(wire, address, REG_PWR_MGMT_1, 0x01)) return false; /* PLL, X gyro clock */
    delay(10);
    bool ok = write_reg(wire, address, REG_SMPLRT_DIV, 19)  /* 1 kHz / (1+19) = 50 Hz */
           && write_reg(wire, address, REG_CONFIG, 0x05)    /* DLPF_CFG=5: 10 Hz */
           && write_reg(wire, address, REG_GYRO_CONFIG, 0x08)  /* FS_SEL=1: +/-500 deg/s */
           && write_reg(wire, address, REG_ACCEL_CONFIG, 0x08); /* AFS_SEL=1: +/-4 g */
    return ok;
}

bool imu_read(TwoWire &wire, uint8_t address, int16_t out[6]) {
    uint8_t block[14];
    if (!read_regs(wire, address, REG_ACCEL_XOUT_H, block, sizeof block)) return false;
    pod_mpu_parse_block(block, out);
    return true;
}
