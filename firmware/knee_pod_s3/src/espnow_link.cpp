#include "espnow_link.h"

#include <Arduino.h>
#include <WiFi.h>
#include <esp_err.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <string.h>

namespace {
kp_rx_fn g_rx = nullptr;
uint8_t g_peer[6];
volatile uint32_t g_send_failures = 0;

void on_recv(const esp_now_recv_info_t *info, const uint8_t *data, int len) {
    /* Runs in the Wi-Fi task. Keep it short: the caller only pushes a fixed record. */
    if (g_rx && info && info->src_addr) g_rx(info->src_addr, data, len);
}

void on_sent(const wifi_tx_info_t *info, esp_now_send_status_t status) {
    (void)info;
    if (status != ESP_NOW_SEND_SUCCESS) g_send_failures++;
}
}  // namespace

bool link_begin(const kp_config_t &cfg, kp_rx_fn on_rx) {
    g_rx = on_rx;
    memcpy(g_peer, cfg.peer_mac, 6);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    if (esp_wifi_set_channel(cfg.channel, WIFI_SECOND_CHAN_NONE) != ESP_OK) return false;
    if (esp_now_init() != ESP_OK) return false;
    if (esp_now_set_pmk(cfg.pmk) != ESP_OK) return false;
    esp_now_register_recv_cb(on_recv);
    esp_now_register_send_cb(on_sent);

    esp_now_peer_info_t peer;
    memset(&peer, 0, sizeof peer);
    memcpy(peer.peer_addr, cfg.peer_mac, 6);
    memcpy(peer.lmk, cfg.lmk, 16);
    peer.channel = cfg.channel;
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = true;
    return esp_now_add_peer(&peer) == ESP_OK;
}

bool link_send(const uint8_t *data, size_t len) {
    return esp_now_send(g_peer, data, len) == ESP_OK;
}

uint32_t link_send_failures(void) { return g_send_failures; }

void link_mac(uint8_t out[6]) { WiFi.macAddress(out); }
