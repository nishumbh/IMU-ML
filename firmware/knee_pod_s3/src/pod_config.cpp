/* NVS provisioning. The device stores what the host gives it; it never runs PBKDF2.
 *
 * Serial commands (115200, USB CDC), one per line, while no session is recording:
 *   KPOD status
 *   KPOD set role thigh|shank
 *   KPOD set channel <1..13>
 *   KPOD set dev_id <uint32>
 *   KPOD set peer_mac <12 hex>
 *   KPOD set pmk <32 hex>          ESP-NOW primary key, both pods, same value
 *   KPOD set lmk <32 hex>          ESP-NOW local key for the peer, same value on both
 *   KPOD set aes_key <64 hex>      thigh only, derived on the host (PBKDF2-SHA256)
 *   KPOD set salt <32 hex>         thigh only, per-device salt for the .kbx header
 *   KPOD erase                     removes every kpod key (then set them again)
 * Keys are stored in plaintext NVS unless ESP32 flash encryption is enabled.
 */
#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <string.h>

#include "pod_config.h"

volatile bool kp_session_active = false;

static bool parse_hex(const char *hex, uint8_t *out, size_t bytes) {
    if (strlen(hex) != bytes * 2) return false;
    for (size_t i = 0; i < bytes; ++i) {
        char pair[3] = {hex[2 * i], hex[2 * i + 1], 0};
        char *end = nullptr;
        long v = strtol(pair, &end, 16);
        if (end == pair || *end != 0 || v < 0 || v > 255) return false;
        out[i] = (uint8_t)v;
    }
    return true;
}

static bool read_blob(Preferences &prefs, const char *key, uint8_t *out, size_t bytes) {
    return prefs.getBytesLength(key) == bytes && prefs.getBytes(key, out, bytes) == bytes;
}

static void read_all(kp_config_t *cfg) {
    memset(cfg, 0, sizeof *cfg);
    Preferences prefs;
    if (!prefs.begin(KP_NVS_NAMESPACE, true)) return;
    cfg->role = prefs.getUChar("role", KP_ROLE_NONE);
    cfg->channel = prefs.getUChar("channel", 0);
    cfg->dev_id = prefs.getUInt("dev_id", 0);
    cfg->has_channel = cfg->channel >= 1 && cfg->channel <= 13;
    cfg->has_dev_id = prefs.isKey("dev_id");
    cfg->has_pmk = read_blob(prefs, "pmk", cfg->pmk, 16);
    cfg->has_lmk = read_blob(prefs, "lmk", cfg->lmk, 16);
    cfg->has_peer = read_blob(prefs, "peer_mac", cfg->peer_mac, 6);
    cfg->has_aes = read_blob(prefs, "aes_key", cfg->aes_key, 32);
    cfg->has_salt = read_blob(prefs, "salt", cfg->salt, 16);
    prefs.end();
}

bool kp_config_load(kp_config_t *cfg, uint8_t expected_role) {
    read_all(cfg);
    bool ok = cfg->role == expected_role && cfg->has_channel && cfg->has_pmk && cfg->has_lmk &&
              cfg->has_peer;
    if (expected_role == KP_ROLE_THIGH) ok = ok && cfg->has_aes && cfg->has_salt && cfg->has_dev_id;
    if (cfg->role != expected_role) {
        Serial.printf("# NVS role=%u does not match this firmware (expected %u).\n", cfg->role,
                      expected_role);
    }
    if (!ok) Serial.println("# Provisioning incomplete. Send KPOD status over serial.");
    return ok;
}

static void print_status(void) {
    kp_config_t cfg;
    read_all(&cfg);
    uint8_t mac[6];
    WiFi.macAddress(mac);
    Serial.printf("# own MAC %02x:%02x:%02x:%02x:%02x:%02x\n", mac[0], mac[1], mac[2], mac[3],
                  mac[4], mac[5]);
    Serial.printf("# role=%u channel=%u dev_id=%lu pmk=%d lmk=%d peer=%d aes_key=%d salt=%d\n",
                  cfg.role, cfg.channel, (unsigned long)cfg.dev_id, cfg.has_pmk, cfg.has_lmk,
                  cfg.has_peer, cfg.has_aes, cfg.has_salt);
}

static void handle_set(char *key, char *value) {
    Preferences prefs;
    if (!prefs.begin(KP_NVS_NAMESPACE, false)) {
        Serial.println("# NVS open failed");
        return;
    }
    bool ok = false;
    uint8_t buf[32];
    if (strcmp(key, "role") == 0) {
        uint8_t r = strcmp(value, "thigh") == 0 ? KP_ROLE_THIGH
                  : strcmp(value, "shank") == 0 ? KP_ROLE_SHANK : KP_ROLE_NONE;
        ok = r != KP_ROLE_NONE && prefs.putUChar("role", r) == 1;
    } else if (strcmp(key, "channel") == 0) {
        long c = atol(value);
        ok = c >= 1 && c <= 13 && prefs.putUChar("channel", (uint8_t)c) == 1;
    } else if (strcmp(key, "dev_id") == 0) {
        char *end = nullptr;
        unsigned long v = strtoul(value, &end, 10);
        ok = end != value && *end == 0 && prefs.putUInt("dev_id", (uint32_t)v) == 4;
    } else if (strcmp(key, "peer_mac") == 0) {
        ok = parse_hex(value, buf, 6) && prefs.putBytes("peer_mac", buf, 6) == 6;
    } else if (strcmp(key, "pmk") == 0) {
        ok = parse_hex(value, buf, 16) && prefs.putBytes("pmk", buf, 16) == 16;
    } else if (strcmp(key, "lmk") == 0) {
        ok = parse_hex(value, buf, 16) && prefs.putBytes("lmk", buf, 16) == 16;
    } else if (strcmp(key, "aes_key") == 0) {
        ok = parse_hex(value, buf, 32) && prefs.putBytes("aes_key", buf, 32) == 32;
    } else if (strcmp(key, "salt") == 0) {
        ok = parse_hex(value, buf, 16) && prefs.putBytes("salt", buf, 16) == 16;
    }
    prefs.end();
    Serial.println(ok ? "# OK" : "# REJECTED (bad key name or value length)");
}

static void run_line(char *line) {
    char *tok = strtok(line, " \t\r\n");
    if (!tok || strcmp(tok, "KPOD") != 0) return;
    char *cmd = strtok(nullptr, " \t\r\n");
    if (!cmd) return;
    if (kp_session_active) {
        Serial.println("# REFUSED: stop the session before changing provisioning");
        return;
    }
    if (strcmp(cmd, "status") == 0) {
        print_status();
    } else if (strcmp(cmd, "set") == 0) {
        char *key = strtok(nullptr, " \t\r\n");
        char *value = strtok(nullptr, " \t\r\n");
        if (key && value) handle_set(key, value);
        else Serial.println("# usage: KPOD set <key> <value>");
    } else if (strcmp(cmd, "erase") == 0) {
        Preferences prefs;
        prefs.begin(KP_NVS_NAMESPACE, false);
        bool ok = prefs.clear();
        prefs.end();
        Serial.println(ok ? "# erased kpod namespace" : "# erase failed");
    } else {
        Serial.println("# unknown KPOD command");
    }
}

void kp_provision_poll(void) {
    static char line[160];
    static size_t len = 0;
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (len > 0) {
                line[len] = 0;
                run_line(line);
                len = 0;
            }
        } else if (len + 1 < sizeof line) {
            line[len++] = c;
        } else {
            len = 0; /* overlong line: drop it */
        }
    }
}
