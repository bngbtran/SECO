# Wokwi simulator: admin desk RFID reader (FR02)

`READER-01` is the RFID reader on the admin desk (ESP32-S3 + OLED SSD1306 128x64 I2C).
It is used to give a member their card; it is **not** a charging station.

## Business flow

1. **Web Admin → Người dùng → Tạo member**, fill in the basic info,
   click **Tạo & gán thẻ**.
2. The web opens **Member details** and immediately asks the reader for a card:
   the dialog shows *"Quẹt thẻ lên đầu đọc tại quầy"*, the OLED shows
   `QUET THE CHO: <MEMBER>`.
3. Tap the member's card on the MFRC522 reader. Its UID appears in the dialog.
   A card that belongs to someone else is refused (`THE DA CO CHU`); tap another one.
4. Optionally type a label and click **Hoàn tất**. The UID is saved as the
   member's card and the OLED shows `DA GAN THE`.
   From now on the member taps this card at a charging station to start charging.

**Update a card** (lost / broken card): on Member details click **Đổi thẻ** on
the current card and tap the new card. After **Hoàn tất** the old card is
unlinked and can no longer start a charge. Members that already exist but have
no card get a **Gán thẻ** button in the *Thẻ RFID* tab.

## Tapping a card

The circuit has a real **MFRC522** reader (Wokwi part `board-mfrc522`, SPI).
Click the reader in the simulator to open its control panel, pick a card and
press **Tap** (a 0.5 s tap) or toggle **Hold**.

| Wokwi card | UID sent | Use it to |
|---|---|---|
| Blue Card (UID set to `A1:B2:C3:D4` in `diagram.json`) | `A1B2C3D4` | see a refusal: it belongs to `user@seco.com` |
| Green Card | `11223344` | assign a new card |
| Yellow Card | `55667788` | replace it with another new card |
| Red Card / NFC Tag | `AABBCCDD` / `04112233445566` | more new cards |
| Key Fob | `C0FFEE99` | see a refusal: it is the technician's card (`tech@seco.com`) |

To use other UIDs, change the Blue Card `uid` attribute in `diagram.json`, or
type a hex UID + Enter in the Serial Monitor.

Wiring (ESP32-S3), straight to the board, one wire per ESP32 pin:

| ESP32-S3 | Goes to |
|---|---|
| 3V3.1 | MFRC522 3.3V |
| 3V3.2 | OLED VCC |
| GND.1 / GND.2 | OLED GND / MFRC522 GND |
| GPIO 10, 11, 12, 13, 14 | MFRC522 SDA, MOSI, SCK, MISO, RST |
| GPIO 8, 9 | OLED SDA, SCL (address 0x3C) |

## Run it (Wokwi for VS Code)

**Broker:** `#define MQTT_CLOUD 1` (default) connects to `broker.hivemq.com:1883` (plain, `MQTT_TLS 0`: the Wokwi gateway cannot open TLS to 8883, rc=-2; it is the same broker on both ports), the
broker of the API deployed on Render, so the web app at `https://iad-seco.vercel.app` can assign
cards with nothing running locally. Set `MQTT_CLOUD 0` to use Mosquitto on your computer with the
API run locally (steps below).

Prerequisites: the **Wokwi Simulator** VS Code extension (activate the license
once with *Wokwi: Request a New License*), `arduino-cli`, and Mosquitto on
port 1883 (the broker the API uses).

```bash
conda activate iad    # every command below runs in the project env

# API (apps/api) and web (repo root), as usual
python -m app
npm run dev:web

# Build the firmware
cd simulator/admin
./build.sh            # installs the ESP32 core and libraries on first run
```

Then open `simulator/admin/diagram.json` in VS Code and run
**Wokwi: Start Simulator** (F1). The Serial Monitor shows the MQTT traffic, and
the *Gán thẻ* dialog stops saying the reader is offline.

Rebuild with `./build.sh` after editing `sketch.ino`, then restart the simulator.

### Network

The firmware connects to `host.wokwi.internal:1883`, which Wokwi for VS Code
maps to your computer through its private IoT gateway. If your setup cannot
reach the host, use a public broker on both sides (demo only, anyone can read it):

- `sketch.ino`: `MQTT_HOST = "broker.hivemq.com"`, then `./build.sh`;
- `apps/api/.env`: `MQTT_HOST=broker.hivemq.com`, then restart the API.

On wokwi.com, copy `sketch.ino`, `diagram.json` and `libraries.txt` into a new
ESP32-S3 project and use a public broker the same way.

## MQTT contract

| Direction | Topic | Payload |
|---|---|---|
| API → reader | `seco/reader/READER-01/cmd` | `{"command":"SCAN","registration_id","mode":"ASSIGN"\|"REPLACE","user_name","timeout_s":90}` |
| reader → API | `seco/reader/READER-01/scan` | `{"uid","registration_id"}` |
| API → reader | `seco/reader/READER-01/res` | `{"ok":true,"uid","user_name"}` or `{"ok":false,"error":"UID_TAKEN"\|"NO_REQUEST","uid","message"}` |
| API → reader | `seco/reader/READER-01/cmd` | `{"command":"DONE","registration_id","uid","user_name"}` |
| API → reader | `seco/reader/READER-01/cmd` | `{"command":"CANCEL","registration_id"}` (cancelled, replaced or timed out) |
| reader → API | `seco/reader/READER-01/hb` | `{"fw","waiting"}` every 10 s |
| reader → API | `seco/reader/READER-01/status` | `ONLINE` retained, `OFFLINE` as last will |

The API uses the reader in `ADMIN_READER_ID` (default `READER-01`). The reader
serves one request at a time; a newer request cancels the previous one, and a
request expires after 90 s. REST endpoints: `/api/v1/admin/card-registrations`.

## Files

| File | Purpose |
|---|---|
| `sketch.ino` | Reader firmware (Arduino, ESP32-S3) |
| `diagram.json` | Wokwi circuit: ESP32-S3, MFRC522, OLED SSD1306 I2C (no breadboard) |
| `wokwi.toml` | Points Wokwi for VS Code to the built firmware in `build/` |
| `build.sh` | Builds the firmware with arduino-cli |
| `libraries.txt` | Library list for the Wokwi web editor |
