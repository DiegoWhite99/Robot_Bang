/*
  Sprite de la carita "dibujada" (la que sale de los PNG de assets/img/).

  Vive en un header por lo mismo que face_bands.h: el IDE/arduino-cli
  inserta los prototipos de las funciones del .ino arriba del archivo, y una
  funcion que reciba este tipo no compilaria si el struct estuviera en el .ino.
*/

#pragma once

#include <stdint.h>

// Estados de parpadeo que traen los PNG (la secuencia 1-2-3-4-5 de las
// imagenes es abierto, entrecerrado, cerrado, entrecerrado, abierto).
const uint8_t FACE_EYE_OPEN = 0;
const uint8_t FACE_EYE_HALF = 1;
const uint8_t FACE_EYE_CLOSED = 2;
const uint8_t FACE_EYE_STATES = 3;

// Niveles de apertura de la boca: 0 = cerrada (la sonrisa del PNG). Deben
// coincidir con MOUTH_LEVELS de python/voice.py.
const uint8_t FACE_MOUTH_LEVELS = 5;

// Rectangulo de pantalla + pixeles de 2 bits (0 = fondo ... 3 = trazo), 4 por
// byte, el primero en los bits bajos, filas de (w + 3) / 4 bytes.
struct FaceSprite {
  int16_t x, y, w, h;
  const uint8_t *px;

  uint8_t at(int16_t col, int16_t row) const {
    return (px[(uint32_t)row * ((w + 3) / 4) + (col >> 2)] >> ((col & 3) * 2)) & 3;
  }
};

// Un pedazo rectangular de un sprite que hay que reenviar (coordenadas
// locales al sprite). Ver planSpriteDiff() en sketch.ino.
struct SpanRect {
  int16_t x, y, w, h;
};

// Los sprites de un guia con cara dibujada (los genera, ya armados como
// <GUIA>_SPRITES, tools/make_face_sprites.py). Un guia sin PNG usaria la cara
// geometrica de siempre (ver spriteSetFor() en sketch.ino).
struct FaceSpriteSet {
  const FaceSprite *base;    // pantalla completa: ojos abiertos, boca cerrada
  const FaceSprite *eyesL;   // [FACE_EYE_STATES]
  const FaceSprite *eyesR;   // [FACE_EYE_STATES]
  const FaceSprite *mouths;  // [FACE_MOUTH_LEVELS]
  // false = una tinta (0 = fondo ... 3 = trazo, con antialiasing).
  // true  = dos tintas (Carmel y su bigote): 0 = fondo, 1 = medio trazo,
  //         2 = trazo (color de ojos), 3 = segunda tinta (color de boca).
  bool twoInks;
};
