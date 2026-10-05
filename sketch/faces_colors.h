/*
  Colores de la carita, por personaje.

  Desde la 1.0.1 las caras son los SVG a todo color de assets/img/<guia>/:
  los colores (fondo incluido) salen de esos dibujos, en la paleta de 15
  colores que arma tools/make_face_sprites.py para cada guia (ver
  <guia>_face.h). Para cambiar los colores de un guia se edita su SVG y se
  vuelve a correr la herramienta; aqui ya no hay nada que tocar.

  El orden de los guias tiene que coincidir con PERSONA_IDS de
  python/gestures.py:
    0 = crispi   1 = carmel   2 = cesia   3 = cori   4 = cristal
*/

#pragma once

#include <stdint.h>

// Cuantos guias con cara hay (FACES[] en sketch.ino).
const uint8_t FACE_COUNT = 5;
