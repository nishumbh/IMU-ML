/* Provisioned settings, read from NVS namespace "kpod". See firmware/knee_pod_s3/README.md. */
#ifndef KP_POD_CONFIG_H
#define KP_POD_CONFIG_H

#include <stdbool.h>
#include <stdint.h>

#define KP_NVS_NAMESPACE "kpod"

enum kp_role { KP_ROLE_NONE = 0, KP_ROLE_THIGH = 1, KP_ROLE_SHANK = 2 };

typedef struct {
    uint8_t role;        /* must match the env the board was flashed with */
    uint8_t channel;     /* Wi-Fi channel 1..13, same on both pods */
    uint32_t dev_id;     /* written into the .kbx header (thigh only) */
    uint8_t pmk[16];     /* ESP-NOW primary key (both) */
    uint8_t lmk[16];     /* ESP-NOW local key for the one peer (both) */
    uint8_t peer_mac[6]; /* thigh: shank MAC; shank: thigh MAC */
    uint8_t aes_key[32]; /* thigh: derived on the host, never on the device */
    uint8_t salt[16];    /* thigh: per-device salt written into the .kbx header */
    bool has_pmk, has_lmk, has_peer, has_aes, has_salt, has_dev_id, has_channel;
} kp_config_t;

/* Loads and validates what the role needs. Returns false and prints the missing key. */
bool kp_config_load(kp_config_t *cfg, uint8_t expected_role);

/* Reads serial bytes without blocking and runs complete lines as KPOD commands.
 * Call from loop(). Writes are refused while a session is recording. */
void kp_provision_poll(void);

/* Set by the thigh while a session is open; provisioning is refused then. */
extern volatile bool kp_session_active;

#endif /* KP_POD_CONFIG_H */
