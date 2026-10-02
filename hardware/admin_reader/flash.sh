#!/usr/bin/env bash
# Build + upload the reader to a real ESP32 over USB, then open the serial monitor.
# Usage:  ./flash.sh              ESP32-S3 DevKitC (default)
#         BOARD=esp32 ./flash.sh  ESP32 DevKit (WROOM)
#         PORT=/dev/cu.usbserial-0001 ./flash.sh   pick the port yourself
set -euo pipefail
cd "$(dirname "$0")"

# Project environment: every SECO command runs inside conda env "iad".
if [ "${CONDA_DEFAULT_ENV:-}" != "iad" ]; then
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate iad
fi

if [ ! -f config.h ]; then
  echo "config.h missing: cp config.example.h config.h, then set WiFi + MQTT_HOST."; exit 1
fi
if grep -q '"your-wifi"' config.h; then
  echo "config.h still has the example WiFi: edit WIFI_SSID / WIFI_PASSWORD / MQTT_HOST first."; exit 1
fi

if ! arduino-cli core list | grep -q "^esp32:esp32"; then
  arduino-cli core update-index
  arduino-cli core install esp32:esp32
fi
for lib in PubSubClient ArduinoJson MFRC522 "Adafruit SSD1306" "Adafruit GFX Library"; do
  arduino-cli lib list | grep -q "^${lib} " || arduino-cli lib install "$lib"
done

# First USB serial port (CP210x / CH340 = usbserial, native USB = usbmodem).
PORT="${PORT:-$(ls /dev/cu.usbserial* /dev/cu.usbmodem* /dev/cu.wchusbserial* 2>/dev/null | head -1 || true)}"
if [ -z "$PORT" ]; then
  echo "No ESP32 found on USB. Plug it in (data cable), or set PORT=/dev/cu.xxx"; exit 1
fi
# Serial goes to the port you are plugged into: the "UART" port (usbserial,
# USB-UART chip) needs CDC off, the native "USB" port (usbmodem) needs CDC on.
case "${BOARD:-esp32s3}" in
  esp32s3)
    if [[ "$PORT" == *usbmodem* ]]; then FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc"
    else FQBN="esp32:esp32:esp32s3:CDCOnBoot=default"; fi ;;
  esp32) FQBN="esp32:esp32:esp32" ;;
  *) echo "BOARD must be esp32s3 or esp32"; exit 1 ;;
esac
echo "Board $FQBN on $PORT"

arduino-cli compile --fqbn "$FQBN" --output-dir build .
arduino-cli upload --fqbn "$FQBN" --port "$PORT" --input-dir build .

echo "Serial monitor (Ctrl+C to quit). Type a hex UID + Enter to fake a card tap."
# DTR/RTS off: on the UART port they drive EN / IO0 and would hold the chip in reset.
arduino-cli monitor --port "$PORT" --config baudrate=115200,dtr=off,rts=off
