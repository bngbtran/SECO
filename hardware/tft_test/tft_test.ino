/*
 * Test màn ILI9341 (2.4" / 2.8" SPI, 240x320) trên ESP32-S3 DevKitC.
 * Nạp xong màn phải sáng, chạy lần lượt đỏ / xanh lá / xanh dương / trắng,
 * rồi hiện chữ và một bộ đếm giây. Không cần WiFi, không cần config.h.
 *
 * Thư viện: Adafruit ILI9341 + Adafruit GFX Library.
 * Board: ESP32S3 Dev Module, USB CDC On Boot: Enabled (để có log Serial).
 *
 * Nối dây (dùng chung bus SPI với MFRC522 của admin_reader, chỉ khác chân CS):
 *
 *   ILI9341     ESP32-S3
 *   VCC         3V3 (module có jumper J1 thì để hở, cấp 5V vào VCC cũng được)
 *   GND         GND
 *   CS          GPIO 15
 *   RESET       GPIO 17
 *   DC/RS       GPIO 16
 *   SDI(MOSI)   GPIO 11
 *   SCK         GPIO 12
 *   LED         GPIO 18 (hoặc nối thẳng 3V3 cho đèn nền luôn sáng)
 *   SDO(MISO)   GPIO 13 (không bắt buộc)
 *   T_* (touch) không nối
 */
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>

#define TFT_SCK 12
#define TFT_MOSI 11
#define TFT_MISO 13
#define TFT_CS 15
#define TFT_DC 16
#define TFT_RST 17
#define TFT_LED 18

// Màn chập chờn / sai màu thì hạ xuống 20000000 hoặc 10000000.
#define TFT_SPI_HZ 40000000

Adafruit_ILI9341 tft(&SPI, TFT_DC, TFT_CS, TFT_RST);

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("ILI9341 test");

  // Bật đèn nền trước: màn trắng xóa = có đèn nhưng chưa nhận lệnh SPI.
  pinMode(TFT_LED, OUTPUT);
  digitalWrite(TFT_LED, HIGH);

  SPI.begin(TFT_SCK, TFT_MISO, TFT_MOSI, TFT_CS);
  tft.begin(TFT_SPI_HZ);

  // Đọc thử thanh ghi: 0x00 hoặc 0xFF thường là MISO chưa nối hoặc sai dây.
  Serial.printf("Power mode: 0x%02X\n", tft.readcommand8(ILI9341_RDMODE));
  Serial.printf("Pixel fmt:  0x%02X\n", tft.readcommand8(ILI9341_RDPIXFMT));

  tft.setRotation(1);  // ngang, 320x240
  const uint16_t colors[] = {ILI9341_RED, ILI9341_GREEN, ILI9341_BLUE, ILI9341_WHITE};
  for (uint16_t c : colors) {
    tft.fillScreen(c);
    delay(400);
  }

  tft.fillScreen(ILI9341_BLACK);
  tft.drawRect(0, 0, tft.width(), tft.height(), ILI9341_YELLOW);
  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(3);
  tft.setCursor(20, 30);
  tft.println("SECO");
  tft.setTextSize(2);
  tft.setCursor(20, 80);
  tft.println("ILI9341 + ESP32-S3");
  tft.setCursor(20, 110);
  tft.printf("%dx%d OK", tft.width(), tft.height());
  Serial.println("Done drawing");
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last < 1000) return;
  last = millis();

  tft.fillRect(20, 160, 280, 30, ILI9341_BLACK);
  tft.setTextColor(ILI9341_CYAN);
  tft.setTextSize(3);
  tft.setCursor(20, 160);
  tft.printf("Uptime %lus", millis() / 1000);
}
