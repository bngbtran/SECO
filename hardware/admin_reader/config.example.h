// Copy this file to config.h (same folder) and fill in your values.
// config.h is gitignored: never commit your WiFi password.
#pragma once

// WiFi the reader joins (2.4 GHz only - the ESP32 has no 5 GHz radio).
#define WIFI_SSID "your-wifi"
#define WIFI_PASSWORD "your-password"

// LAN IP of the computer running Mosquitto + the API (macOS: `ipconfig getifaddr en0`).
// Not "localhost": that would be the ESP32 itself.
#define MQTT_HOST "192.168.1.56"
#define MQTT_PORT 1883

// Must match ADMIN_READER_ID in apps/api/.env (default READER-01).
#define READER_ID "READER-01"

// OLED SSD1306 128x64 I2C: address 0x3C (most modules) or 0x3D. Set OLED_ENABLED 0
// if you have no screen - the Serial Monitor shows everything anyway.
#define OLED_ENABLED 1
#define OLED_ADDRESS 0x3C
