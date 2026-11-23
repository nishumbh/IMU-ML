/* Host test for lib/pod_core. Build and run with test/host/run.sh.
 * Checks byte parity with the Python protocol (vectors.h, generated), the
 * master pairing and loss counting, the slave request gate, the row ring and
 * the MPU6050 count <-> SI conversion. No hardware is involved. */
#include <stdio.h>
#include <string.h>

#include "pod_core.h"
#include "vectors.h"

static const uint8_t OTHER_SESSION[8] = {0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA, 0xAA};

static int failures = 0;
static int checks = 0;

#define CHECK(cond)                                                            \
    do {                                                                       \
        checks++;                                                              \
        if (!(cond)) {                                                         \
            failures++;                                                        \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);    \
        }                                                                      \
    } while (0)

#define PEER_OK 1
#define PEER_BAD 0

/* Pack the Python SHANK float32 bit patterns into SI values. */
static void shank_values(float out[6]) {
    for (int i = 0; i < 6; ++i) {
        uint32_t bits = V_SHANK_BITS[i];
        memcpy(&out[i], &bits, sizeof bits);
    }
}

static void test_conversions(void) {
    /* Every int16 count must survive counts -> SI -> counts. */
    int bad = 0;
    for (int c = -32768; c <= 32767; ++c) {
        if (pod_accel_to_counts(pod_accel_to_ms2((int16_t)c)) != (int16_t)c) bad++;
        if (pod_gyro_to_counts(pod_gyro_to_rads((int16_t)c)) != (int16_t)c) bad++;
    }
    CHECK(bad == 0);
    /* 1 g on the +/-4 g range is 8192 counts = 9.80665 m/s^2. */
    CHECK(pod_accel_to_counts(9.80665f) == 8192);
}

static void test_mpu_block(void) {
    /* ax=0x1234, ay=0xFF00 (-256), az=0x0001, temp ignored, gx=0x7FFF, gy=0x8000, gz=0 */
    const uint8_t block[14] = {0x12, 0x34, 0xFF, 0x00, 0x00, 0x01, 0x11, 0x22,
                               0x7F, 0xFF, 0x80, 0x00, 0x00, 0x00};
    int16_t out[6];
    pod_mpu_parse_block(block, out);
    CHECK(out[0] == 0x1234);
    CHECK(out[1] == -256);
    CHECK(out[2] == 1);
    CHECK(out[3] == 0x7FFF);
    CHECK(out[4] == -32768);
    CHECK(out[5] == 0);
}

static void test_packets_match_python(void) {
    uint8_t buf[64];
    int n = pod_encode_request(buf, sizeof buf, V_SESSION, 5, 100);
    CHECK(n == 18);
    CHECK(memcmp(buf, V_REQ_SEQ5, 18) == 0);

    float v[6];
    shank_values(v);
    n = pod_encode_response(buf, sizeof buf, V_SESSION, 7, 140, v);
    CHECK(n == 42);
    CHECK(memcmp(buf, V_RESP_SEQ7, 42) == 0);

    pod_packet_t pkt;
    CHECK(pod_decode(V_RESP_SEQ7, 42, &pkt) == 0);
    CHECK(pkt.kind == POD_KIND_RESPONSE && pkt.seq == 7 && pkt.ticks == 140);
    CHECK(memcmp(pkt.session, V_SESSION, 8) == 0);

    /* Strictness: wrong version, wrong length, NaN, out-of-range value. */
    uint8_t bad[42];
    memcpy(bad, V_RESP_SEQ7, 42);
    bad[0] = 2;
    CHECK(pod_decode(bad, 42, &pkt) != 0);
    CHECK(pod_decode(V_RESP_SEQ7, 41, &pkt) != 0);
    CHECK(pod_decode(V_REQ_SEQ5, 42, &pkt) != 0);
    memcpy(bad, V_RESP_SEQ7, 42);
    bad[0] = 1;
    bad[18] = bad[19] = bad[20] = bad[21] = 0xFF; /* NaN in ax */
    CHECK(pod_decode(bad, 42, &pkt) != 0);
    float big = 40.0f; /* > 4 g */
    CHECK(pod_valid_sample((float[6]){big, 0, 0, 0, 0, 0}) == 0);
    CHECK(pod_valid_sample((float[6]){3.9f * 9.80665f, 0, 0, 0, 0, 0}) == 1);
    CHECK(pod_encode_request(buf, 17, V_SESSION, 1, 1) < 0);
}

/* Build a response from the shank values; sess may differ to test rejection. */
static int make_resp(uint8_t *buf, const uint8_t sess[8], uint32_t seq, uint32_t ticks) {
    float v[6];
    shank_values(v);
    return pod_encode_response(buf, 64, sess, seq, ticks, v);
}

static void test_pairing_scenario(void) {
    static const int16_t thigh[6] = {1000, -2000, 8192, 10, -20, 30};
    pod_pairer_t p;
    pod_pairer_init(&p, V_SESSION);
    pod_ring_t ring;
    static pod_row_t storage[16];
    pod_ring_init(&ring, storage, 16);

    uint8_t req[18], pkt[64];
    size_t reqlen = 0;
    pod_row_t row;
    int64_t t;
    int rc;

#define TICK(ms, s)                                                                 \
    do {                                                                            \
        t = (int64_t)(ms) * 1000;                                                   \
        rc = pod_pairer_tick(&p, t, (s), (uint32_t)(ms), thigh, 0, req, sizeof req, \
                             &reqlen, &row);                                        \
        CHECK(rc >= 0);                                                             \
        if (rc == 1) pod_ring_push(&ring, &row);                                    \
    } while (0)

#define RX(ms, ok, len)                                                             \
    do {                                                                            \
        t = (int64_t)(ms) * 1000;                                                   \
        if (pod_pairer_accept(&p, (ok), pkt, (len), t, &row) == 1)                  \
            pod_ring_push(&ring, &row);                                             \
    } while (0)

    /* Mirrors STEPS in gen_vectors.py. */
    TICK(0, 0);
    int n = make_resp(pkt, V_SESSION, 0, 0);
    RX(5, PEER_OK, n);
    TICK(20, 1);
    TICK(40, 2);                       /* seq 1 expires */
    n = make_resp(pkt, V_SESSION, 1, 20);
    RX(45, PEER_OK, n);                /* late and unmatched */
    n = make_resp(pkt, V_SESSION, 2, 40);
    RX(50, PEER_OK, n);                /* paired */
    TICK(60, 3);
    n = make_resp(pkt, V_SESSION, 3, 60);
    RX(80, PEER_OK, n);                /* late: unmatched */
    TICK(100, 4);                      /* seq 3 expires */
    n = make_resp(pkt, V_SESSION, 4, 80);
    RX(103, PEER_BAD, n);              /* wrong sender */
    memcpy(pkt, "\x01\x02\x03", 3);
    RX(104, PEER_OK, 3);               /* invalid: 3 bytes */
    n = make_resp(pkt, OTHER_SESSION, 4, 80);
    RX(106, PEER_OK, n);               /* stale session */
    n = make_resp(pkt, V_SESSION, 4, 80);
    RX(110, PEER_OK, n);               /* paired */

    CHECK(p.counts.paired == PY_PAIR_PAIRED);
    CHECK(p.counts.timeouts == PY_PAIR_TIMEOUT);
    CHECK(p.counts.wrong_sender == PY_PAIR_WRONG_SENDER);
    CHECK(p.counts.invalid == PY_PAIR_INVALID);
    CHECK(p.counts.stale_session == PY_PAIR_STALE_SESSION);
    CHECK(p.counts.unmatched == PY_PAIR_UNMATCHED);

    /* Rows: 3 paired (seq 0, 2, 4) and 2 loss rows (seq 1, 3), in tick order. */
    CHECK(ring.count == 5);
    pod_row_t r;
    int idx = 0;
    uint32_t expect_seq[5] = {0, 1, 2, 3, 4};
    uint8_t expect_loss[5] = {0, 1, 0, 1, 0};
    while (pod_ring_pop(&ring, &r)) {
        CHECK(r.seq == expect_seq[idx]);
        CHECK((r.flags & POD_FLAG_LINK_LOSS) == (expect_loss[idx] ? POD_FLAG_LINK_LOSS : 0));
        if (!expect_loss[idx]) {
            CHECK(memcmp(&r.raw[0], thigh, sizeof thigh) == 0);
            CHECK(memcmp(&r.raw[6], V_SHANK_COUNTS, sizeof V_SHANK_COUNTS) == 0);
        }
        idx++;
    }
    CHECK(idx == 5);

    /* The request for the last tick is the Python-compatible encoding. */
    CHECK(reqlen == 18);
#undef TICK
#undef RX
}

static void test_sequence_must_increase(void) {
    static const int16_t thigh[6] = {0};
    pod_pairer_t p;
    pod_pairer_init(&p, V_SESSION);
    uint8_t req[18];
    size_t len = 0;
    pod_row_t row;
    CHECK(pod_pairer_tick(&p, 0, 5, 0, thigh, 0, req, sizeof req, &len, &row) == 0);
    CHECK(pod_pairer_tick(&p, 20000, 5, 20, thigh, 0, req, sizeof req, &len, &row) < 0);
    CHECK(pod_pairer_tick(&p, 20000, 6, 20, thigh, 0, req, sizeof req, &len, &row) == 1);
    /* Pending seq 6 is not expired at 25 ms, so a new tick is refused. */
    CHECK(pod_pairer_tick(&p, 25000, 7, 25, thigh, 0, req, sizeof req, &len, &row) == -2);
}

static void test_gate_scenario(void) {
    pod_gate_t g;
    pod_gate_init(&g);
    uint8_t req[18];
    uint32_t seq, ticks;
    int n;
#define REQ(seq_v, sess) \
    (n = pod_encode_request(req, sizeof req, (sess), (seq_v), (seq_v) * 20), n > 0)
    CHECK(REQ(5, V_SESSION) && pod_gate_accept(&g, 1, req, 18, &seq, &ticks) == 1);
    CHECK(REQ(5, V_SESSION) && pod_gate_accept(&g, 1, req, 18, &seq, &ticks) == 0); /* replay */
    CHECK(REQ(6, OTHER_SESSION) &&
          pod_gate_accept(&g, 1, req, 18, &seq, &ticks) == 0);                      /* stale */
    CHECK(REQ(7, V_SESSION) && pod_gate_accept(&g, 0, req, 18, &seq, &ticks) == 0); /* sender */
    CHECK(pod_gate_accept(&g, 1, req, 2, &seq, &ticks) == 0);                        /* length */
    float v[6];
    shank_values(v);
    uint8_t resp[64];
    n = pod_encode_response(resp, sizeof resp, V_SESSION, 9, 0, v);
    CHECK(n == 42 && pod_gate_accept(&g, 1, resp, 42, &seq, &ticks) == 0);           /* kind */
    CHECK(REQ(7, V_SESSION) && pod_gate_accept(&g, 1, req, 18, &seq, &ticks) == 1);
    CHECK(g.accepted == PY_GATE_ACCEPTED);
    CHECK(g.wrong_sender == PY_GATE_WRONG_SENDER);
    CHECK(g.invalid == PY_GATE_INVALID);
    CHECK(g.stale_session == PY_GATE_STALE_SESSION);
    CHECK(g.replay == PY_GATE_REPLAY);
#undef REQ
}

static void test_ring(void) {
    static pod_row_t storage[3];
    pod_ring_t r;
    pod_ring_init(&r, storage, 3);
    pod_row_t row;
    memset(&row, 0, sizeof row);
    for (uint32_t i = 0; i < 4; ++i) {
        row.seq = i;
        int ok = pod_ring_push(&r, &row);
        CHECK(ok == (i < 3));
    }
    CHECK(r.overflow == 1);
    pod_row_t out;
    CHECK(pod_ring_pop(&r, &out) && out.seq == 0);
    row.seq = 9;
    CHECK(pod_ring_push(&r, &row) == 1); /* wrap-around */
    CHECK(pod_ring_pop(&r, &out) && out.seq == 1);
    CHECK(pod_ring_pop(&r, &out) && out.seq == 2);
    CHECK(pod_ring_pop(&r, &out) && out.seq == 9);
    CHECK(!pod_ring_pop(&r, &out));
}


/* ---- knee angle ----------------------------------------------------------- */
#include <math.h>

#define KA_FS 50.0
#define KA_G 9.80665
#define KA_PI 3.14159265358979323846

/* Exact sagittal motion about +Y: gravity components in the sensor frame and the
 * gyro rate. Derived from R^T R' (rigid-body kinematics), so no hand-made signs. */
static void ka_sensor(double theta_deg, double theta_dot_deg_s, float *ax, float *az, float *gy) {
    double th = theta_deg * KA_PI / 180.0;
    *ax = (float)(-KA_G * sin(th));
    *az = (float)(KA_G * cos(th));
    *gy = (float)(theta_dot_deg_s * KA_PI / 180.0);
}

static void test_knee_angle(void) {
    /* Tilt sign: a segment rotated forward (ax negative) reads a positive tilt. */
    float ax = 0, az = 0, gy = 0;
    ka_sensor(25.0, 0.0, &ax, &az, &gy);
    CHECK(fabsf(pod_accel_tilt_deg(ax, az) - 25.0f) < 1e-3f);

    /* Gyro-driven filter tracks the true angle through a slow sinusoid. */
    pod_tilt_t f;
    pod_tilt_init(&f, 0.98f, 0.1f);
    double max_err = 0.0;
    for (int i = 0; i < (int)(10 * KA_FS); ++i) {
        double t = i / KA_FS;
        double th = 20.0 * sin(2.0 * KA_PI * 0.2 * t);
        double thd = 20.0 * 2.0 * KA_PI * 0.2 * cos(2.0 * KA_PI * 0.2 * t);
        ka_sensor(th, thd, &ax, &az, &gy);
        float est = pod_tilt_update(&f, ax, az, gy, (float)t);
        if (i > 10) {
            double e = fabs((double)est - th);
            if (e > max_err) max_err = e;
        }
    }
    CHECK(max_err < 1.0);

    /* A gap longer than max_gap_s restarts from the accelerometer (no stale integral). */
    pod_tilt_init(&f, 0.98f, 0.1f);
    ka_sensor(10.0, 0.0, &ax, &az, &gy);
    pod_tilt_update(&f, ax, az, 0.0f, 0.0f);
    ka_sensor(-30.0, 0.0, &ax, &az, &gy);
    float after_gap = pod_tilt_update(&f, ax, az, 500.0f, 5.0f);
    CHECK(fabsf(after_gap + 30.0f) < 0.5f);

    /* Flexion = thigh - shank, zeroed on a standing reference window. */
    pod_knee_zero_t z;
    pod_knee_zero_init(&z);
    for (int i = 0; i < 100; ++i) pod_knee_zero_add(&z, 7.0f); /* standing offset of 7 deg */
    pod_knee_zero_done(&z);
    CHECK(fabsf(pod_knee_flexion(&z, 7.0f)) < 1e-4f);
    CHECK(fabsf(pod_knee_flexion(&z, 27.0f) - 20.0f) < 1e-4f);
}

int main(void) {
    test_conversions();
    test_mpu_block();
    test_packets_match_python();
    test_pairing_scenario();
    test_sequence_must_increase();
    test_gate_scenario();
    test_ring();
    test_knee_angle();
    printf("host tests: %d checks, %d failures\n", checks, failures);
    return failures == 0 ? 0 : 1;
}
