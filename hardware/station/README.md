# Trạm sạc EVCS-01: phần cứng thật

Firmware cho trạm sạc thật ở viện FSB (`EVCS-01`). Nghiệp vụ và giao thức MQTT giống hệt bản mô phỏng `simulator/station`; chỉ khác phần cứng. Chân và linh kiện lấy theo sketch test phần cứng của nhóm.

Backend và web không cần sửa gì. Trong database, `EVCS-01` là trạm thật (`is_simulated: false`): kỹ thuật viên phải quẹt thẻ + giữ STOP tại trạm để đưa trạm từ lỗi về sẵn sàng.

## 1. Chân nối (ESP32-S3 DevKitC-1)

| Linh kiện | Chân ESP32-S3 | Ghi chú |
|---|---|---|
| MFRC522 (SPI) | SDA/CS 10, RST 14 | SCK 12, MOSI 11, MISO 13 dùng chung với TFT. Cấp **3V3** |
| TFT ILI9341 2.8" (SPI) | CS 15, DC 6, RST 21 | cùng bus SPI |
| INA219 (I2C 0x40) | SDA 8, SCL 9 | đo dòng pin 9V → EV |
| DS18B20 | 4 | điện trở kéo lên 4,7 kΩ lên 3V3 |
| Nút **PLUG** | 5 → GND | **nút nhấn**: mỗi lần nhấn = cắm sạc / rút sạc |
| Nút **STOP** (kỹ thuật viên) | 7 → GND | |
| Relay (fail-safe) | 16 | HIGH = đóng; luôn tắt lúc khởi động |
| Servo khoá đầu sạc | **45** | 0° mở, 90° khoá |
| Buzzer | 18 | |
| Joystick VRX / SW | 1 (ADC) / 2 → GND | chân "5V" của module nối **3V3**, KHÔNG nối 5V |
| LED RGB trạng thái (common cathode) | R 41, G 38, B 39 | chân chung → GND, mỗi chân màu qua 220 Ω. Xanh lá READY, xanh dương CHARGING, vàng chờ thanh toán, đỏ ERROR (nháy = offline), cam MAINTENANCE |
| LED EV | không nối GPIO | sáng khi relay đóng (pin 9V → relay → INA219 → EV) |

`diagram.json` là sơ đồ Wokwi đúng theo bảng trên (servo GPIO 45, PLUG là nút nhấn, một LED RGB).

## 2. Cấu hình

Copy `config.example.h` thành `config.h` (cùng thư mục) rồi sửa WiFi. `config.h` nằm trong `.gitignore`, **không commit mật khẩu WiFi**.

```c
#define WIFI_SSID "FSB-Student"        // WiFi 2.4 GHz
#define WIFI_PASSWORD "..."
#define MQTT_HOST "broker.hivemq.com"   // broker của API trên Render
#define MQTT_PORT 8883
#define MQTT_TLS 1
#define STATION_ID "EVCS-01"
#define OVER_TEMPERATURE_C 35.0f        // hạ xuống 28.0f chỉ khi muốn thử bảo vệ quá nhiệt bằng tay
```

## 3. Nạp firmware (PowerShell)

```powershell
cd D:\IAD501\Project\SECO\hardware\station
powershell -ExecutionPolicy Bypass -File .\flash.ps1               # build + nạp + Serial Monitor
powershell -ExecutionPolicy Bypass -File .\flash.ps1 -MonitorOnly  # chỉ xem log
```

Script tự dùng conda env `iad`, cài thư viện còn thiếu và tìm cổng COM. **Không chạm joystick lúc khởi động**: firmware đo điểm giữa của joystick trong lúc đó. Log đúng:

```
[boot] INA219 0x40: ok
[boot] MFRC522 version: 0x92
[boot] DS18B20 sensors: 1
[joy] centre X=1890 ok
[boot] WiFi "FSB-Student"..... connected, IP 192.168.x.x
```

## 4. Test nghiệp vụ trên trạm thật

**Member** (thẻ đã gán cho member trên web admin):
1. Trạm READY (đèn RGB xanh lá), màn hình chào "Xin chao!". Nhấn **PLUG** (màn hình "Da cam sac (PLUG)").
2. Quẹt thẻ: màn hình hiện **THANH VIEN** + tên + số dư. Gạt joystick trái / phải chọn ngân sách (≤ số dư), **nhấn joystick để bắt đầu** (trạm không tự sạc; để yên 60 giây thì huỷ, về màn hình chào).
3. Relay đóng, LED EV sáng, đèn RGB xanh dương, app member thấy phiên sạc realtime.
4. Nhấn **PLUG** lần nữa (rút sạc): phiên kết thúc, màn hình hiện số tiền đã trừ và số dư.

**Khách** (không quẹt thẻ): ở màn hình chào gạt joystick chọn ngân sách (Không GH / 10k / 20k / 50k / 100k), nhấn PLUG, nhấn joystick (START). Nhấn PLUG để rút: servo khoá 90°, màn hình hiện QR; thanh toán xong servo mở.

**An toàn**: nắm DS18B20 cho nóng tới ngưỡng (`OVER_TEMPERATURE_C`): relay ngắt, phiên dừng, trạm sang ERROR (đèn đỏ). Kỹ thuật viên nhận thông báo trên app.

**Kỹ thuật viên**: ở ERROR, quẹt thẻ KTV rồi **giữ STOP 3 giây** → MAINTENANCE (đèn cam). Sửa xong, quẹt thẻ + giữ STOP 3 giây → READY. Sau đó KTV mới hoàn tất yêu cầu hỗ trợ của member trên app.

**Admin**: web admin → Trạm sạc → EVCS-01 gửi lệnh STOP / LOCK / UNLOCK / MAINTENANCE / READY; trạm trả `cmd_ack`.

Gõ UID hex + Enter trong Serial Monitor để giả lập quẹt thẻ.

## 5. Khác với bản mô phỏng Wokwi

Bản mô phỏng chạy **cùng một code** với trạm thật: cùng chân (servo GPIO 45), cùng cách xử lý joystick, cùng các mức ngân sách và cùng giao thức MQTT. Chỉ khác phần cấu hình:

| | `simulator/station` (Wokwi) | `hardware/station` (trạm thật) |
|---|---|---|
| WiFi | `Wokwi-GUEST`, kênh 6 cố định | `config.h`, kênh tự dò |
| MQTT | `broker.hivemq.com:1883`, không TLS (gateway Wokwi không mở được TLS) | `broker.hivemq.com:8883`, TLS |
| INA219, pin 9V | chip tự viết trong `chips/`, chỉnh bằng thanh trượt | linh kiện thật |

## 6. Lỗi thường gặp

| Hiện tượng | Cách sửa |
|---|---|
| `[joy] centre ... INVALID` | VRX chưa nối GPIO 1, hoặc module chưa có nguồn 3V3 (breadboard dài hay bị ngắt ray nguồn ở giữa) |
| Gạt joystick bị nhảy 2 mức / nhảy ngược | Module đang cấp 5V: chuyển sang 3V3. Không chạm joystick lúc khởi động |
| `MFRC522 version: 0x00 / 0xFF` | Sai dây SPI hoặc chưa cấp 3V3 cho MFRC522 |
| `INA219 0x40: NOT FOUND` | Sai SDA 8 / SCL 9 hoặc thiếu VCC |
| `DS18B20 sensors: 0` | Thiếu điện trở kéo lên 4,7 kΩ, sai chân 4 |
| WiFi không vào | WiFi phải 2.4 GHz; kiểm tra `config.h` |
| Web báo trạm offline | Trạm chưa kết nối MQTT, hoặc `STATION_ID` khác `EVCS-01` |

Chỉ bật **một** trạm `EVCS-01` cùng lúc: tắt Wokwi `simulator/station` khi chạy trạm thật.
