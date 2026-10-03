# Đầu đọc thẻ tại quầy (phần cứng thật, Arduino IDE)

Firmware cho đầu đọc RFID thật ở quầy admin. Dùng để **gán thẻ / đổi thẻ cho member**: admin bấm "Gán thẻ" trên web, quẹt thẻ thật lên MFRC522, UID của thẻ hiện ra trên web, bấm "Hoàn tất" là UID được lưu cho member.

Firmware dùng chung giao thức MQTT với bản mô phỏng `simulator/admin` (`seco/reader/READER-01/*`), nên backend và web **không cần sửa gì**. Hai bản chỉ khác nhau ở WiFi, IP broker và chân nối.

## 1. Linh kiện

- ESP32 DevKit (ESP32-WROOM) **hoặc** ESP32-S3 DevKitC
- Module RFID **MFRC522** (RC522), thẻ / móc khóa Mifare 13.56 MHz
- Màn OLED SSD1306 128x64 I2C (0.96"). Không bắt buộc, xem `OLED_ENABLED`.

## 2. Nối dây

Sketch tự chọn chân theo board bạn chọn trong *Tools > Board*.

| MFRC522 | ESP32 DevKit | ESP32-S3 DevKitC |
|---|---|---|
| SDA (SS) | GPIO 5 | GPIO 10 |
| SCK | GPIO 18 | GPIO 12 |
| MOSI | GPIO 23 | GPIO 11 |
| MISO | GPIO 19 | GPIO 13 |
| RST | GPIO 4 | GPIO 14 |
| 3.3V | **3V3** (không cắm 5V) | **3V3** |
| GND | GND | GND |
| IRQ | không nối | không nối |

| OLED I2C | ESP32 DevKit | ESP32-S3 DevKitC |
|---|---|---|
| SDA | GPIO 21 | GPIO 8 |
| SCL | GPIO 22 | GPIO 9 |
| VCC | 3V3 | 3V3 |
| GND | GND | GND |

> MFRC522 chạy 3.3V. Cắm vào 5V có thể làm hỏng module.

## 3. Cài Arduino IDE

1. **Board:** *Tools > Board > Boards Manager*, cài **esp32 by Espressif Systems**.
2. **Thư viện:** *Tools > Manage Libraries*, cài:
   - **PubSubClient** (Nick O'Leary)
   - **ArduinoJson** (Benoit Blanchon, bản 7.x)
   - **Adafruit SSD1306** (cài kèm **Adafruit GFX Library** khi được hỏi)
   - **MFRC522** (GithubCommunity)
3. **Chọn board:** *Tools > Board*, chọn **ESP32 Dev Module** hoặc **ESP32S3 Dev Module**.
   - Với ESP32-S3 cắm vào cổng USB native: bật *Tools > USB CDC On Boot: Enabled* thì Serial Monitor mới có log.

## 4. Cấu hình

Copy `config.example.h` thành `config.h` (cùng thư mục) rồi sửa:

```c
#define WIFI_SSID "ten-wifi"            // WiFi 2.4 GHz
#define WIFI_PASSWORD "mat-khau"
#define MQTT_HOST "192.168.1.56"        // IP LAN của máy chạy Mosquitto + API
#define READER_ID "READER-01"           // = ADMIN_READER_ID trong apps/api/.env
#define OLED_ADDRESS 0x3C               // màn không hiện gì thì thử 0x3D
```

- `config.h` đã nằm trong `.gitignore`. **Không commit mật khẩu WiFi.**
- IP của máy: `ipconfig getifaddr en0` (macOS). ESP32 và laptop phải ở **cùng một mạng WiFi**.
- **Không** dùng `localhost` hay `host.wokwi.internal`: đó là địa chỉ cho bản mô phỏng.

**Chọn broker trong `config.h`.** Đầu đọc và API phải dùng chung một broker:
- **API trên Render:** `MQTT_HOST "broker.hivemq.com"`, `MQTT_PORT 8883`, `MQTT_TLS 1`. Đây là mặc định trong `config.example.h`.
- **API chạy local:** `MQTT_HOST` là IP LAN của máy, `MQTT_PORT 1883`, `MQTT_TLS 0`.

## 5. Chạy

Mọi lệnh đều chạy sau `conda activate iad`.

1. **Mosquitto** phải nghe trên mạng LAN, không chỉ localhost:
   ```bash
   mosquitto -c hardware/mosquitto.conf -v
   ```
   Nếu máy đã có Mosquitto đang chạy ở cổng 1883 thì bỏ qua bước này.
2. **API** (`cd apps/api && python -m app`) và **web** (`npm run dev:web`).
3. Mở `hardware/admin_reader/admin_reader.ino` bằng Arduino IDE, bấm **Upload**.
4. Mở **Serial Monitor**, baud **115200**. Log đúng sẽ như sau:
   ```
   MFRC522 Firmware Version: 0x92 = v2.0
   WiFi connected, IP 192.168.1.80
   MQTT connected to 192.168.1.56:1883
   ```

**Lưu ý:** chỉ được bật **một** đầu đọc `READER-01` cùng lúc. Nếu Wokwi `simulator/admin` cũng đang chạy, hãy tắt nó đi. Nếu không, cả hai cùng nhận lệnh SCAN.

## 6. Gán thẻ cho member

1. Vào web admin, mở **Người dùng > Tạo member**, bấm **Tạo & gán thẻ**. Với member đã có, vào **Member Details > Gán thẻ** (hoặc **Đổi thẻ**).
2. OLED hiện `QUET THE CHO: <TEN>`. Đưa thẻ lại gần đầu đọc, cách khoảng 1–3 cm.
3. UID hiện trên dialog của web. OLED hiện `DA DOC <UID>`. Serial Monitor in ra `Card 04A31F7C (MIFARE 1KB)`.
4. Bấm **Hoàn tất**. OLED hiện `DA GAN THE`. Từ giờ member quẹt thẻ này ở trụ sạc là bắt đầu sạc được.

Nếu thẻ đã thuộc về người khác, OLED hiện `THE DA CO CHU`; hãy quẹt thẻ khác.

UID được gửi dưới dạng hex viết hoa, không có dấu phân cách (ví dụ `04A31F7C`), cùng định dạng với trụ sạc. Một thẻ gán ở quầy sẽ được trụ sạc nhận đúng.

## 7. Lỗi thường gặp

| Hiện tượng | Nguyên nhân / cách sửa |
|---|---|
| `Firmware Version: 0x0 / 0xFF`, `MFRC522 not answering` | Sai dây SPI hoặc chưa cấp 3.3V. Kiểm tra lại bảng ở mục 2. |
| Quẹt thẻ không có phản ứng | Thẻ không phải loại 13.56 MHz (thẻ 125 kHz không đọc được), hoặc đưa quá xa. |
| `WiFi failed, retrying` | Sai SSID hoặc mật khẩu, hoặc WiFi chỉ phát 5 GHz. |
| `MQTT connect failed, rc=-2` | Sai `MQTT_HOST`, khác mạng WiFi, hoặc Mosquitto chỉ nghe trên localhost. Xem bước 5.1. |
| OLED không hiện gì / log `OLED not found` | Kiểm tra SDA/SCL, thử `OLED_ADDRESS 0x3D`. |
| Web báo đầu đọc offline | `READER_ID` không khớp với `ADMIN_READER_ID` của API, hoặc ESP32 chưa kết nối MQTT. |
| Muốn thử mà chưa có thẻ | Gõ UID hex vào Serial Monitor rồi Enter, ví dụ `04A31F7C`. |

## Chạy từ VS Code (không cần Arduino IDE)

Dùng `arduino-cli` trong terminal của VS Code:

```bash
conda activate iad
cd hardware/admin_reader
cp config.example.h config.h      # lần đầu, rồi sửa WiFi + MQTT_HOST
./flash.sh                        # ESP32-S3 (mặc định)
BOARD=esp32 ./flash.sh            # ESP32 DevKit thường
```

Script tự cài core ESP32 và các thư viện nếu thiếu, tìm cổng USB, build, nạp firmware rồi mở Serial Monitor (Ctrl+C để thoát). Phải đóng Serial Monitor của Arduino IDE trước, vì hai chương trình không dùng chung được một cổng.

Nếu không thấy cổng: dùng cáp USB có truyền dữ liệu, không phải cáp chỉ để sạc. Kiểm tra bằng `ls /dev/cu.usb*`. Nếu board không tự vào chế độ nạp, giữ nút **BOOT** khi bắt đầu upload.

**Windows:** chạy `./flash.sh` trong **Git Bash** (terminal của VS Code chọn Git Bash). Script tự tìm cổng `COMx` của ESP32 (bỏ qua cổng Bluetooth). Kiểm tra cổng bằng `python -m serial.tools.list_ports -v`. Không thấy cổng thì cài driver USB-UART của board: **CP210x** (Silicon Labs) hoặc **CH340** (WCH). Chọn cổng thủ công: `PORT=COM3 ./flash.sh`.

## Không có màn hình: `no_lcd/`

Dùng để test chức năng **gán thẻ** khi chỉ có ESP32 + MFRC522. Bản này cùng giao thức MQTT với bản có màn hình, nên web và API không cần sửa gì.

- `no_lcd/no_lcd.ino`: firmware không dùng OLED (không cần thư viện Adafruit).
- `no_lcd/no_lcd.json`: sơ đồ Wokwi gồm ESP32-S3 + MFRC522, đi dây giống board thật (mục 2).

Nối dây MFRC522 như bảng ở mục 2, bỏ qua phần OLED.

**Phản hồi thay cho màn hình:**

| Trạng thái | Serial Monitor | LED ESP32-S3 (RGB) | LED ESP32 DevKit (GPIO 2) |
|---|---|---|---|
| Chưa kết nối MQTT | `MQTT connect failed ...` | vàng nhấp nháy | nháy chậm |
| Sẵn sàng | `[READY] ...` | tắt | chớp ngắn mỗi 3 giây |
| Web bấm "Gán thẻ" | `[WAITING] assign card to <tên> ...` | xanh dương nhấp nháy | nháy nhanh |
| Đọc thẻ thành công | `[CARD READ] <UID> ...` | xanh lá | 1 lần sáng dài |
| Web bấm "Hoàn tất" | `[ASSIGNED] card <UID> now belongs to <tên>` | xanh lá | 1 lần sáng dài |
| Thẻ đã có chủ / chưa có yêu cầu / hết giờ | `[REFUSED]` / `[NO REQUEST]` / `[TIMEOUT]` | đỏ | 3 lần chớp |

Gõ `?` + Enter trong Serial Monitor để in trạng thái (WiFi, MQTT, MFRC522, yêu cầu gán thẻ đang mở). Gõ UID hex + Enter để giả lập quẹt thẻ.

**Phần cứng thật:** `flash.sh` tự chép `config.h` vào `no_lcd/` rồi nạp:

```bash
conda activate iad
cd hardware/admin_reader
SKETCH=no_lcd ./flash.sh              # ESP32-S3
SKETCH=no_lcd BOARD=esp32 ./flash.sh  # ESP32 DevKit
```

Với Arduino IDE: copy `config.h` vào `no_lcd/`, rồi mở `no_lcd/no_lcd.ino`.

**Wokwi (wokwi.com):** tạo project ESP32-S3, dán `no_lcd.ino` vào `sketch.ino` và `no_lcd.json` vào `diagram.json`, thêm thư viện `PubSubClient`, `ArduinoJson`, `MFRC522` vào `libraries.txt`. Không có `config.h` thì firmware tự dùng WiFi `Wokwi-GUEST` và broker `broker.hivemq.com:8883`, tức là nói chuyện thẳng với API trên Render. Bấm vào MFRC522 trong simulator để chọn thẻ và **Tap**.

Chỉ bật **một** đầu đọc `READER-01` cùng lúc (bản có màn hình, bản `no_lcd` hoặc Wokwi `simulator/admin`).

## Màn hình màu ILI9341: `tft_reader/`

Đầu đọc có màn TFT ILI9341 2.8" (240x320). Ngoài chức năng **gán thẻ** như các bản khác, bản này còn **tra cứu thẻ**: quẹt một thẻ đã gán khi web không mở "Gán thẻ", API tìm chủ thẻ trong DB và màn hình hiện:

- "Quẹt thẻ thành công" (hoặc "Thẻ đã bị khoá" / "Tài khoản bị khoá" màu đỏ)
- Tên chủ thẻ, vai trò, nhãn thẻ
- **Số dư ví** (`balance_vnd`), UID thẻ

Thẻ chưa gán cho ai thì hiện "Thẻ chưa đăng ký".

| ILI9341 | ESP32-S3 DevKitC | ESP32 DevKit |
|---|---|---|
| CS | GPIO 15 | GPIO 15 |
| DC (D/C, RS) | GPIO 6 | GPIO 2 |
| RST | GPIO 21 | GPIO 27 |
| SCK | GPIO 12 (chung MFRC522) | GPIO 18 |
| SDI (MOSI) | GPIO 11 (chung MFRC522) | GPIO 23 |
| SDO (MISO) | GPIO 13 (chung MFRC522) | GPIO 19 |
| VCC, LED | 3V3 | 3V3 |
| GND | GND | GND |

MFRC522 nối như mục 2. Muốn đổi chân TFT: thêm `#define TFT_CS ...`, `TFT_DC`, `TFT_RST` vào `config.h`.

```bash
SKETCH=tft_reader PORT=COM8 ./flash.sh
```

Giao thức: khi không có yêu cầu gán thẻ, API trả về `seco/reader/<id>/res`:
`{"ok":true,"kind":"CARD_INFO","uid","user_name","role","card_label","card_active","user_active","balance_vnd"}`.
