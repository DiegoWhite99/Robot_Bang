/*
  Colores de la carita, por personaje.

  Este es el UNICO archivo que hay que tocar para cambiar el look de cada
  guia BANG: fondo, ojos, cejas y boca. sketch.ino solo lee esta tabla, no
  hace falta entender el resto del sketch para personalizar los colores.

  Cada color se escribe en RGB de 8 bits (0-255, como en cualquier editor de
  color: rojo, verde, azul). No hace falta convertir nada mas: sketch.ino se
  encarga de pasarlo al formato que entiende la pantalla.

  El orden de la tabla FACE_PALETTES tiene que coincidir con PERSONA_IDS de
  python/gestures.py:
    0 = crispi   1 = carmel   2 = cesia   3 = cori   4 = cristal

  Los valores de partida usan el mismo color que cada guia ya tiene en la
  interfaz web (brain.py -> PERSONAS[...]["color"]), para que la carita en
  pantalla combine con su tarjeta en el celular. Cambialos como quieras.
*/

#pragma once

#include <stdint.h>

struct FaceColors {
  uint8_t bgR,    bgG,    bgB;     // fondo de la pantalla
  uint8_t eyeR,   eyeG,   eyeB;    // color de los ojos
  uint8_t browR,  browG,  browB;   // color de las cejas
  uint8_t mouthR, mouthG, mouthB;  // color de la boca
};

/*
  Guias con cara dibujada (PNG en assets/img/<guia>/, hoy los 5): se usan el
  FONDO y el color de OJOS, que pinta todo el trazo (cejas, nariz y boca
  incluidas). La BOCA solo cuenta en los dibujos de dos tintas (Carmel): es
  el color de su bigote. Cejas no se usa en la cara dibujada.
*/

const FaceColors FACE_PALETTES[] = {
  // ---- 0: Crispi (el constructor) — los colores de sus PNG: fondo blanco,
  // trazo cian (#05cee8). ----
  { 255, 255, 255,     /* fondo */
      5, 206, 232,     /* ojos (= todo el trazo de la cara dibujada) */
      5, 206, 232,     /* cejas */
      5, 206, 232 },   /* boca */

  // ---- 1: Carmel (la estratega) — sus PNG: trazo amarillo (#fdd001) y
  // bigote verde (#01ba3f) ----
  { 255, 255, 255,     /* fondo */
    253, 208,   1,     /* ojos (= todo el trazo amarillo) */
    253, 208,   1,     /* cejas */
      1, 186,  63 },   /* boca (= el bigote verde) */

  // ---- 2: Cesia (la disruptora) — sus PNG: trazo rojo (#d7193d) ----
  { 255, 255, 255,
    215,  25,  61,
    215,  25,  61,
    215,  25,  61 },

  // ---- 3: Cori (la pensadora lateral) — sus PNG: trazo verde (#1ed494) ----
  { 255, 255, 255,
     30, 212, 148,
     30, 212, 148,
     30, 212, 148 },

  // ---- 4: Cristal (la musa reflexiva) — sus PNG: trazo violeta (#7d0ccf) ----
  { 255, 255, 255,
    125,  12, 207,
    125,  12, 207,
    125,  12, 207 },
};

const uint8_t FACE_PALETTES_COUNT = sizeof(FACE_PALETTES) / sizeof(FACE_PALETTES[0]);
