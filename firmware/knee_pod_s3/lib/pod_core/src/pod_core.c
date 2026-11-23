/* Pure C core. See include/pod_core.h. No dynamic allocation, no globals. */
#include "pod_core.h"

#include <math.h>
#include <string.h>

/* ---- helpers --------------------------------------------------------------- */
static void put_u32le(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xFFu);
    p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu);
    p[3] = (uint8_t)((v >> 24) & 0xFFu);
}

static uint32_t get_u32le(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static void put_f32le(uint8_t *p, float f) {
    uint32_t bits;
    memcpy(&bits, &f, sizeof bits);
    put_u32le(p, bits);
}

static float get_f32le(const uint8_t *p) {
    uint32_t bits = get_u32le(p);
    float f;
    memcpy(&f, &bits, sizeof f);
    return f;
}

static int16_t clamp_counts(double v) {
    double r = floor(v + 0.5);
    if (r > 32767.0) return 32767;
    if (r < -32768.0) return -32768;
    return (int16_t)r;
}

/* ---- raw conversion ------------------------------------------------------- */
float pod_accel_to_ms2(int16_t counts) {
    return (float)((double)counts / POD_ACCEL_LSB_PER_G * POD_G_MS2);
}

float pod_gyro_to_rads(int16_t counts) {
    return (float)((double)counts / POD_GYRO_LSB_PER_DPS * POD_PI_D / 180.0);
}

int16_t pod_accel_to_counts(float ms2) {
    return clamp_counts((double)ms2 * POD_ACCEL_LSB_PER_G / POD_G_MS2);
}

int16_t pod_gyro_to_counts(float rads) {
    return clamp_counts((double)rads * 180.0 / POD_PI_D * POD_GYRO_LSB_PER_DPS);
}

void pod_mpu_parse_block(const uint8_t block[14], int16_t out[6]) {
    /* Register map: AX,AY,AZ (0x3B..0x40), TEMP (0x41..0x42), GX,GY,GZ (0x43..0x48). */
    out[0] = (int16_t)(((uint16_t)block[0] << 8) | block[1]);
    out[1] = (int16_t)(((uint16_t)block[2] << 8) | block[3]);
    out[2] = (int16_t)(((uint16_t)block[4] << 8) | block[5]);
    out[3] = (int16_t)(((uint16_t)block[8] << 8) | block[9]);
    out[4] = (int16_t)(((uint16_t)block[10] << 8) | block[11]);
    out[5] = (int16_t)(((uint16_t)block[12] << 8) | block[13]);
}

/* ---- packets ---------------------------------------------------------------- */
static int write_header(uint8_t *out, size_t cap, uint8_t kind, const uint8_t session[8],
                        uint32_t seq, uint32_t ticks) {
    if (session == NULL) return -1;
    if (cap < POD_HEADER_SIZE) return -1;
    out[0] = POD_WIRE_VERSION;
    out[1] = kind;
    memcpy(&out[2], session, POD_SESSION_LEN);
    put_u32le(&out[10], seq);
    put_u32le(&out[14], ticks);
    return POD_HEADER_SIZE;
}

int pod_encode_request(uint8_t *out, size_t cap, const uint8_t session[8], uint32_t seq,
                       uint32_t ticks) {
    return write_header(out, cap, POD_KIND_REQUEST, session, seq, ticks);
}

int pod_valid_sample(const float values[6]) {
    static const double accel_limit = 4.0 * 9.80665 * 1.001;
    static const double gyro_limit = 500.0 * 3.14159265358979323846 / 180.0 * 1.001;
    if (values == NULL) return 0;
    for (int i = 0; i < 6; ++i) {
        double v = (double)values[i];
        double limit = (i < 3) ? accel_limit : gyro_limit;
        if (!(fabs(v) <= limit)) return 0; /* also rejects NaN */
    }
    return 1;
}

int pod_encode_response(uint8_t *out, size_t cap, const uint8_t session[8], uint32_t seq,
                        uint32_t ticks, const float values[6]) {
    if (cap < POD_RESPONSE_SIZE || !pod_valid_sample(values)) return -1;
    if (write_header(out, cap, POD_KIND_RESPONSE, session, seq, ticks) < 0) return -1;
    for (int i = 0; i < 6; ++i) put_f32le(&out[POD_HEADER_SIZE + 4 * i], values[i]);
    return POD_RESPONSE_SIZE;
}

int pod_decode(const uint8_t *data, size_t len, pod_packet_t *out) {
    if (data == NULL || out == NULL || len < POD_HEADER_SIZE) return -1;
    if (data[0] != POD_WIRE_VERSION) return -1;
    uint8_t kind = data[1];
    if (kind == POD_KIND_REQUEST) {
        if (len != POD_REQUEST_SIZE) return -1;
    } else if (kind == POD_KIND_RESPONSE) {
        if (len != POD_RESPONSE_SIZE) return -1;
    } else {
        return -1;
    }
    out->kind = kind;
    memcpy(out->session, &data[2], POD_SESSION_LEN);
    out->seq = get_u32le(&data[10]);
    out->ticks = get_u32le(&data[14]);
    if (kind == POD_KIND_RESPONSE) {
        float v[6];
        for (int i = 0; i < 6; ++i) v[i] = get_f32le(&data[POD_HEADER_SIZE + 4 * i]);
        if (!pod_valid_sample(v)) return -1;
        memcpy(out->values, v, sizeof v);
    } else {
        memset(out->values, 0, sizeof out->values);
    }
    return 0;
}

/* ---- master pairing ---------------------------------------------------------- */
static int pending_expired(const pod_pairer_t *p, int64_t now_us) {
    return p->has_pending && (now_us - p->pending_us >= POD_PAIR_DEADLINE_US);
}

void pod_pairer_init(pod_pairer_t *p, const uint8_t session[8]) {
    memset(p, 0, sizeof *p);
    memcpy(p->session, session, POD_SESSION_LEN);
}

int pod_pairer_tick(pod_pairer_t *p, int64_t now_us, uint32_t seq, uint32_t ticks_ms,
                    const int16_t thigh[6], uint8_t extra_flags,
                    uint8_t *request_out, size_t request_cap, size_t *request_len,
                    pod_row_t *loss_row) {
    if (p->has_last && seq <= p->last_seq) return -1;
    if (p->has_pending && !pending_expired(p, now_us)) return -2;
    int len = pod_encode_request(request_out, request_cap, p->session, seq, ticks_ms);
    if (len < 0) return -1;
    if (request_len) *request_len = (size_t)len;

    int emitted = 0;
    if (p->has_pending) { /* expired: emit it as a loss row, never pair it later */
        memset(loss_row, 0, sizeof *loss_row);
        loss_row->seq = p->pending_seq;
        loss_row->t_ms = p->pending_ticks;
        loss_row->flags = (uint8_t)(p->pending_flags | POD_FLAG_LINK_LOSS);
        memcpy(loss_row->raw, p->pending_thigh, sizeof p->pending_thigh);
        p->counts.timeouts++;
        p->has_pending = 0;
        emitted = 1;
    }

    p->has_pending = 1;
    p->pending_seq = seq;
    p->pending_us = now_us;
    p->pending_ticks = ticks_ms;
    p->pending_flags = extra_flags;
    memcpy(p->pending_thigh, thigh, sizeof p->pending_thigh);
    p->has_last = 1;
    p->last_seq = seq;
    return emitted;
}

int pod_pairer_accept(pod_pairer_t *p, int sender_ok, const uint8_t *data, size_t len,
                      int64_t now_us, pod_row_t *row) {
    if (!sender_ok) {
        p->counts.wrong_sender++;
        return 0;
    }
    pod_packet_t pkt;
    if (pod_decode(data, len, &pkt) != 0) {
        p->counts.invalid++;
        return 0;
    }
    if (memcmp(pkt.session, p->session, POD_SESSION_LEN) != 0) {
        p->counts.stale_session++;
        return 0;
    }
    if (pkt.kind != POD_KIND_RESPONSE || !p->has_pending || pkt.seq != p->pending_seq) {
        p->counts.unmatched++;
        return 0;
    }
    if (pending_expired(p, now_us)) { /* late: the tick will emit the loss row */
        p->counts.unmatched++;
        return 0;
    }
    int16_t shank[6];
    for (int i = 0; i < 6; ++i) {
        shank[i] = (i < 3) ? pod_accel_to_counts(pkt.values[i])
                           : pod_gyro_to_counts(pkt.values[i]);
    }
    memset(row, 0, sizeof *row);
    row->seq = p->pending_seq;
    row->t_ms = p->pending_ticks;
    row->flags = p->pending_flags;
    memcpy(&row->raw[0], p->pending_thigh, sizeof p->pending_thigh);
    memcpy(&row->raw[6], shank, sizeof shank);
    p->has_pending = 0;
    p->counts.paired++;
    return 1;
}

void pod_pairer_note_send_error(pod_pairer_t *p) { p->counts.send_errors++; }

/* ---- slave gate -------------------------------------------------------------- */
void pod_gate_init(pod_gate_t *g) { memset(g, 0, sizeof *g); }

int pod_gate_accept(pod_gate_t *g, int sender_ok, const uint8_t *data, size_t len,
                    uint32_t *seq_out, uint32_t *ticks_out) {
    if (!sender_ok) {
        g->wrong_sender++;
        return 0;
    }
    pod_packet_t pkt;
    if (pod_decode(data, len, &pkt) != 0 || pkt.kind != POD_KIND_REQUEST) {
        g->invalid++;
        return 0;
    }
    if (g->has_session && memcmp(g->session, pkt.session, POD_SESSION_LEN) != 0) {
        g->stale_session++;
        return 0;
    }
    if (g->has_last && pkt.seq <= g->last_seq) {
        g->replay++;
        return 0;
    }
    memcpy(g->session, pkt.session, POD_SESSION_LEN);
    g->has_session = 1;
    g->has_last = 1;
    g->last_seq = pkt.seq;
    g->accepted++;
    *seq_out = pkt.seq;
    *ticks_out = pkt.ticks;
    return 1;
}

/* ---- ring -------------------------------------------------------------------- */
void pod_ring_init(pod_ring_t *r, pod_row_t *storage, uint32_t cap) {
    r->buf = storage;
    r->cap = cap;
    r->head = r->tail = r->count = r->overflow = 0;
}

int pod_ring_push(pod_ring_t *r, const pod_row_t *row) {
    if (r->cap == 0 || r->count == r->cap) {
        r->overflow++;
        return 0;
    }
    r->buf[r->head] = *row;
    r->head = (r->head + 1) % r->cap;
    r->count++;
    return 1;
}

int pod_ring_pop(pod_ring_t *r, pod_row_t *row) {
    if (r->count == 0) return 0;
    *row = r->buf[r->tail];
    r->tail = (r->tail + 1) % r->cap;
    r->count--;
    return 1;
}

/* ---- knee angle (complementary filter) ------------------------------------ */

#include <math.h>

float pod_accel_tilt_deg(float ax, float az) {
    return (float)(atan2((double)-ax, (double)az) * (180.0 / POD_PI_D));
}

void pod_tilt_init(pod_tilt_t *f, float alpha, float max_gap_s) {
    f->alpha = alpha;
    f->max_gap_s = max_gap_s;
    f->angle_deg = 0.0f;
    f->prev_t_s = 0.0f;
    f->has_prev = 0;
}

float pod_tilt_update(pod_tilt_t *f, float ax, float az, float gy_rad_s, float t_s) {
    float accel_deg = pod_accel_tilt_deg(ax, az);
    float dt = t_s - f->prev_t_s;
    if (!f->has_prev || !(dt > 0.0f) || dt > f->max_gap_s) {
        /* First sample, a non-increasing time or a gap: restart from the accelerometer. */
        f->angle_deg = accel_deg;
    } else {
        float gyro_deg_s = gy_rad_s * (float)(180.0 / POD_PI_D);
        f->angle_deg = f->alpha * (f->angle_deg + gyro_deg_s * dt) + (1.0f - f->alpha) * accel_deg;
    }
    f->prev_t_s = t_s;
    f->has_prev = 1;
    return f->angle_deg;
}

void pod_knee_zero_init(pod_knee_zero_t *z) {
    z->sum = 0.0;
    z->n = 0;
    z->zero_deg = 0.0f;
    z->zeroed = 0;
}

void pod_knee_zero_add(pod_knee_zero_t *z, float raw_flex_deg) {
    if (z->zeroed) return;
    z->sum += (double)raw_flex_deg;
    z->n++;
}

void pod_knee_zero_done(pod_knee_zero_t *z) {
    if (z->n > 0) z->zero_deg = (float)(z->sum / (double)z->n);
    z->zeroed = 1;
}

float pod_knee_flexion(const pod_knee_zero_t *z, float raw_flex_deg) {
    return raw_flex_deg - z->zero_deg;
}
