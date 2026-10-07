// Copy this file to config.h (same folder) and fill in your WiFi.
// config.h is gitignored: never commit your WiFi password.
#pragma once

// WiFi the station joins (2.4 GHz only - the ESP32 has no 5 GHz radio).
// In Wokwi use "Wokwi-GUEST" with an empty password.
#define WIFI_SSID "your-wifi"
#define WIFI_PASSWORD "your-password"

// Broker, the same one the API uses.
// Deployed API (Render): broker.hivemq.com over TLS.
#define MQTT_HOST "broker.hivemq.com"
#define MQTT_PORT 8883
#define MQTT_TLS 1
#define MQTT_USERNAME nullptr  // "user" / "password" for a private broker (HiveMQ Cloud)
#define MQTT_PASSWORD nullptr
// Local API instead: LAN IP of the computer running Mosquitto, port 1883, MQTT_TLS 0.

// Must match the station in the database (the real station is EVCS-01).
#define STATION_ID "EVCS-01"

// Safety limits (DD 7). Lower OVER_TEMPERATURE_C only to test the protection
// with your fingers on the DS18B20 (e.g. 28.0f); the business value is 35 C.
#define OVER_TEMPERATURE_C 35.0f
#define OVER_CURRENT_A 1.0f
