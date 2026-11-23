/* Firmware-wide constants. Protocol numbers live in lib/pod_core/include/pod_core.h. */
#ifndef KP_CONFIG_H
#define KP_CONFIG_H

#include <stdint.h>

#include "pod_core.h"

/* Sampling: 50 Hz, one sequence number per 20 ms tick, shared by both pods. */
#define KP_TICK_US 20000
#define KP_TICK_MS 20

/* Inference window: 2 s x 50 Hz = 100 rows x 12 channels, 1 s hop (50%). */
#define KP_WINDOW_ROWS 100
#define KP_HOP_ROWS 50
#define KP_CHANNELS POD_CHANNELS

/* PSRAM ring of merged rows: 8192 rows x 36 B = 288 KB, about 164 s at 50 Hz. */
#define KP_RING_ROWS 8192

/* Writer drains the ring in batches; a chunk is about one second of rows. */
#define KP_WRITER_BATCH_ROWS 50
#define KP_WRITER_PERIOD_MS 100

/* Shank: no response if the latest IMU sample is older than this. */
#define KP_SHANK_STALE_US 25000

/* Exercises use the codes of docs/SD_LOG_FORMAT.md section 7.2 (0 = none).
 * The button cycles squat -> extension -> gait -> squat. The model header says
 * which exercise it was trained for; other exercises log "no model" and never run. */
enum kp_exercise { KP_EX_NONE = 0, KP_EX_SQUAT = 1, KP_EX_EXTENSION = 2, KP_EX_GAIT = 3 };
#define KP_EXERCISE_CYCLE_COUNT 3
static const uint8_t KP_EXERCISE_CYCLE[KP_EXERCISE_CYCLE_COUNT] = {KP_EX_SQUAT, KP_EX_EXTENSION,
                                                                  KP_EX_GAIT};
static const char *const KP_EXERCISE_NAMES[4] = {"none", "squat", "extension", "gait"};

/* Button timing. */
#define KP_BUTTON_DEBOUNCE_MS 30
#define KP_BUTTON_LONG_MS 1500

/* Battery: thresholds on BAT+ volts. Brownout of the on-board LDO is about 3.4 V. */
#define KP_BATTERY_WARN_V 3.5f
#define KP_BATTERY_CRIT_V 3.4f
#define KP_BATTERY_PERIOD_MS 5000

/* SD card SPI clock. */
#define KP_SD_HZ 4000000

#endif /* KP_CONFIG_H */
