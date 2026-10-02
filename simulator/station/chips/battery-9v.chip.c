// 9V battery for Wokwi (custom chip): the charging power source.
// BAT+ is always high; it reaches the EV load only through the relay and the
// INA219 shunt. The voltage itself is set on the INA219 ("Dien ap pin").

#include "wokwi-api.h"

void chip_init(void) {
  pin_init("BAT+", OUTPUT_HIGH);
  pin_init("BAT-", INPUT);
}
