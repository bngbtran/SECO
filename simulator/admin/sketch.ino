/*
 * SECO admin desk RFID reader (READER-01) - assign / replace a member's card.
 *
 * Flow (FR02, Web Admin > Member details)
 *   1. Admin creates a member, then clicks "Gán thẻ" (or "Đổi thẻ").
 *      The API sends cmd SCAN; the OLED shows "QUET THE CHO: <member>".
 *   2. Tap a card. The reader sends its UID; the web dialog shows it.
 *   3. Admin clicks "Hoàn tất": the API saves the UID as the member's card
 *      (a replaced card is unlinked) and sends cmd DONE -> "DA GAN THE".
 *   The member can then tap this card at a charging station to start charging.
 *
 * The card is read by an MFRC522 (SPI). In Wokwi click the reader to open its
 * control panel, pick a card and press "Tap". A hex UID typed in the Serial
 * Monitor (e.g. 04A31F7C + Enter) is accepted too.
 *
 * MQTT (this is not a charging station, so it has its own namespace)
 *   sub seco/reader/READER-01/cmd     SCAN | DONE | CANCEL
 *   sub seco/reader/READER-01/res     result of a scan
 *   pub seco/reader/READER-01/scan    {"uid", "registration_id"}
 *   pub seco/reader/READER-01/hb      heartbeat every 10 s
 *   pub seco/reader/READER-01/status  ONLINE (retained), OFFLINE via last will
 */

#include <ArduinoJson.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Fonts/FreeSansBold9pt7b.h>
#include <MFRC522.h>
#include <PubSubClient.h>
#include <SPI.h>
#include <WiFi.h>
#include <Wire.h>

// ---- Configuration ---------------------------------------------------------

const char *WIFI_SSID = "Wokwi-GUEST";
const char *WIFI_PASSWORD = "";

// host.wokwi.internal = your computer, reached through the Wokwi private
// gateway (VS Code). Run Mosquitto there on port 1883, same broker as the API.
const char *MQTT_HOST = "host.wokwi.internal";
const uint16_t MQTT_PORT = 1883;

// Must match ADMIN_READER_ID of the API (default READER-01).
const char *READER_ID = "READER-01";
const char *FIRMWARE = "desk-reader-1.0";

// MFRC522 on the ESP32-S3 SPI bus (FSPI default pins).
const uint8_t RFID_SCK = 12;
const uint8_t RFID_MISO = 13;
const uint8_t RFID_MOSI = 11;
const uint8_t RFID_SS = 10;   // "SDA" on the module
const uint8_t RFID_RST = 14;


const uint8_t I2C_SDA = 8;
const uint8_t I2C_SCL = 9;

const unsigned long HEARTBEAT_MS = 10000;
const unsigned long MESSAGE_MS = 3500;  // how long a result stays on the OLED

// ---- State -----------------------------------------------------------------

WiFiClient wifi;
PubSubClient mqtt(wifi);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);  // SSD1306 128x64 I2C, 0x3C
MFRC522 rfid(RFID_SS, RFID_RST);

bool waiting = false;           // a SCAN request is open
String registrationId;
String memberName;
bool replacing = false;
unsigned long deadline = 0;

unsigned long lastHeartbeat = 0;
unsigned long messageUntil = 0;  // 0 = show the home screen
String serialLine;

// OLED notice (see "OLED" below). Declared before any function: the Arduino
// builder puts the function prototypes there.
enum class Icon { Ok, Error, Info, Busy };

struct Notice {
  Icon icon;
  String title;
  String detail;
  String hint;
} notice;

String topic(const char *kind) { return String("seco/reader/") + READER_ID + "/" + kind; }

// ---- OLED ------------------------------------------------------------------

/** Keep the ASCII base letter of Vietnamese characters; the OLED font has no accents. */
String ascii(const String &text) {
  // U+1EA0..U+1EF9 (Ạ..ỹ) in order, reduced to their base letters.
  static const char *EXT = "AaAaAaAaAaAaAaAaAaAaAaAaEeEeEeEeEeEeEeEeIiIiOoOoOoOoOoOoOoOoOoOoOoOoUuUuUuUuUuUuUuYyYyYyYy";
  // U+00C0..U+00FF (À..ÿ).
  static const char *LATIN1 = "AAAAAAACEEEEIIIIDNOOOOOxOUUUUYPsaaaaaaaceeeeiiiidnooooo/ouuuuypy";
  String out;
  for (size_t i = 0; i < text.length();) {
    uint8_t c = text[i];
    uint32_t cp;
    size_t len;
    if (c < 0x80) { cp = c; len = 1; }
    else if ((c & 0xE0) == 0xC0 && i + 1 < text.length()) { cp = ((c & 0x1F) << 6) | (text[i + 1] & 0x3F); len = 2; }
    else if ((c & 0xF0) == 0xE0 && i + 2 < text.length()) { cp = ((c & 0x0F) << 12) | ((text[i + 1] & 0x3F) << 6) | (text[i + 2] & 0x3F); len = 3; }
    else { i++; continue; }
    i += len;

    if (cp < 0x80) out += char(cp);
    else if (cp >= 0x1EA0 && cp <= 0x1EF9) out += EXT[cp - 0x1EA0];
    else if (cp >= 0xC0 && cp <= 0xFF) out += LATIN1[cp - 0xC0];
    else if (cp == 0x0102 || cp == 0x0103) out += cp == 0x0102 ? 'A' : 'a';
    else if (cp == 0x0110 || cp == 0x0111) out += cp == 0x0110 ? 'D' : 'd';
    else if (cp == 0x0128 || cp == 0x0129) out += cp == 0x0128 ? 'I' : 'i';
    else if (cp == 0x0168 || cp == 0x0169) out += cp == 0x0168 ? 'U' : 'u';
    else if (cp == 0x01A0 || cp == 0x01A1) out += cp == 0x01A0 ? 'O' : 'o';
    else if (cp == 0x01AF || cp == 0x01B0) out += cp == 0x01AF ? 'U' : 'u';
  }
  return out;
}

#ifndef OLED_ENABLED
#define OLED_ENABLED 1
#endif

// Screens (128x64): a status bar on top, then one of
//   connecting  logo + what we are waiting for
//   idle        card icon with RFID waves, "San sang"
//   waiting     member name + countdown bar while a SCAN request is open
//   notice      icon (ok / error / info / busy) + title + detail, a few seconds
String connectStatus = "Khoi dong...";
unsigned long waitTotalMs = 1;
const GFXfont *BOLD = &FreeSansBold9pt7b;

uint16_t textWidth(const String &text, const GFXfont *font) {
  int16_t x1, y1;
  uint16_t w, h;
  oled.setFont(font);
  oled.setTextSize(1);
  oled.getTextBounds(text, 0, 0, &x1, &y1, &w, &h);
  return w;
}

/**
 * Text whose baseline is at y, inside [x, x + width). Bold falls back to the
 * small font when it does not fit; the small font is clipped with "..".
 */
void label(String text, int16_t x, int16_t y, int16_t width, bool center, bool bold = false) {
  text = ascii(text);
  const GFXfont *font = bold && textWidth(text, BOLD) <= width ? BOLD : nullptr;
  if (!font) {
    int maxChars = width / 6;
    if ((int)text.length() > maxChars) text = text.substring(0, max(0, maxChars - 2)) + "..";
  }
  uint16_t w = textWidth(text, font);
  oled.setFont(font);
  oled.setTextColor(SSD1306_WHITE);
  // Custom fonts are drawn from the baseline, the built-in one from its top.
  oled.setCursor(center ? x + (width - (int16_t)w) / 2 : x, font ? y : y - 7);
  oled.print(text);
  oled.setFont(nullptr);
}

void drawStatusBar() {
  oled.fillRoundRect(0, 0, 29, 10, 2, SSD1306_WHITE);
  oled.setFont(nullptr);
  oled.setTextColor(SSD1306_BLACK);
  oled.setCursor(3, 1);
  oled.print("SECO");
  oled.setTextColor(SSD1306_WHITE);
  oled.setCursor(33, 1);
  oled.print(READER_ID);

  // MQTT: filled dot = connected to the backend.
  if (mqtt.connected()) oled.fillCircle(106, 4, 3, SSD1306_WHITE);
  else oled.drawCircle(106, 4, 3, SSD1306_WHITE);

  // WiFi signal: 3 bars.
  int bars = 0;
  if (WiFi.status() == WL_CONNECTED) {
    long rssi = WiFi.RSSI();
    bars = rssi > -60 ? 3 : rssi > -72 ? 2 : 1;
  }
  for (int i = 0; i < 3; i++) {
    int h = 3 + i * 3, bx = 114 + i * 5;
    if (i < bars) oled.fillRect(bx, 9 - h, 3, h, SSD1306_WHITE);
    else oled.drawFastHLine(bx, 8, 3, SSD1306_WHITE);
  }
  oled.drawFastHLine(0, 12, 128, SSD1306_WHITE);
}

void drawSpinner(int16_t cx, int16_t cy, int16_t r) {
  int active = (millis() / 120) % 8;
  for (int i = 0; i < 8; i++) {
    float a = i * PI / 4;
    int16_t px = cx + cos(a) * r, py = cy + sin(a) * r;
    if (i == active) oled.fillCircle(px, py, 2, SSD1306_WHITE);
    else oled.drawPixel(px, py, SSD1306_WHITE);
  }
}

void drawIcon(Icon icon, int16_t cx, int16_t cy) {
  const int16_t r = 11;
  switch (icon) {
    case Icon::Ok:
      oled.fillCircle(cx, cy, r, SSD1306_WHITE);
      for (int t = 0; t < 2; t++) {  // 2 px thick check mark
        oled.drawLine(cx - 6, cy + t, cx - 2, cy + 4 + t, SSD1306_BLACK);
        oled.drawLine(cx - 2, cy + 4 + t, cx + 6, cy - 4 + t, SSD1306_BLACK);
      }
      break;
    case Icon::Error:
      oled.fillCircle(cx, cy, r, SSD1306_WHITE);
      for (int t = 0; t < 2; t++) {
        oled.drawLine(cx - 5 + t, cy - 5, cx + 5 + t, cy + 5, SSD1306_BLACK);
        oled.drawLine(cx + 5 + t, cy - 5, cx - 5 + t, cy + 5, SSD1306_BLACK);
      }
      break;
    case Icon::Info:
      oled.drawCircle(cx, cy, r, SSD1306_WHITE);
      oled.fillRect(cx - 1, cy - 1, 3, 7, SSD1306_WHITE);
      oled.fillRect(cx - 1, cy - 6, 3, 3, SSD1306_WHITE);
      break;
    case Icon::Busy:
      drawSpinner(cx, cy, 9);
      break;
  }
}

/** Card with a chip and RFID waves that grow one by one. */
void drawCard(int16_t x, int16_t y) {
  oled.drawRoundRect(x, y, 30, 20, 3, SSD1306_WHITE);
  oled.fillRoundRect(x + 4, y + 6, 8, 7, 1, SSD1306_WHITE);
  oled.drawFastHLine(x + 4, y + 16, 18, SSD1306_WHITE);
  int waves = (millis() / 350) % 4;
  for (int i = 0; i < waves; i++) oled.drawCircleHelper(x + 32, y + 10, 4 + i * 4, 0x2 | 0x4, SSD1306_WHITE);
}

void drawConnecting() {
  label("SECO", 0, 36, 128, true, true);
  label(WiFi.status() == WL_CONNECTED ? "Ket noi may chu..." : connectStatus, 0, 50, 128, true);
  for (int i = 0; i < 3; i++) {  // loading dots
    bool on = (millis() / 300) % 3 == (unsigned long)i;
    if (on) oled.fillCircle(56 + i * 8, 59, 2, SSD1306_WHITE);
    else oled.drawCircle(56 + i * 8, 59, 2, SSD1306_WHITE);
  }
}

void drawIdle() {
  drawCard(4, 22);
  label("San sang", 54, 34, 74, false, true);
  label("Cho yeu cau", 54, 47, 74, false);
  label("Gan the tu web admin", 0, 62, 128, true);
}

void drawWaiting() {
  label(replacing ? "DOI THE CHO" : "GAN THE CHO", 0, 22, 128, true);
  label(memberName, 0, 39, 128, true, true);
  // Blinking prompt + countdown bar.
  if ((millis() / 600) % 2) label("Dat the len dau doc", 0, 51, 128, true);
  long left = max(0L, (long)(deadline - millis()));
  int fill = (int)(96L * left / (long)waitTotalMs);
  oled.drawRoundRect(0, 56, 100, 8, 3, SSD1306_WHITE);
  if (fill > 2) oled.fillRoundRect(2, 58, fill, 4, 1, SSD1306_WHITE);
  oled.setFont(nullptr);
  oled.setCursor(104, 56);
  oled.printf("%2lds", (left + 999) / 1000);
}

void drawNotice() {
  drawIcon(notice.icon, 13, 31);
  label(notice.title, 30, 32, 98, false, true);
  label(notice.detail, 30, 46, 98, false);
  if (notice.hint.length()) label(notice.hint, 0, 62, 128, true);
}

void render() {
  if (!OLED_ENABLED) return;
  oled.clearDisplay();
  drawStatusBar();
  if (messageUntil) drawNotice();
  else if (WiFi.status() != WL_CONNECTED || !mqtt.connected()) drawConnecting();
  else if (waiting) drawWaiting();
  else drawIdle();
  oled.display();
}

/** Show a result for a few seconds, then go back to the current screen. */
void notify(Icon icon, const String &title, const String &detail, const String &hint = "") {
  notice = {icon, title, detail, hint};
  messageUntil = millis() + MESSAGE_MS;
  Serial.printf("[OLED] %s | %s %s\n", ascii(title).c_str(), ascii(detail).c_str(), ascii(hint).c_str());
  render();
}

void showHome() { render(); }

// ---- MQTT ------------------------------------------------------------------

void publishJson(const char *kind, JsonDocument &doc) {
  String body;
  serializeJson(doc, body);
  mqtt.publish(topic(kind).c_str(), body.c_str());
  Serial.printf("-> %s %s\n", topic(kind).c_str(), body.c_str());
}

void stopWaiting() {
  waiting = false;
  registrationId = "";
  memberName = "";
  replacing = false;
}

void onCommand(JsonDocument &doc) {
  String command = doc["command"] | "";

  if (command == "SCAN") {
    waiting = true;
    registrationId = doc["registration_id"] | "";
    memberName = doc["user_name"] | "";
    replacing = String(doc["mode"] | "ASSIGN") == "REPLACE";
    deadline = millis() + (unsigned long)(doc["timeout_s"] | 90) * 1000UL;
    waitTotalMs = max(1UL, deadline - millis());
    messageUntil = 0;
    showHome();
    Serial.printf("SCAN for %s: tap a card on the MFRC522 (or type a UID)\n", memberName.c_str());
    return;
  }

  if (command == "DONE") {
    String name = doc["user_name"] | "";
    stopWaiting();
    notify(Icon::Ok, "Da gan the", name, "The da san sang de sac");
    return;
  }

  if (command == "CANCEL") {
    if (registrationId.isEmpty() || registrationId == (doc["registration_id"] | "")) {
      stopWaiting();
      notify(Icon::Info, "Da huy", "Yeu cau gan the da dong");
    }
  }
}

void onResult(JsonDocument &doc) {
  bool ok = doc["ok"] | false;
  String error = doc["error"] | "";
  String uid = doc["uid"] | "";

  if (ok) {
    notify(Icon::Ok, "Da doc the", uid, "Bam Hoan tat tren web");
  } else if (error == "UID_TAKEN") {
    notify(Icon::Error, "The da co chu", uid, "Hay quet the khac");
  } else if (error == "NO_REQUEST") {
    notify(Icon::Info, "Chua co yeu cau", uid, "Bam Gan the tren web");
  } else {
    notify(Icon::Error, "Loi doc the", error);
  }
}

void onMessage(char *rawTopic, byte *payload, unsigned int length) {
  String t(rawTopic);
  Serial.printf("<- %s %.*s\n", rawTopic, length, (const char *)payload);

  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) return;

  if (t.endsWith("/cmd")) onCommand(doc);
  else if (t.endsWith("/res")) onResult(doc);
}

void connectMqtt() {
  String clientId = String("seco-") + READER_ID + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  // Last will: the broker marks the reader OFFLINE if we disappear.
  if (mqtt.connect(clientId.c_str(), nullptr, nullptr, topic("status").c_str(), 0, true, "OFFLINE")) {
    mqtt.publish(topic("status").c_str(), "ONLINE", true);
    mqtt.subscribe(topic("cmd").c_str());
    mqtt.subscribe(topic("res").c_str());
    Serial.printf("MQTT connected to %s:%u\n", MQTT_HOST, MQTT_PORT);
    lastHeartbeat = 0;
  } else {
    Serial.printf("MQTT connect failed, rc=%d\n", mqtt.state());
  }
  if (!messageUntil) showHome();
}

void heartbeat() {
  JsonDocument doc;
  doc["fw"] = FIRMWARE;
  doc["waiting"] = waiting;
  publishJson("hb", doc);
}

// ---- Card taps -------------------------------------------------------------

void onCardTapped(String uid) {
  uid.trim();
  uid.toUpperCase();
  if (uid.isEmpty()) return;
  if (!mqtt.connected()) {
    notify(Icon::Error, "Mat ket noi", "May chu MQTT");
    return;
  }

  JsonDocument doc;
  doc["uid"] = uid;
  if (waiting) doc["registration_id"] = registrationId;
  publishJson("scan", doc);
  notify(Icon::Busy, "Dang gui...", uid);
}

/** UID bytes as uppercase hex without separators, e.g. 11:22:33:44 -> "11223344". */
String uidHex(const MFRC522::Uid &uid) {
  String out;
  for (byte i = 0; i < uid.size; i++) {
    if (uid.uidByte[i] < 0x10) out += '0';
    out += String(uid.uidByte[i], HEX);
  }
  out.toUpperCase();
  return out;
}

void readCard() {
  static String lastUid;
  static unsigned long lastAt = 0;

  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  String uid = uidHex(rfid.uid);
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  // A card held on the reader is read over and over; count it once.
  if (uid == lastUid && millis() - lastAt < 2000) {
    lastAt = millis();
    return;
  }
  lastUid = uid;
  lastAt = millis();

  Serial.printf("Card %s (%s)\n", uid.c_str(), String(rfid.PICC_GetTypeName(rfid.PICC_GetType(rfid.uid.sak))).c_str());
  onCardTapped(uid);
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialLine.length()) onCardTapped(serialLine);
      serialLine = "";
    } else if (isxdigit(c) && serialLine.length() < 20) {
      serialLine += c;
    }
  }
}

// ---- Arduino ---------------------------------------------------------------

void setup() {
  Serial.begin(115200);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);  // full frame in ~25 ms
  oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  render();
  connectStatus = "Ket noi WiFi...";

  SPI.begin(RFID_SCK, RFID_MISO, RFID_MOSI, RFID_SS);
  rfid.PCD_Init();
  Serial.print("MFRC522 ");
  rfid.PCD_DumpVersionToSerial();

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  while (WiFi.status() != WL_CONNECTED) {
    delay(100);
    render();
  }
  Serial.printf("WiFi connected, IP %s\n", WiFi.localIP().toString().c_str());

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(1024);
  mqtt.setCallback(onMessage);
  connectMqtt();
  Serial.println("Tap a card on the MFRC522 (click it in Wokwi, pick a card, Tap) or type a hex UID + Enter");
}

void loop() {
  static unsigned long lastReconnect = 0;
  if (!mqtt.connected() && millis() - lastReconnect > 3000) {
    lastReconnect = millis();
    connectMqtt();
  }
  mqtt.loop();

  if (mqtt.connected() && (lastHeartbeat == 0 || millis() - lastHeartbeat > HEARTBEAT_MS)) {
    lastHeartbeat = millis();
    heartbeat();
  }

  // The API also expires the request (and sends CANCEL); this frees the OLED.
  if (waiting && (long)(millis() - deadline) > 0) {
    stopWaiting();
    notify(Icon::Info, "Het thoi gian", "Chua quet the nao");
  }

  if (messageUntil && (long)(millis() - messageUntil) > 0) {
    messageUntil = 0;
    showHome();
  }

  // Animations (waves, spinner, countdown).
  static unsigned long lastFrame = 0;
  if (millis() - lastFrame > 100) {
    lastFrame = millis();
    render();
  }

  readCard();
  readSerial();
  delay(20);
}
