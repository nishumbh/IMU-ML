/* Button, RGB status LED and battery sense. Shared by both pods. */
#ifndef KP_UI_H
#define KP_UI_H

#include <stdint.h>

typedef enum { UI_NONE = 0, UI_SHORT_PRESS, UI_LONG_PRESS } ui_event_t;

/* Configures the LED and ADC. Button only when with_button is true (thigh). */
void ui_begin(bool with_button);

/* Debounced; call every 10 ms from loop(). A long press fires once, at the threshold. */
ui_event_t ui_poll_button(void);

/* Writes the RGB LED (WS2812 on GPIO47). Values 0..255. */
void ui_led(uint8_t r, uint8_t g, uint8_t b);

/* Battery voltage at BAT+ (divider already undone). NAN if the read failed. */
float ui_battery_volts(void);

#endif /* KP_UI_H */
