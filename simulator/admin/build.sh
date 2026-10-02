#!/usr/bin/env bash
# Build the firmware for Wokwi (VS Code) with arduino-cli.
# arduino-cli needs the .ino inside a folder of the same name, so compile a copy.
set -euo pipefail
cd "$(dirname "$0")"

# Project environment: every SECO command runs inside conda env "iad".
if [ "${CONDA_DEFAULT_ENV:-}" != "iad" ]; then
  source "$(conda info --base)/etc/profile.d/conda.sh"
  conda activate iad
fi

FQBN="esp32:esp32:esp32s3"

if ! arduino-cli core list | grep -q "^esp32:esp32"; then
  echo "Installing the ESP32 core..."
  arduino-cli core update-index
  arduino-cli core install esp32:esp32
fi
for lib in PubSubClient ArduinoJson MFRC522 "Adafruit SSD1306" "Adafruit GFX Library"; do
  arduino-cli lib list | grep -q "^${lib} " || arduino-cli lib install "$lib"
done

rm -rf .build-src && mkdir -p .build-src/sketch build
cp sketch.ino .build-src/sketch/sketch.ino
arduino-cli compile --fqbn "$FQBN" --output-dir build .build-src/sketch
rm -rf .build-src

echo "Built build/sketch.ino.merged.bin - start the simulator in VS Code (Wokwi: Start Simulator)."
