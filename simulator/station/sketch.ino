/*
 * SECO charging station EVCS-01 - full simulation of DD v1.2 / Proposal flows.
 *
 * Hardware (DD 7)                    Wokwi part
 *   ESP32-S3                         board-esp32-s3-devkitc-1
 *   MFRC522 RFID (SPI)               board-mfrc522
 *   LCD 16x2 I2C (0x27)              wokwi-lcd1602
 *   OLED 128x64 I2C (0x3C) - QR      board-ssd1306
 *   INA219 V/I/P                     wokwi-potentiometer (load current; no INA219 in Wokwi)
 *   DS18B20 temperature              wokwi-ds18b20
 *   Fail-safe relay (OFF at boot)    wokwi-relay-module + LED as the 12 V load
 *   Plug micro-switch                wokwi-slide-switch "PLUG"
 *   START / STOP buttons             wokwi-pushbutton
 *   E-Stop (latching)                wokwi-slide-switch "E-STOP"
 *   Connector lock actuator          wokwi-servo
 *   Budget selection               wokwi-analog-joystick (move = browse, press = choose)
 *   Buzzer                           wokwi-buzzer
 *
 * Flows
 *   MEMBER  READY -> (budget on joystick, optional) -> tap RFID -> name + balance
 *           -> plug -> CHARGING (no START needed) -> unplug / budget / balance /
 *           safety -> wallet charged by the API.
 *   GUEST   READY -> (budget on joystick, optional) -> plug -> START -> CHARGING
 *           -> unplug -> LOCK -> QR on the OLED -> paid -> UNLOCK -> READY.
 *           Expired QR: press START for a new one.
 *   SAFETY  E-Stop or temperature >= 60 C cuts the relay at once -> ERROR.
 *   TECH    ERROR -> tap technician card -> hold STOP 3 s -> MAINTENANCE -> fix
 *           -> tap technician card -> hold STOP 3 s -> READY. STOP alone does nothing.
 *
 * MQTT (topics evcs/EVCS-01/...)
 *   pub req        RFID_VERIFY, SESSION_START, GUEST_START, GUEST_PAYMENT_REQUEST,
 *                  TECHNICIAN_VERIFY, TECHNICIAN_ACTION, STATION_SYNC
 *   sub res        replies
 *   pub event      PLUG, UNPLUGGED, CHARGE_STARTED, ESTOP, OVER_TEMPERATURE, SENSOR_FAULT
 *   pub telemetry  voltage_v, current_a, power_kw, temperature_c
 *   sub cmd        STOP, LOCK, UNLOCK, PAYMENT_QR, PAYMENT_STATUS, MAINTENANCE, READY,
 *                  RESET, PING   -> pub cmd_ack {command, ok}
 *   pub hb         every 10 s;  status ONLINE (retained) / OFFLINE (last will)
 */

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <ArduinoJson.h>
#include <DallasTemperature.h>
#include <ESP32Servo.h>
#include <LiquidCrystal_I2C.h>
#include <MFRC522.h>
#include <OneWire.h>
#include <PubSubClient.h>
#include <QRCodeGenerator.h>
#include <SPI.h>
#include <WiFi.h>
#include <Wire.h>

// ---- Configuration ---------------------------------------------------------

const char *WIFI_SSID = "Wokwi-GUEST";
const char *WIFI_PASSWORD = "";

// host.wokwi.internal = your computer through the Wokwi private gateway (VS Code).
const char *MQTT_HOST = "host.wokwi.internal";
const uint16_t MQTT_PORT = 1883;

const char *STATION_ID = "EVCS-01";
const char *FIRMWARE = "station-sim-1.0";

// Billing simulation (DD 5): 0.15 kW per 30 s tick = 0.00125 kWh, 4000 VND/kWh.
const float TICK_KWH = 0.00125f;
const unsigned long TICK_MS = 30000;
const float OVER_TEMPERATURE_C = 60.0f;

// ---- Pins (ESP32-S3) -------------------------------------------------------

const uint8_t PIN_SDA = 8, PIN_SCL = 9;                                  // LCD + OLED
const uint8_t RFID_SS = 10, RFID_MOSI = 11, RFID_SCK = 12, RFID_MISO = 13, RFID_RST = 14;
const uint8_t PIN_DS18B20 = 4;
const uint8_t PIN_CURRENT = 1;      // potentiometer standing in for the INA219
const uint8_t PIN_PLUG = 5;         // LOW = cable plugged
const uint8_t PIN_START = 6;        // LOW = pressed
const uint8_t PIN_STOP = 7;         // LOW = pressed
const uint8_t PIN_ESTOP = 15;       // LOW = E-Stop latched
const uint8_t PIN_RELAY = 16;       // HIGH = load powered
const uint8_t PIN_LOCK = 17;        // servo
const uint8_t PIN_BUZZER = 18;
const uint8_t PIN_JOY_HORZ = 2;     // ADC, max = left
const uint8_t PIN_JOY_VERT = 3;     // ADC, max = up
const uint8_t PIN_JOY_SEL = 39;     // LOW = joystick pressed

// Fixed budget levels (VND). 0 = no limit (member: app budget or whole balance).
const long BUDGET_LEVELS[] = {0, 10000, 20000, 50000, 100000, 200000, 500000};
const int BUDGET_COUNT = sizeof(BUDGET_LEVELS) / sizeof(BUDGET_LEVELS[0]);

// ---- Devices ---------------------------------------------------------------

WiFiClient wifi;
PubSubClient mqtt(wifi);
LiquidCrystal_I2C lcd(0x27, 16, 2);
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
MFRC522 rfid(RFID_SS, RFID_RST);
OneWire oneWire(PIN_DS18B20);
DallasTemperature thermometer(&oneWire);
Servo lockServo;

// ---- State -----------------------------------------------------------------

enum class State {
  Boot,          // waiting for STATION_SYNC
  Ready,
  MemberWaitPlug,  // card accepted, waiting for the cable
  Starting,      // SESSION_START / GUEST_START sent
  Charging,
  Stopping,      // unplugged / stopped, waiting for the API (guest: LOCK + QR)
  WaitPayment,   // guest: connector locked, QR shown
  Error,
  Maintenance,
};
State state = State::Boot;

String sessionId;
String sessionType;        // MEMBER | GUEST
long sessionBudget = 0;    // 0 = no limit
long sessionPrice = 4000;
unsigned long sessionStartedAt = 0;

String memberUid;          // card waiting for the cable
String memberName;
long memberBalance = 0;
long memberBudget = 0;     // budget to send with SESSION_START (0 = none)
unsigned long memberWaitUntil = 0;

long budgetChosen = 0;     // level confirmed with the joystick (0 = none)
int budgetCursor = -1;     // level being browsed, -1 = not browsing
unsigned long budgetBrowseUntil = 0;

String paymentId;
long paymentAmount = 0;
bool paymentExpired = false;

String errorReason;        // shown in ERROR
String techUid;            // technician card verified, waiting for STOP 3 s
String techName;
unsigned long techUntil = 0;

bool relayOn = false;
bool locked = false;
bool plugged = false;
bool estop = false;
float temperatureC = 25.0f;
bool sensorOk = true;

unsigned long messageUntil = 0;
unsigned long lastHeartbeat = 0;
unsigned long lastTelemetry = 0;
unsigned long lastTempRead = 0;
unsigned long stoppingSince = 0;
unsigned long stopPressedAt = 0;
bool stopFired = false;
bool lastStart = false;
String serialLine;

String topic(const char *kind) { return String("evcs/") + STATION_ID + "/" + kind; }

// ---- Text helpers ----------------------------------------------------------

/** Keep the ASCII base letter of Vietnamese characters; LCD / OLED fonts have no accents. */
String ascii(const String &text) {
  static const char *EXT = "AaAaAaAaAaAaAaAaAaAaAaAaEeEeEeEeEeEeEeEeIiIiOoOoOoOoOoOoOoOoOoOoOoOoUuUuUuUuUuUuUuYyYyYyYy";
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

String vnd(long amount) {
  String digits = String(amount < 0 ? -amount : amount);
  String out;
  for (int i = 0; i < (int)digits.length(); i++) {
    if (i && (digits.length() - i) % 3 == 0) out += '.';
    out += digits[i];
  }
  return (amount < 0 ? "-" : "") + out + "d";
}

String budgetLabel(long amount) { return amount ? vnd(amount) : String("KHONG GIOI HAN"); }

String reasonText(const String &reason) {
  if (reason == "UNPLUG") return "RUT SAC";
  if (reason == "MAX_BUDGET") return "HET NGAN SACH";
  if (reason == "INSUFFICIENT_BALANCE") return "HET SO DU";
  if (reason == "TECHNICIAN") return "KTV/ADMIN DUNG";
  if (reason == "E_STOP") return "E-STOP";
  if (reason == "OVER_TEMPERATURE") return "QUA NHIET";
  if (reason == "SYSTEM_ERROR") return "LOI HE THONG";
  return reason;
}

// ---- Outputs ---------------------------------------------------------------

void beep(int frequency = 2000, int ms = 80) { tone(PIN_BUZZER, frequency, ms); }

void setRelay(bool on) {
  // Fail-safe: never power the load without a cable, with E-Stop or overheating.
  if (on && (!plugged || estop || temperatureC >= OVER_TEMPERATURE_C)) on = false;
  relayOn = on;
  digitalWrite(PIN_RELAY, on ? HIGH : LOW);
}

void setLock(bool on) {
  locked = on;
  lockServo.write(on ? 90 : 0);
}

void show(const String &line1, const String &line2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(ascii(line1).substring(0, 16));
  lcd.setCursor(0, 1);
  lcd.print(ascii(line2).substring(0, 16));
}

/** Show a message for a few seconds, then the screen of the current state. */
void flash(const String &line1, const String &line2, unsigned long ms = 3000) {
  show(line1, line2);
  messageUntil = millis() + ms;
}

float sessionEnergy() {
  if (!sessionStartedAt) return 0;
  unsigned long ticks = (millis() - sessionStartedAt) / TICK_MS;
  return ticks * TICK_KWH;
}

long sessionCost() { return lround(sessionEnergy() * sessionPrice); }

float currentA() {
  if (!relayOn) return 0;
  return analogRead(PIN_CURRENT) * 15.0f / 4095.0f;  // 0..15 A
}

const char *stateName() {
  switch (state) {
    case State::Boot: return "BOOT";
    case State::Ready: return "READY";
    case State::MemberWaitPlug: return "READY";
    case State::Starting: return "STARTING";
    case State::Charging: return "CHARGING";
    case State::Stopping: return "STOPPING";
    case State::WaitPayment: return "WAIT PAYMENT";
    case State::Error: return "ERROR";
    case State::Maintenance: return "MAINTENANCE";
  }
  return "";
}

void drawOledStatus() {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(1);
  oled.setCursor(0, 0);
  oled.printf("SECO %s %s", STATION_ID, mqtt.connected() ? "ON" : "OFF");
  oled.setCursor(0, 12);
  oled.setTextSize(2);
  oled.print(stateName());
  oled.setTextSize(1);
  oled.setCursor(0, 34);
  oled.printf("T %.1fC  I %.1fA", temperatureC, currentA());
  oled.setCursor(0, 44);
  oled.printf("Relay %s  Lock %s", relayOn ? "ON" : "OFF", locked ? "ON" : "OFF");
  oled.setCursor(0, 54);
  oled.printf("Plug %s  E-Stop %s", plugged ? "Y" : "N", estop ? "Y" : "N");
  oled.display();
}

/** Payment QR on the left, amount on the right. */
void drawOledQr(const String &payload) {
  QRCode qr;
  static uint8_t modules[256];
  bool ok = false;
  for (uint8_t version = 3; version <= 6 && !ok; version++) {
    if (qrcode_getBufferSize(version) <= sizeof(modules)) {
      ok = qrcode_initText(&qr, modules, version, ECC_LOW, payload.c_str()) == 0;
    }
  }
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  if (ok) {
    uint8_t scale = max(1, 64 / (qr.size + 4));
    int origin = (64 - qr.size * scale) / 2;
    oled.fillRect(0, 0, 64, 64, SSD1306_WHITE);  // light background = quiet zone
    for (uint8_t y = 0; y < qr.size; y++)
      for (uint8_t x = 0; x < qr.size; x++)
        if (qrcode_getModule(&qr, x, y)) oled.fillRect(origin + x * scale, origin + y * scale, scale, scale, SSD1306_BLACK);
  }
  oled.setTextSize(1);
  oled.setCursor(68, 4);
  oled.print("QUET QR");
  oled.setCursor(68, 16);
  oled.print("THANH TOAN");
  oled.setCursor(68, 34);
  oled.setTextSize(1);
  oled.print(vnd(paymentAmount));
  oled.setCursor(68, 52);
  oled.print("Sandbox");
  oled.display();
}

void drawOledExpired() {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  oled.setTextSize(2);
  oled.setCursor(0, 8);
  oled.print("QR HET HAN");
  oled.setTextSize(1);
  oled.setCursor(0, 40);
  oled.print("Nhan START de tao QR moi");
  oled.display();
}

/** LCD screen of the current state (when no temporary message is shown). */
void showState() {
  switch (state) {
    case State::Boot:
      show(String("SECO ") + STATION_ID, mqtt.connected() ? "DONG BO..." : "KET NOI MQTT...");
      break;
    case State::Ready:
      if (budgetCursor >= 0) show("CHON NGAN SACH", "< " + budgetLabel(BUDGET_LEVELS[budgetCursor]) + " >");
      else if (budgetChosen) show("NS " + vnd(budgetChosen), plugged ? "QUET THE/START" : "CAM SAC / THE");
      else show(String("SECO ") + STATION_ID + " READY", plugged ? "QUET THE/START" : "CAM SAC / THE");
      break;
    case State::MemberWaitPlug:
      show(memberName, "CAM SAC DE SAC");
      break;
    case State::Starting:
      show("DANG BAT DAU...", "");
      break;
    case State::Charging: {
      String line2 = vnd(sessionCost());
      if (sessionBudget > 0) line2 += "/" + vnd(sessionBudget);
      show(String("SAC ") + String(sessionEnergy(), 5) + "kWh", line2);
      break;
    }
    case State::Stopping:
      show("DANG DUNG SAC", "XU LY...");
      break;
    case State::WaitPayment:
      if (paymentExpired) show("QR HET HAN", "START: QR MOI");
      else show("QUET QR TREN OLED", "TT " + vnd(paymentAmount));
      break;
    case State::Error:
      show("LOI: " + errorReason, techUid.length() ? "GIU STOP 3S" : "CHO KY THUAT VIEN");
      break;
    case State::Maintenance:
      show("BAO TRI", techUid.length() ? "GIU STOP 3S XONG" : "QUET THE KTV");
      break;
  }
}

void enter(State next) {
  state = next;
  if (next == State::Ready) {
    sessionId = "";
    sessionType = "";
    sessionStartedAt = 0;
    memberUid = "";
    techUid = "";
    paymentId = "";
    paymentExpired = false;
    setRelay(false);
  }
  if (next == State::Error || next == State::Maintenance) setRelay(false);
  if (next != State::WaitPayment) drawOledStatus();
  if (!messageUntil) showState();
}

// ---- MQTT out --------------------------------------------------------------

void publishJson(const char *kind, JsonDocument &doc, bool retained = false) {
  String body;
  serializeJson(doc, body);
  mqtt.publish(topic(kind).c_str(), body.c_str(), retained);
  Serial.printf("-> %s %s\n", kind, body.c_str());
}

void request(JsonDocument &doc) {
  doc["request_id"] = String(millis());
  publishJson("req", doc);
}

void sendEvent(const char *event, const char *key = nullptr, float value = 0) {
  JsonDocument doc;
  doc["event"] = event;
  if (sessionId.length()) doc["session_id"] = sessionId;
  if (key) doc[key] = value;
  publishJson("event", doc);
}

void ack(const String &command, bool ok = true) {
  JsonDocument doc;
  doc["command"] = command;
  doc["ok"] = ok;
  publishJson("cmd_ack", doc);
}

void sendTelemetry() {
  JsonDocument doc;
  float amps = currentA();
  float volts = mqtt.connected() ? 12.0f + random(-8, 9) / 100.0f : 0;
  doc["voltage_v"] = roundf(volts * 100) / 100;
  doc["current_a"] = roundf(amps * 100) / 100;
  doc["power_kw"] = roundf(volts * amps) / 1000.0f;
  doc["temperature_c"] = roundf(temperatureC * 10) / 10;
  doc["relay"] = relayOn;
  doc["plugged"] = plugged;
  publishJson("telemetry", doc);
}

void heartbeat() {
  JsonDocument doc;
  doc["fw"] = FIRMWARE;
  doc["state"] = stateName();
  publishJson("hb", doc);
}

// ---- Safety ----------------------------------------------------------------

void tripSafety(const String &reason, const char *event, float temperature = 0) {
  setRelay(false);
  errorReason = reason;
  techUid = "";
  beep(400, 600);
  if (strcmp(event, "OVER_TEMPERATURE") == 0) sendEvent(event, "temperature_c", temperature);
  else sendEvent(event);
  // The API stops the session (member wallet charged / guest waits for payment)
  // and puts the station in ERROR; the relay is already off.
  messageUntil = 0;
  enter(State::Error);
}

// ---- Commands from the API -------------------------------------------------

void onCommand(JsonDocument &doc) {
  String command = doc["command"] | "";

  if (command == "STOP") {
    setRelay(false);
    String reason = doc["reason"] | "";
    ack(command);
    if (state == State::Error || state == State::Maintenance) return;
    if (sessionType == "GUEST") {
      // A guest with something to pay receives LOCK + PAYMENT_QR next.
      stoppingSince = millis();
      enter(State::Stopping);
      flash("DA DUNG: " + reasonText(reason), vnd(sessionCost()), 2000);
    } else {
      flash("DA DUNG: " + reasonText(reason), String(sessionEnergy(), 4) + "kWh " + vnd(sessionCost()), 5000);
      enter(State::Ready);
    }
    beep(1200, 200);
    return;
  }

  if (command == "LOCK" || command == "UNLOCK") {
    setLock(command == "LOCK");
    ack(command);  // lock state in the API follows this acknowledgement
    if (command == "UNLOCK" && state == State::WaitPayment) {
      flash("DA THANH TOAN", "CAM ON QUY KHACH", 4000);
      beep(2400, 150);
      enter(State::Ready);
    }
    drawOledStatus();
    return;
  }

  if (command == "PAYMENT_QR") {
    paymentId = doc["payment_id"] | "";
    paymentAmount = doc["amount_vnd"] | 0;
    paymentExpired = false;
    if (doc["session_id"].is<const char *>()) sessionId = doc["session_id"].as<String>();
    state = State::WaitPayment;
    messageUntil = 0;
    drawOledQr(doc["qr_payload"] | "");
    showState();
    ack(command);
    return;
  }

  if (command == "PAYMENT_STATUS") {
    String status = doc["status"] | "";
    if (status == "EXPIRED" || status == "CANCELLED") {
      paymentExpired = true;
      drawOledExpired();
      showState();
      beep(800, 300);
    }
    ack(command);
    return;
  }

  if (command == "MAINTENANCE") {
    techUid = "";
    messageUntil = 0;
    enter(State::Maintenance);
    ack(command);
    return;
  }

  if (command == "READY") {
    techUid = "";
    messageUntil = 0;
    enter(State::Ready);
    ack(command);
    return;
  }

  if (command == "RESET") {
    ack(command);
    setRelay(false);
    show("KHOI DONG LAI", "");
    delay(500);
    ESP.restart();
  }

  // PING and anything unknown: just acknowledge so the admin sees a response.
  ack(command, command == "PING");
}

// ---- Replies from the API --------------------------------------------------

void startMemberSession() {
  JsonDocument doc;
  doc["type"] = "SESSION_START";
  doc["uid"] = memberUid;
  if (memberBudget > 0) doc["max_budget_vnd"] = memberBudget;
  request(doc);
  enter(State::Starting);
}

void onResponse(JsonDocument &doc) {
  String type = doc["type"] | "";
  bool ok = doc["ok"] | false;
  String error = doc["error"] | "";

  if (type == "STATION_SYNC") {
    if (!ok) { enter(State::Ready); return; }
    String status = doc["status"] | "READY";
    setLock(String(doc["connector_lock"] | "UNLOCKED") == "LOCKED");
    JsonObject session = doc["session"];
    if (status == "ERROR") { errorReason = "TRAM LOI"; enter(State::Error); return; }
    if (status == "MAINTENANCE") { enter(State::Maintenance); return; }
    if (!session.isNull()) {
      sessionId = session["session_id"] | "";
      sessionType = session["type"] | "";
      sessionBudget = session["max_budget_vnd"] | 0;
      sessionPrice = session["price_per_kwh_vnd"] | 4000;
      sessionStartedAt = millis() - (unsigned long)(session["duration_seconds"] | 0) * 1000UL;
      if (String(session["status"] | "") == "WAITING_PAYMENT") {
        JsonObject payment = doc["payment"];
        paymentAmount = payment["amount_vnd"] | (long)(session["cost_vnd"] | 0);
        paymentId = payment["payment_id"] | "";
        state = State::WaitPayment;
        paymentExpired = String(payment["status"] | "PENDING") != "PENDING";
        if (paymentExpired) drawOledExpired();
        else drawOledQr(payment["qr_payload"] | "");
        showState();
        return;
      }
      enter(State::Charging);
      setRelay(true);
      if (!plugged) sendEvent("UNPLUGGED");  // cable removed while we were rebooting
      return;
    }
    enter(State::Ready);
    return;
  }

  if (type == "RFID_VERIFY") {
    if (ok && String(doc["role"] | "") == "TECHNICIAN") {
      // A technician card tapped on a READY station: preventive maintenance.
      memberUid = "";
      JsonDocument tech;
      tech["type"] = "TECHNICIAN_VERIFY";
      tech["uid"] = techUid;
      request(tech);
    } else if (ok) {
      memberName = doc["name"] | "MEMBER";
      memberBalance = doc["balance_vnd"] | 0;
      // Budget chosen on the joystick wins over the one chosen in the app.
      long appBudget = doc["max_budget_vnd"] | 0;
      memberBudget = budgetChosen ? budgetChosen : appBudget;
      budgetChosen = 0;
      beep(2400, 80);
      flash("XIN CHAO " + memberName, "SO DU " + vnd(memberBalance), 2500);
      if (plugged) startMemberSession();
      else {
        memberWaitUntil = millis() + 30000;
        enter(State::MemberWaitPlug);
      }
    } else {
      beep(400, 400);
      flash("THE KHONG HOP LE", error.indexOf("inactive") >= 0 ? "THE BI KHOA" : memberUid);
      techUid = "";
      memberUid = "";
      enter(State::Ready);
    }
    return;
  }

  if (type == "SESSION_START" || type == "GUEST_START") {
    if (ok) {
      JsonObject session = doc["session"];
      sessionId = session["session_id"] | "";
      sessionType = type == "GUEST_START" ? "GUEST" : "MEMBER";
      sessionBudget = session["max_budget_vnd"] | 0;
      sessionPrice = 4000;
      sessionStartedAt = millis();
      enter(State::Charging);
      setRelay(true);
      sendEvent("CHARGE_STARTED");
      beep(2400, 120);
    } else {
      beep(400, 400);
      String why = error;
      if (error.indexOf("exceed") >= 0) why = "NGAN SACH > SO DU";
      else if (error.indexOf("Insufficient") >= 0) why = "SO DU KHONG DU";
      else if (error.indexOf("offline") >= 0) why = "TRAM OFFLINE";
      else if (error.indexOf("open session") >= 0) why = "DANG CO PHIEN MO";
      flash("KHONG THE SAC", why, 4000);
      enter(State::Ready);
    }
    return;
  }

  if (type == "GUEST_PAYMENT_REQUEST") {
    if (!ok) flash("LOI TAO QR", error);
    return;  // the new QR arrives as cmd PAYMENT_QR
  }

  if (type == "TECHNICIAN_VERIFY") {
    if (ok) {
      techName = doc["name"] | "KTV";
      techUntil = millis() + 15000;
      beep(2400, 80);
      flash("KTV " + techName, "GIU STOP 3 GIAY", 3000);
    } else {
      techUid = "";
      beep(400, 400);
      flash("KHONG PHAI KTV", state == State::Ready ? "THE KHONG HOP LE" : "QUET THE KTV");
    }
    return;
  }

  if (type == "TECHNICIAN_ACTION") {
    techUid = "";
    if (ok) {
      String status = doc["station_status"] | "";
      beep(2400, 150);
      if (status == "MAINTENANCE") { flash("VAO BAO TRI", techName, 2500); enter(State::Maintenance); }
      else { flash("BAO TRI XONG", "TRAM SAN SANG", 2500); errorReason = ""; enter(State::Ready); }
    } else {
      beep(400, 400);
      flash("KHONG THUC HIEN", error);
    }
    return;
  }
}

void onMessage(char *rawTopic, byte *payload, unsigned int length) {
  String t(rawTopic);
  Serial.printf("<- %s %.*s\n", t.substring(t.lastIndexOf('/') + 1).c_str(), length, (const char *)payload);
  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) return;
  if (t.endsWith("/cmd")) onCommand(doc);
  else if (t.endsWith("/res")) onResponse(doc);
}

void connectMqtt() {
  String clientId = String("seco-") + STATION_ID + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  if (mqtt.connect(clientId.c_str(), nullptr, nullptr, topic("status").c_str(), 0, true, "OFFLINE")) {
    mqtt.publish(topic("status").c_str(), "ONLINE", true);
    mqtt.subscribe(topic("cmd").c_str());
    mqtt.subscribe(topic("res").c_str());
    Serial.printf("MQTT connected to %s:%u\n", MQTT_HOST, MQTT_PORT);
    lastHeartbeat = 0;
    // Resume whatever the API thinks this station is doing.
    JsonDocument doc;
    doc["type"] = "STATION_SYNC";
    request(doc);
  } else {
    Serial.printf("MQTT connect failed, rc=%d\n", mqtt.state());
  }
  if (!messageUntil) showState();
}

// ---- Inputs ----------------------------------------------------------------

void onCard(String uid) {
  uid.toUpperCase();
  beep(1800, 60);
  Serial.printf("Card %s\n", uid.c_str());

  if (state == State::Error || state == State::Maintenance) {
    techUid = uid;
    JsonDocument doc;
    doc["type"] = "TECHNICIAN_VERIFY";
    doc["uid"] = uid;
    request(doc);
    show("KIEM TRA THE KTV", uid);
    return;
  }
  if (state == State::Ready || state == State::MemberWaitPlug) {
    memberUid = uid;
    techUid = uid;  // used if this turns out to be a technician card
    JsonDocument doc;
    doc["type"] = "RFID_VERIFY";
    doc["uid"] = uid;
    request(doc);
    show("DANG XAC THUC", uid);
    return;
  }
  flash(stateName(), "KHONG NHAN THE");
}

void readCard() {
  static String lastUid;
  static unsigned long lastAt = 0;
  if (!rfid.PICC_IsNewCardPresent() || !rfid.PICC_ReadCardSerial()) return;

  String uid;
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += '0';
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  if (uid == lastUid && millis() - lastAt < 2000) {  // card held on the reader
    lastAt = millis();
    return;
  }
  lastUid = uid;
  lastAt = millis();
  if (mqtt.connected()) onCard(uid);
  else flash("CHUA KET NOI", "MQTT");
}

void readSerial() {
  // A hex UID + Enter in the Serial Monitor acts like a card tap.
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialLine.length() && mqtt.connected()) onCard(serialLine);
      serialLine = "";
    } else if (isxdigit(c) && serialLine.length() < 20) {
      serialLine += c;
    }
  }
}

/** Joystick: left/right (or up/down) browses the budget levels, pressing it chooses. */
void readJoystick() {
  static bool lastPressed = false;
  static int lastDir = 0;
  static unsigned long lastMove = 0;

  bool pressed = digitalRead(PIN_JOY_SEL) == LOW;
  int horz = analogRead(PIN_JOY_HORZ), vert = analogRead(PIN_JOY_VERT);
  int dir = (horz < 800 || vert < 800) ? 1 : (horz > 3300 || vert > 3300) ? -1 : 0;

  if (state != State::Ready) {
    budgetCursor = -1;
    lastPressed = pressed;
    lastDir = dir;
    return;
  }

  // Move: one step per push, repeating every 300 ms while held.
  if (dir && (dir != lastDir || millis() - lastMove > 300)) {
    lastMove = millis();
    if (budgetCursor < 0) {
      budgetCursor = 0;
      for (int i = 0; i < BUDGET_COUNT; i++) if (BUDGET_LEVELS[i] == budgetChosen) budgetCursor = i;
    } else {
      budgetCursor = (budgetCursor + dir + BUDGET_COUNT) % BUDGET_COUNT;
    }
    budgetBrowseUntil = millis() + 10000;
    beep(2600, 20);
    messageUntil = 0;
    showState();
  }
  lastDir = dir;

  // Press: choose the level shown (or open the list when not browsing).
  if (pressed && !lastPressed) {
    if (budgetCursor < 0) {
      budgetCursor = 0;
      for (int i = 0; i < BUDGET_COUNT; i++) if (BUDGET_LEVELS[i] == budgetChosen) budgetCursor = i;
      budgetBrowseUntil = millis() + 10000;
      beep(2600, 20);
      messageUntil = 0;
      showState();
    } else {
      budgetChosen = BUDGET_LEVELS[budgetCursor];
      budgetCursor = -1;
      beep(2400, 120);
      flash("DA CHON NS", budgetLabel(budgetChosen), 2000);
    }
  }
  lastPressed = pressed;

  if (budgetCursor >= 0 && millis() > budgetBrowseUntil) {  // left the joystick alone: cancel
    budgetCursor = -1;
    if (!messageUntil) showState();
  }
}

void readSwitches() {
  bool nowPlugged = digitalRead(PIN_PLUG) == LOW;
  if (nowPlugged != plugged) {
    plugged = nowPlugged;
    sendEvent(plugged ? "PLUG" : "UNPLUGGED");
    if (!plugged) {
      setRelay(false);
      if (state == State::Charging) {
        // The API stops the session and replies with STOP (+ LOCK / QR for a guest).
        stoppingSince = millis();
        enter(State::Stopping);
      }
    } else if (state == State::MemberWaitPlug) {
      startMemberSession();
    }
    drawOledStatus();
    if (!messageUntil) showState();
  }

  bool nowEstop = digitalRead(PIN_ESTOP) == LOW;
  if (nowEstop != estop) {
    estop = nowEstop;
    if (estop) tripSafety("E-STOP", "ESTOP");
    else if (state == State::Error) flash("DA NHA E-STOP", "CHO KY THUAT VIEN");
    drawOledStatus();
  }
}

void readButtons() {
  // START: guest session (plugged), or a new QR once the previous one expired.
  bool start = digitalRead(PIN_START) == LOW;
  if (start && !lastStart) {
    beep(2200, 40);
    if (state == State::Ready) {
      if (!plugged) flash("CAM SAC TRUOC", "ROI NHAN START");
      else {
        JsonDocument doc;
        doc["type"] = "GUEST_START";
        if (budgetChosen) doc["max_budget_vnd"] = budgetChosen;
        budgetChosen = 0;
        request(doc);
        enter(State::Starting);
      }
    } else if (state == State::WaitPayment && paymentExpired) {
      JsonDocument doc;
      doc["type"] = "GUEST_PAYMENT_REQUEST";
      doc["session_id"] = sessionId;
      request(doc);
      show("DANG TAO QR MOI", "");
    }
  }
  lastStart = start;

  // STOP held 3 s after a technician card: enter / leave maintenance (DD 3.3).
  bool stop = digitalRead(PIN_STOP) == LOW;
  if (stop) {
    if (!stopPressedAt) {
      stopPressedAt = millis();
      stopFired = false;
    }
    unsigned long held = millis() - stopPressedAt;
    bool armed = techUid.length() && millis() < techUntil;
    if (armed && !stopFired) {
      if (held >= 3000) {
        stopFired = true;
        bool exiting = state == State::Maintenance;
        if (exiting && (estop || temperatureC >= OVER_TEMPERATURE_C)) {
          flash("CHUA THE XONG", estop ? "NHA E-STOP TRUOC" : "NHIET DO CAO");
        } else {
          JsonDocument doc;
          doc["type"] = "TECHNICIAN_ACTION";
          doc["uid"] = techUid;
          doc["action"] = exiting ? "EXIT_MAINTENANCE" : "ENTER_MAINTENANCE";
          request(doc);
          show("DANG XU LY...", "");
        }
      } else {
        show("GIU STOP...", String(3 - held / 1000) + "s");
        messageUntil = millis() + 600;
      }
    }
  } else if (stopPressedAt) {
    if (!stopFired && !(techUid.length() && millis() < techUntil)) flash("STOP", "CAN THE KTV + 3S", 1500);
    stopPressedAt = 0;
  }
}

void readTemperature() {
  if (millis() - lastTempRead < 1000) return;
  lastTempRead = millis();
  float t = thermometer.getTempCByIndex(0);
  thermometer.requestTemperatures();  // non-blocking, read on the next pass

  if (t == DEVICE_DISCONNECTED_C) {
    if (sensorOk) {
      sensorOk = false;
      if (state != State::Error && state != State::Maintenance) tripSafety("CAM BIEN NHIET", "SENSOR_FAULT");
    }
    return;
  }
  sensorOk = true;
  temperatureC = t;
  if (temperatureC >= OVER_TEMPERATURE_C && state != State::Error && state != State::Maintenance) {
    tripSafety("QUA NHIET", "OVER_TEMPERATURE", temperatureC);
  }
}

// ---- Arduino ---------------------------------------------------------------

void setup() {
  // Fail-safe relay OFF before anything else (DD 7).
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_RELAY, LOW);

  Serial.begin(115200);
  pinMode(PIN_PLUG, INPUT_PULLUP);
  pinMode(PIN_START, INPUT_PULLUP);
  pinMode(PIN_STOP, INPUT_PULLUP);
  pinMode(PIN_ESTOP, INPUT_PULLUP);
  pinMode(PIN_JOY_SEL, INPUT_PULLUP);
  pinMode(PIN_BUZZER, OUTPUT);
  analogReadResolution(12);

  Wire.begin(PIN_SDA, PIN_SCL);
  lcd.init();
  lcd.backlight();
  show(String("SECO ") + STATION_ID, "KHOI DONG...");
  oled.begin(SSD1306_SWITCHCAPVCC, 0x3C);

  SPI.begin(RFID_SCK, RFID_MISO, RFID_MOSI, RFID_SS);
  rfid.PCD_Init();

  thermometer.begin();
  thermometer.setWaitForConversion(false);
  thermometer.requestTemperatures();

  lockServo.attach(PIN_LOCK, 500, 2400);
  setLock(false);

  plugged = digitalRead(PIN_PLUG) == LOW;
  estop = digitalRead(PIN_ESTOP) == LOW;
  drawOledStatus();

  show(String("SECO ") + STATION_ID, "KET NOI WIFI...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD, 6);
  while (WiFi.status() != WL_CONNECTED) delay(100);
  Serial.printf("WiFi connected, IP %s\n", WiFi.localIP().toString().c_str());

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(2048);
  mqtt.setCallback(onMessage);
  connectMqtt();
}

void loop() {
  static unsigned long lastReconnect = 0;
  if (!mqtt.connected() && millis() - lastReconnect > 3000) {
    lastReconnect = millis();
    setRelay(false);  // fail-safe without the backend
    connectMqtt();
  }
  mqtt.loop();

  readSwitches();
  readTemperature();
  readButtons();
  readJoystick();
  readCard();
  readSerial();

  if (estop && state != State::Error && state != State::Maintenance && state != State::Boot) {
    tripSafety("E-STOP", "ESTOP");
  }

  if (state == State::MemberWaitPlug && millis() > memberWaitUntil) {
    flash("HET THOI GIAN", "QUET THE LAI");
    enter(State::Ready);
  }
  // A guest session with nothing to pay gets no QR: back to READY.
  if (state == State::Stopping && millis() - stoppingSince > 6000) enter(State::Ready);
  if (techUid.length() && techUntil && millis() > techUntil && state != State::Ready) {
    techUid = "";
    techUntil = 0;
    if (!messageUntil) showState();
  }

  if (mqtt.connected()) {
    if (!lastHeartbeat || millis() - lastHeartbeat > 10000) {
      lastHeartbeat = millis();
      heartbeat();
    }
    unsigned long every = state == State::Charging ? 3000 : 15000;
    if (millis() - lastTelemetry > every) {
      lastTelemetry = millis();
      sendTelemetry();
      if (state != State::WaitPayment) drawOledStatus();
    }
  }

  static unsigned long lastRefresh = 0;
  if (messageUntil && (long)(millis() - messageUntil) > 0) {
    messageUntil = 0;
    showState();
  } else if (!messageUntil && state == State::Charging && millis() - lastRefresh > 1000) {
    lastRefresh = millis();
    showState();
  }
  delay(10);
}
