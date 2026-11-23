/* Pin map for LOLIN S3 Mini (ESP32-S3FH4R2: 4 MB in-package flash, 2 MB PSRAM).
 *
 * Avoided on purpose:
 *   GPIO0, GPIO3, GPIO45, GPIO46  strapping pins (boot mode, VDD_SPI, log level)
 *   GPIO19, GPIO20                 native USB D-/D+ (serial and flashing)
 *   GPIO26..GPIO37                 in-package flash/PSRAM bus, not broken out
 *   GPIO39..GPIO42                 JTAG; kept free so debugging stays possible
 *
 * ADC: only ADC1 (GPIO1..GPIO10) is used, because ADC2 cannot be read while
 * Wi-Fi/ESP-NOW is active.
 *
 * Verify every choice against the LOLIN S3 Mini silkscreen and schematic
 * before soldering; the GPIO assignments below are the ones this firmware uses.
 */
#ifndef KNEE_POD_PINS_H
#define KNEE_POD_PINS_H

/* MPU6050 (GY-521) on I2C. AD0 is strapped LOW on thigh (0x68), HIGH on shank (0x69).
 * External 4.7 k pull-ups to 3V3 are on most GY-521 boards; do not add more. */
#define PIN_I2C_SDA 8
#define PIN_I2C_SCL 9

/* microSD on SPI (FSPI). Thigh pod only. 3.3 V card module, no level shifter. */
#define PIN_SD_MOSI 11
#define PIN_SD_SCK 12
#define PIN_SD_MISO 13
#define PIN_SD_CS 10

/* Tactile button to GND, internal pull-up. Thigh pod only. */
#define PIN_BUTTON 4

/* Battery sense: BAT+ -> 100 k -> ADC node -> 100 k -> GND (halves 4.2 V to 2.1 V).
 * ADC1_CH4. The divider is the only thing on this node. */
#define PIN_BATTERY_ADC 5
#define BATTERY_DIVIDER_RATIO 2.0f

/* On-board WS2812 RGB LED (LOLIN S3 Mini: GPIO47), driven with rgbLedWrite(). */
#define PIN_RGB_LED 47

#endif /* KNEE_POD_PINS_H */
