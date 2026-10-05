/*
  Cara a color de los guias (la que sale de los SVG de assets/img/<guia>/).

  Vive en un header por lo mismo que siempre: el IDE/arduino-cli inserta los
  prototipos de las funciones del .ino arriba del archivo, y una funcion que
  reciba estos tipos no compilaria si los structs estuvieran en el .ino.

  Los datos (<guia>_face.h) los genera tools/make_face_sprites.py. Capas:
    - base:  la Cara 1 a pantalla completa (ojos abiertos, boca en reposo).
    - arriba (filas < split): ojos y cejas. Estados FACE_TOP_*.
    - boca   (filas >= split): visemas y bocas de emocion. FACE_MOUTH_*.
    - extra  (filas >= split, uno por estado FACE_TOP_*): lo que la emocion
      cambia lejos de la boca (cachetes, lagrimas). Va con los ojos, no con
      la boca: se queda toda la frase aunque la boca cambie de visema.
  Arriba y boca nunca se pisan, asi que cada fila de pantalla es la base +
  la capa de arriba, o la base + el extra + la boca (ver faceComposeRow()
  en sketch.ino).

  Formato de cada sprite (RLE por fila, 4 bits de indice de color):
    token = indice << 4 | (largo - 1), largo 1..15; si los 4 bits bajos
    valen 15, el largo es 16 + el byte siguiente. Indice FACE_TRANSPARENT =
    se deja lo de abajo. La tabla de filas tiene un uint16 little endian por
    fila, offset desde `data`; filas iguales comparten bytes.
*/

#pragma once

#include <stdint.h>

const uint8_t FACE_TRANSPARENT = 15;

// --- Capa de arriba (ojos/cejas) ---
// Parpadeo: cuadros guardados de la Cara 2..6 (la Cara 1 es la base). Deben
// coincidir con BLINK_FRAMES de tools/make_face_sprites.py (lo verifica).
const uint8_t FACE_BLINK_FRAMES = 5;  // Cara 2, 3, 4, 5, 6
const uint8_t FACE_BLINK_HALF = 1;    // cual de esos es la Cara 3 (ojos de FRUSTRADO)

const uint8_t FACE_TOP_OPEN = 0;                            // = base
const uint8_t FACE_TOP_BLINK = 1;                           // 1 .. FACE_BLINK_FRAMES
const uint8_t FACE_TOP_ANGRY = FACE_TOP_BLINK + FACE_BLINK_FRAMES;
const uint8_t FACE_TOP_HAPPY = FACE_TOP_ANGRY + 1;
const uint8_t FACE_TOP_SURPRISE = FACE_TOP_ANGRY + 2;
const uint8_t FACE_TOP_SAD = FACE_TOP_ANGRY + 3;
const uint8_t FACE_TOP_FRUSTRATED = FACE_TOP_ANGRY + 4;     // mismos datos que la Cara 3
const uint8_t FACE_TOP_STATES = FACE_TOP_FRUSTRATED + 1;

// --- Capa de la boca ---
// 0 = reposo (la boca de la base) y 1..10 = visemas, en el MISMO orden que
// el Bridge "viseme" (python/gestures.py): a,e,i  b,m,p  c,d,g,k,n,s,t,x,y,z
// ch,sh,j  F  L  O  q,w  R  u. Despues, la boca de cada emocion.
const uint8_t FACE_MOUTH_REST = 0;
const uint8_t FACE_VIS_AEI = 1, FACE_VIS_BMP = 2, FACE_VIS_CDG = 3, FACE_VIS_CHJ = 4;
const uint8_t FACE_VIS_F = 5, FACE_VIS_L = 6, FACE_VIS_O = 7, FACE_VIS_QW = 8;
const uint8_t FACE_VIS_R = 9, FACE_VIS_U = 10;
const uint8_t FACE_VISEMES = 11;  // validos del Bridge: 0..10
const uint8_t FACE_MOUTH_ANGRY = 11;
const uint8_t FACE_MOUTH_HAPPY = 12;
const uint8_t FACE_MOUTH_SURPRISE = 13;
const uint8_t FACE_MOUTH_SAD = 14;
const uint8_t FACE_MOUTH_STATES = 15;
// FRUSTRADO no tiene boca propia: usa la de la F (FACE_VIS_F).

// Un sprite de una capa. w == 0: el estado es igual a la base.
struct FaceLayer {
  int16_t x, y, w, h;  // rectangulo de pantalla
  uint32_t rows;       // offset de la tabla de filas en el blob
  uint32_t data;       // base de los offsets de esa tabla
};

// Todo lo de un guia. Un solo blob con todos los sprites: menos simbolos y
// relocaciones en el link dinamico del core (que tambien ocupan flash).
struct ColorFace {
  const uint8_t *blob;
  const uint16_t *lut;  // 16 colores RGB565 ya con byte swap (15 no se usa)
  uint16_t bg;          // color de fondo, RGB565 normal (badges, canvas)
  int16_t split;        // primera fila de la capa de la boca
  FaceLayer base;
  FaceLayer top[FACE_TOP_STATES];
  FaceLayer extra[FACE_TOP_STATES];  // w == 0 salvo las emociones
  FaceLayer mouth[FACE_MOUTH_STATES];
};

// Un pedazo rectangular de pantalla que hay que reenviar. Ver
// facePlanDiff() en sketch.ino.
struct SpanRect {
  int16_t x, y, w, h;
};
