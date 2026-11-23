/* Direct-register MPU6050 driver (no Adafruit library). See README for why. */
#ifndef KP_IMU_MPU6050_H
#define KP_IMU_MPU6050_H

#include <stdbool.h>
#include <stdint.h>

#include <Wire.h>

/* Configures +/-4 g, +/-500 deg/s, DLPF 10 Hz, 50 Hz output (SMPLRT_DIV=19).
 * Returns false if WHO_AM_I is not 0x68 or any write fails. */
bool imu_init(TwoWire &wire, uint8_t address);

/* Burst-reads ACCEL_XOUT..GYRO_ZOUT (14 bytes). out[] = ax,ay,az,gx,gy,gz raw counts.
 * Returns false on a short read or NACK. */
bool imu_read(TwoWire &wire, uint8_t address, int16_t out[6]);

#endif /* KP_IMU_MPU6050_H */
