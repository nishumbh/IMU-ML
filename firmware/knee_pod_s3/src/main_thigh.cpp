/* THIGH pod (master). Owns the sampling clock (20 ms, esp_timer) and the merged
 * stream. Producers (esp_timer task, ESP-NOW receive task) touch only the pairer
 * and the PSRAM row ring, under a short spinlock. The SD writer, the inference
 * window and all file I/O run in a separate FreeRTOS task. loop() handles the
 * button, the LED, battery and serial provisioning. Nothing here blocks sampling. */
#include <Arduino.h>
#include <Wire.h>
#include <esp_heap_caps.h>
#include <esp_random.h>
#include <esp_timer.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/task.h>
#include <math.h>
#include <string.h>

#include "config.h"
#include "espnow_link.h"
#include "imu_mpu6050.h"
#include "infer.h"
#include "pins.h"
#include "pod_config.h"
#include "pod_core.h"
#include "sd_writer.h"
#include "ui.h"

namespace {
constexpr uint8_t IMU_ADDRESS = 0x68; /* AD0 low on the thigh pod */

enum cmd_kind : uint8_t { CMD_START, CMD_STOP, CMD_EXERCISE, CMD_LOW_BATTERY };
struct cmd_t {
    uint8_t kind;
    uint8_t exercise;
    uint16_t battery_mv;
};

portMUX_TYPE g_lock = portMUX_INITIALIZER_UNLOCKED;
pod_pairer_t g_pairer;      /* guarded by g_lock */
pod_ring_t g_ring;          /* guarded by g_lock */
bool g_recording = false;   /* guarded by g_lock */
uint32_t g_session_base = 0;/* guarded by g_lock */
uint32_t g_next_seq = 0;    /* guarded by g_lock; sampler increments */
uint8_t g_shank_mac[6];
kp_config_t g_cfg;
esp_timer_handle_t g_timer = nullptr;
QueueHandle_t g_cmds = nullptr;
volatile bool g_sd_ok = false;
volatile bool g_imu_ok = false;
volatile uint8_t g_exercise = KP_EX_SQUAT;
volatile uint32_t g_thigh_read_errors = 0;
volatile uint32_t g_tick_errors = 0;
volatile uint32_t g_recorded_rows = 0;

/* Writer-task state. Only the writer task touches these. */
pod_row_t g_win[KP_WINDOW_ROWS];
uint32_t g_win_count = 0;
uint32_t g_win_head = 0;
uint32_t g_since_hop = 0;

/* 50 Hz sampler: esp_timer task. One I2C burst, one request, no SD, no logging. */
void on_tick(void *arg) {
    (void)arg;
    portENTER_CRITICAL(&g_lock);
    uint32_t seq = g_next_seq++;
    portEXIT_CRITICAL(&g_lock);

    int16_t thigh[6];
    if (!imu_read(Wire, IMU_ADDRESS, thigh)) {
        /* Local read failed: skip this tick. The seq gap is the lost-sample record. */
        g_thigh_read_errors++;
        return;
    }
    uint8_t req[POD_REQUEST_SIZE];
    size_t req_len = 0;
    pod_row_t loss;
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&g_lock);
    int rc = pod_pairer_tick(&g_pairer, now, seq, seq * KP_TICK_MS, thigh, 0, req, sizeof req,
                             &req_len, &loss);
    if (rc == 1 && g_recording && loss.seq >= g_session_base) {
        pod_ring_push(&g_ring, &loss);
    }
    portEXIT_CRITICAL(&g_lock);
    if (rc < 0) {
        g_tick_errors++;
        return;
    }
    if (!link_send(req, req_len)) {
        portENTER_CRITICAL(&g_lock);
        pod_pairer_note_send_error(&g_pairer);
        portEXIT_CRITICAL(&g_lock);
    }
}

/* ESP-NOW receive: Wi-Fi task. Pairs the shank response with the pending request. */
void on_rx(const uint8_t *src, const uint8_t *data, int len) {
    bool sender_ok = memcmp(src, g_shank_mac, 6) == 0;
    pod_row_t row;
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&g_lock);
    int paired = pod_pairer_accept(&g_pairer, sender_ok ? 1 : 0, data, (size_t)len, now, &row);
    if (paired && g_recording && row.seq >= g_session_base) pod_ring_push(&g_ring, &row);
    portEXIT_CRITICAL(&g_lock);
}

/* Run the model on a full window, if one is loaded for this exercise. */
void maybe_infer(void) {
    uint8_t ex = g_exercise;
    if (!infer_supports(ex)) return;
    pod_row_t linear[KP_WINDOW_ROWS];
    for (uint32_t i = 0; i < KP_WINDOW_ROWS; ++i) {
        linear[i] = g_win[(g_win_head + i) % KP_WINDOW_ROWS];
    }
    /* Only consecutive seqs make a real window; gaps would mix time. */
    if (linear[KP_WINDOW_ROWS - 1].seq - linear[0].seq != KP_WINDOW_ROWS - 1) return;
    kp_infer_result_t res;
    if (infer_window(linear, KP_WINDOW_ROWS, &res)) {
        sdw_infer(res.window_seq, ex, res.p, res.label);
    }
}

void window_push(const pod_row_t &row) {
    g_win[(g_win_head + g_win_count) % KP_WINDOW_ROWS] = row;
    if (g_win_count < KP_WINDOW_ROWS) {
        g_win_count++;
    } else {
        g_win_head = (g_win_head + 1) % KP_WINDOW_ROWS; /* overwrite the oldest */
    }
    if (g_win_count == KP_WINDOW_ROWS && ++g_since_hop >= KP_HOP_ROWS) {
        g_since_hop = 0;
        maybe_infer();
    }
}

void drain_ring_to_sd(void) {
    static pod_row_t batch[KP_WRITER_BATCH_ROWS];
    for (;;) {
        int n = 0;
        portENTER_CRITICAL(&g_lock);
        while (n < KP_WRITER_BATCH_ROWS && pod_ring_pop(&g_ring, &batch[n])) n++;
        portEXIT_CRITICAL(&g_lock);
        if (n == 0) return;
        for (int i = 0; i < n; ++i) {
            if (!sdw_is_open()) break;
            sdw_imu(batch[i]);
            window_push(batch[i]);
            g_recorded_rows++;
        }
    }
}

void handle_cmd(const cmd_t &c) {
    switch (c.kind) {
    case CMD_START: {
        if (!g_sd_ok || sdw_is_open()) break;
        uint32_t session = 0;
        if (!sdw_open(g_cfg, &session)) {
            g_sd_ok = false; /* card removed or full: the next button press retries mount */
            Serial.println("# START refused: SD open failed");
            break;
        }
        portENTER_CRITICAL(&g_lock);
        uint32_t base = g_next_seq;
        g_session_base = base;
        g_recording = true;
        portEXIT_CRITICAL(&g_lock);
        sdw_set_base(base);
        memset(g_win, 0, sizeof g_win);
        g_win_count = g_win_head = g_since_hop = 0;
        kp_session_active = true;
        sdw_event(KBX_EVENT_START, c.battery_mv, 0);
        sdw_event(KBX_EVENT_EXERCISE_CHANGE, c.battery_mv, g_exercise);
        Serial.printf("# RECORDING S%04lu.kbx base_seq=%lu\n", (unsigned long)session,
                      (unsigned long)base);
        break;
    }
    case CMD_STOP: {
        if (!sdw_is_open()) break;
        portENTER_CRITICAL(&g_lock);
        g_recording = false;
        portEXIT_CRITICAL(&g_lock);
        drain_ring_to_sd();
        sdw_event(KBX_EVENT_STOP, c.battery_mv, sdw_next_seq());
        bool ok = sdw_close();
        kp_session_active = false;
        Serial.printf("# STOP: %s\n", ok ? "closed cleanly" : "WRITE ERROR, file may be truncated");
        if (!ok) g_sd_ok = false;
        break;
    }
    case CMD_EXERCISE:
        g_exercise = c.exercise;
        if (sdw_is_open()) sdw_event(KBX_EVENT_EXERCISE_CHANGE, c.battery_mv, c.exercise);
        break;
    case CMD_LOW_BATTERY:
        if (sdw_is_open()) sdw_event(KBX_EVENT_LOW_BATTERY, c.battery_mv, 0);
        break;
    }
}

/* Writer task: core 0, below the sampler. Owns the SD card and the window. */
void writer_task(void *arg) {
    (void)arg;
    cmd_t c;
    for (;;) {
        while (xQueueReceive(g_cmds, &c, 0) == pdTRUE) handle_cmd(c);
        drain_ring_to_sd();
        if (sdw_is_open() && sdw_failed()) {
            cmd_t stop = {CMD_STOP, 0, 0};
            handle_cmd(stop);
            Serial.println("# SD write failed: session stopped, card needs a check");
        }
        vTaskDelay(pdMS_TO_TICKS(KP_WRITER_PERIOD_MS));
    }
}

bool start_sampler(void) {
    esp_timer_create_args_t args = {};
    args.callback = on_tick;
    args.dispatch_method = ESP_TIMER_TASK;
    args.name = "imu50";
    if (esp_timer_create(&args, &g_timer) != ESP_OK) return false;
    return esp_timer_start_periodic(g_timer, KP_TICK_US) == ESP_OK;
}

void send_cmd(uint8_t kind, uint8_t exercise = 0) {
    if (!g_cmds) return; /* not provisioned: no writer task */
    cmd_t c = {kind, exercise, 0};
    uint32_t mv = (uint32_t)(ui_battery_volts() * 1000.0f);
    c.battery_mv = (uint16_t)(mv > 65535 ? 65535 : mv);
    xQueueSend(g_cmds, &c, 0);
}

uint32_t exercise_index(uint8_t code) {
    for (uint32_t i = 0; i < KP_EXERCISE_CYCLE_COUNT; ++i) {
        if (KP_EXERCISE_CYCLE[i] == code) return i;
    }
    return 0;
}

void show_led(bool recording, float bat_v) {
    static const uint8_t colours[4][3] = {{0, 0, 0}, {0, 0, 60}, {50, 0, 50}, {50, 40, 0}};
    const uint8_t *c = colours[g_exercise];
    if (!g_cfg.has_aes) { ui_led(40, 0, 0); return; }
    if (!g_sd_ok) { ui_led(60, 0, 0); return; }
    if (!isnan(bat_v) && bat_v < KP_BATTERY_WARN_V) { ui_led(60, 30, 0); return; }
    if (recording) {
        ui_led(c[0], c[1], c[2]);
    } else {
        ui_led(c[0] / 4, c[1] / 4, c[2] / 4);
    }
}
}  // namespace

void setup() {
    Serial.begin(115200);
    uint32_t wait_until = millis() + 3000;
    while (!Serial && millis() < wait_until) delay(10);
    Serial.println("# knee_pod_s3 THIGH. Research prototype, not a medical device.");
    ui_begin(true);
    ui_led(0, 0, 40); /* blue: booting */

    bool cfg_ok = kp_config_load(&g_cfg, KP_ROLE_THIGH);
    if (!cfg_ok) {
        Serial.println("# Not provisioned: streaming and recording disabled.");
        return;
    }
    memcpy(g_shank_mac, g_cfg.peer_mac, 6);

    Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);
    Wire.setClock(400000);
    g_imu_ok = imu_init(Wire, IMU_ADDRESS);
    if (!g_imu_ok) Serial.println("# IMU init failed (WHO_AM_I 0x68). Check AD0 LOW and 3V3.");

    g_sd_ok = sdw_mount();
    if (!g_sd_ok) Serial.println("# SD mount failed. Recording disabled until reset.");

    pod_row_t *storage = (pod_row_t *)heap_caps_malloc(KP_RING_ROWS * sizeof(pod_row_t),
                                                       MALLOC_CAP_SPIRAM);
    if (!storage) {
        Serial.println("# PSRAM ring allocation failed. Recording disabled.");
        g_sd_ok = false;
    } else {
        pod_ring_init(&g_ring, storage, KP_RING_ROWS);
    }

    uint8_t session[POD_SESSION_LEN];
    esp_fill_random(session, sizeof session); /* new random session per boot */
    pod_pairer_init(&g_pairer, session);

    if (!link_begin(g_cfg, on_rx)) {
        Serial.println("# ESP-NOW init failed.");
        ui_led(60, 0, 0);
        return;
    }
    infer_init();
    Serial.printf("# model: %s\n", infer_status_text());

    g_cmds = xQueueCreate(16, sizeof(cmd_t));
    xTaskCreatePinnedToCore(writer_task, "kbx_writer", 12288, nullptr, 2, nullptr, 0);
    if (!g_imu_ok || !start_sampler()) {
        Serial.println("# sampler not started (IMU or timer failure).");
        ui_led(60, 0, 0);
        return;
    }
    Serial.println("# Running at 50 Hz. Short press: next exercise. Long press: start/stop.");
}

void loop() {
    static uint32_t last_report = 0;
    static uint32_t last_battery = 0;
    static float bat_v = NAN;
    static bool low_sent = false;
    kp_provision_poll();

    ui_event_t ev = ui_poll_button();
    if (ev == UI_SHORT_PRESS) {
        uint32_t i = (exercise_index(g_exercise) + 1) % KP_EXERCISE_CYCLE_COUNT;
        g_exercise = KP_EXERCISE_CYCLE[i];
        Serial.printf("# exercise: %s\n", KP_EXERCISE_NAMES[g_exercise]);
        send_cmd(CMD_EXERCISE, g_exercise);
    } else if (ev == UI_LONG_PRESS) {
        send_cmd(kp_session_active ? CMD_STOP : CMD_START);
    }

    if (millis() - last_battery >= KP_BATTERY_PERIOD_MS) {
        last_battery = millis();
        bat_v = ui_battery_volts();
        if (kp_session_active && !isnan(bat_v) && bat_v < KP_BATTERY_WARN_V && !low_sent) {
            low_sent = true;
            send_cmd(CMD_LOW_BATTERY);
        }
    }
    show_led(kp_session_active, bat_v);

    if (millis() - last_report >= 5000) {
        last_report = millis();
        uint32_t pairs = 0, timeouts = 0, unmatched = 0, overflow = 0, send_err = 0;
        portENTER_CRITICAL(&g_lock);
        pairs = g_pairer.counts.paired;
        timeouts = g_pairer.counts.timeouts;
        unmatched = g_pairer.counts.unmatched;
        overflow = g_ring.overflow;
        send_err = g_pairer.counts.send_errors;
        portEXIT_CRITICAL(&g_lock);
        Serial.printf("# paired=%lu link_loss=%lu unmatched=%lu send_err=%lu tx_fail=%lu "
                      "imu_err=%lu ring_overflow=%lu rec_rows=%lu bat=%.2fV rec=%d\n",
                      (unsigned long)pairs, (unsigned long)timeouts, (unsigned long)unmatched,
                      (unsigned long)send_err, (unsigned long)link_send_failures(),
                      (unsigned long)g_thigh_read_errors, (unsigned long)overflow,
                      (unsigned long)g_recorded_rows, isnan(bat_v) ? 0.0 : bat_v,
                      (int)kp_session_active);
    }
    delay(10);
}
