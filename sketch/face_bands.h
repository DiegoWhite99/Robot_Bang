/*
  Bandas de filas que cambian entre dos frames de la carita.

  Vive en un header (y no en sketch.ino) porque el IDE/arduino-cli inserta los
  prototipos de las funciones del .ino ARRIBA del archivo, antes de cualquier
  struct declarado ahi: una funcion que devuelva este tipo no compilaria.
*/

#pragma once

#include <stdint.h>

struct ChangeBands {
  int16_t topY, topH, botY, botH;
  uint32_t px(int16_t w) const { return (uint32_t)w * (uint32_t)(topH + botH); }
};
