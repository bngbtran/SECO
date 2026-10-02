// INA219 current / power monitor for Wokwi (custom chip).
//
// Behaves like the real chip on I2C address 0x40 so the firmware uses the
// stock Adafruit_INA219 library: registers 0x00 config, 0x01 shunt voltage,
// 0x02 bus voltage, 0x03 power, 0x04 current, 0x05 calibration (datasheet
// SBOS448). Shunt resistor 0.1 ohm, like the common breakout boards.
//
// Power path:  9V battery -> relay -> VIN+ [shunt] VIN- -> EV load -> GND
// Current only flows while VIN+ is powered (relay closed); VIN- then drives
// the load. The battery voltage and the load current are the two sliders.

#include "wokwi-api.h"

#define SHUNT_OHMS 0.1f

typedef struct {
  pin_t vin_plus;
  pin_t vin_minus;
  uint32_t attr_voltage;
  uint32_t attr_current;
  uint16_t regs[6];
  uint8_t pointer;
  uint8_t write_count;  // bytes received in this write transaction
  uint16_t write_value;
  bool lsb_next;        // reads return MSB then LSB
} chip_state_t;

static chip_state_t state;  // one INA219 per chip instance (no libc / malloc)

static int16_t clamp16(int64_t v) { return v > 32767 ? 32767 : v < -32768 ? -32768 : (int16_t)v; }

static void update(chip_state_t *chip) {
  bool powered = pin_read(chip->vin_plus) == HIGH;
  pin_write(chip->vin_minus, powered ? HIGH : LOW);  // feeds the EV load

  float volts = powered ? attr_read_float(chip->attr_voltage) : 0;
  float amps = powered ? attr_read_float(chip->attr_current) / 1000.0f : 0;

  int16_t shunt = clamp16((int64_t)(amps * SHUNT_OHMS / 0.00001f));  // LSB 10 uV
  uint16_t bus = (uint16_t)(volts * 1000.0f / 4.0f);                 // LSB 4 mV
  int16_t current = clamp16((int64_t)shunt * chip->regs[5] / 4096);

  chip->regs[1] = (uint16_t)shunt;
  chip->regs[2] = (uint16_t)((bus << 3) | 0x2);  // CNVR: conversion ready
  chip->regs[4] = (uint16_t)current;
  int32_t magnitude = current < 0 ? -current : current;
  chip->regs[3] = (uint16_t)((int64_t)magnitude * bus / 5000);
}

static void reset(chip_state_t *chip) {
  for (int i = 0; i < 6; i++) chip->regs[i] = 0;
  chip->regs[0] = 0x399F;  // power-on default configuration
}

static void write_register(chip_state_t *chip, uint8_t reg, uint16_t value) {
  if (reg == 0) {
    if (value & 0x8000) reset(chip);  // RST bit
    else chip->regs[0] = value;
  } else if (reg == 5) {
    chip->regs[5] = value & 0xFFFE;  // bit 0 is read-only
  }
}

static bool on_connect(void *user_data, uint32_t address, bool read) {
  chip_state_t *chip = user_data;
  chip->write_count = 0;
  chip->lsb_next = false;
  update(chip);
  return true;
}

static uint8_t on_read(void *user_data) {
  chip_state_t *chip = user_data;
  uint16_t value = chip->regs[chip->pointer];
  uint8_t out = chip->lsb_next ? value & 0xFF : value >> 8;
  chip->lsb_next = !chip->lsb_next;
  return out;
}

static bool on_write(void *user_data, uint8_t data) {
  chip_state_t *chip = user_data;
  if (chip->write_count == 0) {
    chip->pointer = data < 6 ? data : 0;
  } else if (chip->write_count == 1) {
    chip->write_value = (uint16_t)data << 8;
  } else if (chip->write_count == 2) {
    chip->write_value |= data;
    write_register(chip, chip->pointer, chip->write_value);
  }
  chip->write_count++;
  return true;
}

static void on_vin_change(void *user_data, pin_t pin, uint32_t value) { update(user_data); }

void chip_init(void) {
  chip_state_t *chip = &state;
  reset(chip);
  chip->attr_voltage = attr_init_float("busVoltage", 9.0f);
  chip->attr_current = attr_init_float("loadCurrent", 350.0f);
  chip->vin_plus = pin_init("VIN+", INPUT_PULLDOWN);
  chip->vin_minus = pin_init("VIN-", OUTPUT_LOW);

  const pin_watch_config_t watch = {.user_data = chip, .edge = BOTH, .pin_change = on_vin_change};
  pin_watch(chip->vin_plus, &watch);

  const i2c_config_t i2c = {
      .user_data = chip,
      .address = 0x40,
      .scl = pin_init("SCL", INPUT),
      .sda = pin_init("SDA", INPUT),
      .connect = on_connect,
      .read = on_read,
      .write = on_write,
  };
  i2c_init(&i2c);
  update(chip);
}
