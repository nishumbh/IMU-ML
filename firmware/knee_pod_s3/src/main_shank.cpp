/* SHANK pod (slave). Samples its MPU6050 at 50 Hz on an esp_timer and answers
 * the thigh's sequenced requests with the latest sample. Never sends unsolicited
 * data, and never answers with a stale sample (silence is counted as loss). */
#include <Arduino.h>
#include <Wire.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <math.h>
#include <string.h>

#include "config.h"
#include "espnow_link.h"
#include "imu_mpu6050.h"
#include "pins.h"
#include "pod_config.h"
#include "pod_core.h"
#include "ui.h"

namespace {
constexpr uint8_t IMU_ADDRESS = 0x69; /* AD0 high on the shank pod */

portMUX_TYPE g_lock = portMUX_INITIALIZER_UNLOCKED;
struct latest_t {
    int16_t raw[6];
    int64_t t_us;
    bool valid;
} g_latest = {{0, 0, 0, 0, 0, 0}, 0, false};

pod_gate_t g_gate;
uint8_t g_thigh_mac[6];
esp_timer_handle_t g_imu_timer = nullptr;
bool g_running = false;
volatile uint32_t g_imu_errors = 0;
volatile uint32_t g_responses = 0;
volatile uint32_t g_stale = 0;

/* 50 Hz sampler, esp_timer task context. One I2C burst per tick. */
void on_imu_tick(void *arg) {
    (void)arg;
    int16_t raw[6];
    bool ok = imu_read(Wire, IMU_ADDRESS, raw);
    if (!ok) g_imu_errors++;
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&g_lock);
    memcpy(g_latest.raw, raw, sizeof raw);
    g_latest.t_us = now;
    g_latest.valid = ok;
    portEXIT_CRITICAL(&g_lock);
}

/* Wi-Fi task context. Answer a request with the latest sample, echoing its sequence. */
void on_rx(const uint8_t *src, const uint8_t *data, int len) {
    if (memcmp(src, g_thigh_mac, 6) != 0) {
        pod_gate_accept(&g_gate, 0, data, (size_t)len, nullptr, nullptr);
        return;
    }
    uint32_t seq = 0, ticks = 0;
    if (!pod_gate_accept(&g_gate, 1, data, (size_t)len, &seq, &ticks)) return;

    int16_t raw[6];
    bool valid;
    int64_t t_us;
    portENTER_CRITICAL(&g_lock);
    memcpy(raw, g_latest.raw, sizeof raw);
    valid = g_latest.valid;
    t_us = g_latest.t_us;
    portEXIT_CRITICAL(&g_lock);

    if (!valid || esp_timer_get_time() - t_us > KP_SHANK_STALE_US) {
        g_stale++; /* no reply: the master records a link loss for this tick */
        return;
    }
    float v[6];
    for (int i = 0; i < 6; ++i) {
        v[i] = (i < 3) ? pod_accel_to_ms2(raw[i]) : pod_gyro_to_rads(raw[i]);
    }
    uint8_t out[POD_RESPONSE_SIZE];
    int n = pod_encode_response(out, sizeof out, g_gate.session, seq,
                                (uint32_t)(t_us / 1000), v);
    if (n > 0 && link_send(out, (size_t)n)) {
        g_responses++;
    }
}

bool start_imu_timer(void) {
    esp_timer_create_args_t args = {};
    args.callback = on_imu_tick;
    args.dispatch_method = ESP_TIMER_TASK;
    args.name = "imu50";
    if (esp_timer_create(&args, &g_imu_timer) != ESP_OK) return false;
    return esp_timer_start_periodic(g_imu_timer, KP_TICK_US) == ESP_OK;
}
}  // namespace

void setup() {
    Serial.begin(115200);
    uint32_t wait_until = millis() + 3000;
    while (!Serial && millis() < wait_until) delay(10);
    Serial.println("# knee_pod_s3 SHANK. Research prototype, not a medical device.");
    ui_begin(false);
    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.setClock(400000);

    kp_config_t cfg;
    if (!kp_config_load(&cfg, KP_ROLE_SHANK)) {
        ui_led(0, 0, 40); /* blue: needs provisioning */
        return;
    }
    memcpy(g_thigh_mac, cfg.peer_mac, 6);
    pod_gate_init(&g_gate);
    if (!imu_init(Wire, IMU_ADDRESS)) {
        ui_led(60, 0, 0); /* red: IMU */
        Serial.println("# IMU init failed (WHO_AM_I 0x68 expected). Check AD0 HIGH and 3V3.");
        return;
    }
    if (!link_begin(cfg, on_rx) || !start_imu_timer()) {
        ui_led(60, 0, 0);
        Serial.println("# ESP-NOW or timer init failed.");
        return;
    }
    g_running = true;
    ui_led(0, 40, 0); /* green: running */
    Serial.println("# Running: 50 Hz IMU, answering the thigh over encrypted ESP-NOW.");
}

void loop() {
    static uint32_t last_report = 0;
    kp_provision_poll();
    if (millis() - last_report >= 5000) {
        last_report = millis();
        float v = ui_battery_volts();
        Serial.printf("# shank responses=%lu stale=%lu imu_errors=%lu tx_failures=%lu bat=%.2fV\n",
                      (unsigned long)g_responses, (unsigned long)g_stale,
                      (unsigned long)g_imu_errors, (unsigned long)link_send_failures(),
                      isnan(v) ? 0.0f : v);
        if (!isnan(v) && v < KP_BATTERY_WARN_V) ui_led(60, 40, 0); /* amber: low battery */
    }
    delay(10);
}
