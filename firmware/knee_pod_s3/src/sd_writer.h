/* KBX1 session writer (docs/SD_LOG_FORMAT.md). Called only from the writer task,
 * never from the sampling path. Buffers live in PSRAM. */
#ifndef KP_SD_WRITER_H
#define KP_SD_WRITER_H

#include <stdbool.h>
#include <stdint.h>

#include "pod_config.h"
#include "pod_core.h"

#define KBX_EVENT_START 0
#define KBX_EVENT_STOP 1
#define KBX_EVENT_EXERCISE_CHANGE 2
#define KBX_EVENT_LOW_BATTERY 3

/* Mounts the SD card on SPI at KP_SD_HZ and allocates PSRAM buffers. */
bool sdw_mount(void);

/* Creates /KB/S<NNNN>.kbx with exclusive semantics (never overwrites), writes the
 * 96-byte header. Session number = highest existing S<NNNN>.kbx plus one (the SD
 * directory is the source of truth; nothing is kept in NVS). */
bool sdw_open(const kp_config_t &cfg, uint32_t *session_out);

/* IMU seq of the first row of the session (spec: seq counts from 0 at session start). */
void sdw_set_base(uint32_t base_seq);

/* Records; each call only appends to the plaintext buffer. Chunks flush at 50 IMU
 * records, at 32 KiB, or on sdw_close(). */
void sdw_imu(const pod_row_t &row);
void sdw_event(uint8_t kind, uint16_t battery_mv, uint32_t payload);
void sdw_infer(uint32_t window_seq, uint8_t exercise, const float p[3], uint8_t label);

/* Flushes the last chunk, fsyncs and closes. Returns false if any write failed. */
bool sdw_close(void);

bool sdw_is_open(void);
bool sdw_failed(void);
uint32_t sdw_imu_written(void);
/* Last IMU seq written + 1 (the STOP payload per spec 7.3); 0 if none. */
uint32_t sdw_next_seq(void);

#endif /* KP_SD_WRITER_H */
