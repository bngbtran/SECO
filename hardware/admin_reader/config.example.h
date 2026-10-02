// Copy this file to config.h (same folder) and fill in your values.
// config.h is gitignored: never commit your WiFi password.
#pragma once

// WiFi the reader joins (2.4 GHz only - the ESP32 has no 5 GHz radio).
#define WIFI_SSID "your-wifi"
#define WIFI_PASSWORD "your-password"

// Broker, the same one the API uses.
// Deployed API (Render): broker.hivemq.com over TLS.
#define MQTT_HOST "broker.hivemq.com"
#define MQTT_PORT 8883
#define MQTT_TLS 1
#define MQTT_USERNAME nullptr  // "user" / "password" for a private broker (HiveMQ Cloud)
#define MQTT_PASSWORD nullptr
// Local API instead: LAN IP of the computer running Mosquitto (macOS:
// `ipconfig getifaddr en0`, not "localhost"), port 1883, MQTT_TLS 0.

// Must match ADMIN_READER_ID in apps/api/.env (default READER-01).
#define READER_ID "READER-01"

// OLED SSD1306 128x64 I2C: address 0x3C (most modules) or 0x3D. Set OLED_ENABLED 0
// if you have no screen - the Serial Monitor shows everything anyway.
#define OLED_ENABLED 1
#define OLED_ADDRESS 0x3C
