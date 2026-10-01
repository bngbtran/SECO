# Trụ sạc EVCS-01 (Wokwi)

Mô phỏng một trụ sạc SECO hoàn chỉnh theo DD v1.2: member quẹt thẻ, guest nhập ngân sách và thanh toán QR, an toàn (E-Stop, quá nhiệt) và kỹ thuật viên bảo trì. Trụ nói chuyện với API qua MQTT `evcs/EVCS-01/*`.

## Chạy

Mọi lệnh đều chạy trong môi trường conda `iad`: mở terminal rồi `conda activate iad` trước. `build.sh` tự kích hoạt `iad` nếu bạn quên.

1. Chạy Mosquitto ở cổng 1883 và API (`cd apps/api && python -m app`). Station `EVCS-01` phải có trong DB (đã có trong seed).
2. Build firmware: `./build.sh` (cần `arduino-cli`, script tự cài core ESP32 và các thư viện).
3. Trong VS Code, mở `diagram.json` rồi chạy **Wokwi: Start Simulator**.

Firmware kết nối `host.wokwi.internal:1883`. Đây là máy của bạn, đi qua Wokwi Private Gateway.

## Phần cứng

| Chức năng (DD 7) | Linh kiện Wokwi | Chân |
|---|---|---|
| LCD 16x2 I2C 0x27 | `lcd` | SDA 8, SCL 9 |
| OLED 128x64 0x3C (QR, trạng thái) | `oled` | SDA 8, SCL 9 (chung bus, nối qua breadboard) |
| RFID MFRC522 | `rfid` | SS 10, MOSI 11, SCK 12, MISO 13, RST 14 |
| DS18B20 nhiệt độ | `temp` | 4 |
| INA219 (dòng tải) | `current`, biến trở thay INA219 vì Wokwi không có INA219 | ADC 1 |
| Micro-switch đầu sạc | `plug`, gạt **phải** = đã cắm | 5 |
| Nút START | `btnStart` | 6 |
| Nút STOP | `btnStop` | 7 |
| E-Stop (giữ trạng thái) | `estop`, gạt **phải** = nhấn E-Stop | 15 |
| Relay fail-safe + tải 12V | `relay`, LED `load` sáng khi đang cấp điện | 16 |
| Khóa đầu sạc | `lock` (servo, 90° = khóa) | 17 |
| Buzzer | `buzzer` | 18 |
| Chọn ngân sách | `joystick` | HORZ 2, VERT 3 (ADC), SEL 39 |

Breadboard chỉ dùng để chia nguồn. Rail trên là 3.3V/GND, rail dưới là 5V/GND. Mỗi chân ESP chỉ có một dây.

**Chọn ngân sách bằng joystick (ở trạng thái READY):** các mức cố định là Không giới hạn, 10.000, 20.000, 50.000, 100.000, 200.000 và 500.000đ.

- Gạt joystick sang phải hoặc xuống để tới mức tiếp theo, sang trái hoặc lên để lùi lại. LCD hiện `< 50.000d >`.
- Nhấn joystick để chọn mức đang hiện.
- Nếu để yên 10 giây thì hủy, không chọn gì.

Nếu không chọn mức nào: member dùng ngân sách đã đặt trên app, hoặc sạc tối đa bằng số dư; guest không giới hạn. Mức đã chọn chỉ áp dụng cho phiên sạc kế tiếp.

**Thẻ RFID:** bấm vào đầu đọc để chọn thẻ. Thẻ xanh có UID `A1:B2:C3:D4`, là thẻ của `user@seco.com` trong seed. Để thử thẻ kỹ thuật viên `7E1C2A9F` (`tech@seco.com`) hoặc thẻ đã khóa `D4E5F6A7`, có hai cách:

- Gõ UID vào Serial Monitor rồi Enter.
- Đổi attr `uid` của `rfid` trong `diagram.json`.

## Luồng

**Member**
1. Ở READY, chọn ngân sách bằng joystick (tùy chọn). Nếu không nhập, trụ dùng ngân sách đã đặt trên app.
2. Quẹt thẻ. Trụ gửi `RFID_VERIFY`, LCD hiện tên và số dư.
3. Cắm sạc (gạt `plug` sang phải). Nếu đã cắm từ trước, trụ bắt đầu ngay. Trụ gửi `SESSION_START`, không cần nhấn START.
4. Trạng thái CHARGING: relay đóng, LED tải sáng. LCD hiện kWh và tiền: cứ mỗi 30 giây thêm 0,00125 kWh, tương đương 5đ.
5. Phiên dừng khi rút sạc (`UNPLUGGED`), khi hết ngân sách hoặc số dư (API gửi `STOP`), hoặc khi có sự cố an toàn. API trừ ví, LCD hiện tổng.

**Guest**
1. Chọn ngân sách bằng joystick (tùy chọn), cắm sạc, nhấn START. Trụ gửi `GUEST_START`.
2. Rút sạc: trụ gửi `UNPLUGGED`. API gửi lần lượt `STOP`, `LOCK` (servo khóa) và `PAYMENT_QR` (mã QR hiện trên OLED, số tiền hiện trên LCD).
3. Thanh toán trên web/app (sandbox). API gửi `UNLOCK`: servo mở, trụ về READY.
4. Nếu QR hết hạn (`PAYMENT_STATUS EXPIRED`), nhấn START để tạo QR mới (`GUEST_PAYMENT_REQUEST`).

**An toàn:** khi gạt `estop` sang phải, hoặc kéo nhiệt độ `temp` lên ≥ 60°C, relay cắt ngay. Trụ gửi `ESTOP` / `OVER_TEMPERATURE` và chuyển sang ERROR. Nếu mất cảm biến nhiệt, trụ gửi `SENSOR_FAULT`.

**Kỹ thuật viên**
1. Ở ERROR, quẹt thẻ KTV (`TECHNICIAN_VERIFY`), rồi giữ STOP 3 giây. Trụ gửi `TECHNICIAN_ACTION ENTER_MAINTENANCE` và chuyển sang MAINTENANCE.
2. Sửa lỗi: nhả E-Stop, hạ nhiệt độ.
3. Quẹt thẻ KTV lần nữa, giữ STOP 3 giây. Trụ gửi `EXIT_MAINTENANCE` và về READY.

STOP không kèm thẻ KTV thì không có tác dụng. Thẻ KTV quẹt lúc READY sẽ đưa trụ vào bảo trì chủ động.

**Admin (web):** các lệnh STOP, MAINTENANCE, READY, RESET, LOCK, UNLOCK, PING. Mỗi lệnh đều được trả `cmd_ack`.

**Kết nối**
- Trụ gửi `hb` mỗi 10 giây.
- `status` là `ONLINE` (retained). Last Will là `OFFLINE`.
- Mỗi lần kết nối, trụ gửi `STATION_SYNC` để khôi phục phiên đang sạc, QR đang chờ, hoặc trạng thái ERROR/MAINTENANCE.
- Telemetry (`voltage_v`, `current_a`, `power_kw`, `temperature_c`) gửi mỗi 3 giây khi đang sạc và mỗi 15 giây khi rảnh.
