/*
 * SECO charging station EVCS-01 - REAL HARDWARE firmware (Arduino IDE / arduino-cli).
 * Same business flows and MQTT contract as simulator/station (Wokwi); only the
 * hardware setup differs. Pins and parts are those of the bench test sketch.
 *
 * Hardware (DD 7)                    Pin(s)
 *   ESP32-S3 DevKitC-1 (N16R8)       -
 *   MFRC522 RFID (SPI)               CS 10, RST 14   (SCK 12, MOSI 11, MISO 13 shared)
 *   TFT 2.8" ILI9341 240x320 (SPI)   CS 15, DC 6, RST 21 (same SPI bus)
 *   INA219 V/I/P (I2C 0x40)          SDA 8, SCL 9
 *   DS18B20 temperature              4
 *   PLUG push button                 5 -> GND   (each press = plug in / unplug)
 *   STOP button (technician)         7 -> GND
 *   Fail-safe relay (OFF at boot)    16  (9V battery -> relay -> INA219 -> EV LED)
 *   Connector lock servo             45
 *   Buzzer                           18
 *   Joystick (VRX, SW)               1 (ADC), 2 -> GND; module "5V" pin on 3V3 (NOT 5V)
 *   Status light (RGB, common cathode) R 41, G 38, B 39 (colour = state)
 *   EV LED                           no GPIO: lit when the relay closes
 * Settings (WiFi, broker, station id, safety limits) live in config.h.
 *
 * States (DD state table) - the firmware keeps the same values as the API:
 *   Station         READY | CHARGING | ERROR | MAINTENANCE
 *   Session         ACTIVE | WAITING_PAYMENT (COMPLETED / ERROR = no open session)
 *   Payment         PENDING | PAID | EXPIRED | CANCELLED
 *   Connector lock  UNLOCKED | LOCKED | UNKNOWN (until the API tells us)
 *   Stop reason     UNPLUG | MAX_BUDGET | INSUFFICIENT_BALANCE | TECHNICIAN |
 *                   OVER_TEMPERATURE | E_STOP | SYSTEM_ERROR (set by the API)
 * plus a local Step for what the screen shows while waiting for the API.
 *
 * Flows
 *   MEMBER  READY (welcome) -> tap RFID -> MEMBER: name + balance -> budget <= balance
 *           on the joystick (optional) -> press the joystick -> CHARGING
 *           -> unplug / budget / balance / safety -> wallet charged. No input for
 *           60 s: back to the welcome screen. Without a card the customer is a GUEST.
 *   GUEST   READY -> budget on the joystick (optional) -> plug -> START (press the
 *           joystick) -> CHARGING
 *           -> unplug -> LOCK -> QR on the TFT -> PAID -> UNLOCK -> READY.
 *           Expired QR: press the joystick for a new one.
 *   SAFETY  over temperature (>= 35 C) or over current (> 1 A) cuts the relay at
 *           once; the API ends the session (guest: QR to pay now, member: wallet
 *           charged) and the station goes to ERROR until a technician clears it
 *           (a guest still pays: the QR is shown in ERROR too).
 *   TECH    ERROR -> tap technician card -> hold STOP 3 s -> MAINTENANCE -> fix
 *           -> tap technician card -> hold STOP 3 s -> READY. STOP alone does nothing.
 *
 * MQTT (topics evcs/EVCS-01/...)
 *   pub req        RFID_VERIFY, SESSION_START, GUEST_START, GUEST_PAYMENT_REQUEST,
 *                  TECHNICIAN_VERIFY, TECHNICIAN_ACTION, STATION_SYNC
 *   sub res        replies
 *   pub event      PLUG, UNPLUGGED, CHARGE_STARTED, OVER_TEMPERATURE, FAULT (over current),
 *                  SENSOR_FAULT
 *   pub telemetry  voltage_v, current_a, power_kw, temperature_c (INA219 + DS18B20)
 *   sub cmd        STOP, LOCK, UNLOCK, PAYMENT_QR, PAYMENT_STATUS, MAINTENANCE, READY,
 *                  RESET, PING   -> pub cmd_ack {command, ok}
 *   pub hb         every 10 s: station / session / payment / connector_lock
 *   pub status     ONLINE (retained) / OFFLINE (last will)
 */

#include <Adafruit_GFX.h>
#include <Adafruit_INA219.h>
#include <Adafruit_ILI9341.h>
#include <ArduinoJson.h>
#include <DallasTemperature.h>
#include <Fonts/FreeSans9pt7b.h>
#include <Fonts/FreeSansBold12pt7b.h>
#include <Fonts/FreeSansBold18pt7b.h>
#include <Fonts/FreeSansBold24pt7b.h>
#include <Fonts/FreeSansBold9pt7b.h>
#include <MFRC522.h>
#include <OneWire.h>
#include <PubSubClient.h>
#include <QRCodeGenerator.h>
#include <SPI.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <Wire.h>

// ---- Configuration ---------------------------------------------------------

#if __has_include("config.h")
#include "config.h"
#else
#warning "config.h not found - using config.example.h. Copy it to config.h and set your WiFi."
#include "config.example.h"
#endif
#ifndef MQTT_TLS
#define MQTT_TLS 1
#endif
#ifndef MQTT_USERNAME
#define MQTT_USERNAME nullptr
#define MQTT_PASSWORD nullptr
#endif
#ifndef STATION_ID
#define STATION_ID "EVCS-01"
#endif
#ifndef OVER_TEMPERATURE_C
#define OVER_TEMPERATURE_C 35.0f  // DS18B20 at / above this -> OVER_TEMPERATURE -> ERROR
#endif
#ifndef OVER_CURRENT_A
#define OVER_CURRENT_A 1.0f       // INA219 above this for 0.5 s -> FAULT (SYSTEM_ERROR)
#endif

const char *FIRMWARE = "station-hw-1.0";

// Billing: the station measures the energy (INA219 power integrated over time),
// scales it up (ENERGY_SCALE) and sends it as energy_kwh_sim; the API bills
// energy_kwh_sim x the session price. Without an INA219 nothing is sent and
// the API falls back to time based billing: 0.1 kW per 30 s tick.
const float POWER_KW = 0.1f;
const float TICK_KWH = POWER_KW * 30.0f / 3600.0f;
// The bench load is a 9 V battery and an LED (about 11 mA, 0.1 W). What the
// screen and the telemetry show is scaled up to charger-like numbers:
// 9 V -> 900 V, 11 mA -> 0.11 A, so 0.1 W reads as 0.1 kW. The safety check
// (OVER_CURRENT_A) still uses the real current.
const float SHOWN_VOLT_SCALE = 100.0f;
const float SHOWN_AMP_SCALE = 10.0f;
const float SHOWN_KW_PER_W = SHOWN_VOLT_SCALE * SHOWN_AMP_SCALE / 1000.0f;
// Billed energy = measured energy x this, the same factor as the power shown.
const float ENERGY_SCALE = SHOWN_VOLT_SCALE * SHOWN_AMP_SCALE;
const unsigned long TICK_MS = 30000;

// ---- Pins (ESP32-S3) -------------------------------------------------------

const uint8_t PIN_SDA = 8, PIN_SCL = 9;  // I2C: INA219 0x40
const uint8_t RFID_SS = 10, RFID_MOSI = 11, RFID_SCK = 12, RFID_MISO = 13, RFID_RST = 14;
const uint8_t TFT_CS = 15, TFT_DC = 6, TFT_RST = 21;  // TFT on the same SPI bus (SCK 12, MOSI 11, MISO 13)
const uint8_t PIN_DS18B20 = 4;
const uint8_t PIN_PLUG = 5;       // push button, LOW while pressed: each press = plug in / unplug
const uint8_t PIN_STOP = 7;       // LOW = pressed
const uint8_t PIN_RELAY = 16;     // HIGH = battery connected to the EV
const uint8_t PIN_LOCK = 45;      // servo
const uint8_t PIN_BUZZER = 18;
const uint8_t PIN_JOY_HORZ = 1;   // ADC (VRX), max = left; module powered from 3V3
const uint8_t PIN_JOY_SEL = 2;    // LOW = joystick pressed = START / OK
const uint8_t LED_R = 41, LED_G = 38, LED_B = 39;  // one RGB status LED, common cathode (see updateLeds)

// Fixed budget levels (VND). 0 = no limit (member: app budget or whole balance).
const long BUDGET_LEVELS[] = {0, 10000, 20000, 50000, 100000};
const int BUDGET_COUNT = sizeof(BUDGET_LEVELS) / sizeof(BUDGET_LEVELS[0]);

// Joystick (real module, noisy): see readJoystick().
const unsigned long JOY_SW_DEBOUNCE_MS = 30;   // SW must stay stable this long to count
const unsigned long JOY_LOCKOUT_MS = 200;      // ignore X while / right after pressing SW
const unsigned long JOY_DWELL_MS = 30;         // a direction must hold this long to step
const unsigned long JOY_RECENTER_MS = 2500;    // stuck off-centre but still: new centre
const unsigned long JOY_UNDO_MS = 250;         // SW right after a step: that step was a wobble
const int JOY_VALID_MIN = 300, JOY_VALID_MAX = 3800;  // centre outside = VRX not powered / not wired
const int JOY_ENTER_PCT = 50, JOY_EXIT_PCT = 25;      // hysteresis, % of the travel to the end
const unsigned long RFID_POLL_MS = 150;        // a card poll takes ~30 ms: do not slow the joystick

// ---- Devices ---------------------------------------------------------------

#if MQTT_TLS
WiFiClientSecure wifi;  // TLS
#else
WiFiClient wifi;
#endif
PubSubClient mqtt(wifi);
Adafruit_ILI9341 tft(TFT_CS, TFT_DC, TFT_RST);  // 2.8" 240x320 colour TFT on the RFID SPI bus
Adafruit_INA219 ina219(0x40);
MFRC522 rfid(RFID_SS, RFID_RST);
OneWire oneWire(PIN_DS18B20);
DallasTemperature thermometer(&oneWire);

// ---- State -----------------------------------------------------------------
// Types are declared before any function: the Arduino builder puts the
// function prototypes there.

// DD state table, same values as the API.
enum class StationStatus { Ready, Charging, Error, Maintenance };
enum class SessionStatus { None, Active, WaitingPayment };  // None = COMPLETED / ERROR / no session
enum class PaymentStatus { None, Pending, Paid, Expired, Cancelled };
enum class LockStatus { Unlocked, Locked, Unknown };

// Local step while the station waits for the API (not a DD state).
enum class Step {
  Boot,      // connected, waiting for STATION_SYNC
  Idle,
  MemberBudget,  // member card accepted: name + balance, budget on the joystick
  WaitPlug,      // member budget set, waiting for the cable
  Starting,  // SESSION_START / GUEST_START sent
  Stopping,  // unplugged / stopped, waiting for STOP (+ LOCK / PAYMENT_QR)
};

enum class Icon { Ok, Error, Info, Busy };
enum class Align { Left, Center, Right };

struct Notice {
  Icon icon;
  String title;
  String detail;
  String hint;
  String extra;
} notice;

StationStatus station = StationStatus::Ready;
SessionStatus session = SessionStatus::None;
PaymentStatus payment = PaymentStatus::None;
LockStatus lockStatus = LockStatus::Unknown;
Step step = Step::Boot;

String sessionId;
String sessionType;        // MEMBER | GUEST
long sessionBudget = 0;    // 0 = no limit
long sessionPrice = 4000;
unsigned long sessionStartedAt = 0;
String stopReason;         // last stop reason from the API

String memberUid;          // card waiting for the cable
String memberName;
long memberBalance = 0;
long memberBudget = 0;     // budget to send with SESSION_START (0 = none)
unsigned long memberWaitUntil = 0;
long memberOptions[BUDGET_COUNT + 1];  // budgets <= balance (+ the one set in the app)
int memberOptionCount = 0;
int memberOption = 0;
unsigned long memberDecideUntil = 0;   // no input until then: back to the welcome screen

int guestBudget = 0;       // BUDGET_LEVELS index shown on the READY screen (0 = no limit)

String paymentId;
long paymentAmount = 0;
QRCode qr;
uint8_t qrModules[256];
bool qrReady = false;

String errorReason;        // why the station is in ERROR
String errorDetail;        // e.g. the current that tripped the over-current protection
String techUid;            // technician card being checked / verified
String techName;
unsigned long techUntil = 0;  // verified technician may hold STOP until then

bool relayOn = false;
bool plugged = false;
float temperatureC = 25.0f;
bool sensorOk = true;

bool inaOk = false;        // INA219 found on the I2C bus
float busVoltage = 0;      // V
float loadCurrent = 0;     // A
float loadPower = 0;       // W
double meteredWs = 0;      // real energy of the open session, watt seconds

unsigned long messageUntil = 0;  // a notice is on screen until then
unsigned long lastHeartbeat = 0;
unsigned long lastTelemetry = 0;
unsigned long lastTempRead = 0;
unsigned long stoppingSince = 0;
unsigned long stopPressedAt = 0;
bool stopFired = false;
String serialLine;

String topic(const char *kind) { return String("evcs/") + STATION_ID + "/" + kind; }

// ---- Text helpers ----------------------------------------------------------

/** Keep the ASCII base letter of Vietnamese characters; the TFT fonts have no accents. */
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

String budgetLabel(long amount, const char *none = "Khong GH") { return amount ? vnd(amount) : String(none); }

String reasonText(const String &reason) {
  if (reason == "UNPLUG") return "Rut sac";
  if (reason == "MAX_BUDGET") return "Het ngan sach";
  if (reason == "INSUFFICIENT_BALANCE") return "Het so du";
  if (reason == "TECHNICIAN") return "KTV / admin dung";
  if (reason == "E_STOP") return "E-Stop";
  if (reason == "OVER_TEMPERATURE") return "Qua nhiet";
  if (reason == "SYSTEM_ERROR") return "Loi he thong";
  return reason;
}

// ---- State names (exactly the API values) ---------------------------------

const char *stationName(StationStatus s) {
  switch (s) {
    case StationStatus::Ready: return "READY";
    case StationStatus::Charging: return "CHARGING";
    case StationStatus::Error: return "ERROR";
    case StationStatus::Maintenance: return "MAINTENANCE";
  }
  return "";
}

const char *sessionName(SessionStatus s) {
  return s == SessionStatus::Active ? "ACTIVE" : s == SessionStatus::WaitingPayment ? "WAITING_PAYMENT" : nullptr;
}

const char *paymentName(PaymentStatus p) {
  switch (p) {
    case PaymentStatus::Pending: return "PENDING";
    case PaymentStatus::Paid: return "PAID";
    case PaymentStatus::Expired: return "EXPIRED";
    case PaymentStatus::Cancelled: return "CANCELLED";
    default: return nullptr;
  }
}

const char *lockName(LockStatus l) {
  return l == LockStatus::Locked ? "LOCKED" : l == LockStatus::Unlocked ? "UNLOCKED" : "UNKNOWN";
}

StationStatus parseStation(const String &s) {
  if (s == "CHARGING") return StationStatus::Charging;
  if (s == "ERROR") return StationStatus::Error;
  if (s == "MAINTENANCE") return StationStatus::Maintenance;
  return StationStatus::Ready;
}

PaymentStatus parsePayment(const String &s) {
  if (s == "PENDING") return PaymentStatus::Pending;
  if (s == "PAID") return PaymentStatus::Paid;
  if (s == "EXPIRED") return PaymentStatus::Expired;
  if (s == "CANCELLED") return PaymentStatus::Cancelled;
  return PaymentStatus::None;
}

LockStatus parseLock(const String &s) {
  if (s == "LOCKED") return LockStatus::Locked;
  if (s == "UNLOCKED") return LockStatus::Unlocked;
  return LockStatus::Unknown;
}

// ---- Session numbers -------------------------------------------------------

/** Real energy the INA219 measured in this session, kWh. */
double meteredRawKwh() { return meteredWs / 3600000.0; }

/** Energy the session is billed for, kWh: measured x ENERGY_SCALE. */
float sessionEnergy() {
  if (!sessionStartedAt) return 0;
  if (inaOk) return (float)(meteredRawKwh() * ENERGY_SCALE);
  unsigned long ticks = (millis() - sessionStartedAt) / TICK_MS;
  return ticks * TICK_KWH;
}

long sessionCost() {
  long cost = lround(sessionEnergy() * sessionPrice);
  return sessionBudget > 0 ? min(cost, sessionBudget) : cost;  // the API never bills past the budget
}

bool techArmed() { return techUid.length() && techUntil && millis() < techUntil; }

bool online() { return WiFi.status() == WL_CONNECTED && mqtt.connected(); }

/** READY with no open session and nothing pending: a new customer may start. */
bool available() {
  return station == StationStatus::Ready && session == SessionStatus::None &&
         (step == Step::Idle || step == Step::MemberBudget || step == Step::WaitPlug);
}

bool paymentRetryable() {
  return session == SessionStatus::WaitingPayment &&
         (payment == PaymentStatus::Expired || payment == PaymentStatus::Cancelled);
}

// ---- Outputs ---------------------------------------------------------------

void beep(int frequency = 2000, int ms = 80) { tone(PIN_BUZZER, frequency, ms); }

void setRelay(bool on) {
  // Fail-safe: never power the EV without a cable or while overheating.
  if (on && (!plugged || temperatureC >= OVER_TEMPERATURE_C)) on = false;
  relayOn = on;
  digitalWrite(PIN_RELAY, on ? HIGH : LOW);
}

/**
 * Lock servo on the core LEDC PWM (no servo library): 50 Hz, 14-bit,
 * pulse 500 us (0 deg, unlocked) .. 2400 us (180 deg); 90 deg = locked.
 */
void servoWrite(int degrees) {
  uint32_t pulseUs = 500 + (uint32_t)degrees * (2400 - 500) / 180;
  ledcWrite(PIN_LOCK, pulseUs * 16384 / 20000);
}

void setLock(LockStatus status) {
  lockStatus = status;
  if (status != LockStatus::Unknown) servoWrite(status == LockStatus::Locked ? 90 : 0);
}

/**
 * One RGB status light (common cathode):
 *   green   READY                     blue    CHARGING (blinking = starting / stopping)
 *   yellow  WAITING_PAYMENT (blinking = QR expired / cancelled)
 *   red     ERROR (blinking = offline: no WiFi / MQTT)
 *   orange  MAINTENANCE               green blinking = syncing with the API at boot
 */
void setRgb(uint8_t r, uint8_t g, uint8_t b) {
  static int32_t last = -1;
  int32_t packed = (int32_t)r << 16 | g << 8 | b;
  if (packed == last) return;  // analogWrite only on change
  last = packed;
  analogWrite(LED_R, r);
  analogWrite(LED_G, g);
  analogWrite(LED_B, b);
}

void updateLeds() {
  bool blink = (millis() / 400) % 2;
  uint32_t color = 0;  // 0xRRGGBB
  const uint32_t GREEN = 0x00FF00, BLUE = 0x0000FF, YELLOW = 0xFFA000, RED = 0xFF0000, ORANGE = 0xFF3C00;
  if (!online()) {
    color = blink ? RED : 0;
  } else if (step == Step::Boot) {
    color = blink ? GREEN : 0;
  } else if (session == SessionStatus::WaitingPayment) {
    // A guest still pays when the station is in ERROR: alternate red / yellow.
    if (station == StationStatus::Error) color = blink ? RED : YELLOW;
    else color = paymentRetryable() && !blink ? 0 : YELLOW;
  } else {
    switch (station) {
      case StationStatus::Ready: color = step == Step::Starting ? (blink ? BLUE : 0) : GREEN; break;
      case StationStatus::Charging: color = step == Step::Stopping && !blink ? 0 : BLUE; break;
      case StationStatus::Error: color = RED; break;
      case StationStatus::Maintenance: color = ORANGE; break;
    }
  }
  setRgb(color >> 16, (color >> 8) & 0xFF, color & 0xFF);
}

// ---- TFT 240x320 -----------------------------------------------------------
// ILI9341 2.8" colour TFT (SPI, shares the bus with the MFRC522). Layout:
//   0..43    header in the station status colour (station id + status)
//   44..65   status strip: cable, connector lock, temperature, backend link
//   72..319  the screen of the current state, or a notice after an event
// Every piece of text lives in a fixed box (slot). A box is only redrawn when
// its text changes, and a text that does not fit falls back to a smaller
// font, so nothing overlaps and the SPI bus stays mostly idle.

constexpr uint16_t rgb(uint8_t r, uint8_t g, uint8_t b) { return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3); }
const uint16_t C_BG = rgb(11, 17, 32), C_CARD = rgb(30, 41, 59), C_LINE = rgb(51, 65, 85);
const uint16_t C_TEXT = rgb(241, 245, 249), C_MUTED = rgb(148, 163, 184), C_DIM = rgb(71, 85, 105);
const uint16_t C_GREEN = rgb(34, 197, 94), C_AMBER = rgb(245, 158, 11);
const uint16_t C_RED = rgb(239, 68, 68), C_ORANGE = rgb(249, 115, 22);
// Web admin primary colour #437066 (fills, header, bars) and a lighter tint of
// it for text on the dark background (#7fb3a6), instead of blue / cyan.
const uint16_t C_BRAND = rgb(67, 112, 102), C_BRAND_TEXT = rgb(127, 179, 166);
const int16_t W = 240, H = 320;

// Fonts from large to small; nullptr = built-in 6x8 font.
const GFXfont *const FONTS[] = {&FreeSansBold24pt7b, &FreeSansBold18pt7b, &FreeSansBold12pt7b,
                                &FreeSansBold9pt7b, &FreeSans9pt7b, nullptr};
const GFXfont *const F_XL = FONTS[0], *const F_L = FONTS[1], *const F_M = FONTS[2], *const F_B = FONTS[3],
                     *const F_S = FONTS[4];

const int SLOTS = 40;
String slotCache[SLOTS];
String screenKey;

void textBounds(const String &s, const GFXfont *font, int16_t &x1, int16_t &y1, uint16_t &w, uint16_t &h) {
  tft.setFont(font);
  tft.setTextSize(1);
  tft.getTextBounds(s, 0, 0, &x1, &y1, &w, &h);
}

/**
 * Text in the box (x, y, w, h), vertically centred. Redrawn only when it
 * changed; falls back to smaller fonts (and finally clips) to fit the box.
 */
void text(uint8_t slot, int16_t x, int16_t y, int16_t w, int16_t h, String s, const GFXfont *font, uint16_t color,
          Align align = Align::Center, uint16_t bg = C_BG) {
  s = ascii(s);
  String key = s + '|' + color + '|' + bg + '|' + (uintptr_t)font;
  if (slot < SLOTS && slotCache[slot] == key) return;
  if (slot < SLOTS) slotCache[slot] = key;

  int f = 0;
  while (FONTS[f] != font) f++;
  int16_t x1, y1;
  uint16_t tw, th;
  textBounds(s, FONTS[f], x1, y1, tw, th);
  while ((tw > w || th > h) && FONTS[f]) textBounds(s, FONTS[++f], x1, y1, tw, th);
  while (tw > w && s.length() > 1) {  // even the 6x8 font is too wide: clip
    s.remove(s.length() - 1);
    textBounds(s, nullptr, x1, y1, tw, th);
  }
  tft.fillRect(x, y, w, h, bg);
  int16_t left = align == Align::Center ? x + (w - tw) / 2 - x1 : align == Align::Right ? x + w - tw - x1 : x - x1;
  int16_t top = y + (h - th) / 2;
  tft.setTextColor(color);
  tft.setCursor(left, top - y1);  // y1 = offset from the cursor (baseline / top) to the glyph top
  tft.print(s);
  tft.setFont(nullptr);
}

/** Start drawing a screen: clears it when it is another screen than last time. */
bool beginScreen(const String &key, uint16_t headerColor) {
  if (key == screenKey) return false;
  screenKey = key;
  for (auto &c : slotCache) c = "";
  tft.fillScreen(C_BG);
  tft.fillRect(0, 0, W, 44, headerColor);
  tft.fillRect(0, 44, W, 22, C_CARD);
  return true;
}

void card(int16_t x, int16_t y, int16_t w, int16_t h) { tft.fillRoundRect(x, y, w, h, 8, C_CARD); }

uint16_t statusColor() {
  if (!online()) return C_DIM;
  if (session == SessionStatus::WaitingPayment && station != StationStatus::Error) return C_AMBER;
  switch (station) {
    case StationStatus::Ready: return C_GREEN;
    case StationStatus::Charging: return C_BRAND;
    case StationStatus::Error: return C_RED;
    case StationStatus::Maintenance: return C_ORANGE;
  }
  return C_DIM;
}

String statusText() {
  if (!online()) return "OFFLINE";
  if (session == SessionStatus::WaitingPayment) return "THANH TOAN";
  switch (station) {
    case StationStatus::Ready: return "SAN SANG";
    case StationStatus::Charging: return "DANG SAC";
    case StationStatus::Error: return "LOI";
    case StationStatus::Maintenance: return "BAO TRI";
  }
  return "";
}

void drawHeader() {
  uint16_t hc = statusColor();
  text(0, 10, 0, 96, 44, STATION_ID, F_B, C_TEXT, Align::Left, hc);
  text(1, 106, 0, 126, 44, statusText(), F_B, C_TEXT, Align::Right, hc);
  // Strip: four cells of 60 px.
  text(2, 0, 44, 60, 22, "PLUG", F_B, plugged ? C_GREEN : C_DIM, Align::Center, C_CARD);
  text(3, 60, 44, 60, 22, lockStatus == LockStatus::Locked ? "LOCK" : "OPEN", F_B,
       lockStatus == LockStatus::Locked ? C_AMBER : C_DIM, Align::Center, C_CARD);
  text(4, 120, 44, 60, 22, String(temperatureC, 0) + "C", F_B, temperatureC >= OVER_TEMPERATURE_C ? C_RED : C_TEXT,
       Align::Center, C_CARD);
  text(5, 180, 44, 60, 22, "MQTT", F_B, mqtt.connected() ? C_GREEN : C_RED, Align::Center, C_CARD);
}

void drawSpinner(uint8_t slot, int16_t cx, int16_t cy, uint16_t color) {
  int active = (millis() / 200) % 8;
  String key = String("spin") + active;
  if (slotCache[slot] == key) return;
  slotCache[slot] = key;
  for (int i = 0; i < 8; i++) {
    float a = i * PI / 4;
    int16_t px = cx + cos(a) * 22, py = cy + sin(a) * 22;
    tft.fillCircle(px, py, 6, C_BG);
    tft.fillCircle(px, py, i == active ? 6 : 3, i == active ? color : C_DIM);
  }
}

void drawIcon(Icon icon, int16_t cx, int16_t cy, uint16_t color) {
  const int16_t r = 32;
  if (icon == Icon::Busy) return;  // animated in render()
  if (icon == Icon::Info) {
    tft.fillCircle(cx, cy, r, color);
    tft.fillRoundRect(cx - 4, cy - 6, 9, 24, 2, C_BG);
    tft.fillCircle(cx, cy - 16, 5, C_BG);
    return;
  }
  tft.fillCircle(cx, cy, r, color);
  for (int t = -3; t <= 3; t++) {
    if (icon == Icon::Ok) {
      tft.drawLine(cx - 16, cy + t, cx - 5, cy + 11 + t, C_BG);
      tft.drawLine(cx - 5, cy + 11 + t, cx + 16, cy - 10 + t, C_BG);
    } else {
      tft.drawLine(cx - 13 + t, cy - 13, cx + 13 + t, cy + 13, C_BG);
      tft.drawLine(cx + 13 + t, cy - 13, cx - 13 + t, cy + 13, C_BG);
    }
  }
}

uint16_t iconColor(Icon icon) {
  return icon == Icon::Ok ? C_GREEN : icon == Icon::Error ? C_RED : icon == Icon::Info ? C_AMBER : C_BRAND_TEXT;
}

/** Icon + title + detail + two hint lines, centred (notices, errors, maintenance). */
void drawCard(const String &key, Icon icon, const String &title, const String &detail, const String &hint,
              const String &extra = "", uint16_t color = 0) {
  if (!color) color = iconColor(icon);
  if (beginScreen(key, statusColor())) drawIcon(icon, W / 2, 124, color);
  drawHeader();
  if (icon == Icon::Busy) drawSpinner(30, W / 2, 124, color);
  text(10, 8, 168, W - 16, 34, title, F_M, C_TEXT);
  text(11, 8, 206, W - 16, 26, detail, F_B, color);
  text(12, 8, 240, W - 16, 24, hint, F_S, C_MUTED);
  text(13, 8, 266, W - 16, 24, extra, F_S, C_MUTED);
}

/** Budget picker card: label, value and arrows (joystick left / right). */
void drawBudgetCard(bool fresh, int16_t top, const String &caption, const String &value) {
  if (fresh) {
    card(10, top, W - 20, 80);
    tft.fillTriangle(22, top + 52, 34, top + 42, 34, top + 62, C_BRAND_TEXT);
    tft.fillTriangle(W - 22, top + 52, W - 34, top + 42, W - 34, top + 62, C_BRAND_TEXT);
  }
  text(20, 20, top + 6, W - 40, 22, caption, F_S, C_MUTED, Align::Center, C_CARD);
  text(21, 40, top + 32, W - 80, 40, value, F_L, C_TEXT, Align::Center, C_CARD);
}

void drawConnecting() {
  beginScreen("connecting", C_DIM);
  drawHeader();
  text(10, 0, 96, W, 60, "SECO", F_XL, C_BRAND_TEXT);
  text(11, 0, 158, W, 24, String("Tram sac ") + STATION_ID, F_S, C_MUTED);
  text(12, 0, 186, W, 26,
       WiFi.status() != WL_CONNECTED ? "Ket noi WiFi..." : !mqtt.connected() ? "Ket noi may chu..." : "Dong bo trang thai...",
       F_B, C_TEXT);
  drawSpinner(30, W / 2, 262, C_BRAND_TEXT);
}

/** Welcome: a member taps the card; without a card the customer charges as a GUEST. */
void drawReady() {
  bool fresh = beginScreen("ready", statusColor());
  drawHeader();
  text(10, 0, 72, W, 40, "Xin chao!", F_L, C_BRAND_TEXT);
  text(11, 8, 112, W - 16, 22, "Thanh vien: quet the RFID", F_B, C_TEXT);
  text(16, 8, 134, W - 16, 18, "hoac sac voi tu cach KHACH", F_S, C_MUTED);
  drawBudgetCard(fresh, 158, "KHACH - NGAN SACH", budgetLabel(BUDGET_LEVELS[guestBudget], "Khong gioi han"));
  text(12, 8, 244, W - 16, 22, plugged ? "Khach: bam joystick = START" : "Khach: nhan PLUG roi bam joy", F_S,
       plugged ? C_TEXT : C_MUTED);
  text(13, 8, 268, W - 16, 22, plugged ? "Da cam sac (PLUG)" : "Chua cam sac", F_S, plugged ? C_GREEN : C_DIM);
  text(14, 8, 294, W - 16, 22, "Gia 4.000d / kWh", F_S, C_MUTED);
}

/** Member identified by the card: name, balance, budget; the member presses to start. */
void drawMemberBudget() {
  bool fresh = beginScreen("member:" + memberName, statusColor());
  drawHeader();
  long left = max(0L, (long)(memberDecideUntil - millis()));
  text(10, 0, 70, W, 22, "THANH VIEN", F_B, C_BRAND_TEXT);
  text(11, 8, 92, W - 16, 34, memberName, F_M, C_TEXT);
  text(12, 0, 126, W, 24, "So du " + vnd(memberBalance), F_B, C_GREEN);
  drawBudgetCard(fresh, 156, "NGAN SACH (<= so du)", budgetLabel(memberOptions[memberOption], "Toi da"));
  text(13, 0, 242, W, 24, plugged ? "Bam joystick de bat dau sac" : "Nhan PLUG, roi bam joystick", F_B, C_TEXT);
  text(14, 0, 270, W, 22, "Tu huy sau " + String((left + 999) / 1000) + " giay", F_S, C_MUTED);
}

void drawWaitPlug() {
  long left = max(0L, (long)(memberWaitUntil - millis())) / 1000;
  drawCard("waitplug", Icon::Info, memberName, "Nhan nut PLUG", "de cam sac va sac", "Con " + String(left) + " giay",
           C_BRAND_TEXT);
}

void drawCharging() {
  bool fresh = beginScreen("charging", statusColor());
  drawHeader();
  unsigned long secs = sessionStartedAt ? (millis() - sessionStartedAt) / 1000 : 0;
  char elapsed[12];
  snprintf(elapsed, sizeof(elapsed), "%02lu:%02lu", secs / 60, secs % 60);
  text(10, 10, 70, 150, 24, sessionType == "GUEST" ? "KHACH" : "THANH VIEN", F_B, C_BRAND_TEXT, Align::Left);
  text(11, 160, 70, 70, 24, elapsed, F_B, C_TEXT, Align::Right);
  text(12, 0, 98, W, 54, String(sessionEnergy(), 5), F_XL, C_TEXT);
  text(13, 0, 152, W, 20, "kWh", F_S, C_MUTED);

  if (fresh) card(10, 178, W - 20, 92);
  text(14, 20, 182, W - 40, 20, "So tien", F_S, C_MUTED, Align::Left, C_CARD);
  text(15, 20, 202, W - 40, 36, vnd(sessionCost()), F_L, C_TEXT, Align::Left, C_CARD);
  if (sessionBudget > 0) {
    int16_t fill = (int16_t)min((long)(W - 40), (long)(W - 40) * sessionCost() / sessionBudget);
    String bar = String(fill);
    if (slotCache[16] != bar) {
      slotCache[16] = bar;
      tft.fillRoundRect(20, 244, W - 40, 8, 4, C_LINE);
      if (fill > 4) tft.fillRoundRect(20, 244, fill, 8, 4, C_BRAND);
    }
    text(17, 20, 252, W - 40, 16, "/ " + vnd(sessionBudget), F_S, C_MUTED, Align::Right, C_CARD);
  } else {
    text(17, 20, 246, W - 40, 20, sessionType == "GUEST" ? "Khong gioi han" : "Toi da: den khi het so du", F_S,
         C_MUTED, Align::Left, C_CARD);
  }

  // V / A / kW (scaled, see SHOWN_VOLT_SCALE) / C from the INA219 and the DS18B20.
  const char *names[] = {"Dien ap", "Dong", "Cong suat", "Nhiet do"};
  String values[] = {String(busVoltage * SHOWN_VOLT_SCALE, 0) + "V", String(loadCurrent * SHOWN_AMP_SCALE, 2) + "A",
                     String(loadPower * SHOWN_KW_PER_W, 2) + "kW",
                     String(temperatureC, 1) + "C"};
  for (int i = 0; i < 4; i++) {
    text(20 + i, i * 60, 278, 60, 12, names[i], nullptr, C_MUTED);
    text(24 + i, i * 60, 292, 60, 24, values[i], F_B, C_TEXT);
  }
}

/** QR (sandbox payment link) as large as fits, the amount under it. */
void drawPayment() {
  if (beginScreen("pay:" + paymentId, statusColor())) {
    // Phone cameras need the 4-module white quiet zone of the QR standard (also at
    // the corners: a square box, not a rounded one) and modules as large as fit
    // between the title (ends at y 96) and the amount (starts at y 286).
    const int quiet = 4, top = 96, room = 190;
    int span = qr.size + 2 * quiet;
    uint8_t scale = max(1, min((W - 8) / span, room / span));
    int16_t box = span * scale;
    int16_t bx = (W - box) / 2, by = top + (room - box) / 2;
    tft.fillRect(bx, by, box, box, ILI9341_WHITE);
    for (uint8_t y = 0; y < qr.size; y++)
      for (uint8_t x = 0; x < qr.size; x++)
        if (qrcode_getModule(&qr, x, y))
          tft.fillRect(bx + (quiet + x) * scale, by + (quiet + y) * scale, scale, scale, ILI9341_BLACK);
  }
  drawHeader();
  text(10, 0, 70, W, 26, "Quet ma QR de thanh toan", F_B, C_TEXT);
  text(11, 0, 286, W, 34, vnd(paymentAmount), F_M, C_AMBER);
}

/** The technician is holding STOP after a valid card: only the 3 s countdown is shown. */
bool holdingStop() { return stopPressedAt && !stopFired && techArmed() && online(); }

void drawHold() {
  bool fresh = beginScreen("hold", statusColor());
  drawHeader();
  bool exiting = station == StationStatus::Maintenance;
  unsigned long held = min(3000UL, millis() - stopPressedAt);
  text(10, 0, 76, W, 30, "KTV " + techName, F_S, C_MUTED);
  text(11, 0, 104, W, 34, "Dang giu nut STOP", F_M, C_TEXT);
  if (fresh) tft.drawCircle(W / 2, 192, 46, C_LINE);
  text(12, W / 2 - 40, 164, 80, 56, String((3000 - held + 999) / 1000), F_XL, C_ORANGE);
  int16_t fill = (int16_t)((long)(W - 40) * held / 3000);
  String bar = String(fill);
  if (slotCache[13] != bar) {
    slotCache[13] = bar;
    if (fresh || fill <= 4) tft.fillRoundRect(20, 254, W - 40, 12, 6, C_CARD);
    if (fill > 4) tft.fillRoundRect(20, 254, fill, 12, 6, C_ORANGE);
  }
  text(14, 0, 274, W, 24, exiting ? "de ket thuc bao tri" : "de vao bao tri", F_B, C_TEXT);
  text(15, 0, 298, W, 20, "Tha tay = huy", F_S, C_MUTED);
}

void render() {
  if (holdingStop()) {
    drawHold();
    return;
  }
  if (messageUntil) {
    drawCard("notice:" + notice.title + notice.detail, notice.icon, notice.title, notice.detail, notice.hint,
             notice.extra);
  } else if (!online() || step == Step::Boot) {
    drawConnecting();
  } else if (session == SessionStatus::WaitingPayment) {
    // A guest pays even when the station went to ERROR / MAINTENANCE meanwhile.
    if (payment == PaymentStatus::Pending && qrReady) drawPayment();
    else if (paymentRetryable())
      drawCard("qr-expired", Icon::Info, payment == PaymentStatus::Cancelled ? "QR da huy" : "QR het han",
               "Can thanh toan " + vnd(paymentAmount), "Nhan joystick", "de tao QR moi");
    else drawCard("qr-wait", Icon::Busy, "Dang tao QR", "Thanh toan " + vnd(paymentAmount), "");
  } else if (station == StationStatus::Error) {
    drawCard("error:" + errorReason, Icon::Error, "Loi: " + (errorReason.length() ? errorReason : String("tram")),
             errorDetail.length() ? errorDetail : "Nhiet do " + String(temperatureC, 1) + " C",
             techArmed() ? "KTV: giu STOP 3 giay" : "Cho ky thuat vien xu ly", techArmed() ? "" : "(quet the KTV)");
  } else if (station == StationStatus::Maintenance) {
    drawCard("maintenance", Icon::Info, "Bao tri", "Tram tam ngung phuc vu",
             techArmed() ? "Giu STOP 3 giay" : "Quet the KTV", "de ket thuc bao tri", C_ORANGE);
  } else if (step == Step::Starting) {
    drawCard("starting", Icon::Busy, "Dang bat dau", "Cho may chu...", "");
  } else if (step == Step::Stopping) {
    drawCard("stopping", Icon::Busy, "Dang dung sac", reasonText(stopReason), "Dang tinh tien...");
  } else if (session == SessionStatus::Active) {
    drawCharging();
  } else if (step == Step::MemberBudget) {
    drawMemberBudget();
  } else if (step == Step::WaitPlug) {
    drawWaitPlug();
  } else {
    drawReady();
  }
}

/** Show an event for a few seconds, then go back to the screen of the state. */
void notify(Icon icon, const String &title, const String &detail, const String &hint = "", unsigned long ms = 3000,
            const String &extra = "") {
  notice = {icon, title, detail, hint, extra};
  messageUntil = millis() + ms;
  Serial.printf("[TFT] %s | %s | %s %s\r\n", ascii(title).c_str(), ascii(detail).c_str(), ascii(hint).c_str(),
                ascii(extra).c_str());
  render();
}

void setQr(const String &payload) {
  // QRCodeGenerator does not check the capacity: too long a text for the version
  // still returns 0 and gives a corrupt code no phone can read (the payment link,
  // 68 characters, does not fit version 3). So pick the smallest version whose
  // data capacity (QR spec, ECC LOW, versions 1..6) holds the text in byte mode:
  // 4-bit mode + 8-bit length + 8 bits per character = length + 2 codewords.
  static const uint8_t DATA_CODEWORDS_LOW[] = {19, 34, 55, 80, 108, 136};
  qrReady = false;
  for (uint8_t version = 1; version <= 6 && !qrReady; version++) {
    if (DATA_CODEWORDS_LOW[version - 1] < payload.length() + 2) continue;
    if (qrcode_getBufferSize(version) > sizeof(qrModules)) break;
    qrReady = qrcode_initText(&qr, qrModules, version, ECC_LOW, payload.c_str()) == 0;
  }
  if (!qrReady) Serial.printf("[QR] link too long for the screen QR (%u chars)\r\n", payload.length());
  screenKey = "";  // a new QR: redraw the payment screen
  // The same link as the QR, clickable from the Serial Monitor.
  Serial.printf("[QR] Thanh toan %s: %s\r\n", vnd(paymentAmount).c_str(), payload.c_str());
}

/** The open session is over (COMPLETED / ERROR on the API side). */
void closeSession() {
  setRelay(false);
  session = SessionStatus::None;
  payment = PaymentStatus::None;
  sessionId = "";
  sessionType = "";
  sessionStartedAt = 0;
  paymentId = "";
  qrReady = false;
  step = Step::Idle;
  // The API keeps ERROR / MAINTENANCE; a finished session frees a CHARGING station.
  if (station == StationStatus::Charging) station = StationStatus::Ready;
}

void setStation(StationStatus next) {
  station = next;
  if (next == StationStatus::Error || next == StationStatus::Maintenance) {
    setRelay(false);
    step = Step::Idle;
    memberUid = "";
  }
  if (next != StationStatus::Error) {
    errorReason = "";
    errorDetail = "";
  }
}

// ---- MQTT out --------------------------------------------------------------

void publishJson(const char *kind, JsonDocument &doc, bool retained = false) {
  String body;
  serializeJson(doc, body);
  mqtt.publish(topic(kind).c_str(), body.c_str(), retained);
  Serial.printf("-> %s %s\r\n", kind, body.c_str());
}

void request(JsonDocument &doc) {
  doc["request_id"] = String(millis());
  publishJson("req", doc);
}

/** Measured energy of the open session: the API bills from it (see ENERGY_SCALE). */
void addMeter(JsonDocument &doc) {
  if (!inaOk || session != SessionStatus::Active || !sessionId.length()) return;
  doc["session_id"] = sessionId;
  doc["energy_kwh_sim"] = meteredRawKwh() * ENERGY_SCALE;
  doc["energy_kwh_raw"] = meteredRawKwh();
}

void sendEvent(const char *event, const char *key = nullptr, float value = 0) {
  JsonDocument doc;
  doc["event"] = event;
  if (sessionId.length()) doc["session_id"] = sessionId;
  addMeter(doc);  // UNPLUGGED / safety events carry the final measured energy
  if (key) doc[key] = value;
  publishJson("event", doc);
}

void ack(const String &command, bool ok = true) {
  JsonDocument doc;
  doc["command"] = command;
  doc["ok"] = ok;
  if (command == "LOCK" || command == "UNLOCK") doc["connector_lock"] = lockName(lockStatus);
  publishJson("cmd_ack", doc);
}

void sendTelemetry() {
  JsonDocument doc;
  doc["voltage_v"] = roundf(busVoltage * SHOWN_VOLT_SCALE);
  doc["current_a"] = roundf(loadCurrent * SHOWN_AMP_SCALE * 1000) / 1000;
  doc["power_kw"] = roundf(loadPower * SHOWN_KW_PER_W * 1000) / 1000;
  doc["temperature_c"] = roundf(temperatureC * 10) / 10;
  doc["relay"] = relayOn;
  doc["plugged"] = plugged;
  addMeter(doc);
  publishJson("telemetry", doc);
}

/** Heartbeat with the DD states as the station sees them (for debugging). */
void heartbeat() {
  JsonDocument doc;
  doc["fw"] = FIRMWARE;
  doc["station_status"] = stationName(station);
  if (sessionName(session)) doc["session_status"] = sessionName(session);
  if (paymentName(payment)) doc["payment_status"] = paymentName(payment);
  doc["connector_lock"] = lockName(lockStatus);
  publishJson("hb", doc);
}

// ---- Safety ----------------------------------------------------------------

/**
 * Over temperature / over current / sensor fault: relay off now, then tell the
 * API. The API stops the session (reason OVER_TEMPERATURE / SYSTEM_ERROR,
 * member charged, guest moves to WAITING_PAYMENT) and keeps the station ERROR.
 */
void tripSafety(const String &reason, const char *event, float temperature = 0) {  // value: C or A
  setRelay(false);
  errorDetail = strcmp(event, "OVER_TEMPERATURE") == 0 ? "Do duoc " + String(temperature, 1) + " C (>= 35)"
                : strcmp(event, "FAULT") == 0         ? "Do duoc " + String(temperature, 2) + " A (> 1 A)"
                                                      : "";
  techUid = "";
  techUntil = 0;
  beep(400, 600);
  if (strcmp(event, "OVER_TEMPERATURE") == 0) sendEvent(event, "temperature_c", temperature);
  else if (strcmp(event, "FAULT") == 0) sendEvent(event, "current_a", temperature);
  else sendEvent(event);
  setStation(StationStatus::Error);
  errorReason = reason;
  if (session == SessionStatus::Active) {
    step = Step::Stopping;
    stoppingSince = millis();
  }
  messageUntil = 0;
  render();
}

// ---- Commands from the API -------------------------------------------------

void onCommand(JsonDocument &doc) {
  String command = doc["command"] | "";

  if (command == "STOP") {
    setRelay(false);
    stopReason = doc["reason"] | "";
    ack(command);
    if (session != SessionStatus::Active) return;
    // The API sends the final bill with STOP (energy, cost, member charge).
    float energy = doc["energy_kwh"] | sessionEnergy();
    long cost = doc["cost_vnd"] | sessionCost();
    String used = String(energy, 5) + " kWh - " + reasonText(stopReason);
    if (sessionType == "GUEST") {
      if (doc["requires_payment"].is<bool>() && !doc["requires_payment"].as<bool>()) {
        // Under one 30 s billing tick: cost 0, COMPLETED without a QR (DD 5).
        closeSession();
        notify(Icon::Ok, "Mien phi", "Sac duoi 30 giay", "Khong can thanh toan", 6000, used);
      } else {
        // LOCK + PAYMENT_QR follow; meanwhile show the amount to pay.
        paymentAmount = cost;
        step = Step::Stopping;
        stoppingSince = millis();
        render();
      }
    } else {
      long charged = doc["charged_vnd"] | cost;
      String balance = doc["balance_after_vnd"].is<long>() ? "So du con " + vnd(doc["balance_after_vnd"].as<long>()) : "";
      closeSession();
      notify(Icon::Ok, "Da tru " + vnd(charged), balance, used, 8000, "Cam on quy khach");
    }
    beep(1200, 200);
    return;
  }

  if (command == "LOCK" || command == "UNLOCK") {
    setLock(command == "LOCK" ? LockStatus::Locked : LockStatus::Unlocked);
    ack(command);  // connector_lock in the API follows this acknowledgement
    if (command == "UNLOCK" && session == SessionStatus::WaitingPayment) {
      // The API unlocks once the payment is PAID: the session is COMPLETED.
      payment = PaymentStatus::Paid;
      closeSession();
      beep(2400, 150);
      notify(Icon::Ok, "Da thanh toan", "Cam on quy khach", "Dau sac da mo khoa", 4000);
    }
    return;
  }

  if (command == "PAYMENT_QR") {
    paymentId = doc["payment_id"] | "";
    paymentAmount = doc["amount_vnd"] | 0;
    payment = parsePayment(doc["status"] | "PENDING");
    if (doc["session_id"].is<const char *>()) sessionId = doc["session_id"].as<String>();
    setQr(doc["qr_payload"] | "");
    session = SessionStatus::WaitingPayment;
    step = Step::Idle;
    messageUntil = 0;
    beep(2000, 100);
    render();
    ack(command);
    return;
  }

  if (command == "PAYMENT_STATUS") {
    PaymentStatus status = parsePayment(doc["status"] | "");
    if (status != PaymentStatus::None && session == SessionStatus::WaitingPayment) {
      payment = status;
      if (paymentRetryable()) beep(800, 300);
      render();
    }
    ack(command);
    return;
  }

  if (command == "MAINTENANCE" || command == "READY") {
    techUid = "";
    techUntil = 0;
    messageUntil = 0;
    setStation(command == "MAINTENANCE" ? StationStatus::Maintenance : StationStatus::Ready);
    render();
    ack(command);
    return;
  }

  if (command == "RESET") {
    ack(command);
    setRelay(false);
    notify(Icon::Busy, "Khoi dong lai", "Lenh tu admin");
    delay(500);
    ESP.restart();
  }

  // PING and anything unknown: just acknowledge so the admin sees a response.
  ack(command, command == "PING");
}

// ---- Replies from the API --------------------------------------------------

/** Member budget chosen (joystick press): charge now, or once the cable is plugged in. */
void confirmMemberBudget() {
  memberBudget = memberOptions[memberOption];
  if (plugged) {
    startMemberSession();
  } else {
    memberWaitUntil = millis() + 30000;
    messageUntil = 0;
    step = Step::WaitPlug;
    render();
  }
}

void startMemberSession() {
  JsonDocument doc;
  doc["type"] = "SESSION_START";
  doc["uid"] = memberUid;
  if (memberBudget > 0) doc["max_budget_vnd"] = memberBudget;
  request(doc);
  messageUntil = 0;
  step = Step::Starting;
  render();
}

/** STATION_SYNC: take every DD state from the API after a (re)connect. */
void applySync(JsonDocument &doc) {
  setLock(parseLock(doc["connector_lock"] | "UNKNOWN"));
  setStation(parseStation(doc["status"] | "READY"));
  if (station == StationStatus::Error) {
    // Why the station is in ERROR (latest open error log), e.g. overheating
    // before a reboot: only a technician can clear it (DD 3.3).
    String type = doc["error"]["error_type"] | "";
    String event = doc["error"]["details"]["event"] | "";
    errorReason = type == "E_STOP"             ? "E-STOP"
                  : type == "OVER_TEMPERATURE" ? "Qua nhiet"
                  : event == "FAULT"           ? "Qua dong"
                  : event == "SENSOR_FAULT"    ? "Cam bien"
                  : type.length()              ? "He thong"
                                               : "Tram dang loi";
    float temp = doc["error"]["details"]["temperature_c"] | 0.0f;
    float amps = doc["error"]["details"]["current_a"] | 0.0f;
    errorDetail = event == "FAULT" ? "Da do " + String(amps, 2) + " A (> 1 A)"
                  : temp > 0       ? "Da do " + String(temp, 1) + " C (>= 35)"
                                   : "";
  }

  JsonObject s = doc["session"];
  String status = s.isNull() ? "" : String(s["status"] | "");
  session = status == "ACTIVE" ? SessionStatus::Active
            : status == "WAITING_PAYMENT" ? SessionStatus::WaitingPayment
                                          : SessionStatus::None;
  step = Step::Idle;
  if (session == SessionStatus::None) {
    closeSession();
    station = parseStation(doc["status"] | "READY");
    return;
  }

  sessionId = s["session_id"] | "";
  sessionType = s["type"] | "";
  sessionBudget = s["max_budget_vnd"] | 0;
  sessionPrice = s["price_per_kwh_vnd"] | 4000;
  stopReason = s["stop_reason"] | "";
  sessionStartedAt = millis() - (unsigned long)(s["duration_seconds"] | 0) * 1000UL;
  // The API stored the energy we measured: carry on counting from it.
  double storedRaw = s["energy_kwh_raw"] | 0.0;
  double storedSim = s["energy_kwh_sim"] | 0.0;
  meteredWs = (storedRaw > 0 ? storedRaw : storedSim / ENERGY_SCALE) * 3600000.0;

  if (session == SessionStatus::WaitingPayment) {
    JsonObject p = doc["payment"];
    paymentAmount = p["amount_vnd"] | (long)(s["cost_vnd"] | 0);
    paymentId = p["payment_id"] | "";
    payment = parsePayment(p["status"] | "PENDING");
    if (payment == PaymentStatus::Pending) setQr(p["qr_payload"] | "");
    return;
  }
  // ACTIVE: resume charging, unless the cable was pulled while we were away.
  setRelay(station == StationStatus::Charging);
  if (!plugged) {
    sendEvent("UNPLUGGED");
    step = Step::Stopping;
    stoppingSince = millis();
  }
}

void onResponse(JsonDocument &doc) {
  String type = doc["type"] | "";
  bool ok = doc["ok"] | false;
  String error = doc["error"] | "";

  if (type == "STATION_SYNC") {
    if (ok) applySync(doc);
    else step = Step::Idle;
    render();
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
      techUid = "";
      memberName = doc["name"] | "MEMBER";
      memberBalance = doc["balance_vnd"] | 0;
      // DD 3.1: show name + balance, then an optional budget <= balance.
      // The budget set in the app (if any) comes first, then "Toi da" (no
      // budget = until the balance runs out), then the fixed levels.
      long appBudget = doc["max_budget_vnd"] | 0;
      memberOptionCount = 0;
      if (appBudget > 0 && appBudget <= memberBalance) memberOptions[memberOptionCount++] = appBudget;
      memberOptions[memberOptionCount++] = 0;
      for (int i = 1; i < BUDGET_COUNT; i++) {
        if (BUDGET_LEVELS[i] <= memberBalance && BUDGET_LEVELS[i] != appBudget) memberOptions[memberOptionCount++] = BUDGET_LEVELS[i];
      }
      memberOption = 0;
      memberDecideUntil = millis() + 60000;
      beep(2400, 80);
      messageUntil = 0;
      step = Step::MemberBudget;
      render();
    } else {
      beep(400, 400);
      notify(Icon::Error, "The khong hop le", error.indexOf("inactive") >= 0 ? "The da bi khoa" : memberUid,
             "Lien he quay de cap the");
      techUid = "";
      memberUid = "";
    }
    return;
  }

  if (type == "SESSION_START" || type == "GUEST_START") {
    if (ok) {
      JsonObject s = doc["session"];
      sessionId = s["session_id"] | "";
      sessionType = type == "GUEST_START" ? "GUEST" : "MEMBER";
      sessionBudget = s["max_budget_vnd"] | 0;
      sessionPrice = s["price_per_kwh_vnd"] | 4000;
      sessionStartedAt = millis();
      meteredWs = 0;
      stopReason = "";
      session = SessionStatus::Active;
      payment = PaymentStatus::None;
      station = StationStatus::Charging;
      step = Step::Idle;
      memberUid = "";
      messageUntil = 0;
      setRelay(true);
      sendEvent("CHARGE_STARTED");
      beep(2400, 120);
      render();
    } else {
      beep(400, 400);
      String why = error;
      if (error.indexOf("exceed") >= 0) why = "Ngan sach > so du";
      else if (error.indexOf("Insufficient") >= 0) why = "So du khong du";
      else if (error.indexOf("offline") >= 0) why = "Tram offline";
      else if (error.indexOf("open session") >= 0) why = "Dang co phien mo";
      else if (error.indexOf("not READY") >= 0) why = "Tram khong READY";
      step = Step::Idle;
      memberUid = "";
      notify(Icon::Error, "Khong the sac", why, "", 4000);
    }
    return;
  }

  if (type == "GUEST_PAYMENT_REQUEST") {
    if (!ok) notify(Icon::Error, "Loi tao QR", error);
    return;  // the new QR arrives as cmd PAYMENT_QR
  }

  if (type == "TECHNICIAN_VERIFY") {
    if (ok) {
      techName = doc["name"] | "KTV";
      techUntil = millis() + 15000;
      beep(2400, 80);
      notify(Icon::Ok, "Ky thuat vien", techName, "Giu STOP 3 giay");
    } else {
      String uid = techUid;
      techUid = "";
      techUntil = 0;
      beep(400, 400);
      if (station == StationStatus::Ready) notify(Icon::Error, "The khong hop le", uid);
      else notify(Icon::Error, "Chi the KTV", "Tram dang " + String(station == StationStatus::Error ? "loi" : "bao tri"),
                  "Lien he ky thuat vien");
    }
    return;
  }

  if (type == "TECHNICIAN_ACTION") {
    techUid = "";
    techUntil = 0;
    if (ok) {
      beep(2400, 150);
      setStation(parseStation(doc["station_status"] | "READY"));
      if (station == StationStatus::Maintenance) notify(Icon::Ok, "Vao bao tri", techName, "", 2500);
      else notify(Icon::Ok, "Bao tri xong", "Tram san sang", "", 2500);
    } else {
      beep(400, 400);
      notify(Icon::Error, "Khong thuc hien", error);
    }
    return;
  }
}

void onMessage(char *rawTopic, byte *payload, unsigned int length) {
  String t(rawTopic);
  Serial.printf("<- %s %.*s\r\n", t.substring(t.lastIndexOf('/') + 1).c_str(), length, (const char *)payload);
  JsonDocument doc;
  if (deserializeJson(doc, payload, length)) return;
  if (t.endsWith("/cmd")) onCommand(doc);
  else if (t.endsWith("/res")) onResponse(doc);
}

void connectMqtt() {
  String clientId = String("seco-") + STATION_ID + "-" + String((uint32_t)ESP.getEfuseMac(), HEX);
  if (mqtt.connect(clientId.c_str(), MQTT_USERNAME, MQTT_PASSWORD, topic("status").c_str(), 0, true, "OFFLINE")) {
    mqtt.publish(topic("status").c_str(), "ONLINE", true);
    mqtt.subscribe(topic("cmd").c_str());
    mqtt.subscribe(topic("res").c_str());
    Serial.printf("MQTT connected to %s:%u\r\n", MQTT_HOST, MQTT_PORT);
    lastHeartbeat = 0;
    // Resume whatever the API thinks this station is doing.
    step = Step::Boot;
    JsonDocument doc;
    doc["type"] = "STATION_SYNC";
    request(doc);
  } else {
    Serial.printf("MQTT connect failed, rc=%d\r\n", mqtt.state());
  }
  render();
}

// ---- Inputs ----------------------------------------------------------------

void onCard(String uid) {
  uid.toUpperCase();
  beep(1800, 60);
  Serial.printf("Card %s\r\n", uid.c_str());

  if (station == StationStatus::Error || station == StationStatus::Maintenance) {
    techUid = uid;
    techUntil = 0;
    JsonDocument doc;
    doc["type"] = "TECHNICIAN_VERIFY";
    doc["uid"] = uid;
    request(doc);
    notify(Icon::Busy, "Kiem tra the", uid, "", 5000);
    return;
  }
  if (available()) {
    memberUid = uid;
    techUid = uid;  // used if this turns out to be a technician card
    techUntil = 0;
    JsonDocument doc;
    doc["type"] = "RFID_VERIFY";
    doc["uid"] = uid;
    request(doc);
    notify(Icon::Busy, "Dang xac thuc", uid, "", 5000);
    return;
  }
  notify(Icon::Info, "Khong nhan the", String("Tram ") + stationName(station));
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
  if (online() && step != Step::Boot) onCard(uid);
  else notify(Icon::Error, "Mat ket noi", "May chu MQTT");
}

void readSerial() {
  // A hex UID + Enter in the Serial Monitor acts like a card tap.
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialLine.length() && online() && step != Step::Boot) onCard(serialLine);
      serialLine = "";
    } else if (isxdigit(c) && serialLine.length() < 20) {
      serialLine += c;
    }
  }
}

// Joystick calibration: ADC large = LEFT, small = RIGHT (same as the Wokwi part).
int joyCenterRaw = 2048;
bool joyXOk = false;  // VRX gives a sane reading at rest

/** Average of 8 ADC samples (the module is noisy). */
int readJoyX() {
  long sum = 0;
  for (int i = 0; i < 8; i++) sum += analogRead(PIN_JOY_HORZ);
  return sum / 8;
}

/** How far towards an end, in % of the travel from the centre (0 = centre, 100 = end). */
int joyPercent(int x, int end) {
  int span = end - joyCenterRaw;
  return abs(span) < 50 ? 0 : (long)(x - joyCenterRaw) * 100 / span;
}

/** Measure the centre with the joystick at rest (boot, or when it got stuck). */
void calibrateJoystick() {
  long sum = 0;
  for (int i = 0; i < 16; i++) {
    sum += readJoyX();
    delay(5);
  }
  int center = sum / 16;
  joyXOk = center >= JOY_VALID_MIN && center <= JOY_VALID_MAX;
  if (joyXOk) joyCenterRaw = center;
  Serial.printf("[joy] centre X=%d %s\r\n", center,
                joyXOk ? "ok" : "INVALID: VRX not on GPIO1, or module not powered from 3V3 (left/right off)");
}

/** -1 = LEFT, 1 = RIGHT, 0 = centre, with hysteresis around the current direction. */
int joyDirection(int x, int current) {
  int left = joyPercent(x, 4095), right = joyPercent(x, 0);
  if (current == -1) return left > JOY_EXIT_PCT ? -1 : 0;
  if (current == 1) return right > JOY_EXIT_PCT ? 1 : 0;
  if (left > JOY_ENTER_PCT) return -1;
  if (right > JOY_ENTER_PCT) return 1;
  return 0;
}

/**
 * Joystick, the only customer control besides the cable:
 *   left / right  budget (READY: guest levels, after a member card: <= balance)
 *   press         START for a guest, OK for a member, new QR once it expired
 * One push = one step (no auto-repeat). Pressing the stick tilts it a little, so
 * X is ignored while SW is down, and a step just before the press is undone.
 */
void readJoystick() {
  static bool selRaw = false, pressed = false;
  static unsigned long selChangedAt = 0, lockUntil = 0, lastMove = 0;
  static int joyDir = 0, candidate = 0, lastStepDir = 0, stableRef = 0;
  static unsigned long candidateSince = 0, stableSince = 0, lastRetry = 0;

  // ---- SW: debounce ----
  bool clicked = false;
  bool rawSel = digitalRead(PIN_JOY_SEL) == LOW;
  if (rawSel != selRaw) {
    selRaw = rawSel;
    selChangedAt = millis();
    lockUntil = millis() + JOY_LOCKOUT_MS;
  }
  if (selRaw != pressed && millis() - selChangedAt >= JOY_SW_DEBOUNCE_MS) {
    pressed = selRaw;
    lockUntil = millis() + JOY_LOCKOUT_MS;
    clicked = pressed;
  }

  // ---- X: filtered, hysteresis, must hold JOY_DWELL_MS ----
  int moved = 0;
  int horz = readJoyX();
  if (!joyXOk) {
    joyDir = candidate = 0;
    if (millis() - lastRetry > 1000) {  // VRX came back: calibrate again
      lastRetry = millis();
      if (horz >= JOY_VALID_MIN && horz <= JOY_VALID_MAX) calibrateJoystick();
    }
  } else {
    bool xLocked = selRaw || pressed || millis() < lockUntil;
    int dir = xLocked ? 0 : joyDirection(horz, joyDir);
    if (dir != candidate) {
      candidate = dir;
      candidateSince = millis();
    }
    // Stuck to one side while X does not move and is not at the end: the rest
    // position drifted, take it as the new centre.
    if (abs(horz - stableRef) > 60) {
      stableRef = horz;
      stableSince = millis();
    }
    bool atEnd = joyPercent(horz, joyDir < 0 ? 4095 : 0) >= 80;
    if (joyDir && !xLocked && !atEnd && millis() - stableSince > JOY_RECENTER_MS) {
      Serial.printf("[joy] stuck %s at X=%d -> new centre\r\n", joyDir > 0 ? "RIGHT" : "LEFT", horz);
      joyCenterRaw = horz;
      joyDir = candidate = 0;
      stableSince = millis();
    } else if (millis() - candidateSince >= JOY_DWELL_MS && candidate != joyDir) {
      joyDir = candidate;
      if (joyDir) {
        moved = joyDir;  // right = next, left = previous
        lastMove = millis();
        lastStepDir = joyDir;
      }
    }
  }

  // A press right after a step: the step came from tilting the stick while pressing.
  int undo = 0;
  if (clicked && lastStepDir && millis() - lastMove < JOY_UNDO_MS) undo = -lastStepDir;
  if (clicked) lastStepDir = 0;
  if (!online() || step == Step::Boot || (!moved && !clicked)) return;

  if (step == Step::MemberBudget) {
    int delta = moved + undo;
    if (delta) {
      memberOption = (memberOption + delta + memberOptionCount) % memberOptionCount;
      memberDecideUntil = millis() + 60000;
      if (moved) beep(2600, 20);
    }
    messageUntil = 0;
    if (clicked) {
      beep(2400, 80);
      confirmMemberBudget();
    }
    render();
    return;
  }

  if (clicked && paymentRetryable()) {
    beep(2200, 40);
    JsonDocument doc;
    doc["type"] = "GUEST_PAYMENT_REQUEST";
    doc["session_id"] = sessionId;
    request(doc);
    notify(Icon::Busy, "Dang tao QR moi", vnd(paymentAmount), "", 5000);
    return;
  }

  if (!available() || step != Step::Idle) return;
  int delta = moved + undo;
  if (delta) {
    guestBudget = (guestBudget + delta + BUDGET_COUNT) % BUDGET_COUNT;
    if (moved) beep(2600, 20);
    messageUntil = 0;
    render();
  }
  if (clicked) {  // START (guest, DD 3.2)
    beep(2200, 40);
    if (!plugged) {
      notify(Icon::Info, "Chua cam sac", "Nhan PLUG roi bam");
      return;
    }
    JsonDocument doc;
    doc["type"] = "GUEST_START";
    if (BUDGET_LEVELS[guestBudget]) doc["max_budget_vnd"] = BUDGET_LEVELS[guestBudget];
    guestBudget = 0;
    request(doc);
    messageUntil = 0;
    step = Step::Starting;
    render();
  }
}

/** A switch input that must stay at the new level for 50 ms before it counts. */
bool settled(uint8_t pin, bool current, unsigned long &since) {
  bool now = digitalRead(pin) == LOW;
  if (now == current) {
    since = 0;
    return current;
  }
  if (!since) since = millis();
  return millis() - since >= 50 ? now : current;
}

void readSwitches() {
  // PLUG is a push button on the bench: each press toggles plugged / unplugged
  // (stands for the micro-switch in the connector).
  static bool rawDown = false, stableDown = false;
  static unsigned long changedAt = 0;
  bool down = digitalRead(PIN_PLUG) == LOW;
  if (down != rawDown) {
    rawDown = down;
    changedAt = millis();
  }
  if (millis() - changedAt < 30 || down == stableDown) return;
  stableDown = down;
  if (!stableDown) return;  // act on the press, not the release

  plugged = !plugged;
  beep(plugged ? 2200 : 1200, 60);
  Serial.printf("[plug] %s\r\n", plugged ? "DA CAM" : "DA RUT");
  if (online()) sendEvent(plugged ? "PLUG" : "UNPLUGGED");
  if (!plugged) {
    setRelay(false);
    if (session == SessionStatus::Active && step != Step::Stopping) {
      // The API stops the session (reason UNPLUG) and replies with STOP
      // (+ LOCK / PAYMENT_QR for a guest).
      step = Step::Stopping;
      stopReason = "UNPLUG";
      stoppingSince = millis();
    }
  } else if (step == Step::WaitPlug) {
    startMemberSession();
  }
}

void readButtons() {
  // STOP held 3 s after a technician card: enter / leave maintenance (DD 3.3).
  bool stop = digitalRead(PIN_STOP) == LOW;
  if (stop) {
    if (!stopPressedAt) {
      stopPressedAt = millis();
      stopFired = false;
    }
    unsigned long held = millis() - stopPressedAt;
    if (techArmed() && !stopFired) {
      if (held >= 3000) {
        stopFired = true;
        bool exiting = station == StationStatus::Maintenance;
        if (exiting && temperatureC >= OVER_TEMPERATURE_C) {
          notify(Icon::Error, "Chua the xong", "Nhiet do con cao");
        } else {
          JsonDocument doc;
          doc["type"] = "TECHNICIAN_ACTION";
          doc["uid"] = techUid;
          doc["action"] = exiting ? "EXIT_MAINTENANCE" : "ENTER_MAINTENANCE";
          request(doc);
          notify(Icon::Busy, "Dang xu ly", exiting ? "Ket thuc bao tri" : "Vao bao tri", "", 5000);
        }
      } else {
        messageUntil = 0;  // the countdown screen replaces any notice
        render();          // only the number and the bar change
      }
    }
  } else if (stopPressedAt) {
    if (!stopFired && !techArmed()) notify(Icon::Info, "Nut STOP", "Can the KTV + giu 3s", "", 1500);
    stopPressedAt = 0;
    render();  // released before 3 s: back to the maintenance / error screen
  }
}

void readTemperature() {
  if (millis() - lastTempRead < 1000) return;
  lastTempRead = millis();
  float t = thermometer.getTempCByIndex(0);
  thermometer.requestTemperatures();  // non-blocking, read on the next pass
  bool armed = online() && step != Step::Boot && station != StationStatus::Error &&
               station != StationStatus::Maintenance;

  if (t == DEVICE_DISCONNECTED_C) {
    if (sensorOk) {
      sensorOk = false;
      if (armed) tripSafety("Cam bien nhiet", "SENSOR_FAULT");
    }
    return;
  }
  sensorOk = true;
  temperatureC = t;
  if (temperatureC >= OVER_TEMPERATURE_C) {
    setRelay(false);
    if (armed) tripSafety("Qua nhiet", "OVER_TEMPERATURE", temperatureC);
  }
}

/** INA219 on the battery -> EV line: real V / I / P while the relay is closed. */
void readPower() {
  static unsigned long lastRead = 0;
  if (!inaOk || millis() - lastRead < 500) return;
  lastRead = millis();
  busVoltage = ina219.getBusVoltage_V();
  loadCurrent = ina219.getCurrent_mA() / 1000.0f;
  loadPower = ina219.getPower_mW() / 1000.0f;

  // Energy meter: add up the real power while the relay feeds an open session.
  static unsigned long lastMeterAt = 0;
  if (relayOn && session == SessionStatus::Active) meteredWs += loadPower * (lastRead - lastMeterAt) / 1000.0;
  lastMeterAt = lastRead;

  // Over current: two readings in a row (0.5 s) above the limit while charging.
  static int overReadings = 0;
  overReadings = relayOn && loadCurrent > OVER_CURRENT_A ? overReadings + 1 : 0;
  if (overReadings >= 2 && online() && step != Step::Boot && station != StationStatus::Error &&
      station != StationStatus::Maintenance) {
    overReadings = 0;
    Serial.printf("[safety] over current %.2f A > %.2f A\r\n", loadCurrent, OVER_CURRENT_A);
    tripSafety("Qua dong", "FAULT", loadCurrent);
  }
}

// ---- Arduino ---------------------------------------------------------------

void setup() {
  // Fail-safe relay OFF before anything else (DD 7).
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_RELAY, LOW);

  Serial.begin(115200);
  Serial.printf("\n[boot] SECO %s %s\r\n", STATION_ID, FIRMWARE);
  for (uint8_t pin : {PIN_PLUG, PIN_STOP, PIN_JOY_SEL}) pinMode(pin, INPUT_PULLUP);
  for (uint8_t pin : {PIN_BUZZER, LED_R, LED_G, LED_B}) pinMode(pin, OUTPUT);
  analogReadResolution(12);

  // I2C bus: list every device that answers (expected 0x40 INA219).
  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setTimeOut(50);
  Serial.print("[boot] I2C scan:");
  int found = 0;
  for (uint8_t address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.printf(" 0x%02X", address);
      found++;
    }
  }
  Serial.print(found ? "\r\n" : " nothing (check SDA=8 / SCL=9 wiring and VCC)\r\n");

  inaOk = ina219.begin(&Wire);
  Serial.printf("[boot] INA219 0x40: %s\r\n", inaOk ? "ok" : "NOT FOUND (telemetry V/I/P = 0)");

  // SPI bus shared by the TFT (CS 15) and the MFRC522 (CS 10): deselect both first.
  pinMode(RFID_SS, OUTPUT);
  digitalWrite(RFID_SS, HIGH);
  pinMode(TFT_CS, OUTPUT);
  digitalWrite(TFT_CS, HIGH);
  SPI.begin(RFID_SCK, RFID_MISO, RFID_MOSI, RFID_SS);
  tft.begin(40000000);
  tft.setRotation(0);  // portrait 240x320
  Serial.printf("[boot] TFT ILI9341: ok\r\n");
  render();
  rfid.PCD_Init();
  Serial.printf("[boot] MFRC522 version: 0x%02X\r\n", rfid.PCD_ReadRegister(MFRC522::VersionReg));

  thermometer.begin();
  thermometer.setWaitForConversion(false);
  thermometer.requestTemperatures();
  Serial.printf("[boot] DS18B20 sensors: %d\r\n", thermometer.getDeviceCount());

  ledcAttach(PIN_LOCK, 50, 14);  // servo PWM; position stays UNKNOWN until STATION_SYNC

  // PLUG is a push button: the cable starts unplugged; press it to plug in.
  plugged = false;
  calibrateJoystick();  // do not touch the joystick while booting
  render();

  Serial.printf("[boot] WiFi \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long wifiStarted = millis();
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print('.');
    updateLeds();
    render();
    if (millis() - wifiStarted > 15000) {  // retry instead of waiting forever
      Serial.printf(" status=%d, retry", WiFi.status());
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
      wifiStarted = millis();
    }
  }
  Serial.printf(" connected, IP %s\r\n", WiFi.localIP().toString().c_str());

#if MQTT_TLS
  // Encrypted but not authenticated: fine for the demo broker. Pin the CA
  // (wifi.setCACert) for a private broker.
  wifi.setInsecure();
#endif
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(2048);
  mqtt.setKeepAlive(30);
  mqtt.setSocketTimeout(25);  // the public broker can take ~20 s to answer CONNECT when busy
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
  readPower();
  readButtons();
  readJoystick();
  static unsigned long lastCard = 0;
  if (millis() - lastCard >= RFID_POLL_MS) {
    lastCard = millis();
    readCard();
    readJoystick();  // read the stick again right after the slow card poll
  }
  readSerial();

  if (step == Step::MemberBudget && millis() > memberDecideUntil) {  // nobody pressed: cancel
    step = Step::Idle;
    memberUid = "";
    notify(Icon::Info, "Het thoi gian", "Quet the lai de sac");
  }
  if (step == Step::WaitPlug && millis() > memberWaitUntil) {
    step = Step::Idle;
    memberUid = "";
    notify(Icon::Info, "Het thoi gian", "Quet the lai");
  }
  // A free guest session (cost 0) is COMPLETED without a QR: stop waiting.
  if (step == Step::Stopping && millis() - stoppingSince > 6000) closeSession();
  if (techUid.length() && techUntil && millis() > techUntil) {
    techUid = "";
    techUntil = 0;
  }

  if (mqtt.connected()) {
    if (!lastHeartbeat || millis() - lastHeartbeat > 10000) {
      lastHeartbeat = millis();
      heartbeat();
    }
    unsigned long every = session == SessionStatus::Active ? 3000 : 15000;
    if (millis() - lastTelemetry > every) {
      lastTelemetry = millis();
      sendTelemetry();
    }
  }

  if (messageUntil && (long)(millis() - messageUntil) > 0) messageUntil = 0;

  // Lights and screen animations (spinner, countdowns, live numbers).
  static unsigned long lastFrame = 0;
  updateLeds();
  if (millis() - lastFrame > 250) {
    lastFrame = millis();
    render();
  }
  delay(10);
}
