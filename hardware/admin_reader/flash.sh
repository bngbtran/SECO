#!/usr/bin/env bash
# Build + upload the reader to a real ESP32 over USB, then open the serial monitor.
# Usage:  ./flash.sh              ESP32-S3 DevKitC (default)
#         BOARD=esp32 ./flash.sh  ESP32 DevKit (WROOM)
#         PORT=COM3 ./flash.sh    pick the port yourself (macOS: /dev/cu.usbserial-0001)
# Windows: run from Git Bash.
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

# First USB serial port, macOS (/dev/cu.*) or Windows (COMx). Bluetooth ports
# have no USB VID and are skipped. VID 303A = the ESP32-S3 native USB port.
read -r FOUND_PORT FOUND_VID < <(python - <<'EOF'
from serial.tools import list_ports
usb = [p for p in list_ports.comports() if p.vid is not None]
print(f"{usb[0].device} {usb[0].vid:04X}" if usb else "")
EOF
) || true
PORT="${PORT:-${FOUND_PORT:-}}"
if [ -z "$PORT" ]; then
  echo "No ESP32 found on USB. Plug it in (data cable), or set PORT=COM3 / PORT=/dev/cu.xxx"; exit 1
fi
NATIVE_USB=0
if [[ "$PORT" == *usbmodem* ]] || { [ "$PORT" == "${FOUND_PORT:-}" ] && [ "${FOUND_VID:-}" == "303A" ]; }; then
  NATIVE_USB=1
fi
# Serial goes to the port you are plugged into: the "UART" port (usbserial,
# USB-UART chip) needs CDC off, the native "USB" port (usbmodem) needs CDC on.
case "${BOARD:-esp32s3}" in
  esp32s3)
    if [ "$NATIVE_USB" == 1 ]; then FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc"
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
