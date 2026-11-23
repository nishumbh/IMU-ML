/* Pure C core for the knee pod firmware: no Arduino, no ESP-IDF, host-testable.
 *
 * Ported from tech/iot/app/protocol.py (wire v1) and its Pairer / RequestGate
 * state machines. Only the idea is ported; MicroPython code is not.
 *
 * Wire v1, little endian:
 *   request  (18 B): version u8, kind u8, session 8 B, sequence u32, ticks u32
 *   response (42 B): the same header followed by ax,ay,az,gx,gy,gz float32
 *                    (m/s^2 and rad/s). Shank ticks are diagnostic only.
 *
 * Row storage keeps the sensor's raw int16 counts (what the .kbx IMU record
 * stores). The shank float values are converted back to counts with the exact
 * inverse of the driver scale, so nothing is re-quantised on the way.
 */
#ifndef POD_CORE_H
#define POD_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- constants ------------------------------------------------------------ */
#define POD_WIRE_VERSION 1u
#define POD_KIND_REQUEST 1u
#define POD_KIND_RESPONSE 2u
#define POD_HEADER_SIZE 18u
#define POD_REQUEST_SIZE 18u
#define POD_RESPONSE_SIZE 42u
#define POD_CHANNELS 12u       /* thigh ax,ay,az,gx,gy,gz then shank ax..gz */
#define POD_SESSION_LEN 8u
#define POD_SAMPLE_PERIOD_MS 20u /* 50 Hz */
#define POD_PAIR_DEADLINE_US 15000 /* must be shorter than the 20 ms period */

/* MPU6050 configuration: +/-4 g, +/-500 deg/s, DLPF 10 Hz, 50 Hz output. */
#define POD_ACCEL_LSB_PER_G 8192.0f
/* Spec docs/SD_LOG_FORMAT.md section 4 fixes 65.536 (datasheet typical 65.5; 0.07% apart).
 * Both the shank conversion and the .kbx header use this value so raw x lsb is self-consistent. */
#define POD_GYRO_LSB_PER_DPS 65.536f
#define POD_G_MS2 9.80665f
#define POD_PI_F 3.14159265358979f
#define POD_PI_D 3.14159265358979323846

/* Row flags */
#define POD_FLAG_LINK_LOSS 0x01u   /* shank sample missing or late */
#define POD_FLAG_THIGH_ERROR 0x02u /* local IMU read failed; thigh values are zero */
#define POD_FLAG_SHANK_ERROR 0x04u /* shank reported a sensor error */

/* ---- raw IMU conversion --------------------------------------------------- */
/* Signed 16-bit counts -> SI units, and back (round to nearest). */
float pod_accel_to_ms2(int16_t counts);
float pod_gyro_to_rads(int16_t counts);
int16_t pod_accel_to_counts(float ms2);
int16_t pod_gyro_to_counts(float rads);

/* Big-endian MPU6050 register block (0x3B..0x48, 14 bytes) -> six raw counts
 * ax,ay,az,gx,gy,gz. The temperature word is skipped. */
void pod_mpu_parse_block(const uint8_t block[14], int16_t out[6]);

/* ---- packets -------------------------------------------------------------- */
typedef struct {
    uint8_t kind;
    uint8_t session[POD_SESSION_LEN];
    uint32_t seq;
    uint32_t ticks;
    float values[6]; /* valid only for responses */
} pod_packet_t;

/* Encoders return the number of bytes written, or -1 on invalid input. */
int pod_encode_request(uint8_t *out, size_t cap, const uint8_t session[8],
                       uint32_t seq, uint32_t ticks);
int pod_encode_response(uint8_t *out, size_t cap, const uint8_t session[8],
                        uint32_t seq, uint32_t ticks, const float values[6]);

/* Strict decode: version, kind, exact length and value ranges are checked.
 * Returns 0 on success, -1 otherwise. */
int pod_decode(const uint8_t *data, size_t len, pod_packet_t *out);

/* Range check used by decode and by the shank before it sends. */
int pod_valid_sample(const float values[6]);

/* ---- master pairing (thigh) ----------------------------------------------- */
typedef struct {
    uint32_t timeouts;
    uint32_t paired;
    uint32_t wrong_sender;
    uint32_t invalid;
    uint32_t stale_session;
    uint32_t unmatched;
    uint32_t send_errors;
} pod_pair_counts_t;

typedef struct {
    uint8_t session[POD_SESSION_LEN];
    int has_pending;
    uint32_t pending_seq;
    int64_t pending_us;
    uint32_t pending_ticks;
    int16_t pending_thigh[6];
    uint8_t pending_flags;
    int has_last;
    uint32_t last_seq;
    pod_pair_counts_t counts;
} pod_pairer_t;

/* One merged row: thigh counts (raw) and shank counts (raw). */
typedef struct {
    uint32_t seq;
    uint32_t t_ms;     /* thigh tick, ms since session start (diagnostic) */
    uint8_t flags;     /* POD_FLAG_* */
    uint8_t pad;
    int16_t raw[POD_CHANNELS];
} pod_row_t;

void pod_pairer_init(pod_pairer_t *p, const uint8_t session[8]);

/* Called once per 50 Hz tick. Order of checks:
 *  - seq must be strictly greater than the last one (else returns -1);
 *  - the previous request must have expired (else returns -2, nothing changes);
 *  - the request is encoded into request_out (*request_len set);
 *  - an expired previous request is emitted as a loss row (returns 1).
 * Returns 0 when no row was emitted. extra_flags carries local IMU errors. */
int pod_pairer_tick(pod_pairer_t *p, int64_t now_us, uint32_t seq, uint32_t ticks_ms,
                    const int16_t thigh[6], uint8_t extra_flags,
                    uint8_t *request_out, size_t request_cap, size_t *request_len,
                    pod_row_t *loss_row);

/* Process a received packet. sender_ok is the MAC check done by the caller.
 * Returns 1 and fills *row when a response matches the pending request and
 * arrived before its deadline. A late match is rejected (counted as unmatched);
 * the tick emits it as a loss row instead. */
int pod_pairer_accept(pod_pairer_t *p, int sender_ok, const uint8_t *data, size_t len,
                      int64_t now_us, pod_row_t *row);

/* Count a request that the radio did not accept. */
void pod_pairer_note_send_error(pod_pairer_t *p);

/* ---- slave request gate (shank) ------------------------------------------- */
typedef struct {
    uint8_t session[POD_SESSION_LEN];
    int has_session;
    int has_last;
    uint32_t last_seq;
    uint32_t accepted, wrong_sender, invalid, stale_session, replay;
} pod_gate_t;

void pod_gate_init(pod_gate_t *g);

/* Pins the first session until the shank restarts; accepts strictly increasing
 * sequence numbers. Returns 1 and fills session/seq on acceptance. */
int pod_gate_accept(pod_gate_t *g, int sender_ok, const uint8_t *data, size_t len,
                    uint32_t *seq_out, uint32_t *ticks_out);

/* ---- fixed ring of rows (PSRAM storage supplied by the caller) ------------ */
typedef struct {
    pod_row_t *buf;
    uint32_t cap;
    uint32_t head; /* next write */
    uint32_t tail; /* next read */
    uint32_t count;
    uint32_t overflow; /* rows dropped because the ring was full */
} pod_ring_t;

void pod_ring_init(pod_ring_t *r, pod_row_t *storage, uint32_t cap);
/* Returns 1 when stored, 0 when full (the row is dropped and counted). */
int pod_ring_push(pod_ring_t *r, const pod_row_t *row);
/* Returns 1 and copies the oldest row out, 0 when empty. */
int pod_ring_pop(pod_ring_t *r, pod_row_t *row);

/* ---- knee angle (complementary filter) ------------------------------------ */
/* Same convention as knee_rehab.knee_angle: tilt = atan2(-ax, az) in degrees,
 * gyro rate about +Y in rad/s equals the derivative of that tilt. Verified by
 * the host test against exact rigid-body motion. */
float pod_accel_tilt_deg(float ax, float az);

typedef struct {
    float alpha;      /* gyro weight, 0.98 by default */
    float max_gap_s;  /* longer gaps restart from the accelerometer */
    float angle_deg;  /* current filtered tilt */
    float prev_t_s;
    int has_prev;
} pod_tilt_t;

void pod_tilt_init(pod_tilt_t *f, float alpha, float max_gap_s);
/* Returns the filtered tilt (degrees) for one sample. t_s is seconds, monotonic. */
float pod_tilt_update(pod_tilt_t *f, float ax, float az, float gy_rad_s, float t_s);

/* Knee flexion = thigh tilt - shank tilt, zeroed by a standing reference: the
 * caller feeds the first standing samples through pod_knee_zero_add and then
 * calls pod_knee_zero_done. Flexion before zeroing is reported raw. */
typedef struct {
    double sum;
    uint32_t n;
    float zero_deg;
    int zeroed;
} pod_knee_zero_t;

void pod_knee_zero_init(pod_knee_zero_t *z);
void pod_knee_zero_add(pod_knee_zero_t *z, float raw_flex_deg);
void pod_knee_zero_done(pod_knee_zero_t *z);
float pod_knee_flexion(const pod_knee_zero_t *z, float raw_flex_deg);

#ifdef __cplusplus
}
#endif

#endif /* POD_CORE_H */
