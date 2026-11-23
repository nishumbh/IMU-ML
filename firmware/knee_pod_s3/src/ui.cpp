#include "ui.h"

#include <Arduino.h>
#include <math.h>

#include "config.h"
#include "pins.h"

namespace {
bool g_has_button = false;
bool g_pressed = false;        /* debounced level, true while held */
bool g_long_fired = false;
uint32_t g_edge_ms = 0;        /* time of the last raw level change */
uint32_t g_press_ms = 0;       /* time the debounced press started */
bool g_raw_last = false;
}  // namespace

void ui_begin(bool with_button) {
    g_has_button = with_button;
    if (with_button) pinMode(PIN_BUTTON, INPUT_PULLUP);
    analogSetPinAttenuation(PIN_BATTERY_ADC, ADC_11db); /* up to ~2.5 V at the pin */
    ui_led(0, 0, 0);
}

ui_event_t ui_poll_button(void) {
    if (!g_has_button) return UI_NONE;
    uint32_t now = millis();
    bool raw = digitalRead(PIN_BUTTON) == LOW; /* active low */
    if (raw != g_raw_last) {
        g_raw_last = raw;
        g_edge_ms = now;
    }
    if (now - g_edge_ms < KP_BUTTON_DEBOUNCE_MS) return UI_NONE;
    if (raw && !g_pressed) {
        g_pressed = true;
        g_long_fired = false;
        g_press_ms = now;
        return UI_NONE;
    }
    if (raw && g_pressed && !g_long_fired && now - g_press_ms >= KP_BUTTON_LONG_MS) {
        g_long_fired = true;
        return UI_LONG_PRESS;
    }
    if (!raw && g_pressed) {
        g_pressed = false;
        if (!g_long_fired) return UI_SHORT_PRESS;
    }
    return UI_NONE;
}

void ui_led(uint8_t r, uint8_t g, uint8_t b) { rgbLedWrite(PIN_RGB_LED, r, g, b); }

float ui_battery_volts(void) {
    int mv = analogReadMilliVolts(PIN_BATTERY_ADC);
    if (mv <= 0) return NAN;
    return (float)mv / 1000.0f * BATTERY_DIVIDER_RATIO;
}
