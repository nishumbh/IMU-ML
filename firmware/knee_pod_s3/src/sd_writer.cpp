#include "sd_writer.h"

#include <Arduino.h>
#include <SD.h>
#include <SPI.h>
#include <esp_heap_caps.h>
#include <esp_random.h>
#include <mbedtls/gcm.h>
#include <stdio.h>
#include <string.h>

#include "config.h"
#include "pins.h"
#include "pod_core.h"

namespace {
constexpr uint32_t HEADER_LEN = 96;
constexpr uint32_t PBKDF2_ITERATIONS = 600000;  /* documentation only; the PC derives the key */
constexpr uint32_t PLAINTEXT_CAP = 32 * 1024;   /* spec 5: flush at 32 KiB */
constexpr uint32_t CHUNK_IMU_RECORDS = 50; /* spec 5: about one second at 50 Hz */
constexpr uint32_t OUT_CAP = 4 + 12 + PLAINTEXT_CAP + 16;
constexpr uint8_t REC_IMU = 0x01, REC_INFER = 0x02, REC_EVENT = 0x03;
constexpr uint16_t IMU_BODY = 29, INFER_BODY = 18, EVENT_BODY = 7;
constexpr float SAMPLE_RATE_HZ = 50.0f;

struct writer_state {
    bool mounted = false;
    bool open = false;
    bool failed = false;
    File file;
    uint8_t header[HEADER_LEN];
    uint8_t nonce_prefix[4];
    uint64_t counter = 0;
    uint8_t *pt = nullptr;      /* PSRAM */
    uint32_t pt_len = 0;
    uint32_t imu_in_chunk = 0;
    uint8_t *out = nullptr;     /* PSRAM: [ct_len][nonce][ciphertext][tag] */
    mbedtls_gcm_context gcm;
    bool gcm_ready = false;
    uint32_t base_seq = 0;
    uint32_t last_seq = 0;      /* last IMU seq written, for the STOP payload */
    uint32_t imu_written = 0;
} W;

void put_u16(uint8_t *p, uint16_t v) { p[0] = v & 0xFF; p[1] = v >> 8; }
void put_u32(uint8_t *p, uint32_t v) {
    for (int i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8 * i));
}
void put_u64(uint8_t *p, uint64_t v) {
    for (int i = 0; i < 8; ++i) p[i] = (uint8_t)(v >> (8 * i));
}
void put_f32(uint8_t *p, float f) {
    uint32_t bits;
    memcpy(&bits, &f, 4);
    put_u32(p, bits);
}

/* Encrypts the plaintext buffer as one chunk and writes it with one write call. */
bool flush_chunk(void) {
    if (W.pt_len == 0) return true; /* never write an empty chunk */
    uint8_t aad[HEADER_LEN + 8];
    memcpy(aad, W.header, HEADER_LEN);
    put_u64(aad + HEADER_LEN, W.counter);

    uint8_t nonce[12];
    memcpy(nonce, W.nonce_prefix, 4);
    put_u64(nonce + 4, W.counter);

    uint8_t *ct = W.out + 16;
    uint8_t *tag = ct + W.pt_len;
    if (mbedtls_gcm_crypt_and_tag(&W.gcm, MBEDTLS_GCM_ENCRYPT, W.pt_len, nonce, sizeof nonce,
                                  aad, sizeof aad, W.pt, ct, 16, tag) != 0) {
        return false;
    }
    put_u32(W.out, W.pt_len);
    memcpy(W.out + 4, nonce, sizeof nonce);
    size_t total = 16 + (size_t)W.pt_len + 16;
    size_t wrote = W.file.write(W.out, total);
    if (wrote != total) return false;
    W.file.flush(); /* spec 5: one fsync per chunk */
    W.counter++;
    W.pt_len = 0;
    W.imu_in_chunk = 0;
    return true;
}

void append(uint8_t type, const uint8_t *body, uint16_t body_len) {
    if (!W.open || W.failed) return;
    uint32_t need = 3 + (uint32_t)body_len;
    if (W.pt_len + need > PLAINTEXT_CAP && !flush_chunk()) {
        W.failed = true;
        return;
    }
    uint8_t *p = W.pt + W.pt_len;
    p[0] = type;
    put_u16(p + 1, body_len);
    memcpy(p + 3, body, body_len);
    W.pt_len += need;
}

bool file_name_session(const char *name, uint32_t *n) {
    /* Accepts "S0001.kbx" with or without a directory prefix. */
    const char *base = strrchr(name, '/');
    base = base ? base + 1 : name;
    unsigned int v = 0;
    char tail[8] = {0};
    if (sscanf(base, "S%4u%7s", &v, tail) != 2) return false;
    if (strcmp(tail, ".kbx") != 0 || v > 9999) return false;
    *n = v;
    return true;
}

uint32_t next_session_number(void) {
    uint32_t highest = 0;
    File dir = SD.open("/KB");
    if (dir && dir.isDirectory()) {
        File f = dir.openNextFile();
        while (f) {
            uint32_t n;
            if (!f.isDirectory() && file_name_session(f.name(), &n) && n > highest) highest = n;
            f.close();
            f = dir.openNextFile();
        }
    }
    dir.close();
    return highest + 1;
}
}  // namespace

bool sdw_mount(void) {
    SPI.begin(PIN_SD_SCK, PIN_SD_MISO, PIN_SD_MOSI, PIN_SD_CS);
    if (!SD.begin(PIN_SD_CS, SPI, KP_SD_HZ)) return false;
    W.pt = (uint8_t *)heap_caps_malloc(PLAINTEXT_CAP, MALLOC_CAP_SPIRAM);
    W.out = (uint8_t *)heap_caps_malloc(OUT_CAP, MALLOC_CAP_SPIRAM);
    W.mounted = W.pt && W.out;
    return W.mounted;
}

bool sdw_open(const kp_config_t &cfg, uint32_t *session_out) {
    if (!W.mounted || W.open) return false;
    if (!SD.exists("/KB")) SD.mkdir("/KB");
    uint32_t session = next_session_number();
    if (session > 9999) return false;
    char name[24];
    snprintf(name, sizeof name, "/KB/S%04lu.kbx", (unsigned long)session);
    if (SD.exists(name)) return false; /* never overwrite */

    memset(W.header, 0, sizeof W.header);
    memcpy(W.header, "KBX1", 4);
    W.header[4] = 1;                  /* format_version */
    W.header[5] = 0;                  /* header_flags */
    put_u16(W.header + 6, HEADER_LEN);
    char dev[17] = {0};
    snprintf(dev, sizeof dev, "KP-%lu", (unsigned long)cfg.dev_id);
    memcpy(W.header + 8, dev, strlen(dev));
    put_u16(W.header + 24, (uint16_t)session);
    put_u16(W.header + 26, 50);
    put_f32(W.header + 28, POD_G_MS2 / POD_ACCEL_LSB_PER_G);                  /* accel_lsb_mps2 */
    put_f32(W.header + 32, (float)((3.14159265358979323846 / 180.0) / POD_GYRO_LSB_PER_DPS)); /* gyro_lsb_radps */
    memcpy(W.header + 36, cfg.salt, 16);
    put_u32(W.header + 52, PBKDF2_ITERATIONS);
    uint32_t prefix = esp_random();
    put_u32(W.nonce_prefix, prefix);
    memcpy(W.header + 56, W.nonce_prefix, 4);
    put_u64(W.header + 60, 0);        /* wall clock unknown: no NTP on the pods */
    const char *fw = "knee_pod_s3 0.1.0";
    memcpy(W.header + 68, fw, strlen(fw));
    memset(W.header + 92, 0, 4);

    if (W.gcm_ready) mbedtls_gcm_free(&W.gcm);
    mbedtls_gcm_init(&W.gcm);
    if (mbedtls_gcm_setkey(&W.gcm, MBEDTLS_CIPHER_ID_AES, cfg.aes_key, 256) != 0) {
        mbedtls_gcm_free(&W.gcm);
        return false;
    }
    W.gcm_ready = true;

    W.file = SD.open(name, FILE_WRITE);
    if (!W.file) return false;
    if (W.file.write(W.header, HEADER_LEN) != HEADER_LEN) {
        W.file.close();
        return false;
    }
    W.file.flush();
    memcpy(W.nonce_prefix, W.header + 56, 4);
    W.counter = 0;
    W.pt_len = 0;
    W.imu_in_chunk = 0;
    W.base_seq = 0;
    W.last_seq = 0;
    W.imu_written = 0;
    W.failed = false;
    W.open = true;
    if (session_out) *session_out = session;
    return true;
}

void sdw_set_base(uint32_t base_seq) { W.base_seq = base_seq; }

void sdw_imu(const pod_row_t &row) {
    if (!W.open || W.failed || row.seq < W.base_seq) return;
    uint8_t body[IMU_BODY];
    uint32_t seq = row.seq - W.base_seq;
    put_u32(body, seq);
    body[4] = row.flags & POD_FLAG_LINK_LOSS; /* bits 1..7 are reserved, written as 0 */
    for (int i = 0; i < KP_CHANNELS; ++i) put_u16(body + 5 + 2 * i, (uint16_t)row.raw[i]);
    append(REC_IMU, body, IMU_BODY);
    if (!W.open || W.failed) return;
    W.last_seq = seq;
    W.imu_written++;
    if (++W.imu_in_chunk >= CHUNK_IMU_RECORDS) {
        if (!flush_chunk()) W.failed = true;
    }
}

void sdw_event(uint8_t kind, uint16_t battery_mv, uint32_t payload) {
    uint8_t body[EVENT_BODY];
    body[0] = kind;
    put_u16(body + 1, battery_mv);
    put_u32(body + 3, payload);
    append(REC_EVENT, body, EVENT_BODY);
}

void sdw_infer(uint32_t window_seq, uint8_t exercise, const float p[3], uint8_t label) {
    uint8_t body[INFER_BODY];
    put_u32(body, window_seq - W.base_seq);
    body[4] = exercise;
    put_f32(body + 5, p[0]);
    put_f32(body + 9, p[1]);
    put_f32(body + 13, p[2]);
    body[17] = label;
    append(REC_INFER, body, INFER_BODY);
}

bool sdw_close(void) {
    if (!W.open) return !W.failed;
    if (!flush_chunk()) W.failed = true;
    W.file.flush();
    W.file.close();
    W.open = false;
    return !W.failed;
}

uint32_t sdw_next_seq(void) { return W.imu_written ? W.last_seq + 1 : 0; }

bool sdw_is_open(void) { return W.open; }
bool sdw_failed(void) { return W.failed; }
uint32_t sdw_imu_written(void) { return W.imu_written; }
