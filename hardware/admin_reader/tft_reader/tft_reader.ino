/*
 * SECO admin desk RFID reader with an ILI9341 2.8" colour TFT (240x320).
 * Same MQTT contract as admin_reader.ino / no_lcd.ino, plus a card lookup:
 *
 *   Tap a linked card while no "Gán thẻ" is open
 *     -> the API looks the card up in the database and answers
 *        {"ok": true, "kind": "CARD_INFO", "user_name", "balance_vnd", ...}
 *     -> the TFT shows the owner, the account role, the card and the balance.
 *   "Gán thẻ" / "Đổi thẻ" on the web admin works as before (SCAN -> tap -> DONE).
 *
 * Wiring (ESP32-S3 DevKitC, same as the charging station): the TFT shares the
 * SPI bus with the MFRC522, each has its own CS.
 *
 *   MFRC522   SDA(SS) 10  SCK 12  MOSI 11  MISO 13  RST 14  3V3  GND
 *   ILI9341   CS 15  DC 6  RST 21  SCK 12  MOSI(SDI) 11  MISO(SDO) 13
 *             VCC 3V3  LED 3V3  GND
 *
 * ESP32 DevKit (WROOM): MFRC522 as in ../README.md (SS 5, SCK 18, MOSI 23,
 * MISO 19, RST 4), TFT CS 15, DC 2, RST 27. Override any TFT pin in config.h
 * (#define TFT_CS 5 ...).
 *
 * Settings: config.h in this folder (copy ../config.h). Without it the sketch
 * uses Wokwi-GUEST + broker.hivemq.com:8883 (TLS).
 *
 * MQTT
 *   sub seco/reader/<READER_ID>/cmd     SCAN | DONE | CANCEL
 *   sub seco/reader/<READER_ID>/res     result of a scan (or CARD_INFO)
 *   pub seco/reader/<READER_ID>/scan    {"uid", "registration_id"}
 *   pub seco/reader/<READER_ID>/hb      heartbeat every 10 s
 *   pub seco/reader/<READER_ID>/status  ONLINE (retained), OFFLINE via last will
 */

#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>
#include <ArduinoJson.h>
#include <Fonts/FreeSans9pt7b.h>
#include <Fonts/FreeSansBold12pt7b.h>
#include <Fonts/FreeSansBold18pt7b.h>
#include <Fonts/FreeSansBold9pt7b.h>
#include <MFRC522.h>
#include <PubSubClient.h>
#include <SPI.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>

// ---- Configuration ---------------------------------------------------------

#if __has_include("config.h")
#include "config.h"
#endif
#ifndef WIFI_SSID  // no config.h: Wokwi simulator defaults
#define WIFI_SSID "Wokwi-GUEST"
#define WIFI_PASSWORD ""
#endif
#ifndef MQTT_HOST
#define MQTT_HOST "broker.hivemq.com"
#define MQTT_PORT 8883
#define MQTT_TLS 1
#endif
#ifndef MQTT_TLS
#define MQTT_TLS 0
#endif
#ifndef MQTT_USERNAME
#define MQTT_USERNAME nullptr
#define MQTT_PASSWORD nullptr
#endif
#ifndef READER_ID
#define READER_ID "READER-01"
#endif

const char *FIRMWARE = "desk-reader-tft-1.0";

// Pins for the board selected in Tools > Board.
#if defined(CONFIG_IDF_TARGET_ESP32S3)
const uint8_t RFID_SCK = 12, RFID_MISO = 13, RFID_MOSI = 11;
const uint8_t RFID_SS = 10;  // "SDA" on the module
const uint8_t RFID_RST = 14;
#ifndef TFT_CS
#define TFT_CS 15
#define TFT_DC 6
#define TFT_RST 21
#endif
#elif defined(CONFIG_IDF_TARGET_ESP32)
const uint8_t RFID_SCK = 18, RFID_MISO = 19, RFID_MOSI = 23;
const uint8_t RFID_SS = 5;  // "SDA" on the module
const uint8_t RFID_RST = 4;
#ifndef TFT_CS
#define TFT_CS 15
#define TFT_DC 2
#define TFT_RST 27
#endif
#else
#error "Select an ESP32 or ESP32-S3 board in Tools > Board (the reader needs WiFi)."
#endif

const unsigned long HEARTBEAT_MS = 10000;
const unsigned long INFO_MS = 8000;     // how long the card owner stays on screen
const unsigned long NOTICE_MS = 3500;   // how long a result stays on screen
const unsigned long REPLY_MS = 6000;    // no answer from the API after this -> error

// ---- State -----------------------------------------------------------------

#if MQTT_TLS
WiFiClientSecure wifi;
#else
WiFiClient wifi;
#endif
PubSubClient mqtt(wifi);
MFRC522 rfid(RFID_SS, RFID_RST);
Adafruit_ILI9341 tft(TFT_CS, TFT_DC, TFT_RST);

bool waiting = false;  // a SCAN request is open
String registrationId;
String memberName;
bool replacing = false;
unsigned long deadline = 0;
unsigned long waitTotalMs = 1;

unsigned long lastHeartbeat = 0;
String serialLine;
String connectStatus = "Khoi dong...";

// Declared before any function: the Arduino builder puts the prototypes here.
enum class Screen { Connecting, Idle, Waiting, Sending, Info, Notice };
enum class Icon { Ok, Error, Info };

struct Notice {
  Icon icon;
  String title;
  String detail;
  String hint;
} notice;

struct CardInfo {
  String uid;
  String name;
  String role;
  String label;
  long balance;
  bool cardActive;
  bool userActive;
} info;

Screen overlay = Screen::Idle;  // Sending / Info / Notice while overlayUntil runs
unsigned long overlayUntil = 0;
Screen shown = Screen::Connecting;
bool dirty = true;  // redraw the whole screen
String sentUid;

String topic(const char *kind) { return String("seco/reader/") + READER_ID + "/" + kind; }

// ---- Text helpers ----------------------------------------------------------

/** Keep the ASCII base letter of Vietnamese characters; the GFX fonts have no accents. */
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

/** 1250000 -> "1.250.000 VND". */
String vnd(long amount) {
  String digits(amount < 0 ? -amount : amount);
  String out;
  for (int i = 0; i < (int)digits.length(); i++) {
    out += digits[i];
    int rest = digits.length() - 1 - i;
    if (rest > 0 && rest % 3 == 0) out += '.';
  }
  return String(amount < 0 ? "-" : "") + out + " VND";
}

String roleText(const String &role) {
  if (role == "TECHNICIAN") return "Ky thuat vien";
  if (role == "ADMIN") return "Quan tri vien";
  return "Thanh vien";
}

// ---- TFT 240x320 -----------------------------------------------------------
// Layout (portrait):
//   0..39    header: SECO, reader id, backend link dot
//   40..319  the screen of the current state

constexpr uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) {
  return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3);
}
const uint16_t C_BG = rgb(16, 22, 20);
const uint16_t C_PANEL = rgb(32, 42, 39);
const uint16_t C_BRAND = rgb(47, 93, 80);  // web admin green
const uint16_t C_TEXT = rgb(240, 244, 242);
const uint16_t C_MUTED = rgb(150, 165, 160);
const uint16_t C_OK = rgb(64, 196, 120);
const uint16_t C_ERROR = rgb(230, 80, 70);
const uint16_t C_INFO = rgb(80, 150, 230);
const uint16_t C_WARN = rgb(240, 180, 60);

const int16_t W = 240, H = 320, TOP = 40;

// Bold fonts from large to small; text that does not fit steps down.
const GFXfont *const STEPS[] = {&FreeSansBold18pt7b, &FreeSansBold12pt7b, &FreeSansBold9pt7b, &FreeSans9pt7b};
const int BIG = 0, MEDIUM = 1, SMALL_BOLD = 2, SMALL = 3;

int16_t textWidth(const String &text, const GFXfont *font, int16_t *x1 = nullptr) {
  int16_t bx, by;
  uint16_t w, h;
  tft.setFont(font);
  tft.setTextSize(1);
  tft.getTextBounds(text, 0, 0, &bx, &by, &w, &h);
  if (x1) *x1 = bx;
  return w;
}

/**
 * Text whose baseline is at y inside [x, x + width), centred or left aligned.
 * Starts with STEPS[step] and steps down to smaller fonts, then clips with "..".
 */
void label(String text, int16_t x, int16_t y, int16_t width, uint16_t color, int step, bool center = true) {
  text = ascii(text);
  const GFXfont *font = STEPS[step];
  while (step < SMALL && textWidth(text, font) > width) font = STEPS[++step];
  while (text.length() > 2 && textWidth(text, font) > width) text = text.substring(0, text.length() - 3) + "..";
  int16_t x1;
  int16_t w = textWidth(text, font, &x1);
  tft.setTextColor(color);
  tft.setCursor((center ? x + (width - w) / 2 : x) - x1, y);
  tft.print(text);
}

void drawHeader() {
  tft.fillRect(0, 0, W, TOP, C_BRAND);
  label("SECO", 10, 28, 80, C_TEXT, MEDIUM, false);
  label(READER_ID, 90, 26, 110, C_TEXT, SMALL, false);
  // Backend link: filled = MQTT connected, ring = offline.
  bool online = WiFi.status() == WL_CONNECTED && mqtt.connected();
  tft.fillCircle(222, 20, 7, online ? C_OK : C_BRAND);
  tft.drawCircle(222, 20, 7, online ? C_OK : C_ERROR);
}

/** Card with a chip and RFID waves, top left at (x, y), 120x76. */
void drawCard(int16_t x, int16_t y, uint16_t color) {
  tft.drawRoundRect(x, y, 120, 76, 8, color);
  tft.drawRoundRect(x + 1, y + 1, 118, 74, 7, color);
  tft.fillRoundRect(x + 14, y + 24, 26, 20, 3, C_WARN);
  tft.drawFastHLine(x + 14, y + 58, 70, C_MUTED);
  for (int i = 0; i < 3; i++) tft.drawCircleHelper(x + 84, y + 34, 8 + i * 8, 0x2 | 0x4, color);
}

void drawIcon(Icon icon, int16_t cx, int16_t cy, int16_t r) {
  uint16_t color = icon == Icon::Ok ? C_OK : icon == Icon::Error ? C_ERROR : C_INFO;
  tft.fillCircle(cx, cy, r, color);
  int16_t s = r / 2;
  for (int t = -1; t <= 1; t++) {  // 3 px thick strokes
    if (icon == Icon::Ok) {
      tft.drawLine(cx - s, cy + t, cx - s / 3, cy + s * 2 / 3 + t, C_TEXT);
      tft.drawLine(cx - s / 3, cy + s * 2 / 3 + t, cx + s, cy - s / 2 + t, C_TEXT);
    } else if (icon == Icon::Error) {
      tft.drawLine(cx - s + t, cy - s, cx + s + t, cy + s, C_TEXT);
      tft.drawLine(cx + s + t, cy - s, cx - s + t, cy + s, C_TEXT);
    }
  }
  if (icon == Icon::Info) {
    tft.fillRect(cx - 2, cy - 2, 5, s + 4, C_TEXT);
    tft.fillRect(cx - 2, cy - s - 3, 5, 5, C_TEXT);
  }
}

void drawConnecting() {
  label("SECO", 0, 150, W, C_TEXT, BIG);
  label(WiFi.status() == WL_CONNECTED ? String("Ket noi may chu...") : connectStatus, 10, 190, W - 20, C_MUTED, SMALL);
  label("Dau doc the tai quay", 0, 300, W, C_MUTED, SMALL);
}

void drawIdle() {
  drawCard(60, 78, C_INFO);
  label("Quet the", 0, 210, W, C_TEXT, BIG);
  label("de xem chu the va so du", 10, 240, W - 20, C_MUTED, SMALL);
  tft.drawFastHLine(20, 276, W - 40, C_PANEL);
  label("Gan the: bam \"Gan the\" tren web", 10, 302, W - 20, C_MUTED, SMALL);
}

/** Countdown bar of the open SCAN request, redrawn every second on its own. */
void drawCountdown() {
  long left = max(0L, (long)(deadline - millis()));
  int16_t fill = (int16_t)(196L * left / (long)waitTotalMs);
  tft.fillRect(0, 266, W, 50, C_BG);
  tft.drawRoundRect(20, 270, 200, 14, 7, C_MUTED);
  if (fill > 4) tft.fillRoundRect(22, 272, fill, 10, 5, C_INFO);
  label(String((left + 999) / 1000) + " giay", 0, 308, W, C_MUTED, SMALL);
}

void drawWaiting() {
  label(replacing ? "DOI THE CHO" : "GAN THE CHO", 0, 70, W, C_MUTED, SMALL_BOLD);
  label(memberName, 10, 106, W - 20, C_TEXT, MEDIUM);
  drawCard(60, 128, C_INFO);
  label("Dat the len dau doc", 0, 244, W, C_TEXT, SMALL_BOLD);
  drawCountdown();
}

void drawSending() {
  drawCard(60, 90, C_MUTED);
  label("Dang tra cuu...", 0, 220, W, C_TEXT, MEDIUM);
  label(sentUid, 0, 250, W, C_MUTED, SMALL);
}

void drawInfo() {
  bool ok = info.cardActive && info.userActive;
  drawIcon(ok ? Icon::Ok : Icon::Error, 120, 70, 20);
  label(ok ? "Quet the thanh cong" : (info.userActive ? "The da bi khoa" : "Tai khoan bi khoa"), 0, 116, W,
        ok ? C_OK : C_ERROR, SMALL_BOLD);

  tft.fillRoundRect(10, 128, W - 20, 184, 10, C_PANEL);
  label("Chu the", 24, 152, 100, C_MUTED, SMALL, false);
  label(info.name, 24, 180, W - 48, C_TEXT, MEDIUM, false);
  String meta = roleText(info.role);
  if (info.label.length()) meta += " - " + info.label;
  label(meta, 24, 204, W - 48, C_MUTED, SMALL, false);
  tft.drawFastHLine(24, 216, W - 48, C_BG);

  label("So du vi", 24, 240, 100, C_MUTED, SMALL, false);
  label(vnd(info.balance), 24, 276, W - 48, info.balance > 0 ? C_OK : C_WARN, BIG, false);
  label("The " + info.uid, 24, 300, W - 48, C_MUTED, SMALL, false);
}

void drawNotice() {
  drawIcon(notice.icon, 120, 120, 30);
  label(notice.title, 10, 190, W - 20, C_TEXT, MEDIUM);
  label(notice.detail, 10, 220, W - 20, C_MUTED, SMALL);
  if (notice.hint.length()) label(notice.hint, 10, 290, W - 20, C_MUTED, SMALL);
}

Screen baseScreen() {
  if (WiFi.status() != WL_CONNECTED || !mqtt.connected()) return Screen::Connecting;
  return waiting ? Screen::Waiting : Screen::Idle;
}

/** Redraws only what changed: a full screen on a state change, the countdown each second. */
void render() {
  static bool lastOnline = false;
  static unsigned long lastSecond = 0;

  if (overlayUntil && (long)(millis() - overlayUntil) > 0) {
    if (overlay == Screen::Sending) {  // the API never answered
      overlayUntil = 0;
      showNotice(Icon::Error, "Khong co phan hoi", "May chu khong tra loi", "Quet lai the");
      return;
    }
    overlayUntil = 0;
  }
  Screen want = overlayUntil ? overlay : baseScreen();
  bool online = WiFi.status() == WL_CONNECTED && mqtt.connected();

  if (want != shown || dirty) {
    shown = want;
    dirty = false;
    tft.fillRect(0, TOP, W, H - TOP, C_BG);
    drawHeader();
    switch (want) {
      case Screen::Connecting: drawConnecting(); break;
      case Screen::Idle: drawIdle(); break;
      case Screen::Waiting: drawWaiting(); break;
      case Screen::Sending: drawSending(); break;
      case Screen::Info: drawInfo(); break;
      case Screen::Notice: drawNotice(); break;
    }
    lastOnline = online;
    lastSecond = millis() / 1000;
    return;
  }
  if (online != lastOnline) {
    lastOnline = online;
    drawHeader();
  }
  if (shown == Screen::Waiting && millis() / 1000 != lastSecond) {
    lastSecond = millis() / 1000;
    drawCountdown();
  }
}

void showOverlay(Screen screen, unsigned long ms) {
  overlay = screen;
  overlayUntil = millis() + ms;
  dirty = true;
  render();
}

void showNotice(Icon icon, const String &title, const String &detail, const String &hint) {
  notice = {icon, title, detail, hint};
  Serial.printf("[TFT] %s | %s %s\n", ascii(title).c_str(), ascii(detail).c_str(), ascii(hint).c_str());
  showOverlay(Screen::Notice, NOTICE_MS);
}

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
    overlayUntil = 0;
    dirty = true;
    Serial.printf("SCAN for %s: tap a card on the reader (or type a UID)\n", memberName.c_str());
    render();
    return;
  }

  if (command == "DONE") {
    String name = doc["user_name"] | "";
    stopWaiting();
    showNotice(Icon::Ok, "Da gan the", name, "The da san sang de sac");
    return;
  }

  if (command == "CANCEL") {
    if (registrationId.isEmpty() || registrationId == (doc["registration_id"] | "")) {
      bool wasWaiting = waiting;
      stopWaiting();
      if (wasWaiting) showNotice(Icon::Info, "Da huy", "Yeu cau gan the da dong", "");
    }
  }
}

void onResult(JsonDocument &doc) {
  // Last card the API answered "ok" for. Another API on the same broker (e.g.
  // the Render deploy next to a local one) may answer NO_REQUEST for it right
  // after; that answer must not hide the owner / "Da doc the" screen.
  static String okUid;
  static unsigned long okAt = 0;

  bool ok = doc["ok"] | false;
  String error = doc["error"] | "";
  String uid = doc["uid"] | "";

  if (!ok && error == "NO_REQUEST" && uid == okUid && millis() - okAt < INFO_MS) {
    Serial.printf("ignored NO_REQUEST for %s: already answered by another API\n", uid.c_str());
    return;
  }
  if (ok) {
    okUid = uid;
    okAt = millis();
  }

  // No "Gán thẻ" open: the API looked the card up in the database.
  if (ok && String(doc["kind"] | "") == "CARD_INFO") {
    info.uid = uid;
    info.name = doc["user_name"] | "";
    info.role = doc["role"] | "USER";
    info.label = doc["card_label"] | "";
    info.balance = doc["balance_vnd"] | 0L;
    info.cardActive = doc["card_active"] | true;
    info.userActive = doc["user_active"] | true;
    Serial.printf("[CARD INFO] %s - %s - balance %ld VND\n", uid.c_str(), ascii(info.name).c_str(), info.balance);
    showOverlay(Screen::Info, INFO_MS);
    return;
  }
  if (ok) {
    showNotice(Icon::Ok, "Da doc the", uid, "Bam Hoan tat tren web");
  } else if (error == "UID_TAKEN") {
    showNotice(Icon::Error, "The da co chu", uid, "Hay quet the khac");
  } else if (error == "NO_REQUEST") {
    showNotice(Icon::Info, "The chua dang ky", uid, "Bam \"Gan the\" tren web de gan");
  } else {
    showNotice(Icon::Error, "Loi doc the", error.length() ? error : String("Khong ro"), "");
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
  if (mqtt.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD, topic("status").c_str(), 0, true, "OFFLINE")) {
    mqtt.publish(topic("status").c_str(), "ONLINE", true);
    mqtt.subscribe(topic("cmd").c_str());
    mqtt.subscribe(topic("res").c_str());
    Serial.printf("MQTT connected to %s:%u\n", MQTT_HOST, MQTT_PORT);
    lastHeartbeat = 0;
  } else {
    Serial.printf("MQTT connect failed, rc=%d (check MQTT_HOST / MQTT_PORT / MQTT_TLS)\n", mqtt.state());
  }
  render();
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
    showNotice(Icon::Error, "Mat ket noi", "May chu MQTT", "Kiem tra WiFi");
    return;
  }

  JsonDocument doc;
  doc["uid"] = uid;
  if (waiting) doc["registration_id"] = registrationId;
  publishJson("scan", doc);
  sentUid = uid;
  showOverlay(Screen::Sending, REPLY_MS);
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

/** A hex UID + Enter in the Serial Monitor fakes a card tap. */
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

// ---- Hardware checks -------------------------------------------------------

/** 0x91 / 0x92 = genuine MFRC522, 0x88 / 0xB2 = common clones; 0x00 / 0xFF = no answer. */
bool rfidAlive() {
  byte version = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  return version != 0x00 && version != 0xFF;
}

void initRfid() {
  rfid.PCD_Init();
  rfid.PCD_SetAntennaGain(MFRC522::RxGain_max);  // better range with cheap antennas
  Serial.print("MFRC522 ");
  rfid.PCD_DumpVersionToSerial();
  if (!rfidAlive()) {
    Serial.printf("MFRC522 not answering - expected: SDA->%u SCK->%u MOSI->%u MISO->%u RST->%u 3.3V GND\n",
                  RFID_SS, RFID_SCK, RFID_MOSI, RFID_MISO, RFID_RST);
    showNotice(Icon::Error, "Loi dau doc", "Kiem tra day RFID", "Xem Serial Monitor");
  }
}

void connectWifi() {
  connectStatus = String("WiFi ") + WIFI_SSID;
  dirty = true;
  render();
  Serial.printf("WiFi: joining \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print('.');
    if (millis() - started > 20000) {  // wrong password / 5 GHz only / out of range
      Serial.println("\nWiFi failed, retrying (check WIFI_SSID / WIFI_PASSWORD, 2.4 GHz)");
      connectStatus = "Loi WiFi, thu lai...";
      dirty = true;
      render();
      WiFi.disconnect();
      delay(1000);
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      started = millis();
    }
  }
  Serial.printf("\nWiFi connected, IP %s\n", WiFi.localIP().toString().c_str());
  dirty = true;
  render();
}

// ---- Arduino ---------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(300);  // let the USB serial come up
  Serial.printf("\nSECO desk reader %s (%s) - ILI9341 TFT\n", READER_ID, FIRMWARE);

  // Both chips sit on one SPI bus: keep every CS high until its driver takes over.
  pinMode(TFT_CS, OUTPUT);
  digitalWrite(TFT_CS, HIGH);
  pinMode(RFID_SS, OUTPUT);
  digitalWrite(RFID_SS, HIGH);
  SPI.begin(RFID_SCK, RFID_MISO, RFID_MOSI);

  tft.begin(40000000);
  tft.setRotation(0);  // portrait 240x320
  tft.fillScreen(C_BG);
  Serial.printf("TFT ILI9341: CS %u DC %u RST %u\n", TFT_CS, TFT_DC, TFT_RST);
  render();

  initRfid();
  connectWifi();

#if MQTT_TLS
  wifi.setInsecure();  // encrypted; pin the CA (wifi.setCACert) for a private broker
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(1024);
  mqtt.setKeepAlive(30);
  mqtt.setCallback(onMessage);
  connectMqtt();
  Serial.println("Tap a card on the MFRC522 or type a hex UID + Enter");
}

void loop() {
  static unsigned long lastReconnect = 0;
  static unsigned long lastRfidCheck = 0;

  if (WiFi.status() != WL_CONNECTED) {  // router restarted / out of range
    connectWifi();
    lastReconnect = 0;
  }
  if (!mqtt.connected() && millis() - lastReconnect > 3000) {
    lastReconnect = millis();
    Serial.printf("MQTT: connecting to %s:%u\n", MQTT_HOST, MQTT_PORT);
    connectMqtt();
  }
  mqtt.loop();

  // Cheap MFRC522 modules sometimes lock up (brown-out, loose wire): re-init.
  if (millis() - lastRfidCheck > 30000) {
    lastRfidCheck = millis();
    if (!rfidAlive()) initRfid();
  }

  if (mqtt.connected() && (lastHeartbeat == 0 || millis() - lastHeartbeat > HEARTBEAT_MS)) {
    lastHeartbeat = millis();
    heartbeat();
  }

  // The API also expires the request (and sends CANCEL).
  if (waiting && (long)(millis() - deadline) > 0) {
    stopWaiting();
    showNotice(Icon::Info, "Het thoi gian", "Chua quet the nao", "Bam \"Gan the\" lai tren web");
  }

  render();
  readCard();
  readSerial();
  delay(20);
}
