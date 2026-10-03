/*
 * SECO admin desk RFID reader WITHOUT a screen - test the card registration
 * (FR02) with just an ESP32 + MFRC522. Same MQTT contract as admin_reader.ino,
 * so the API and the web admin need no change.
 *
 * Feedback instead of the OLED:
 *   Serial Monitor (115200)  every step in plain text; type "?" + Enter for the status
 *   On-board LED             ESP32-S3: RGB LED   ESP32 DevKit: blue LED on GPIO 2
 *     blinking blue / fast blink   waiting for a card (SCAN from the web)
 *     green / 1 long flash         card read, card assigned
 *     red / 3 short flashes        error (card already owned, no request, ...)
 *
 * Settings: config.h in this folder (copy ../config.h). Without it the sketch
 * uses Wokwi-GUEST + broker.hivemq.com:8883 (TLS), i.e. it runs as-is in Wokwi
 * with no_lcd.json against the API deployed on Render.
 *
 * Flow (Web Admin > Người dùng > member > "Gán thẻ" / "Đổi thẻ")
 *   1. The API sends cmd SCAN       -> "WAITING for <member>", LED blinks
 *   2. Tap a card (or type its hex UID + Enter)
 *   3. res ok                       -> "CARD READ <uid>", click "Hoàn tất" on the web
 *   4. cmd DONE                     -> "CARD ASSIGNED"
 *
 * MQTT
 *   sub seco/reader/<READER_ID>/cmd     SCAN | DONE | CANCEL
 *   sub seco/reader/<READER_ID>/res     result of a scan
 *   pub seco/reader/<READER_ID>/scan    {"uid", "registration_id"}
 *   pub seco/reader/<READER_ID>/hb      heartbeat every 10 s
 *   pub seco/reader/<READER_ID>/status  ONLINE (retained), OFFLINE via last will
 */

#include <ArduinoJson.h>
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

const char *FIRMWARE = "desk-reader-nolcd-1.0";

// Pins for the board selected in Tools > Board (same wiring as admin_reader).
#if defined(CONFIG_IDF_TARGET_ESP32S3)
const uint8_t RFID_SCK = 12, RFID_MISO = 13, RFID_MOSI = 11;
const uint8_t RFID_SS = 10;  // "SDA" on the module
const uint8_t RFID_RST = 14;
#elif defined(CONFIG_IDF_TARGET_ESP32)
const uint8_t RFID_SCK = 18, RFID_MISO = 19, RFID_MOSI = 23;
const uint8_t RFID_SS = 5;   // "SDA" on the module
const uint8_t RFID_RST = 4;
const uint8_t STATUS_LED = 2;  // blue LED on most ESP32 DevKit boards
#else
#error "Select an ESP32 or ESP32-S3 board in Tools > Board (the reader needs WiFi)."
#endif

const unsigned long HEARTBEAT_MS = 10000;
const unsigned long RESULT_MS = 1500;  // how long a result colour / flash lasts

// ---- State -----------------------------------------------------------------

#if MQTT_TLS
WiFiClientSecure wifi;
#else
WiFiClient wifi;
#endif
PubSubClient mqtt(wifi);
MFRC522 rfid(RFID_SS, RFID_RST);

bool waiting = false;  // a SCAN request is open
String registrationId;
String memberName;
bool replacing = false;
unsigned long deadline = 0;

unsigned long lastHeartbeat = 0;
String serialLine;

// LED result shown until resultUntil, then back to idle / waiting.
// Declared before any function: the Arduino builder puts the prototypes here.
enum class Result { None, Ok, Error };
Result result = Result::None;
unsigned long resultAt = 0;

String topic(const char *kind) { return String("seco/reader/") + READER_ID + "/" + kind; }

// ---- Feedback: Serial + LED ------------------------------------------------

void say(const char *tag, const String &text) {
  Serial.printf("[%s] %s\n", tag, text.c_str());
}

void setLed(uint8_t r, uint8_t g, uint8_t b) {
#if defined(RGB_BUILTIN)
  rgbLedWrite(RGB_BUILTIN, r, g, b);
#elif defined(CONFIG_IDF_TARGET_ESP32)
  digitalWrite(STATUS_LED, (r || g || b) ? HIGH : LOW);
#endif
}

void showResult(Result r) {
  result = r;
  resultAt = millis();
}

/** Called every loop: colour (RGB) or blink pattern (single LED) for the current state. */
void updateLed() {
  unsigned long now = millis();
  if (result != Result::None && now - resultAt > RESULT_MS) result = Result::None;

#if defined(RGB_BUILTIN)
  const uint8_t level = 40;  // the RGB LED is blinding at full power
  if (result == Result::Ok) setLed(0, level, 0);
  else if (result == Result::Error) setLed(level, 0, 0);
  else if (waiting) setLed(0, 0, (now / 300) % 2 ? level : 0);
  else if (!mqtt.connected()) setLed((now / 1000) % 2 ? level : 0, level / 2, 0);  // amber: offline
  else setLed(0, 0, 0);
#else
  unsigned long t = now - resultAt;
  bool on;
  if (result == Result::Ok) on = t < 1000;                              // one long flash
  else if (result == Result::Error) on = t < 900 && (t / 150) % 2 == 0;  // three short flashes
  else if (waiting) on = (now / 150) % 2;                                // fast blink
  else if (!mqtt.connected()) on = (now / 1000) % 2;                     // slow blink: offline
  else on = (now % 3000) < 60;                                           // short tick: ready
  setLed(on, 0, 0);
#endif
}

void printStatus() {
  Serial.println("---- status ----");
  Serial.printf("reader   %s (%s)\n", READER_ID, FIRMWARE);
  Serial.printf("wifi     %s, IP %s, RSSI %d dBm\n", WiFi.status() == WL_CONNECTED ? "connected" : "DISCONNECTED",
                WiFi.localIP().toString().c_str(), WiFi.RSSI());
  Serial.printf("mqtt     %s %s:%u\n", mqtt.connected() ? "connected" : "DISCONNECTED", MQTT_HOST, MQTT_PORT);
  Serial.printf("mfrc522  %s\n", rfidAlive() ? "ok" : "NOT ANSWERING - check the wiring");
  if (waiting) {
    long left = max(0L, (long)(deadline - millis()) / 1000);
    Serial.printf("request  %s for %s, %lds left (registration %s)\n", replacing ? "REPLACE" : "ASSIGN",
                  memberName.c_str(), left, registrationId.c_str());
  } else {
    Serial.println("request  none - click \"Gán thẻ\" on the web admin");
  }
  Serial.println("----------------");
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
    unsigned long timeout = doc["timeout_s"] | 90;
    deadline = millis() + timeout * 1000UL;
    result = Result::None;
    say("WAITING", String(replacing ? "replace card for " : "assign card to ") + memberName + " - tap a card within " +
                       timeout + " s (or type its UID + Enter)");
    return;
  }

  if (command == "DONE") {
    String name = doc["user_name"] | "";
    String uid = doc["uid"] | "";
    stopWaiting();
    showResult(Result::Ok);
    say("ASSIGNED", "card " + uid + " now belongs to " + name + " - it can start a charge");
    return;
  }

  if (command == "CANCEL") {
    if (registrationId.isEmpty() || registrationId == (doc["registration_id"] | "")) {
      stopWaiting();
      showResult(Result::Error);
      say("CANCELLED", "the request was closed on the web (or timed out)");
    }
  }
}

void onResult(JsonDocument &doc) {
  bool ok = doc["ok"] | false;
  String error = doc["error"] | "";
  String uid = doc["uid"] | "";

  if (ok) {
    showResult(Result::Ok);
    say("CARD READ", uid + " - the web dialog shows it, click \"Hoàn tất\" to save");
  } else if (error == "UID_TAKEN") {
    showResult(Result::Error);
    say("REFUSED", uid + " already belongs to someone else - tap another card");
  } else if (error == "NO_REQUEST") {
    showResult(Result::Error);
    say("NO REQUEST", uid + " - click \"Gán thẻ\" on the web admin first");
  } else {
    showResult(Result::Error);
    say("ERROR", error.length() ? error : String("unknown"));
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
    say("READY", String("MQTT connected to ") + MQTT_HOST + ":" + MQTT_PORT + " - waiting for \"Gán thẻ\" on the web");
    lastHeartbeat = 0;
  } else {
    Serial.printf("MQTT connect failed, rc=%d (check MQTT_HOST / MQTT_PORT / MQTT_TLS)\n", mqtt.state());
  }
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
    showResult(Result::Error);
    say("OFFLINE", "card " + uid + " ignored: not connected to the MQTT broker");
    return;
  }

  JsonDocument doc;
  doc["uid"] = uid;
  if (waiting) doc["registration_id"] = registrationId;
  publishJson("scan", doc);
  say("SENT", uid + " - waiting for the API");
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

/** "?" + Enter prints the status; a hex UID + Enter fakes a card tap. */
void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialLine == "?") printStatus();
      else if (serialLine.length()) onCardTapped(serialLine);
      serialLine = "";
    } else if (c == '?' && serialLine.isEmpty()) {
      serialLine = "?";
    } else if (isxdigit(c) && serialLine.length() < 20 && serialLine != "?") {
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
    byte version = rfid.PCD_ReadRegister(MFRC522::VersionReg);
    Serial.printf("MFRC522 not answering (VersionReg=0x%02X): %s\n", version,
                  version == 0xFF ? "MISO floating - check MISO wire, 3.3V and GND"
                                  : "MISO stuck low - check SDA(SS)/SCK/MOSI wires, RST must not be held low");
    Serial.printf("  expected: SDA->%u SCK->%u MOSI->%u MISO->%u RST->%u 3.3V->3V3 GND->GND\n",
                  RFID_SS, RFID_SCK, RFID_MOSI, RFID_MISO, RFID_RST);
    showResult(Result::Error);
  }
}

void connectWifi() {
  Serial.printf("WiFi: joining \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print('.');
    updateLed();
    if (millis() - started > 20000) {  // wrong password / 5 GHz only / out of range
      Serial.println("\nWiFi failed, retrying (check WIFI_SSID / WIFI_PASSWORD, 2.4 GHz)");
      WiFi.disconnect();
      delay(1000);
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      started = millis();
    }
  }
  Serial.printf("\nWiFi connected, IP %s\n", WiFi.localIP().toString().c_str());
}

// ---- Arduino ---------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  delay(300);  // let the USB serial come up
  Serial.printf("\nSECO desk reader %s (%s) - no screen, feedback here and on the LED\n", READER_ID, FIRMWARE);

#if defined(CONFIG_IDF_TARGET_ESP32) && !defined(RGB_BUILTIN)
  pinMode(STATUS_LED, OUTPUT);
#endif
  setLed(0, 0, 0);

  SPI.begin(RFID_SCK, RFID_MISO, RFID_MOSI, RFID_SS);
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
  Serial.println("Tap a card on the MFRC522, type a hex UID + Enter, or \"?\" + Enter for the status");
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
    showResult(Result::Error);
    say("TIMEOUT", "no card tapped - click \"Gán thẻ\" again on the web");
  }

  updateLed();
  readCard();
  readSerial();
  delay(20);
}
