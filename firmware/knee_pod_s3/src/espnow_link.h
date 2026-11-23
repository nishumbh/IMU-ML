/* ESP-NOW link for both roles: one peer, encrypted with the LMK, PMK set first. */
#ifndef KP_ESPNOW_LINK_H
#define KP_ESPNOW_LINK_H

#include <stddef.h>
#include <stdint.h>

#include "pod_config.h"

typedef void (*kp_rx_fn)(const uint8_t *src_mac, const uint8_t *data, int len);

/* Starts Wi-Fi in station mode with power save off (latency over battery here).
 * Returns false on any ESP-NOW error; nothing is sent in plaintext. */
bool link_begin(const kp_config_t &cfg, kp_rx_fn on_rx);

/* Returns true when the radio accepted the frame. Delivery is reported only
 * through link_send_failures() (send-status callback). */
bool link_send(const uint8_t *data, size_t len);

uint32_t link_send_failures(void);
void link_mac(uint8_t out[6]);

#endif /* KP_ESPNOW_LINK_H */
