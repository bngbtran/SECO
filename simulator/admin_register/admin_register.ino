/*
   SECO admin desk RFID reader (READER-01) - assign / replace a member's card.

   Flow (FR02, Web Admin > Member details)
     1. Admin creates a member, then clicks "Gán thẻ" (or "Đổi thẻ").
        The API sends cmd SCAN; the LCD shows "QUET THE CHO: <member>".
     2. Tap a card. The reader sends its UID; the web dialog shows it.
     3. Admin clicks "Hoàn tất": the API saves the UID as the member's card
        (a replaced card is unlinked) and sends cmd DONE -> "DA GAN THE".
     The member can then tap this card at a charging station to start charging.

   The card is read by an MFRC522 (SPI). In Wokwi click the reader to open its
   control panel, pick a card and press "Tap". A hex UID typed in the Serial
   Monitor (e.g. 04A31F7C + Enter) is accepted too.

   MQTT (this is not a charging station, so it has its own namespace)
     sub seco/reader/READER-01/cmd     SCAN | DONE | CANCEL
     sub seco/reader/READER-01/res     result of a scan
     pub seco/reader/READER-01/scan    {"uid", "registration_id"}
     pub seco/reader/READER-01/hb      heartbeat every 10 s
     pub seco/reader/READER-01/status  ONLINE (retained), OFFLINE via last will
*/

#include <ArduinoJson.h>
#include <LiquidCrystal_I2C.h>
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
const unsigned long MESSAGE_MS = 3500;  // how long a result stays on the LCD

// ---- State -----------------------------------------------------------------

WiFiClient wifi;
PubSubClient mqtt(wifi);
LiquidCrystal_I2C lcd(0x27, 16, 2);
MFRC522 rfid(RFID_SS, RFID_RST);

bool waiting = false;           // a SCAN request is open
String registrationId;
String memberName;
bool replacing = false;
unsigned long deadline = 0;

unsigned long lastHeartbeat = 0;
unsigned long messageUntil = 0;  // 0 = show the home screen
String serialLine;

String topic(const char *kind) {
  return String("seco/reader/") + READER_ID + "/" + kind;
}

// ---- LCD -------------------------------------------------------------------

/** Keep the ASCII base letter of Vietnamese characters; the LCD has no accents. */
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
    if (c < 0x80) {
      cp = c;
      len = 1;
    }
    else if ((c & 0xE0) == 0xC0 && i + 1 < text.length()) {
      cp = ((c & 0x1F) << 6) | (text[i + 1] & 0x3F);
      len = 2;
    }
    else if ((c & 0xF0) == 0xE0 && i + 2 < text.length()) {
      cp = ((c & 0x0F) << 12) | ((text[i + 1] & 0x3F) << 6) | (text[i + 2] & 0x3F);
      len = 3;
    }
    else {
      i++;
      continue;
    }
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

void show(const String &line1, const String &line2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(ascii(line1).substring(0, 16));
  lcd.setCursor(0, 1);
  lcd.print(ascii(line2).substring(0, 16));
}

/** Show a result for a few seconds, then go back to the home screen. */
void flash(const String &line1, const String &line2) {
  show(line1, line2);
  messageUntil = millis() + MESSAGE_MS;
}

void showHome() {
  if (waiting) {
    show(replacing ? "DOI THE CHO:" : "QUET THE CHO:", memberName);
  } else {
    show("SECO DAU DOC THE", mqtt.connected() ? String(READER_ID) + " READY" : "DANG KET NOI...");
  }
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
    messageUntil = 0;
    showHome();
    Serial.printf("SCAN for %s: tap a card on the MFRC522 (or type a UID)\n", memberName.c_str());
    return;
  }

  if (command == "DONE") {
    String name = doc["user_name"] | "";
    stopWaiting();
    flash("DA GAN THE", name);
    return;
  }

  if (command == "CANCEL") {
    if (registrationId.isEmpty() || registrationId == (doc["registration_id"] | "")) {
      stopWaiting();
      flash("DA HUY YEU CAU", "");
    }
  }
}

void onResult(JsonDocument &doc) {
  bool ok = doc["ok"] | false;
  String error = doc["error"] | "";
  String uid = doc["uid"] | "";

  if (ok) {
    flash("DA DOC " + uid, "XAC NHAN TREN WEB");
  } else if (error == "UID_TAKEN") {
    flash("THE DA CO CHU", "QUET THE KHAC");
  } else if (error == "NO_REQUEST") {
    flash("UID " + uid, "CHUA CO YEU CAU");
  } else {
    flash("LOI DOC THE", error);
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
    flash("CHUA KET NOI", "MQTT");
    return;
  }

  JsonDocument doc;
  doc["uid"] = uid;
  if (waiting) doc["registration_id"] = registrationId;
  publishJson("scan", doc);
  show("DANG GUI UID...", uid);
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
  lcd.init();
  lcd.backlight();
  show("SECO DAU DOC THE", "KET NOI WIFI...");

  SPI.begin(RFID_SCK, RFID_MISO, RFID_MOSI, RFID_SS);
  rfid.PCD_Init();
  Serial.print("MFRC522 ");
  rfid.PCD_DumpVersionToSerial();

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  while (WiFi.status() != WL_CONNECTED) delay(100);
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

  // The API also expires the request (and sends CANCEL); this frees the LCD.
  if (waiting && (long)(millis() - deadline) > 0) {
    stopWaiting();
    flash("HET THOI GIAN", "QUET THE");
  }

  if (messageUntil && (long)(millis() - messageUntil) > 0) {
    messageUntil = 0;
    showHome();
  }

  readCard();
  readSerial();
  delay(20);
}
