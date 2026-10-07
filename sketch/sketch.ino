/*
  Chat BANG - carita animada en pantalla TFT + 2 servos (lado MCU).

  El MPU (Python) lleva la conversacion; aqui animamos la carita en la
  pantalla Y movemos 2 servos SG90 sincronizados, ambos mientras el celular
  lee la respuesta en voz alta.

  Python avisa con Bridge.notify("face_gesture", <valor>), donde <valor> es
  el gesto y el personaje empaquetados en un solo entero:
    valor = personaId * 16 + gesto      (GESTURE_COUNT, igual en gestures.py)

  Gesto (0-15). Los 7 primeros tienen cara propia; los de 1.1.0 solo mueven
  los brazos y toman prestada una cara que ya existe (el flash esta al 89 %):
    0 REST        cara neutra, parpadeo normal, servos en reposo (90°, sueltos)
    1 TALK        cara neutra + boca con visemas + servos en vaiven
    2 HAPPY       Cara feliz
    3 SURPRISE    Cara sorpresa
    4 ANGRY       Cara enojada (un "grrr" jugueton contra el problema)
    5 FRUSTRATED  ojos entrecerrados (Cara 3) + boca de la F ("uff")
    6 SAD         Cara triste
    7 WAVE        saluda: un brazo arriba moviendose de lado a lado  (cara feliz)
    8 CLAP        aplaude: los dos brazos arriba y abajo, rapido      (cara feliz)
    9 THINK       piensa: un brazo arriba, quieto                     (ojos del "uff")
   10 YES         asiente: dos cabezadas cortas                       (cara feliz)
   11 NO          niega: los brazos en espejo                         (cara neutra)
   12 DANCE       baila: espejo amplio, sin la cancioncita            (cara feliz)
   13 HUG         abraza: los dos brazos suben despacio y se quedan   (cara feliz)
   14 SLEEP       dormido: brazos caidos, respiracion lenta           (cara triste)
   15 STRETCH     se estira: hasta arriba, se queda y baja (bostezo)  (cara feliz)
  La emocion manda en los ojos/cejas toda la frase; la boca hace visemas
  mientras suena la voz y vuelve a la boca de la emocion en las pausas.
  REST vuelve a la cara neutra.

  Cara: los 20 SVG de cada guia (assets/img/<guia>/) convertidos a sprites
  a color por tools/make_face_sprites.py (-> <guia>_face.h, formato en
  face_sprite.h). La base (Cara 1) se pinta entera solo al cambiar de guia;
  despues solo viaja por SPI lo que cambia (ver facePlanDiff()). El
  parpadeo recorre la Cara 1..6 (ida y vuelta).

  Baile (de Diome-chan): Python toca musica y en cada golpe manda
  Bridge.notify("arm_step", i); el sketch pone los brazos en la pose i de
  DANCE_POSES (ver consumeArmStep()). Son dos bailes: la cancioncita corta al
  pasar de fase BANG (voice.celebrate(), ~3 s) y el baile largo que pide el
  niño con "baila" (voice.dance(), ~30 s con ritmo y coreografia).

  Bienvenida al BANG: al arrancar la pantalla muestra el GIF de
  assets/img/menu/Bang.gif (convertido por tools/make_splash.py ->
  bang_splash.h) en vez de la cara, mientras Python da la bienvenida en voz.
  Python lo prende y lo apaga con Bridge.notify("splash", 1 / 0); los gestos
  que lleguen mientras tanto mueven los servos y el guia pedido aparece al
  apagarse (ver consumePendingSplash()).

  Tarjetas BANG: cuando el guia voltea una tarjeta, Python manda
  Bridge.notify("card", guia * 10 + numero - 1) y la pantalla la muestra
  (tools/make_cards.py -> bang_cards.h) hasta que manda "card" 255, y ahi
  vuelve la cara. Misma regla de los gestos que con la bienvenida.

  Boca sincronizada con la voz: mientras suena el TTS, Python manda
  Bridge.notify("viseme", 0..10) con la forma de boca de lo que esta sonando
  (0 = reposo/pausa, 1..10 = visemas, ver face_sprite.h). El viejo
  Bridge.notify("mouth_level", 0..4) (solo volumen) sigue andando: 0 ->
  reposo, 1-2 -> c,d,g..., 3 -> a,e,i, 4 -> O.

  Pantalla TFT GMT028-05 (driver ST7789, 240x320, SPI). Conexion (header
  JDIGITAL del UNO Q):
    GND -> GND   VCC -> 3V3   SCK -> D13   SDA/MOSI -> D11
    CS  -> D10   DC  -> D9    RST -> D8

  Servos SG90 (señal), ver updateServos() mas abajo:
    Servo 1 -> D5   Servo 2 -> D6   (+5V y GND compartidos con la placa;
    con los 2 servos moviendose a la vez, mejor alimentarlos desde una
    fuente externa de 5V con GND comun, no solo del pin 5V de la placa)

  ---------------------------------------------------------------------------
  RENDIMIENTO: por que NO se usa tft.writePixels() (lo importante de leer)
  ---------------------------------------------------------------------------
  En esta placa (ARDUINO_ARCH_ZEPHYR, cortex-m33) Adafruit_SPITFT::writePixels()
  no tiene ninguna ruta optimizada: todas las que trae la libreria son para
  ESP32 / nRF52 / RP2040 / SAMD+DMA. Cae al bucle generico, un pixel por vez,
  y ahi cada pixel cuesta DOS llamadas de 1 byte (SPI_WRITE16 hace
  `transfer(w >> 8); transfer(w);`). Y en el core Zephyr cada transfer() de
  1 byte es un spi_transceive() COMPLETO Y BLOQUEANTE: mutex, mas una espera
  en semaforo que duerme el thread hasta que la ISR lo despierta.

  Resultado: un frame de 30.000 pixeles = 60.000 llamadas bloqueantes al
  kernel ≈ 200 ms por frame (3-5 fps), cuando el tiempo real de bus de esos
  bytes a 40 MHz es de 12 ms. Mas del 90% era overhead de software.

  La solucion es la API bulk del core, SPI.transfer(buf, n_bytes): UNA sola
  llamada al driver para todo el buffer (ver RegionCanvas::flushRect()). Dos
  detalles que hay que respetar y que NO son opcionales:

    1. SPI.transfer() es full-duplex y sobrescribe el buffer que recibe con
       lo que llega por MISO. Por eso JAMAS se le pasa el buffer del canvas:
       se copia a spiScratch y se manda ese. Si alguien "optimiza" esa copia,
       la cara se corrompe.
    2. La API bulk es de 8 bits y el panel espera 16 bits con el byte alto
       primero, asi que la copia hace el byte swap (__builtin_bswap16).

  No hay DMA disponible: CONFIG_SPI_STM32_DMA esta apagado en el firmware del
  core y no se puede habilitar desde el sketch. Y writePixel() (un pixel
  suelto) cuesta 13 llamadas al driver — nunca usarlo.

  Redibujado parcial: la cara se arma fila por fila en RAM (base + la capa
  que toca) y solo se vuelven a mandar por SPI los pedazos de filas que
  cambiaron. Hablando sin parpadear, por ejemplo, solo viaja la boca.
*/

#include "Arduino_RouterBridge.h"
#include "faces_colors.h"
#include "face_sprite.h"
#include "crispi_face.h"
#include "carmel_face.h"
#include "cesia_face.h"
#include "cori_face.h"
#include "cristal_face.h"
#include "bang_splash.h"
#include "bang_aviso.h"
#include "bang_cards.h"
#include <Adafruit_GFX.h>
#include <Adafruit_ST7789.h>
#include <SPI.h>
#include <Servo.h>
#include <vector>

#define TFT_CS   10
#define TFT_DC    9
#define TFT_RST   8

// Version del producto, visible en la pantalla (ver drawVersionBadge()).
// Debe coincidir con APP_VERSION de python/main.py.
#define APP_VERSION "1.1.0"

const int16_t SCREEN_W = 320;
const int16_t SCREEN_H = 240;

Adafruit_ST7789 tft = Adafruit_ST7789(TFT_CS, TFT_DC, TFT_RST);

// Contadores para la medicion de rendimiento (ver la linea [perf] del monitor).
// statsCopyUs/statsSpiUs separan el costo de armar el buffer del costo real de
// mandarlo, que es la unica forma de saber que optimizar.
uint32_t statsPixels = 0;
uint32_t statsCopyUs = 0;
uint32_t statsSpiUs = 0;

// Buffer intermedio para mandar al SPI. NO se puede mandar el canvas directo:
// SPI.transfer() escribe encima del buffer que recibe (ver cabecera).
const uint32_t SCRATCH_PX = 2048; // 4 KB; alcanza para 16 filas de la region mas ancha
uint16_t spiScratch[SCRATCH_PX];

// Buffer en RAM de una parte de la cara (no de la pantalla entera), que se
// vuelca a su rincon de la pantalla con una sola llamada al driver SPI.
class RegionCanvas : public GFXcanvas16 {
  public:
    RegionCanvas(int16_t w, int16_t h, int16_t originX, int16_t originY, Adafruit_ST7789 &tftRef)
      : GFXcanvas16(w, h), _originX(originX), _originY(originY), _tft(tftRef) {}

    bool ready() const { return getBuffer() != nullptr; } // GFXcanvas16 hace malloc()

    // Mueve el rincon de pantalla al que se vuelca este canvas. Sirve para
    // reutilizar UN solo buffer en varias filas (ver el menu de personajes):
    // cinco canvas separados no cabrian comodos en RAM.
    void setOrigin(int16_t x, int16_t y) { _originX = x; _originY = y; }

    void flush() { flushRect(0, 0, width(), height()); }

    // Manda a la pantalla un sub-rectangulo del canvas (coordenadas locales).
    void flushRect(int16_t rx, int16_t ry, int16_t rw, int16_t rh) {
      if (!ready() || rw <= 0 || rh <= 0) return;

      const int16_t rowsPerChunk = (int16_t)(SCRATCH_PX / (uint32_t)rw);
      if (rowsPerChunk < 1) return; // no deberia pasar: rw <= 128 en esta cara

      _tft.startWrite();
      _tft.setAddrWindow(_originX + rx, _originY + ry, rw, rh);

      for (int16_t row = 0; row < rh; ) {
        const int16_t rows = min((int16_t)(rh - row), rowsPerChunk);
        uint16_t *dst = spiScratch;

        // Copia + byte swap. Cada fila del sub-rect es contigua en el canvas.
        uint32_t t0 = micros();
        for (int16_t r = 0; r < rows; r++) {
          const uint16_t *src = getBuffer() + (uint32_t)(ry + row + r) * width() + rx;
          for (int16_t c = 0; c < rw; c++) {
            *dst++ = __builtin_bswap16(src[c]);
          }
        }
        uint32_t t1 = micros();

        // UNA sola llamada al driver por chunk, en vez de 2 por pixel.
        SPI.transfer(spiScratch, (size_t)rows * (size_t)rw * 2);
        uint32_t t2 = micros();

        statsCopyUs += t1 - t0;
        statsSpiUs += t2 - t1;
        row += rows;
      }

      _tft.endWrite();
      statsPixels += (uint32_t)rw * rh;
    }

  private:
    int16_t _originX, _originY;
    Adafruit_ST7789 &_tft;
};

const unsigned long FRAME_MS = 33; // 30 fps

// Fondo de la pantalla: el del guia en pantalla (sale de su SVG, ver
// ColorFace::bg). Lo usan los badges de WiFi y version.
uint16_t BG_COLOR = ST77XX_BLACK;

// --- Gestos recibidos desde Python. Deben coincidir con python/gestures.py ---
const uint8_t G_REST = 0;
const uint8_t G_TALK = 1;
const uint8_t G_HAPPY = 2;
const uint8_t G_SURPRISE = 3;
const uint8_t G_ANGRY = 4;
const uint8_t G_FRUSTRATED = 5;
const uint8_t G_SAD = 6;
const uint8_t G_WAVE = 7;        // saluda con un brazo
const uint8_t G_CLAP = 8;        // aplaude
const uint8_t G_THINK = 9;       // un brazo arriba, quieto: "dejame pensarlo"
const uint8_t G_YES = 10;        // asiente
const uint8_t G_NO = 11;         // niega (brazos en espejo)
const uint8_t G_DANCE = 12;      // baile propio, sin la cancioncita
const uint8_t G_HUG = 13;        // abrazo
const uint8_t G_SLEEP = 14;      // dormido: brazos caidos, respiracion lenta
const uint8_t G_STRETCH = 15;    // se estira (bostezo)
// Para desempaquetar el valor combinado (persona * GESTURE_COUNT + gesto).
// DEBE coincidir con _GESTURE_COUNT de python/gestures.py.
const uint8_t GESTURE_COUNT = 16;

// --- Personajes. Deben coincidir con PERSONA_IDS de python/gestures.py ---
const uint8_t P_CRISPI = 0;
const uint8_t P_CARMEL = 1;
const uint8_t P_CESIA = 2;
const uint8_t P_CORI = 3;
const uint8_t P_CRISTAL = 4;

uint8_t currentPersona = 255; // invalido a proposito: fuerza la 1ra cara

// Las caras de los guias, en el orden de P_* (cada <GUIA>_FACE viene armada
// en su <guia>_face.h).
const ColorFace *const FACES[FACE_COUNT] = {
  &CRISPI_FACE, &CARMEL_FACE, &CESIA_FACE, &CORI_FACE, &CRISTAL_FACE,
};

bool talking = false; // true entre un gesto != REST y el siguiente REST
uint8_t faceGesture = G_REST; // la emocion que manda en la cara (ver faceTopRest())

// --- Cara en pantalla. nullptr = todavia no se pinto ninguna ---
const ColorFace *face = nullptr;
uint8_t drawnTop = FACE_TOP_OPEN;     // estado de la capa de arriba en pantalla
uint8_t drawnMouth = FACE_MOUTH_REST; // estado de la boca en pantalla
unsigned long topDrawnMs = 0;         // cuando se termino de dibujar el estado de arriba
uint8_t eyesWaitFrames = 0;           // frames seguidos que los ojos no entraron

// Una fila de pantalla en indices de paleta (4 bits), para armar la cara
// antes de mandarla: la de ahora y la de antes, para comparar.
uint8_t faceLine[SCREEN_W];
uint8_t faceLineOld[SCREEN_W];

// Parpadeo: Cara 2..6 de ida y 5, 3 de vuelta (indices de cuadro de
// parpadeo, 0 = Cara 2). Cerrar rapido y abrir un poco mas suave, como
// un parpadeo de verdad. Cada paso se sostiene su tiempo DESDE QUE TERMINA
// DE DIBUJARSE, asi ningun cuadro se saltea aunque el SPI se atrase.
static_assert(FACE_BLINK_FRAMES == 5, "BLINK_SEQ supone la Cara 2..6");
const uint8_t BLINK_SEQ[] = { 0, 1, 2, 3, 4, 3, 1 };
const unsigned long BLINK_HOLD_MS[] = { 0, 0, 0, 0, 60, 0, 0 };
const int8_t BLINK_STEPS = sizeof(BLINK_SEQ);
int8_t blinkStep = -1;            // -1 = sin parpadeo en curso

// --- Parpadeo automatico (de vez en cuando, un poco mas seguido al hablar) ---
const unsigned long BLINK_IDLE_MIN_MS = 7000, BLINK_IDLE_MAX_MS = 14000;
const unsigned long BLINK_TALK_MIN_MS = 5000, BLINK_TALK_MAX_MS = 9000;
unsigned long nextAutoBlinkMs = 0;
unsigned long lastFrame = 0;

// --- Boca: visema que manda Python (Bridge "viseme" o "mouth_level") ---
// 0 = reposo/pausa, 1..10 = visemas (FACE_VIS_*). Lo escribe el hilo del
// Bridge (1 byte, atomico) y lo lee loop(), igual que pendingEncoded.
volatile uint8_t mouthViseme = FACE_MOUTH_REST;

// --- Servos: 2 SG90 (los brazos), se mueven segun el gesto ---
//
// Todo se piensa en "lift" (cuanto suben los brazos, en grados) y no en
// angulos: lift 0 = reposo (90°), lift > 0 = brazos ARRIBA, lift < 0 = brazos
// ABAJO. writeArms(lift) lo convierte a angulo para cada servo:
//   angulo_servo = 90 + ARMn_DIR * lift   (y despues se limita a ARM_MIN..ARM_MAX)
//
// CALIBRAR EN EL ROBOT (nadie sabe todavia hacia que lado sube cada brazo):
//   1. Hacer que el guia diga algo alegre (gesto HAPPY): los brazos van a
//      lift +65 y se quedan arriba mientras habla.
//   2. Si un brazo sube y el otro BAJA: los servos estan montados en espejo
//      (lo normal). Cambiar el signo del que baja (casi seguro ARM2_DIR = -1).
//   3. Si los DOS bajan: cambiar el signo de los dos.
//   4. Si algun brazo pega contra el cuerpo o la carcasa, achicar ARM_MIN /
//      ARM_MAX (son angulos de servo, no lift).
//   Recompilar y flashear (arduino-app-cli app restart) despues de cambiarlo.
//
// Por que +1 / +1 por defecto: asi el robot se mueve IGUAL que antes en lo
// que ya se veia. Hasta ahora TALK escribia el mismo angulo en los dos servos
// (80/100) y el baile los ponia espejados (90-swing / 90+swing); con los dos
// DIR en +1, TALK (lift ±10) y el baile (lift -swing / +swing) dan
// exactamente esos mismos angulos. Ojo: esos dos movimientos no pueden ser
// "simetricos" a la vez en el robot real, uno de los dos ya se veia
// asimetrico. Al calibrar (paso 2), TALK y los gestos quedan simetricos y el
// baile pasa a mover un brazo arriba y el otro abajo.
const uint8_t SERVO1_PIN = 5;
const uint8_t SERVO2_PIN = 6;
Servo servo1, servo2;

const int SERVO_REST_ANGLE = 90;
const int8_t ARM1_DIR = +1;   // servo 1 (D5): +1 si angulo mayor = brazo arriba, -1 si no
const int8_t ARM2_DIR = +1;   // servo 2 (D6): idem (en espejo seria -1)
const int ARM_MIN = 20;       // angulo minimo permitido (lejos del tope mecanico del SG90)
const int ARM_MAX = 160;      // angulo maximo permitido

// Movimiento de cada gesto (lift en grados, tiempos en ms). Todo con millis():
// loop() corre cada ~25-33 ms y a veces se traba ~0,6 s (repintado entero de
// la pantalla), asi que los pasos rapidos son de >= 50 ms y despues de una
// trabada se sigue desde donde se esta, sin escribir de golpe los pasos que
// se perdieron.
const unsigned long SERVO_WRITE_MS       = 20;  // entre escrituras de una rampa (la señal del SG90 es de 50 Hz)
const unsigned long SERVO_REST_SETTLE_MS = 350; // tiempo para llegar a 90 antes de soltar (detach)
const unsigned long SERVO_RESUME_MS      = 250; // rampa para volver a la pose del gesto despues del baile

// HAPPY (feliz): manos arriba
const int SERVO_HAPPY_LIFT = 65;
const unsigned long SERVO_HAPPY_RAMP_MS = 250;
// SURPRISE: respingo arriba, baja un poco y tiembla
const int SERVO_SURPRISE_JUMP = 70;
const unsigned long SERVO_SURPRISE_JUMP_MS = 100;
const unsigned long SERVO_SURPRISE_HOLD_MS = 200;
const int SERVO_SURPRISE_LIFT = 45;
const unsigned long SERVO_SURPRISE_LOWER_MS = 300;
const int SERVO_SURPRISE_SHAKE = 5;             // temblor de ±5° ...
const unsigned long SERVO_SURPRISE_SHAKE_STEP_MS = 60; // ... cada 60 ms ...
const unsigned long SERVO_SURPRISE_SHAKE_MS = 800;     // ... durante 800 ms
// ANGRY (enojada, "grrr" jugueton): arriba/abajo rapido y erratico, corto y
// de amplitud moderada (los dos SG90 frenando y arrancando a la vez piden
// picos de corriente: mas amplitud = riesgo de brownout de la placa)
const int SERVO_ANGRY_MIN_AMP = 10;
const int SERVO_ANGRY_MAX_AMP = 25;             // nunca mas de ±30
const uint8_t SERVO_ANGRY_MIN_STEPS = 10;
const uint8_t SERVO_ANGRY_MAX_STEPS = 14;
const unsigned long SERVO_ANGRY_MIN_STEP_MS = 50;
const unsigned long SERVO_ANGRY_MAX_STEP_MS = 90;
const unsigned long SERVO_ANGRY_MAX_MS = 1200;  // tope total del "grrr"
// FRUSTRATED ("uff"): sube lento, baja hasta la mitad, pausa ("corte medio")
// y termina de bajar; dos veces
const int SERVO_FRUS_TOP = 40;
const int SERVO_FRUS_MID = 20;
const unsigned long SERVO_FRUS_UP_MS = 600;
const unsigned long SERVO_FRUS_DOWN_MS = 300;
const unsigned long SERVO_FRUS_PAUSE_MS = 400;
const unsigned long SERVO_FRUS_END_PAUSE_MS = 150;
const uint8_t SERVO_FRUS_CYCLES = 2;
// SAD (triste): los brazos caen despacio por debajo del reposo
const int SERVO_SAD_LIFT = -25;
const unsigned long SERVO_SAD_RAMP_MS = 700;

// --- Gestos agregados en 1.1.0 ------------------------------------------------
// Todos reusan la misma mecanica (entrada por pasos + vaiven sostenido) y
// ninguno dibuja caras nuevas: solo mueven los brazos (y toman prestada una
// cara que ya existe, ver faceTopFor()).
//
// Los que van en ESPEJO (un brazo sube mientras el otro baja) usan
// armMovePair(); los simetricos, armMove(). Nada pasa de ±70 de lift: los dos
// SG90 arrancando juntos con mucha amplitud piden picos de corriente (ver la
// nota de ANGRY).

// WAVE (saludar): un brazo arriba que se mueve de lado a lado, el otro quieto
const int SERVO_WAVE_LIFT = 65;
const int SERVO_WAVE_SWING = 18;
const unsigned long SERVO_WAVE_UP_MS = 250;
const unsigned long SERVO_WAVE_STEP_MS = 170;
const uint8_t SERVO_WAVE_SWINGS = 4;
// CLAP (aplaudir): los dos brazos suben y bajan juntos, rapido y corto
const int SERVO_CLAP_HIGH = 45;
const int SERVO_CLAP_LOW = 12;
const unsigned long SERVO_CLAP_STEP_MS = 130;
const uint8_t SERVO_CLAP_CLAPS = 6;
// THINK (pensar): un brazo sube despacio y se queda; el otro, quieto
const int SERVO_THINK_LIFT = 55;
const unsigned long SERVO_THINK_UP_MS = 700;
// YES (asentir): los dos brazos asienten dos veces, cortito
const int SERVO_YES_HIGH = 28;
const int SERVO_YES_LOW = 0;
const unsigned long SERVO_YES_STEP_MS = 190;
const uint8_t SERVO_YES_NODS = 2;
// NO (negar): en espejo, como una cabeza que dice que no
const int SERVO_NO_SWING = 25;
const unsigned long SERVO_NO_STEP_MS = 230;
const uint8_t SERVO_NO_SWINGS = 3;
// DANCE (bailar): el baile propio, sin la cancioncita (esa va por arm_step)
const int SERVO_DANCE_SWING = 45;
const unsigned long SERVO_DANCE_STEP_MS = 210;
const uint8_t SERVO_DANCE_SWINGS = 6;
// HUG (abrazar): los dos brazos suben despacio y se quedan arriba, juntos
const int SERVO_HUG_LIFT = 50;
const unsigned long SERVO_HUG_UP_MS = 600;
const unsigned long SERVO_HUG_HOLD_MS = 300;
// SLEEP (dormir): brazos caidos, mas abajo que SAD, y muy despacio
const int SERVO_SLEEP_LIFT = -30;
const unsigned long SERVO_SLEEP_DOWN_MS = 1200;
// STRETCH (estirarse): los dos brazos hasta arriba, se quedan y bajan
const int SERVO_STRETCH_TOP = 75;
const int SERVO_STRETCH_END = 10;
const unsigned long SERVO_STRETCH_UP_MS = 800;
const unsigned long SERVO_STRETCH_HOLD_MS = 600;
const unsigned long SERVO_STRETCH_DOWN_MS = 700;

// Sostenido mientras habla (despues de la entrada del gesto): vaiven de
// ±amp alrededor de la pose del gesto. TALK es el vaiven de siempre (80/100,
// cada 150 ms, a saltos); los demas van con rampa suave.
//
// center2 deja la pose asimetrica (saludar y pensar dejan un brazo abajo);
// mirror hace que el segundo brazo vaya al reves que el primero (negar,
// bailar), que es lo que da la sensacion de "no" y de baile.
struct ServoSway { int8_t center; uint8_t amp; uint16_t stepMs; bool smooth; int8_t center2; bool mirror; };
const ServoSway SERVO_SWAY_TALK       = {   0, 10, 150, false,   0, false };
const ServoSway SERVO_SWAY_HAPPY      = {  65,  6, 180, true,   65, false };
const ServoSway SERVO_SWAY_SURPRISE   = {  45,  4, 220, true,   45, false };
const ServoSway SERVO_SWAY_ANGRY      = {   0,  8, 170, true,    0, false };
const ServoSway SERVO_SWAY_FRUSTRATED = {   0,  8, 400, true,    0, false };
const ServoSway SERVO_SWAY_SAD        = { -25,  5, 450, true,  -25, false };
const ServoSway SERVO_SWAY_WAVE       = {  60,  8, 250, true,    0, false };
const ServoSway SERVO_SWAY_CLAP       = {  30, 12, 200, true,   30, false };
const ServoSway SERVO_SWAY_THINK      = {  55,  3, 600, true,    0, false };
const ServoSway SERVO_SWAY_YES        = {  10,  6, 220, true,   10, false };
const ServoSway SERVO_SWAY_NO         = {   0, 12, 260, true,    0, true  };
const ServoSway SERVO_SWAY_DANCE      = {   0, 35, 220, true,    0, true  };
const ServoSway SERVO_SWAY_HUG        = {  50,  4, 500, true,   50, false };
const ServoSway SERVO_SWAY_SLEEP      = { -30,  3, 900, true,  -30, false };
const ServoSway SERVO_SWAY_STRETCH    = {  10,  5, 400, true,   10, false };

// REST = brazos en 90 y sueltos; INTRO = la entrada propia del gesto (pasos);
// SUSTAIN = el vaiven mientras sigue hablando.
enum ServoPhase : uint8_t { SERVO_PHASE_REST, SERVO_PHASE_INTRO, SERVO_PHASE_SUSTAIN };
ServoPhase servoPhase = SERVO_PHASE_REST;
uint8_t servoGesture = G_REST;          // el gesto que estan haciendo los brazos
uint8_t servoStep = 0;                  // que paso de la entrada va
uint8_t servoStepCount = 0;             // ANGRY: cuantos pasos tiene este "grrr"
unsigned long servoStepUntil = 0;       // cuando toca el proximo paso
unsigned long servoIntroEndMs = 0;      // ANGRY/SURPRISE: fin del tramo rapido
int8_t servoAngrySign = 1;              // ANGRY: alterna arriba/abajo
bool servoSwayHigh = false;             // hacia que extremo va el vaiven

// Rampa en curso: cada brazo va de su lift actual a rampTo en rampDur ms. Se
// calcula por tiempo, asi que despues de una trabada salta a donde ya
// deberia estar (una sola escritura, sin rafaga).
bool rampActive = false;
int rampFrom1 = 0, rampFrom2 = 0, rampTo1 = 0, rampTo2 = 0;
unsigned long rampStart = 0, rampDur = 0;
int armLift1 = 0, armLift2 = 0;         // ultimo lift escrito en cada brazo
unsigned long servoLastWriteMs = 0;

// Baile (arm_step): cada nota alterna entre dos poses espejadas alrededor de
// 90°, como Diome-chan alternaba 30/100 y 0/130 en sus dos brazos. Mientras
// dura, updateServos() no toca los servos; al terminar, vuelve a la pose
// del gesto que estaba (o a reposo si ya no habla).
// Poses del baile, una por golpe de la musica. Python manda el indice con
// Bridge.notify("arm_step", i) justo cuando suena el golpe, asi la coreografia
// vive del lado de Python (se cambia sin reflashear) y aqui solo estan las
// posturas que los SG90 pueden hacer sin pedir demasiada corriente.
//
// Antes esto eran dos poses que solo cambiaban de AMPLITUD (-30/+30 y
// -60/+60): los brazos nunca se cruzaban y el "baile" era un bombeo. Ahora hay
// tijera en los dos sentidos, los dos arriba, los dos abajo y un brazo solo.
// Nada pasa de ±60 (ver la nota de corriente de ANGRY).
const int8_t DANCE_POSES[][2] = {
  { -35,  35},  // 0 tijera: brazo 1 abajo, brazo 2 arriba
  {  35, -35},  // 1 tijera al reves
  { -60,  60},  // 2 tijera grande
  {  60, -60},  // 3 tijera grande al reves
  {  55,  55},  // 4 los dos arriba
  { -25, -25},  // 5 los dos abajo
  {  60,   0},  // 6 solo el brazo 1 arriba
  {   0,  60},  // 7 solo el brazo 2 arriba
  {   0,   0},  // 8 los dos en reposo (para marcar un silencio)
};
const uint8_t DANCE_POSE_COUNT = sizeof(DANCE_POSES) / sizeof(DANCE_POSES[0]);
// Sin golpes nuevos en este tiempo, se termina el baile. Tiene que ser mayor
// que el tiempo entre golpes de la cancion (a 132 pulsos por minuto, 455 ms):
// con los 450 de antes el baile se cortaba entre golpe y golpe.
const unsigned long DANCE_HOLD_MS = 900;
const uint8_t NO_PENDING_STEP = 0xFF;
volatile uint8_t pendingArmStep = NO_PENDING_STEP;
unsigned long danceUntil = 0;
bool servoDancing = false;              // para retomar el gesto al terminar el baile

bool servosAttached = false;
bool servoRestSettling = false;        // esperando llegar a 90 antes de soltar
unsigned long servoRestSettleUntil = 0;

// --- Aviso de version, abajo a la derecha -----------------------------------
// Se compone en RAM y se manda con el camino rapido de una sola llamada al
// driver, NUNCA con tft.print() directo sobre la pantalla: writePixel() cuesta
// 13 llamadas al driver por pixel (ver la cabecera de este archivo).
//
// Va en la esquina inferior derecha, por debajo de la region de la boca
// (que termina en y=225), asi el redibujado parcial de la cara no lo pisa.
const int16_t VER_W = 122, VER_H = 12;
const int16_t VER_X = SCREEN_W - VER_W - 2;   // 196
const int16_t VER_Y = SCREEN_H - VER_H - 2;   // 226
RegionCanvas verCanvas(VER_W, VER_H, VER_X, VER_Y, tft);

// Se llama tras cada repintado completo de pantalla (bienvenida, cambio de
// guia, salir de una tarjeta), no en cada frame: el texto no cambia.
void drawVersionBadge() {
  if (!verCanvas.ready()) return;
  verCanvas.fillScreen(ST77XX_BLACK);
  verCanvas.setTextSize(1);
  verCanvas.setTextColor(ST77XX_WHITE);
  verCanvas.setCursor(1, 1);
  verCanvas.print("BANG v" APP_VERSION " - beta");
  verCanvas.flush();
}

// --- Indicador de WiFi -------------------------------------------------------
// Cuatro barritas arriba a la derecha. Python manda el nivel con
// Bridge.notify("wifi", 0..4): 0 = sin conexion (sale una X roja), 1..4 =
// fuerza de la señal. Importa porque la conversacion va por la nube: si no
// hay WiFi, el robot no puede responder y se tiene que ver de un vistazo.
//
// La esquina superior derecha esta libre: los ojos llegan hasta y=146 y las
// cejas empiezan en y=46, asi que no pisa nada de la cara.
const int16_t WIFI_W = 34, WIFI_H = 16;
const int16_t WIFI_X = SCREEN_W - WIFI_W - 3, WIFI_Y = 3;
RegionCanvas wifiCanvas(WIFI_W, WIFI_H, WIFI_X, WIFI_Y, tft);

const uint8_t NO_PENDING_WIFI = 0xFF;
volatile uint8_t pendingWifi = NO_PENDING_WIFI;
uint8_t wifiLevel = 0;  // 0 = sin conexion; 1..4 barras

void drawWifiBadge(uint16_t bg) {
  if (!wifiCanvas.ready()) return;
  wifiCanvas.fillScreen(bg);

  const uint16_t encendida = ST77XX_WHITE;
  const uint16_t apagada = tft.color565(70, 70, 70);
  // Cuatro barras que crecen de izquierda a derecha.
  for (uint8_t i = 0; i < 4; i++) {
    const int16_t h = 4 + i * 3;             // 4, 7, 10, 13
    const int16_t x = 1 + i * 7;
    const int16_t y = WIFI_H - h - 1;
    wifiCanvas.fillRect(x, y, 5, h, (wifiLevel > i) ? encendida : apagada);
  }
  if (wifiLevel == 0) {
    // Sin conexion: aspa roja encima, se entiende sin leer nada.
    const uint16_t rojo = tft.color565(248, 80, 80);
    for (int16_t i = 0; i < WIFI_H - 2; i++) {
      wifiCanvas.drawPixel(2 + i, 1 + i, rojo);
      wifiCanvas.drawPixel(2 + i, WIFI_H - 2 - i, rojo);
    }
  }
  wifiCanvas.flush();
}

// Llamada desde Python con Bridge.notify("wifi", 0..4). Solo anota el byte.
void wifi(uint8_t level) {
  pendingWifi = level > 4 ? 4 : level;
}

// --- Codigo QR al dashboard --------------------------------------------------
// El QR NO se calcula aqui: lo genera Python (que conoce la IP de la placa) y
// lo manda ya resuelto por el Bridge como lista de bytes. Meter una libreria
// de QR en el MCU costaria flash, y el flash es justo lo que escasea.
//
// Formato: [tamaño, b0, b1, ...] con los modulos empaquetados a bit, fila por
// fila. Tamaño 0 = cerrar la pantalla del QR.
const uint8_t QR_MAX = 45;                                  // hasta version 6
const uint16_t QR_BYTES = (uint16_t)((QR_MAX * QR_MAX + 7) / 8);
uint8_t qrBits[QR_BYTES];
volatile uint8_t qrSizePending = 0;
volatile bool qrPending = false;
uint8_t qrSize = 0;
bool qrOn = false;

bool qrModule(uint8_t x, uint8_t y) {
  const uint32_t bit = (uint32_t)y * qrSize + x;
  return (qrBits[bit >> 3] >> (7 - (bit & 7))) & 1;
}

// Pantalla estatica y de una sola vez, como drawCard(): aqui si se puede usar
// fillRect() aunque sea el camino lento, porque no se repinta por frame.
void drawQr() {
  tft.fillScreen(ST77XX_WHITE);
  if (qrSize == 0) return;

  const int16_t quiet = 3;  // margen obligatorio del QR, en modulos
  int16_t escala = (SCREEN_H - 30) / (qrSize + 2 * quiet);
  if (escala < 1) escala = 1;
  const int16_t lado = (qrSize + 2 * quiet) * escala;
  const int16_t ox = (SCREEN_W - lado) / 2;
  const int16_t oy = (SCREEN_H - lado) / 2 - 6;

  for (uint8_t y = 0; y < qrSize; y++) {
    for (uint8_t x = 0; x < qrSize; x++) {
      if (qrModule(x, y)) {
        tft.fillRect(ox + (x + quiet) * escala, oy + (y + quiet) * escala,
                     escala, escala, ST77XX_BLACK);
      }
    }
  }
  tft.setTextColor(ST77XX_BLACK);
  tft.setTextSize(1);
  tft.setCursor(ox, oy + lado + 6);
  tft.print("Apunta la camara para configurar el WiFi");
}

// Llamada desde Python con Bridge.notify("qr", [tam, b0, b1, ...]).
// Solo copia a RAM y anota: dibuja loop(), como todo lo demas.
void qr(std::vector<int> data) {
  if (data.empty()) return;
  const int size = data[0];
  if (size <= 0 || size > QR_MAX) {
    qrSizePending = 0;
    qrPending = true;
    return;
  }
  const uint16_t need = (uint16_t)((size * size + 7) / 8);
  if (data.size() < (size_t)(1 + need) || need > QR_BYTES) {
    // Llego cortado (el buffer RPC del Bridge son 256 bytes): mejor decirlo
    // que quedarse callado, que es como se perdio el QR la primera vez.
    Serial.print("[chat-bang] QR incompleto: llegaron ");
    Serial.print((int)data.size());
    Serial.print(" de ");
    Serial.println(1 + need);
    return;
  }
  for (uint16_t i = 0; i < need; i++) qrBits[i] = (uint8_t)data[1 + i];
  qrSizePending = (uint8_t)size;
  qrPending = true;
}

// consumePendingQr() vive mas abajo, junto a consumePendingMenu(): necesita
// overlayPersona, que se declara con la bienvenida.

// --- Animacion de "WiFi sincronizado" ---------------------------------------
// Dibujada por codigo (ondas que se abren + palomita), no cuadros guardados:
// cuesta ~0 bytes de flash, que es justo lo que no sobra. Bloquea ~1,4 s, y
// eso esta bien: es un evento de una sola vez, no algo por frame.
volatile bool wifiSyncPending = false;

void wifi_sync() { wifiSyncPending = true; }

void playWifiSync() {
  const uint16_t verde = tft.color565(74, 222, 128);
  const int16_t cx = SCREEN_W / 2, cy = SCREEN_H / 2 + 10;
  tft.fillScreen(ST77XX_BLACK);

  // Tres ondas que se abren desde el punto, como el icono de WiFi.
  for (uint8_t onda = 1; onda <= 3; onda++) {
    const int16_t r = onda * 26;
    for (int16_t g = 0; g < 3; g++) {
      tft.drawCircle(cx, cy, r + g, verde);
      tft.drawCircle(cx, cy, r + g, verde);
    }
    // Se tapa la mitad de abajo para que parezcan arcos y no circulos.
    tft.fillRect(0, cy + 1, SCREEN_W, SCREEN_H - cy, ST77XX_BLACK);
    tft.fillCircle(cx, cy, 6, verde);
    delay(260);
  }

  tft.setTextColor(verde);
  tft.setTextSize(2);
  tft.setCursor(cx - 96, cy + 34);
  tft.print("WiFi conectado");
  delay(600);
}

// --- Menu de seleccion de personaje -----------------------------------------
// Pantalla APARTE: no toca nada del dibujado de las caras. Python la prende con
// Bridge.notify("menu", 0..4) indicando cual esta resaltado, y la apaga con 255.
// El niño elige diciendo el nombre en voz alta (listen_turn() del lado Python ya
// reconoce los cinco nombres como palabra de activacion).
//
// Los colores son los mismos de brain.PERSONAS del lado Python, para que el
// menu y el dashboard web muestren al mismo guia del mismo color.
struct MenuEntry {
  const char *name;
  const char *tag;
  uint8_t r, g, b;
};

const MenuEntry MENU_ENTRIES[] = {
  {"CRISPI",  "El constructor",        74, 222, 128},
  {"CARMEL",  "El estratega",         251, 191,  36},
  {"CESIA",   "La disruptora",        248, 113, 113},
  {"CORI",    "Pensadora lateral",    167, 139, 250},
  {"CRISTAL", "La musa reflexiva",     34, 211, 238},
};
const uint8_t MENU_COUNT = 5;

const int16_t MENU_ROW_W = 300, MENU_ROW_H = 32;
const int16_t MENU_X = 10, MENU_TOP = 40, MENU_GAP = 5;

// UN solo canvas para el titulo y las cinco filas: se mueve con setOrigin().
RegionCanvas menuCanvas(MENU_ROW_W, MENU_ROW_H, MENU_X, MENU_TOP, tft);

const uint8_t NO_PENDING_MENU = 0xFD;
const uint8_t MENU_NO_SEL = 0xFE;  // menu con los 5 iguales, sin resaltar ninguno
const uint8_t MENU_OFF = 0xFF;
volatile uint8_t pendingMenu = NO_PENDING_MENU;
bool menuOn = false;
uint8_t menuIndex = 0;

// Presupuesto de pixeles por frame. A ~4 us/byte, 3200 px = ~25 ms, que entra
// en los 33 ms del frame. Si dos partes quieren actualizarse en el mismo frame
// y no alcanza, una espera al siguiente: asi el frame NUNCA se pasa y los
// 30 fps son estables POR CONSTRUCCION (se degrada la suavidad, no el fps).
const uint32_t FRAME_PX_BUDGET = 3200;
uint32_t framePxLeft = 0;


// --- Aviso de seguridad (LA PRIMERA pantalla), ver renderAviso() -------------
// Orden de arranque: aviso -> bienvenida BANG -> menu de guias -> cara del
// guia elegido. Arranca prendido para que se vea apenas bootea el MCU, sin
// esperar a Python. Si Python nunca lo apaga, se apaga solo.
const unsigned long AVISO_MAX_MS = 60000;
const uint8_t NO_PENDING_AVISO = 0xFF;
volatile uint8_t pendingAviso = 1;
bool avisoOn = false;
unsigned long avisoStartMs = 0;
uint16_t avisoFrame = 0;
unsigned long avisoFrameMs = 0;

// --- Bienvenida (GIF de BANG), ver renderSplash() ---
// Ya NO arranca prendida: ahora va despues del aviso, cuando Python la pide.
const unsigned long SPLASH_MAX_MS = 90000;
const uint8_t NO_PENDING_SPLASH = 0xFF;
volatile uint8_t pendingSplash = NO_PENDING_SPLASH;
bool splashOn = false;
unsigned long splashStartMs = 0;
uint16_t splashFrame = 0;          // cuadro del GIF que esta en pantalla
unsigned long splashFrameMs = 0;   // cuando se dibujo
// El guia a mostrar cuando se apague la bienvenida o la tarjeta (Crispi).
uint8_t overlayPersona = 0;

// --- Tarjeta BANG en pantalla, ver consumePendingCard() ---
const uint8_t NO_PENDING_CARD = 0xFE;
const uint8_t CARD_OFF = 0xFF;
volatile uint8_t pendingCard = NO_PENDING_CARD;
bool cardOn = false;

// --- Gesto pendiente desde el Bridge (ver face_gesture()) ---
const uint8_t NO_PENDING = 0xFF; // los validos son 0..39 (5 personajes * 8 gestos)
volatile uint8_t pendingEncoded = NO_PENDING;

// --- Medicion de rendimiento: se lee con `arduino-app-cli monitor` ---
const unsigned long STATS_PERIOD_MS = 1000;
unsigned long statsWindowStart = 0;
uint16_t statsFrames = 0;
uint32_t statsWorstFrameUs = 0;

void facePushRects(const SpanRect *rects, uint8_t n, bool clipBadges); // mas abajo, con el dibujo

// Pone la cara del personaje que esta hablando. Si es el mismo de antes no
// hace nada: la pantalla completa (~0,6 s de bus) solo se repinta al cambiar
// de guia o al volver de otra pantalla (que deja currentPersona = 255). Los
// cambios de emocion NO pasan por aca: van por el redibujado parcial.
// OJO: toca el SPI, asi que solo puede llamarse desde loop().
void applyPersonaColors(uint8_t personaId) {
  if (personaId >= FACE_COUNT) personaId = 0; // por si llega un id invalido
  if (personaId == currentPersona) return;
  currentPersona = personaId;

  face = FACES[personaId];
  BG_COLOR = face->bg;
  // Base pelada (ojos abiertos, boca de reposo); la emocion que corresponda
  // entra despues por el redibujado parcial, como cualquier cambio.
  drawnTop = FACE_TOP_OPEN;
  drawnMouth = FACE_MOUTH_REST;
  blinkStep = -1;
  eyesWaitFrames = 0;
  const SpanRect full = { 0, 0, SCREEN_W, SCREEN_H };
  facePushRects(&full, 1, false);
  drawVersionBadge();  // la cara acaba de repintar la pantalla entera
  drawWifiBadge(BG_COLOR);
}

void triggerBlink() {
  if (blinkStep < 0) blinkStep = 0;
}

void updateAutoBlink() {
  if (millis() >= nextAutoBlinkMs) {
    triggerBlink();
    nextAutoBlinkMs = millis() + (talking
      ? random(BLINK_TALK_MIN_MS, BLINK_TALK_MAX_MS)
      : random(BLINK_IDLE_MIN_MS, BLINK_IDLE_MAX_MS));
  }
}

// Ojos/cejas de la emocion actual (los de "reposo" mientras dura la frase).
uint8_t faceTopRest() {
  switch (faceGesture) {
    case G_HAPPY:      return FACE_TOP_HAPPY;
    case G_SURPRISE:   return FACE_TOP_SURPRISE;
    case G_ANGRY:      return FACE_TOP_ANGRY;
    case G_SAD:        return FACE_TOP_SAD;
    case G_FRUSTRATED: return FACE_TOP_FRUSTRATED;
    // Gestos de 1.1.0: sin cara propia, toman prestada la que mejor les pega.
    case G_WAVE:
    case G_CLAP:
    case G_YES:
    case G_DANCE:
    case G_HUG:
    case G_STRETCH:    return FACE_TOP_HAPPY;
    case G_THINK:      return FACE_TOP_FRUSTRATED;  // los ojos del "mmm"
    case G_SLEEP:      return FACE_TOP_SAD;         // ojos caidos
    default:           return FACE_TOP_OPEN;      // REST, TALK
  }
}

// Boca de la emocion: la que se ve antes/despues de hablar y en las pausas.
uint8_t faceMouthRest() {
  switch (faceGesture) {
    case G_HAPPY:      return FACE_MOUTH_HAPPY;
    case G_SURPRISE:   return FACE_MOUTH_SURPRISE;
    case G_ANGRY:      return FACE_MOUTH_ANGRY;
    case G_SAD:        return FACE_MOUTH_SAD;
    case G_FRUSTRATED: return FACE_VIS_F;         // el "uff" es la boca de la F
    case G_WAVE:
    case G_CLAP:
    case G_YES:
    case G_DANCE:
    case G_HUG:
    case G_STRETCH:    return FACE_MOUTH_HAPPY;
    case G_SLEEP:      return FACE_MOUTH_SAD;
    // G_THINK se queda con la boca neutra: asi los visemas se le notan igual
    default:           return FACE_MOUTH_REST;
  }
}

// Que tiene que mostrar la capa de arriba ahora. El parpadeo avanza solo
// cuando el paso actual ya se dibujo y cumplio su tiempo. Las emociones con
// ojos propios (feliz, enojada, sorpresa, triste) no parpadean; FRUSTRADO
// (Cara 3) si, pero solo con los cuadros mas cerrados que los suyos.
uint8_t faceTopTarget() {
  const uint8_t rest = faceTopRest();
  if (rest != FACE_TOP_OPEN && rest != FACE_TOP_FRUSTRATED) {
    blinkStep = -1;
    return rest;
  }
  const uint8_t minFrame = (rest == FACE_TOP_FRUSTRATED) ? FACE_BLINK_HALF + 1 : 0;
  if (blinkStep >= 0 &&
      drawnTop == FACE_TOP_BLINK + BLINK_SEQ[blinkStep] &&
      millis() - topDrawnMs >= BLINK_HOLD_MS[blinkStep]) {
    blinkStep++;
  }
  while (blinkStep >= 0 && blinkStep < BLINK_STEPS && BLINK_SEQ[blinkStep] < minFrame) blinkStep++;
  if (blinkStep >= BLINK_STEPS) blinkStep = -1;
  return blinkStep < 0 ? rest : FACE_TOP_BLINK + BLINK_SEQ[blinkStep];
}

// Que tiene que mostrar la boca: el visema mientras suena la voz; en las
// pausas (visema 0) y sin hablar, la boca de la emocion.
uint8_t faceMouthTarget() {
  if (talking) {
    const uint8_t v = mouthViseme;
    if (v != FACE_MOUTH_REST && v < FACE_VISEMES) return v;
  }
  return faceMouthRest();
}

// Los dos servos a angulos crudos (sin DIR). Los vuelve a enganchar (attach)
// si estaban sueltos por un REST anterior.
void writeServoPair(int angle1, int angle2) {
  if (!servosAttached) {
    servo1.attach(SERVO1_PIN);
    servo2.attach(SERVO2_PIN);
    servosAttached = true;
  }
  servo1.write(angle1);
  servo2.write(angle2);
}

// El mismo angulo crudo en los 2 servos (90 = reposo, igual con cualquier DIR).
void writeServos(int angle) {
  writeServoPair(angle, angle);
}

// Cada brazo con su propio lift (> 0 = arriba), ya calibrado con ARMn_DIR y
// limitado a ARM_MIN..ARM_MAX. Recuerda lo escrito para que las rampas
// arranquen desde donde esta cada brazo.
void writeArmPair(int lift1, int lift2) {
  armLift1 = lift1;
  armLift2 = lift2;
  servoLastWriteMs = millis();
  writeServoPair(constrain(SERVO_REST_ANGLE + ARM1_DIR * lift1, ARM_MIN, ARM_MAX),
                 constrain(SERVO_REST_ANGLE + ARM2_DIR * lift2, ARM_MIN, ARM_MAX));
}

// Los dos brazos al mismo lift (lo normal: brazos simetricos).
void writeArms(int lift) {
  writeArmPair(lift, lift);
}

// Lleva los brazos a `lift` en durMs (0 = de un salto) y deja el proximo
// paso para cuando termine la rampa + holdMs. El tiempo se cuenta desde
// AHORA, no desde el paso anterior: si loop() se trabo, la secuencia se
// atrasa un poco en vez de escribir varios pasos de golpe.
void armMovePair(int lift1, int lift2, unsigned long durMs, unsigned long holdMs) {
  const unsigned long now = millis();
  if (durMs == 0) {
    rampActive = false;
    writeArmPair(lift1, lift2);
  } else {
    rampActive = true;
    rampFrom1 = armLift1;
    rampFrom2 = armLift2;
    rampTo1 = lift1;
    rampTo2 = lift2;
    rampStart = now;
    rampDur = durMs;
  }
  servoStepUntil = now + durMs + holdMs;
}

// Lo mismo con los dos brazos al mismo lift (lo normal).
void armMove(int lift, unsigned long durMs, unsigned long holdMs) {
  armMovePair(lift, lift, durMs, holdMs);
}

// Avanza la rampa en curso (como mucho una escritura cada SERVO_WRITE_MS).
void updateArmRamp(unsigned long now) {
  if (!rampActive || now - servoLastWriteMs < SERVO_WRITE_MS) return;
  const unsigned long t = now - rampStart;
  if (t >= rampDur) {
    rampActive = false;
    writeArmPair(rampTo1, rampTo2);
    return;
  }
  const int l1 = rampFrom1 + (int)((long)(rampTo1 - rampFrom1) * (long)t / (long)rampDur);
  const int l2 = rampFrom2 + (int)((long)(rampTo2 - rampFrom2) * (long)t / (long)rampDur);
  if (l1 != armLift1 || l2 != armLift2) writeArmPair(l1, l2);
}

// El vaiven que sostiene cada gesto mientras habla.
const ServoSway &servoSwayFor(uint8_t gesture) {
  switch (gesture) {
    case G_HAPPY:      return SERVO_SWAY_HAPPY;
    case G_SURPRISE:   return SERVO_SWAY_SURPRISE;
    case G_ANGRY:      return SERVO_SWAY_ANGRY;
    case G_FRUSTRATED: return SERVO_SWAY_FRUSTRATED;
    case G_SAD:        return SERVO_SWAY_SAD;
    case G_WAVE:       return SERVO_SWAY_WAVE;
    case G_CLAP:       return SERVO_SWAY_CLAP;
    case G_THINK:      return SERVO_SWAY_THINK;
    case G_YES:        return SERVO_SWAY_YES;
    case G_NO:         return SERVO_SWAY_NO;
    case G_DANCE:      return SERVO_SWAY_DANCE;
    case G_HUG:        return SERVO_SWAY_HUG;
    case G_SLEEP:      return SERVO_SWAY_SLEEP;
    case G_STRETCH:    return SERVO_SWAY_STRETCH;
    default:           return SERVO_SWAY_TALK;
  }
}

// Un paso de la entrada del gesto. Devuelve false cuando la entrada termino
// (y entonces sigue el vaiven sostenido).
bool servoIntroStep(unsigned long now) {
  const uint8_t step = servoStep++;
  switch (servoGesture) {
    case G_HAPPY: // manos arriba
      if (step == 0) { armMove(SERVO_HAPPY_LIFT, SERVO_HAPPY_RAMP_MS, 0); return true; }
      return false;

    case G_SURPRISE: // respingo, baja un poco y tiembla
      if (step == 0) {
        armMove(SERVO_SURPRISE_JUMP, SERVO_SURPRISE_JUMP_MS, SERVO_SURPRISE_HOLD_MS);
        return true;
      }
      if (step == 1) {
        armMove(SERVO_SURPRISE_LIFT, SERVO_SURPRISE_LOWER_MS, 0);
        servoIntroEndMs = now + SERVO_SURPRISE_LOWER_MS + SERVO_SURPRISE_SHAKE_MS;
        return true;
      }
      if ((long)(now - servoIntroEndMs) >= 0 || step > 40) return false;
      armMove(SERVO_SURPRISE_LIFT + ((step % 2) ? SERVO_SURPRISE_SHAKE : -SERVO_SURPRISE_SHAKE),
              0, SERVO_SURPRISE_SHAKE_STEP_MS);
      return true;

    case G_ANGRY: { // "grrr": saltos arriba/abajo de tamaño y tiempo al azar
      if (step == 0) {
        servoStepCount = random(SERVO_ANGRY_MIN_STEPS, SERVO_ANGRY_MAX_STEPS + 1);
        servoIntroEndMs = now + SERVO_ANGRY_MAX_MS;
        servoAngrySign = (random(2) == 0) ? 1 : -1;
      }
      if (step >= servoStepCount || (long)(now - servoIntroEndMs) >= 0) return false;
      servoAngrySign = -servoAngrySign;
      const int amp = random(SERVO_ANGRY_MIN_AMP, SERVO_ANGRY_MAX_AMP + 1);
      armMove(servoAngrySign * amp, 0,
              random(SERVO_ANGRY_MIN_STEP_MS, SERVO_ANGRY_MAX_STEP_MS + 1));
      return true;
    }

    case G_FRUSTRATED: { // "uff": sube lento, corte en la mitad, baja; x2
      if (step >= SERVO_FRUS_CYCLES * 3) return false;
      switch (step % 3) {
        case 0:  armMove(SERVO_FRUS_TOP, SERVO_FRUS_UP_MS, 0); break;
        case 1:  armMove(SERVO_FRUS_MID, SERVO_FRUS_DOWN_MS, SERVO_FRUS_PAUSE_MS); break;
        default: armMove(0, SERVO_FRUS_DOWN_MS, SERVO_FRUS_END_PAUSE_MS); break;
      }
      return true;
    }

    case G_SAD: // brazos caidos, despacio
      if (step == 0) { armMove(SERVO_SAD_LIFT, SERVO_SAD_RAMP_MS, 0); return true; }
      return false;

    case G_WAVE: // saludar: un brazo arriba que se mueve de lado a lado
      if (step == 0) {
        armMovePair(SERVO_WAVE_LIFT, 0, SERVO_WAVE_UP_MS, 0);
        return true;
      }
      if (step > SERVO_WAVE_SWINGS) return false;
      armMovePair(SERVO_WAVE_LIFT - ((step % 2) ? SERVO_WAVE_SWING : 0), 0,
                  SERVO_WAVE_STEP_MS, 0);
      return true;

    case G_CLAP: // aplaudir: arriba y abajo juntos, rapido
      if (step >= SERVO_CLAP_CLAPS * 2) return false;
      armMove((step % 2) ? SERVO_CLAP_LOW : SERVO_CLAP_HIGH, 0, SERVO_CLAP_STEP_MS);
      return true;

    case G_THINK: // pensar: un brazo sube despacio y se queda quieto
      if (step == 0) {
        armMovePair(SERVO_THINK_LIFT, 0, SERVO_THINK_UP_MS, 0);
        return true;
      }
      return false;

    case G_YES: // asentir: dos cabezadas cortas
      if (step >= SERVO_YES_NODS * 2) return false;
      armMove((step % 2) ? SERVO_YES_LOW : SERVO_YES_HIGH, 0, SERVO_YES_STEP_MS);
      return true;

    case G_NO: // negar: los brazos en espejo, como una cabeza que dice que no
      if (step >= SERVO_NO_SWINGS * 2) return false;
      armMovePair((step % 2) ? -SERVO_NO_SWING : SERVO_NO_SWING,
                  (step % 2) ? SERVO_NO_SWING : -SERVO_NO_SWING, 0, SERVO_NO_STEP_MS);
      return true;

    case G_DANCE: // bailar: espejo amplio, como el baile de la cancioncita
      if (step >= SERVO_DANCE_SWINGS * 2) return false;
      armMovePair((step % 2) ? -SERVO_DANCE_SWING : SERVO_DANCE_SWING,
                  (step % 2) ? SERVO_DANCE_SWING : -SERVO_DANCE_SWING,
                  0, SERVO_DANCE_STEP_MS);
      return true;

    case G_HUG: // abrazar: los dos brazos suben despacio y se quedan
      if (step == 0) { armMove(SERVO_HUG_LIFT, SERVO_HUG_UP_MS, SERVO_HUG_HOLD_MS); return true; }
      return false;

    case G_SLEEP: // dormir: brazos caidos del todo, muy despacio
      if (step == 0) { armMove(SERVO_SLEEP_LIFT, SERVO_SLEEP_DOWN_MS, 0); return true; }
      return false;

    case G_STRETCH: // estirarse: hasta arriba, se queda y baja (bostezo)
      if (step == 0) {
        armMove(SERVO_STRETCH_TOP, SERVO_STRETCH_UP_MS, SERVO_STRETCH_HOLD_MS);
        return true;
      }
      if (step == 1) { armMove(SERVO_STRETCH_END, SERVO_STRETCH_DOWN_MS, 0); return true; }
      return false;

    default: // TALK no tiene entrada: directo al vaiven
      return false;
  }
}

// Un paso del baile, ya en el hilo de loop() (dueño de los servos). En lift:
// brazo 1 a -swing y brazo 2 a +swing (con los DIR en +1 son los mismos
// angulos espejados de siempre, 90-swing / 90+swing).
void consumeArmStep() {
  const uint8_t step = pendingArmStep;
  if (step == NO_PENDING_STEP) return;
  pendingArmStep = NO_PENDING_STEP;

  const uint8_t pose = step % DANCE_POSE_COUNT;
  rampActive = false;
  writeArmPair(DANCE_POSES[pose][0], DANCE_POSES[pose][1]);
  danceUntil = millis() + DANCE_HOLD_MS;
  servoDancing = true;
  servoRestSettling = false; // al terminar, updateServos() vuelve a reposo solo
}

// Arranca el movimiento del gesto. Se llama una vez por gesto consumido (ver
// consumePendingGesture()); el resto lo hace updateServos() en cada loop().
// Si llega el MISMO gesto mientras ya lo esta haciendo, sigue sin cortarlo.
void startServoFlourish(uint8_t gesture) {
  if (gesture == G_REST) {
    // updateServos() los lleva a 90 y los suelta (ve talking == false)
    servoGesture = G_REST;
    servoPhase = SERVO_PHASE_REST;
    rampActive = false;
    return;
  }
  servoRestSettling = false; // por si veniamos de REST, cancelar el soltado
  if (gesture == servoGesture && servoPhase != SERVO_PHASE_REST) return;

  servoGesture = gesture;
  servoStep = 0;
  servoStepUntil = millis(); // el primer paso sale en este mismo loop()
  servoPhase = SERVO_PHASE_INTRO;
}

// Mueve los servos segun la fase actual. Se llama en cada loop(), igual que
// updateAutoBlink(): nada de delay(), todo temporizado con millis().
void updateServos() {
  unsigned long now = millis();
  if ((long)(now - danceUntil) < 0) return; // bailando: los mueve consumeArmStep()

  if (!talking) {
    // REST: volver a 90 y, tras un rato de sobra para llegar, soltar el
    // servo (detach) para que no zumbe en reposo — igual que describia el
    // README original ("vuelve a 90° y se suelta, no zumba").
    servoDancing = false;
    if (servoPhase != SERVO_PHASE_REST) {
      servoPhase = SERVO_PHASE_REST; // corta cualquier gesto en curso
      servoGesture = G_REST;
      rampActive = false;
    }
    if (!servoRestSettling && servosAttached) {
      writeArms(0);
      servoRestSettling = true;
      servoRestSettleUntil = now + SERVO_REST_SETTLE_MS;
    }
    if (servoRestSettling && now >= servoRestSettleUntil && servosAttached) {
      servo1.detach();
      servo2.detach();
      servosAttached = false;
      servoRestSettling = false;
    }
    return;
  }

  const ServoSway &sway = servoSwayFor(servoGesture);

  if (servoDancing || servoPhase == SERVO_PHASE_REST) {
    // Termino el baile (o habla sin gesto previo): sin rehacer la entrada,
    // volver suave a la pose del gesto y seguir con su vaiven.
    servoDancing = false;
    servoPhase = SERVO_PHASE_SUSTAIN;
    armMovePair(sway.center, sway.center2, SERVO_RESUME_MS, 0);
  }

  updateArmRamp(now);
  if ((long)(now - servoStepUntil) < 0) return;

  if (servoPhase == SERVO_PHASE_INTRO) {
    if (servoIntroStep(now)) return;
    servoPhase = SERVO_PHASE_SUSTAIN; // entrada terminada: sigue el vaiven
  }

  // SUSTAIN: vaiven de ±amp alrededor de la pose del gesto (en espejo, el
  // segundo brazo va al reves: "no", baile)
  servoSwayHigh = !servoSwayHigh;
  const int delta = servoSwayHigh ? sway.amp : -(int)sway.amp;
  const int target1 = sway.center + delta;
  const int target2 = sway.center2 + (sway.mirror ? -delta : delta);
  if (sway.smooth) armMovePair(target1, target2, sway.stepMs, 0);
  else armMovePair(target1, target2, 0, sway.stepMs);
}

// --- Dibujo de la cara: armar en RAM (barato) y mandar SOLO lo que cambia ---
//
// Medido en esta placa: el driver SPI cuesta ~4 us por byte (una ISR por byte,
// el bus va al 5% de su capacidad). O sea ~253 KB/s reales: la pantalla
// entera son ~0,6 s y un ojo o una boca completos, 1-3 frames. Por eso NO se
// puede mandar la cara entera en cada frame.
//
// La cara son capas (ver face_sprite.h): la base y, encima, o la capa de
// arriba (filas < split) o la de la boca (filas >= split). Para pasar una
// capa de un estado a otro se arman las filas del rectangulo que cubre a
// los dos estados, con el estado viejo y con el nuevo, se comparan, y los
// pedazos de fila que difieren se juntan en pocos rectangulos
// (facePlanDiff()). Solo eso viaja por SPI.

// Cada rectangulo cuesta ademas su setAddrWindow() (~11 bytes de comando, de
// a uno por vez): en pixeles equivale a unos 32. Con eso se decide si
// conviene juntar dos filas en un mismo rectangulo o mandarlas por separado.
const uint32_t WINDOW_COST_PX = 32;
const uint8_t MAX_DIFF_RECTS = 24;
SpanRect diffRects[MAX_DIFF_RECTS], diffRectsAlt[MAX_DIFF_RECTS];

// Decodifica la fila `row` del sprite `s` sobre `line` (fila de pantalla
// entera), dejando sin tocar lo transparente. Formato en face_sprite.h.
void faceDecodeRow(const FaceLayer &s, int16_t row, uint8_t *line) {
  const uint8_t *t = face->blob + s.rows + 2u * (uint32_t)row;
  const uint8_t *p = face->blob + s.data + (uint32_t)(t[0] | (t[1] << 8));
  uint8_t *dst = line + s.x;
  int16_t left = s.w;
  while (left > 0) {
    const uint8_t tok = *p++;
    const uint8_t idx = tok >> 4;
    int16_t n = tok & 0x0F;
    n = (n < 15) ? n + 1 : 16 + *p++;
    if (n > left) n = left;
    if (idx != FACE_TRANSPARENT) memset(dst, idx, n);
    dst += n;
    left -= n;
  }
}

// Pinta en `line` la fila `y` del sprite `s`, si la fila es suya.
void faceDecodeIfIn(const FaceLayer &s, int16_t y, uint8_t *line) {
  if (s.w > 0 && y >= s.y && y < s.y + s.h) faceDecodeRow(s, y - s.y, line);
}

// Arma la fila `y` de pantalla: la base y encima la capa que toca (arriba:
// los ojos; abajo: el extra de esos ojos y la boca).
void faceComposeRow(int16_t y, uint8_t top, uint8_t mouth, uint8_t *line) {
  faceDecodeRow(face->base, y, line);
  if (y < face->split) {
    faceDecodeIfIn(face->top[top], y, line);
  } else {
    faceDecodeIfIn(face->extra[top], y, line);
    faceDecodeIfIn(face->mouth[mouth], y, line);
  }
}

// Los badges (version abajo a la derecha, WiFi arriba a la derecha) se
// pintan encima de la cara: lo que cambie debajo de ellos no se manda, y si
// un rectangulo igual los pisa, se repintan (ver facePushRects()).
bool inBadge(int16_t x, int16_t y) {
  return (x >= VER_X && x < VER_X + VER_W && y >= VER_Y && y < VER_Y + VER_H) ||
         (x >= WIFI_X && x < WIFI_X + WIFI_W && y >= WIFI_Y && y < WIFI_Y + WIFI_H);
}

bool rectsTouch(const SpanRect &r, int16_t x, int16_t y, int16_t w, int16_t h) {
  return r.x < x + w && x < r.x + r.w && r.y < y + h && y < r.y + r.h;
}

// Un rectangulo de pantalla que pisaria un badge (version o WiFi).
bool rectHitsBadge(int16_t x0, int16_t y0, int16_t x1, int16_t y1) {
  const SpanRect r = { x0, y0, (int16_t)(x1 - x0 + 1), (int16_t)(y1 - y0 + 1) };
  return rectsTouch(r, VER_X, VER_Y, VER_W, VER_H) || rectsTouch(r, WIFI_X, WIFI_Y, WIFI_W, WIFI_H);
}

// Arma, para las columnas [cx0, cx1) y las filas [uy0, uy1), los
// rectangulos que llevan la pantalla de la cara (topA, mouthA) a la
// (topB, mouthB), y los agrega a `out` desde `n` (hasta `maxN`). Suma a
// `cost` los pixeles a mandar, overhead de ventana incluido. Devuelve el
// nuevo `n`.
uint8_t facePlanCols(uint8_t topA, uint8_t mouthA, uint8_t topB, uint8_t mouthB, int16_t cx0, int16_t cx1,
                     int16_t uy0, int16_t uy1, SpanRect *out, uint8_t n, uint8_t maxN, uint32_t &cost) {
  const uint8_t first = n; // solo se junta con rectangulos de esta pasada
  for (int16_t y = uy0; y < uy1; y++) {
    faceComposeRow(y, topA, mouthA, faceLineOld);
    faceComposeRow(y, topB, mouthB, faceLine);

    int16_t x0 = -1, x1 = -1;
    for (int16_t x = cx0; x < cx1; x++) {
      if (faceLine[x] != faceLineOld[x] && !inBadge(x, y)) {
        if (x0 < 0) x0 = x;
        x1 = x;
      }
    }
    if (x0 < 0) continue; // fila identica
    const int16_t rowW = x1 - x0 + 1;

    if (n > first) {
      SpanRect &g = out[n - 1];
      if (g.y + g.h == y) { // fila contigua: juntarla si sale mas barato
        const int16_t gx0 = min(g.x, x0), gx1 = max((int16_t)(g.x + g.w - 1), x1);
        const uint32_t merged = (uint32_t)(gx1 - gx0 + 1) * (g.h + 1);
        const uint32_t apart = (uint32_t)g.w * g.h + rowW + WINDOW_COST_PX;
        // Si el rectangulo juntado pisaria un badge, va aparte: lo de
        // debajo del badge no se manda (y el badge no parpadea).
        const bool hitsBadge = rectHitsBadge(gx0, g.y, gx1, y);
        if ((merged <= apart && !hitsBadge) || n == maxN) {
          cost += merged - (uint32_t)g.w * g.h;
          g.x = gx0; g.w = gx1 - gx0 + 1; g.h++;
          continue;
        }
      } else if (n == maxN) { // sin lugar: estirar el ultimo
        const int16_t gx0 = min(g.x, x0), gx1 = max((int16_t)(g.x + g.w - 1), x1);
        cost -= (uint32_t)g.w * g.h;
        g.x = gx0; g.w = gx1 - gx0 + 1; g.h = y - g.y + 1;
        cost += (uint32_t)g.w * g.h;
        continue;
      }
    }
    out[n++] = { x0, y, rowW, 1 };
    cost += rowW + WINDOW_COST_PX;
  }
  return n;
}

// Arma los rectangulos (de pantalla) que llevan una capa del estado `from`
// al `to`, con la otra capa como este. Devuelve cuantos son y deja en `cost`
// los pixeles a mandar, overhead de ventana incluido.
//
// Se prueba tambien cortando al medio de la pantalla: en el parpadeo cada
// fila cambia en los DOS ojos, y una sola franja por fila arrastraria todo
// el hueco entre ellos (el doble de pixeles). Queda la opcion mas barata.
// Agranda [ux0, ux1) x [uy0, uy1) para que cubra el sprite `s` (si tiene algo).
void growBox(const FaceLayer &s, int16_t &ux0, int16_t &uy0, int16_t &ux1, int16_t &uy1) {
  if (s.w == 0) return;
  ux0 = min(ux0, s.x); uy0 = min(uy0, s.y);
  ux1 = max(ux1, (int16_t)(s.x + s.w)); uy1 = max(uy1, (int16_t)(s.y + s.h));
}

uint8_t facePlanDiff(bool topLayer, uint8_t from, uint8_t to, SpanRect *out, uint32_t &cost) {
  // La otra capa queda como esta dibujada.
  const uint8_t topA = topLayer ? from : drawnTop, topB = topLayer ? to : drawnTop;
  const uint8_t mouthA = topLayer ? drawnMouth : from, mouthB = topLayer ? drawnMouth : to;
  cost = 0;

  // El rectangulo que cubre a los dos estados (uno puede ser "igual a la
  // base"). Los ojos arrastran su extra (cachetes, lagrimas, debajo del split).
  int16_t ux0 = SCREEN_W, uy0 = SCREEN_H, ux1 = 0, uy1 = 0;
  if (topLayer) {
    growBox(face->top[from], ux0, uy0, ux1, uy1);
    growBox(face->top[to], ux0, uy0, ux1, uy1);
    growBox(face->extra[from], ux0, uy0, ux1, uy1);
    growBox(face->extra[to], ux0, uy0, ux1, uy1);
  } else {
    growBox(face->mouth[from], ux0, uy0, ux1, uy1);
    growBox(face->mouth[to], ux0, uy0, ux1, uy1);
  }
  if (ux1 <= ux0) return 0;

  uint8_t n = facePlanCols(topA, mouthA, topB, mouthB, ux0, ux1, uy0, uy1, out, 0, MAX_DIFF_RECTS, cost);
  const int16_t mid = SCREEN_W / 2;
  if (ux0 < mid && ux1 > mid) {
    uint32_t costSplit = 0;
    uint8_t m = facePlanCols(topA, mouthA, topB, mouthB, ux0, mid, uy0, uy1, diffRectsAlt, 0, MAX_DIFF_RECTS / 2, costSplit);
    m = facePlanCols(topA, mouthA, topB, mouthB, mid, ux1, uy0, uy1, diffRectsAlt, m, MAX_DIFF_RECTS, costSplit);
    if (costSplit < cost) {
      memcpy(out, diffRectsAlt, m * sizeof(SpanRect));
      n = m;
      cost = costSplit;
    }
  }
  return n;
}

// Manda rectangulos de pantalla con la cara en el estado dibujado
// (drawnTop, drawnMouth). Mismo camino rapido que RegionCanvas::flushRect():
// se arma en spiScratch (nunca se le pasa otro buffer a SPI.transfer(), que
// lo sobrescribe) y va de a chunks de filas enteras del rectangulo.
//
// `clipBadges`: no mandar lo que cae debajo de los badges (los cambios
// parciales); si no (pantalla entera), se manda todo y se repintan.
void facePushRect(const SpanRect &r);
void facePushClipped(const SpanRect &r, uint8_t badge);

void facePushRects(const SpanRect *rects, uint8_t n, bool clipBadges) {
  bool verHit = false, wifiHit = false;
  for (uint8_t i = 0; i < n; i++) {
    const SpanRect &r = rects[i];
    if (r.w <= 0 || r.h <= 0) continue;
    if (clipBadges) {
      facePushClipped(r, 0);
      continue;
    }
    facePushRect(r);
    verHit |= rectsTouch(r, VER_X, VER_Y, VER_W, VER_H);
    wifiHit |= rectsTouch(r, WIFI_X, WIFI_Y, WIFI_W, WIFI_H);
  }
  if (verHit) drawVersionBadge();
  if (wifiHit) drawWifiBadge(BG_COLOR);
}

// Manda `r` menos lo que pisa los badges desde el numero `badge` (0 =
// version, 1 = WiFi, 2 = ninguno mas): lo que sobra de cada lado del badge
// va en pedazos aparte.
void facePushClipped(const SpanRect &r, uint8_t badge) {
  if (r.w <= 0 || r.h <= 0) return;
  if (badge >= 2) {
    facePushRect(r);
    return;
  }
  const int16_t bx = badge ? WIFI_X : VER_X, by = badge ? WIFI_Y : VER_Y;
  const int16_t bw = badge ? WIFI_W : VER_W, bh = badge ? WIFI_H : VER_H;
  if (!rectsTouch(r, bx, by, bw, bh)) {
    facePushClipped(r, badge + 1);
    return;
  }
  const int16_t y0 = max(r.y, by), y1 = min((int16_t)(r.y + r.h), (int16_t)(by + bh));
  const int16_t rx1 = r.x + r.w, bx1 = bx + bw;
  facePushClipped({ r.x, r.y, r.w, (int16_t)(y0 - r.y) }, badge + 1);                    // arriba
  facePushClipped({ r.x, y0, (int16_t)(bx - r.x), (int16_t)(y1 - y0) }, badge + 1);       // izquierda
  facePushClipped({ bx1, y0, (int16_t)(rx1 - bx1), (int16_t)(y1 - y0) }, badge + 1);      // derecha
  facePushClipped({ r.x, y1, r.w, (int16_t)(r.y + r.h - y1) }, badge + 1);                // abajo
}

void facePushRect(const SpanRect &r) {
  const int16_t rowsPerChunk = (int16_t)(SCRATCH_PX / (uint32_t)r.w);
  if (rowsPerChunk < 1) return; // no deberia pasar: r.w <= SCREEN_W

  tft.startWrite();
  tft.setAddrWindow(r.x, r.y, r.w, r.h);
  for (int16_t row = 0; row < r.h; ) {
    const int16_t rows = min((int16_t)(r.h - row), rowsPerChunk);
    uint16_t *dst = spiScratch;

    uint32_t t0 = micros();
    for (int16_t k = 0; k < rows; k++) {
      faceComposeRow(r.y + row + k, drawnTop, drawnMouth, faceLine);
      const uint8_t *src = faceLine + r.x;
      for (int16_t c = 0; c < r.w; c++) *dst++ = face->lut[src[c]];
    }
    uint32_t t1 = micros();

    SPI.transfer(spiScratch, (size_t)rows * (size_t)r.w * 2);
    uint32_t t2 = micros();

    statsCopyUs += t1 - t0;
    statsSpiUs += t2 - t1;
    row += rows;
  }
  tft.endWrite();
  statsPixels += (uint32_t)r.w * r.h;
}

// Descuenta del presupuesto del frame (sin pasarse de 0 cuando se forzo).
void spendFramePx(uint32_t cost) {
  framePxLeft = cost < framePxLeft ? framePxLeft - cost : 0;
}

// Lleva una capa (la de arriba o la boca) al estado `to`. `force`: mandarla
// aunque no entre en el presupuesto (solo si en este frame no salio nada
// todavia, para que nunca quede trabada). Devuelve true si mando algo.
bool faceStepLayer(bool topLayer, uint8_t to, bool force) {
  uint8_t &drawn = topLayer ? drawnTop : drawnMouth;
  if (to == drawn) return false;
  uint32_t cost;
  const uint8_t n = facePlanDiff(topLayer, drawn, to, diffRects, cost);
  if (cost > framePxLeft && !force) return false;
  spendFramePx(cost);
  drawn = to; // antes de mandar: facePushRects() arma el estado dibujado
  facePushRects(diffRects, n, true);
  if (topLayer) topDrawnMs = millis();
  return true;
}

// Si los ojos esperan tantos frames seguidos, pasan antes que la boca: con
// la boca cambiando casi en cada frame al hablar, si no, nunca entrarian.
const uint8_t EYES_MAX_WAIT_FRAMES = 2;

void renderFace() {
  const uint8_t topTarget = faceTopTarget();
  const uint8_t mouthTarget = faceMouthTarget();
  bool sent = false;

  // Presupuesto del frame: si lo primero ya lo gasta entero, lo segundo
  // espera. Nada se pierde, solo se atrasa (se dibuja el ultimo estado pedido).
  const bool eyesFirst = eyesWaitFrames >= EYES_MAX_WAIT_FRAMES;
  if (!eyesFirst) sent = faceStepLayer(false, mouthTarget, true);

  if (topTarget != drawnTop) {
    if (faceStepLayer(true, topTarget, !sent)) {
      sent = true;
      eyesWaitFrames = 0;
    } else {
      eyesWaitFrames++;
    }
  }

  if (eyesFirst) faceStepLayer(false, mouthTarget, !sent);
}

// Manda un rectangulo de una imagen paletizada (indices de 8 bits, w*h
// seguidos) con el camino rapido de facePushRects(). Lo usan la bienvenida y
// el aviso de seguridad, que comparten formato pero no paleta.
void pushPalRect(const uint8_t *src, const uint16_t *pal, int16_t x, int16_t y, int16_t w, int16_t h) {
  if (w <= 0 || h <= 0) return;
  const int16_t rowsPerChunk = (int16_t)(SCRATCH_PX / (uint32_t)w);
  if (rowsPerChunk < 1) return; // no deberia pasar: w <= SCREEN_W

  tft.startWrite();
  tft.setAddrWindow(x, y, w, h);
  for (int16_t row = 0; row < h; ) {
    const int16_t rows = min((int16_t)(h - row), rowsPerChunk);
    const uint32_t n = (uint32_t)rows * w;
    const uint8_t *p = src + (uint32_t)row * w;

    uint32_t t0 = micros();
    for (uint32_t i = 0; i < n; i++) spiScratch[i] = __builtin_bswap16(pal[p[i]]);
    uint32_t t1 = micros();

    SPI.transfer(spiScratch, (size_t)n * 2);
    uint32_t t2 = micros();

    statsCopyUs += t1 - t0;
    statsSpiUs += t2 - t1;
    row += rows;
  }
  tft.endWrite();
  statsPixels += (uint32_t)w * h;
}

void pushSplashRect(const uint8_t *src, int16_t x, int16_t y, int16_t w, int16_t h) {
  pushPalRect(src, SPLASH_PALETTE, x, y, w, h);
}

void pushAvisoRect(const uint8_t *src, int16_t x, int16_t y, int16_t w, int16_t h) {
  pushPalRect(src, AVISO_PALETTE, x, y, w, h);
}

// --- Aviso de seguridad: misma mecanica que la bienvenida -------------------

void showAviso() {
  // El GIF es vertical: las bandas negras de los lados se pintan una sola vez
  // aqui y no se guardan en flash (ver make_aviso.py).
  tft.fillScreen(ST77XX_BLACK);
  pushAvisoRect(AVISO_BASE, AVISO_X, AVISO_Y, AVISO_W, AVISO_H);
  avisoOn = true;
  avisoStartMs = avisoFrameMs = millis();
  avisoFrame = 0;
}

void renderAviso() {
  const AvisoFrame &f = AVISO_FRAMES[avisoFrame];
  if (millis() - avisoFrameMs < f.durationMs) return;
  for (uint16_t i = 0; i < f.rectCount; i++) {
    const AvisoRect &r = AVISO_RECTS[f.firstRect + i];
    pushAvisoRect(AVISO_PX + r.offset, AVISO_X + r.x, AVISO_Y + r.y, r.w, r.h);
  }
  avisoFrame = (avisoFrame + 1) % AVISO_FRAME_COUNT;
  avisoFrameMs = millis();
}

// Prende/apaga el aviso, ya en el hilo de loop() (dueño del SPI).
void consumePendingAviso() {
  uint8_t want = pendingAviso;
  bool porTiempo = false;
  if (want == NO_PENDING_AVISO && avisoOn && millis() - avisoStartMs >= AVISO_MAX_MS) {
    want = 0;          // Python no lo apago: se sigue adelante igual
    porTiempo = true;  // solo en ESTE caso se pinta la cara de respaldo
  }
  if (want == NO_PENDING_AVISO) return;
  pendingAviso = NO_PENDING_AVISO;

  if (want && !avisoOn) {
    showAviso();
  } else if (want && avisoOn) {
    avisoStartMs = millis();
  } else if (!want && avisoOn) {
    avisoOn = false;
    // No se repinta nada aqui: lo que venga despues (bienvenida o menu) pinta
    // la pantalla entera por su cuenta. Salvo si vencio por tiempo: Python no
    // contesta y no va a llegar nada, asi que se deja la cara (y no el ultimo
    // cuadro del aviso congelado), como hace la bienvenida.
    if (porTiempo && !splashOn && !menuOn && !qrOn && !cardOn) {
      currentPersona = 255;  // el aviso tapo la cara: repintarla entera
      applyPersonaColors(overlayPersona);
    }
  }
}

// Llamada desde Python con Bridge.notify("aviso", 1 / 0).
void aviso(uint8_t on) {
  pendingAviso = on ? 1 : 0;
}

// Pantalla completa del cuadro 0 (un frame lento, ~0,6 s, una sola vez).
void showSplash() {
  pushSplashRect(SPLASH_BASE, 0, 0, SPLASH_W, SPLASH_H);
  drawVersionBadge();  // la bienvenida acaba de pintar la pantalla entera
  splashOn = true;
  splashStartMs = splashFrameMs = millis();
  splashFrame = 0;
}

// Avanza el GIF cuando el cuadro actual cumplio su tiempo: solo viaja lo que
// cambia (el avioncito, <= ~1.700 px, ~14 ms de bus), asi que entra en un frame.
void renderSplash() {
  const SplashFrame &f = SPLASH_FRAMES[splashFrame];
  if (millis() - splashFrameMs < f.durationMs) return;
  for (uint16_t i = 0; i < f.rectCount; i++) {
    const SplashRect &r = SPLASH_RECTS[f.firstRect + i];
    pushSplashRect(SPLASH_PX + r.offset, r.x, r.y, r.w, r.h);
  }
  splashFrame = (splashFrame + 1) % SPLASH_FRAME_COUNT;
  splashFrameMs = millis();
}

// Prende/apaga la bienvenida, ya en el hilo de loop() (dueño del SPI).
void consumePendingSplash() {
  uint8_t want = pendingSplash;
  bool porTiempo = false;
  if (want == NO_PENDING_SPLASH && splashOn && millis() - splashStartMs >= SPLASH_MAX_MS) {
    want = 0;          // Python no la apago (¿se cayo?): se sigue adelante
    porTiempo = true;  // solo en ESTE caso se pinta la cara de respaldo
  }
  if (want == NO_PENDING_SPLASH) return;
  pendingSplash = NO_PENDING_SPLASH;

  if (want && !splashOn) {
    if (currentPersona < FACE_COUNT) overlayPersona = currentPersona;
    cardOn = false; // la bienvenida la tapa; al apagarse vuelve la cara
    showSplash();
  } else if (want && splashOn) {
    splashStartMs = millis(); // la vuelven a pedir: reinicia el plazo
  } else if (!want && splashOn) {
    splashOn = false;
    // Ni cara de Crispi ni el ultimo cuadro de la bienvenida congelado: se
    // limpia a negro y ya. Lo que venga detras (el menu, o la cara del guia
    // elegido) pinta encima de negro, sin fotogramas intermedios raros.
    currentPersona = 255;     // la pantalla ya no tiene cara valida
    tft.fillScreen(ST77XX_BLACK);
    // Excepcion: si vencio por tiempo es que Python no contesta y no va a
    // llegar nada detras. Ahi si conviene dejar la cara puesta.
    if (porTiempo) applyPersonaColors(overlayPersona);
  }
}

// Vuelca a la pantalla un RLE de bang_cards.h (ver tools/make_cards.py):
// tokens de 1 byte, `valueBits` altos = valor (indice en lut), el resto =
// largo - 1, y el largo maximo avisa que sigue un byte extra. Se descomprime
// de a SCRATCH_PX pixeles en spiScratch, que se vuelve a llenar despues de
// cada SPI.transfer() (que lo sobrescribe). La ventana ya debe estar abierta.
void pushCardRle(const uint8_t *p, uint32_t size, uint8_t valueBits, const uint16_t *lut) {
  const uint8_t lenBits = 8 - valueBits;
  const uint8_t esc = (1 << lenBits) - 1;
  const uint8_t *end = p + size;
  uint32_t n = 0;
  while (p < end) {
    const uint8_t token = *p++;
    const uint16_t color = lut[token >> lenBits];
    const uint8_t len = token & esc;
    uint16_t run = len < esc ? len + 1 : esc + 1 + *p++;
    while (run--) {
      spiScratch[n++] = color;
      if (n == SCRATCH_PX) {
        SPI.transfer(spiScratch, n * 2);
        n = 0;
      }
    }
  }
  if (n) SPI.transfer(spiScratch, n * 2);
}

// Mezcla fondo -> texto en 4 niveles (la opacidad del cuerpo de la tarjeta),
// ya con el byte swap.
void buildCardLut(uint16_t bg, uint16_t ink, uint16_t *lut) {
  const uint8_t br = (bg >> 11) & 0x1F, bgc = (bg >> 5) & 0x3F, bb = bg & 0x1F;
  const uint8_t ir = (ink >> 11) & 0x1F, ig = (ink >> 5) & 0x3F, ib = ink & 0x1F;
  for (uint8_t a = 0; a < 4; a++) {
    const uint16_t r = (br * (3 - a) + ir * a) / 3;
    const uint16_t g = (bgc * (3 - a) + ig * a) / 3;
    const uint16_t b = (bb * (3 - a) + ib * a) / 3;
    lut[a] = __builtin_bswap16((uint16_t)((r << 11) | (g << 5) | b));
  }
}

// Dibuja una tarjeta entera: la cabecera de su guia y el cuerpo con el
// texto. ~0,6 s de bus, una sola vez por tarjeta.
void drawCard(uint8_t index) {
  const BangCard &c = CARDS[index];
  const CardHeader &hd = CARD_HEADERS[c.header];
  uint16_t lut[16];

  for (uint8_t k = 0; k < 16; k++) lut[k] = __builtin_bswap16(hd.palette[k]);
  tft.startWrite();
  tft.setAddrWindow(0, 0, CARD_W, hd.h);
  pushCardRle(CARDS_RLE + hd.offset, hd.size, 4, lut);
  tft.endWrite();

  buildCardLut(c.bg, c.ink, lut);
  tft.startWrite();
  tft.setAddrWindow(0, hd.h, CARD_W, CARD_H - hd.h);
  pushCardRle(CARDS_RLE + c.offset, c.size, 2, lut);
  tft.endWrite();

  statsPixels += (uint32_t)CARD_W * CARD_H;
}

// --- Dibujado del menu de personajes ---------------------------------------
// Todo se compone en RAM y se manda con el camino rapido de una llamada al
// driver, igual que la cara: nunca tft.print() sobre la pantalla.

// `hablando` NO es un cursor de seleccion: solo marca al guia que se esta
// presentando en voz alta durante el repaso inicial. Al terminar el repaso
// todos quedan iguales, para que nadie piense que hay que pulsar nada.
void drawMenuRow(uint8_t i, bool hablando) {
  if (!menuCanvas.ready() || i >= MENU_COUNT) return;
  const MenuEntry &e = MENU_ENTRIES[i];
  menuCanvas.setOrigin(MENU_X, MENU_TOP + i * (MENU_ROW_H + MENU_GAP));
  menuCanvas.fillScreen(ST77XX_BLACK);

  const uint16_t color = tft.color565(e.r, e.g, e.b);
  menuCanvas.fillRoundRect(0, 0, MENU_ROW_W, MENU_ROW_H, 7, color);

  menuCanvas.setTextColor(ST77XX_BLACK);
  // Las comillas angulares dicen "esto se DICE", no "esto se pulsa".
  menuCanvas.setTextSize(2);
  menuCanvas.setCursor(12, 5);
  menuCanvas.print("\"");
  menuCanvas.print(e.name);
  menuCanvas.print("\"");
  menuCanvas.setTextSize(1);
  menuCanvas.setCursor(14, 22);
  menuCanvas.print(e.tag);

  // Mientras se le nombra, un bocadillo a la derecha. Es momentaneo: no es
  // un estado "elegido".
  if (hablando) {
    menuCanvas.fillCircle(MENU_ROW_W - 26, MENU_ROW_H / 2, 8, ST77XX_BLACK);
    menuCanvas.fillCircle(MENU_ROW_W - 26, MENU_ROW_H / 2, 6, color);
    menuCanvas.fillCircle(MENU_ROW_W - 14, MENU_ROW_H / 2 + 4, 3, ST77XX_BLACK);
  }
  menuCanvas.flush();
}

// Icono de microfono dibujado a mano (no hay fuente con simbolos): capsula
// redondeada, arco y pie.
void drawMic(int16_t cx, int16_t cy, uint16_t color) {
  menuCanvas.fillRoundRect(cx - 3, cy - 9, 7, 12, 3, color);
  menuCanvas.drawCircle(cx, cy + 1, 6, color);
  menuCanvas.drawCircle(cx, cy + 1, 7, color);
  menuCanvas.fillRect(cx - 1, cy + 7, 3, 4, color);
  menuCanvas.fillRect(cx - 5, cy + 11, 11, 2, color);
}

void drawMenuTitle() {
  if (!menuCanvas.ready()) return;
  menuCanvas.setOrigin(MENU_X, 4);
  menuCanvas.fillScreen(ST77XX_BLACK);
  menuCanvas.setTextColor(ST77XX_WHITE);
  menuCanvas.setTextSize(2);
  menuCanvas.setCursor(22, 2);
  menuCanvas.print("DI UN NOMBRE");
  // El microfono deja claro que se habla, sin depender de que sepan leer.
  drawMic(MENU_ROW_W - 40, 13, ST77XX_WHITE);
  menuCanvas.setTextSize(1);
  menuCanvas.setTextColor(tft.color565(180, 180, 180));
  menuCanvas.setCursor(22, 21);
  menuCanvas.print("en voz alta - no hay botones");
  menuCanvas.flush();
}

// `index` >= MENU_COUNT significa "ninguno resaltado": los 5 iguales, que es
// como queda el menu en reposo esperando a que el niño diga un nombre.
// `completo` = primera vez que se abre (pinta todo). Durante el repaso solo se
// repintan las dos filas que cambian.
void drawMenu(uint8_t index, bool completo) {
  if (completo) {
    tft.fillScreen(ST77XX_BLACK);
    drawMenuTitle();
    for (uint8_t i = 0; i < MENU_COUNT; i++) drawMenuRow(i, i == index);
    drawVersionBadge();
    drawWifiBadge(ST77XX_BLACK);
  } else if (index != menuIndex) {
    if (menuIndex < MENU_COUNT) drawMenuRow(menuIndex, false);
    if (index < MENU_COUNT) drawMenuRow(index, true);
  }
  menuIndex = index;
  menuOn = true;
}

// Prende/apaga el menu, ya en el hilo de loop() (dueño del SPI).
void consumePendingMenu() {
  const uint8_t want = pendingMenu;
  if (want == NO_PENDING_MENU) return;
  pendingMenu = NO_PENDING_MENU;

  if (want < MENU_COUNT || want == MENU_NO_SEL) {
    if (splashOn) return;  // la bienvenida manda mientras esta en pantalla
    if (!menuOn && currentPersona < FACE_COUNT) overlayPersona = currentPersona;
    drawMenu(want, !menuOn);
  } else if (menuOn) {
    menuOn = false;
    currentPersona = 255;  // la pantalla ya no tiene la cara: repintarla entera
    applyPersonaColors(overlayPersona);
  }
}

// Llamada desde Python con Bridge.notify("menu", 0..4) o 255 para cerrarlo.
// Misma regla que face_gesture(): solo anota el byte, loop() dibuja.
void menu(uint8_t index) {
  if (index < MENU_COUNT || index == MENU_NO_SEL) pendingMenu = index;
  else pendingMenu = MENU_OFF;
}

// Prende/apaga el QR, ya en el hilo de loop() (dueño del SPI). Va aqui y no
// junto al resto del codigo del QR porque necesita overlayPersona.
void consumePendingQr() {
  if (!qrPending) return;

  // El aviso y la bienvenida mandan: mientras esten en pantalla, el QR ESPERA.
  //
  // Antes aqui se hacia "return" DESPUES de poner qrPending = false, y eso se
  // comia la peticion: Bridge.notify() es fire-and-forget, asi que Python
  // creia haberlo mostrado, se quedaba su minuto en silencio y lo retiraba, y
  // en la pantalla no habia aparecido nada en ningun momento. Sin error por
  // ningun lado. Ahora la peticion se queda pendiente y el QR sale solo en
  // cuanto la pantalla queda libre.
  if (qrSizePending > 0 && (splashOn || avisoOn)) return;

  qrPending = false;
  qrSize = qrSizePending;
  if (qrSize > 0) {
    if (!qrOn && currentPersona < FACE_COUNT) overlayPersona = currentPersona;
    qrOn = true;
    drawQr();
    Serial.print("[chat-bang] QR en pantalla, ");
    Serial.print(qrSize);
    Serial.println(" modulos");
  } else if (qrOn) {
    qrOn = false;
    currentPersona = 255;
    applyPersonaColors(overlayPersona);
    Serial.println("[chat-bang] QR retirado");
  }
}

// Muestra/saca la tarjeta, ya en el hilo de loop() (dueño del SPI).
void consumePendingCard() {
  const uint8_t want = pendingCard;
  if (want == NO_PENDING_CARD) return;
  pendingCard = NO_PENDING_CARD;

  if (want < CARD_COUNT) {
    if (splashOn) return; // con la bienvenida en pantalla no se tapa
    if (!cardOn && currentPersona < FACE_COUNT) overlayPersona = currentPersona;
    drawCard(want);
    cardOn = true;
  } else if (cardOn) {
    cardOn = false;
    currentPersona = 255; // la pantalla ya no tiene la cara: redibujarla entera
    applyPersonaColors(overlayPersona);
  }
}

// Llamada desde Python con Bridge.notify("card", indice) o 255 para sacarla.
// Misma regla que face_gesture(): solo anota el byte, loop() dibuja.
void card(uint8_t index) {
  pendingCard = index < CARD_COUNT ? index : CARD_OFF;
}

// Llamada desde Python con Bridge.notify("splash", 1 / 0). Misma regla que
// face_gesture(): solo anota el byte, loop() dibuja.
void splash(uint8_t on) {
  pendingSplash = on ? 1 : 0;
}

// Llamada desde Python con Bridge.notify("face_gesture", <valor>), donde
// <valor> = personaId * GESTURE_COUNT + gesto (ver comentario de cabecera).
//
// IMPORTANTE: esta funcion NO puede tocar el SPI ni los servos. Corre en el
// hilo del Bridge (prioridad 5), que PREEMPTA a loop() (prioridad 14), y con
// solo 500 bytes de stack. provide_safe() no alcanza para evitarlo: el update()
// del hilo del Bridge acepta cualquier metodo, con tag o sin tag, asi que
// tarde o temprano este callback corre ahi. Si desde aca se llamara a
// tft.fillScreen(), habria dos hilos mandando por SPI a la vez y la pantalla
// se corrompe. Por eso lo unico que hace es dejar el gesto anotado: loop() lo
// consume y hace todo el trabajo (ver consumePendingGesture()).
void face_gesture(uint8_t encoded) {
  pendingEncoded = encoded; // escritura atomica de 1 byte; gana el ultimo gesto
}

// Llamada desde Python con Bridge.notify("viseme", 0..10) mientras suena la
// voz (solo cuando cambia). Misma regla que face_gesture(): solo anota el
// byte, loop() dibuja.
void viseme(uint8_t v) {
  mouthViseme = v < FACE_VISEMES ? v : FACE_MOUTH_REST;
}

// Lo de antes de los visemas: Bridge.notify("mouth_level", 0..4) con el
// volumen. Se sigue aceptando (respaldo): el volumen elige una boca.
void mouth_level(uint8_t level) {
  static const uint8_t LEVEL_VISEME[] = { FACE_MOUTH_REST, FACE_VIS_CDG, FACE_VIS_CDG, FACE_VIS_AEI, FACE_VIS_O };
  mouthViseme = LEVEL_VISEME[level < 4 ? level : 4];
}

// Llamada desde Python con Bridge.notify("arm_step", n), una por nota de la
// melodia de celebracion. Misma regla: solo anota, loop() mueve los servos.
void arm_step(uint8_t step) {
  pendingArmStep = step % 2; // solo importa la pose; nunca 0xFF
}

// Aplica el gesto pendiente, ya en el hilo de loop(), que es el unico dueño
// del SPI y de los servos.
void consumePendingGesture() {
  const uint8_t encoded = pendingEncoded;
  if (encoded == NO_PENDING) return;
  pendingEncoded = NO_PENDING;

  uint8_t personaId = encoded / GESTURE_COUNT;
  uint8_t gesture = encoded % GESTURE_COUNT;
  if (personaId >= FACE_COUNT) personaId = 0; // id invalido: el guia por defecto
  if (gesture > G_SAD) gesture = G_TALK;      // reservado: como TALK

  // Con el aviso, la bienvenida, una tarjeta, el menu o el QR en pantalla, el
  // guia se anota y aparece al salir de ahi (los servos y lo demas siguen
  // igual). applyPersonaColors() no repinta nada si el guia es el mismo: un
  // cambio de emocion solo cambia lo que la cara apunta (faceGesture), y
  // renderFace() lo lleva a pantalla con el redibujado parcial.
  if (avisoOn || splashOn || cardOn || menuOn || qrOn) overlayPersona = personaId;
  else applyPersonaColors(personaId);

  talking = (gesture != G_REST);
  faceGesture = gesture;
  if (!talking) mouthViseme = FACE_MOUTH_REST; // por si el ultimo visema no fue 0
  startServoFlourish(gesture);
}

// Una linea por segundo con el rendimiento real. Sin esto no se puede saber
// si de verdad hay 30 fps: se lee con `arduino-app-cli monitor`.
//
// Se arma en un buffer y se manda de una sola vez, corta a proposito: el ring
// de TX del Serial es de 64 bytes y ZephyrSerial::write() se bloquea (yield)
// hasta que entra todo lo que le pasas. Una linea larga costaria milisegundos
// de frame una vez por segundo, justo lo que estamos tratando de ganar.
void reportStats() {
  unsigned long now = millis();
  if (now - statsWindowStart < STATS_PERIOD_MS) return;

  char line[64];
  snprintf(line, sizeof(line), "[perf] %ufps w=%lu spi=%lu cp=%lu px=%lu",
           (unsigned)statsFrames, (unsigned long)statsWorstFrameUs,
           (unsigned long)statsSpiUs, (unsigned long)statsCopyUs,
           (unsigned long)statsPixels);
  Serial.println(line);

  statsWindowStart = now;
  statsFrames = 0;
  statsWorstFrameUs = 0;
  statsPixels = 0;
  statsCopyUs = 0;
  statsSpiUs = 0;
}

void setup() {
  Serial.begin(115200);

  tft.init(240, 320);         // Resolucion nativa del panel GMT028-05
  tft.invertDisplay(false);    // el panel muestra 0x0000 como blanco sin esto
  // 40 MHz: cae en un divisor limpio (PCLK1 160 MHz / 4). No conviene subir:
  // el bus NO es el cuello de botella (ver cabecera) y con cables largos mete
  // ruido. Si aparecen pixeles corruptos, bajar a 32000000 o 24000000 — sobra
  // presupuesto de tiempo.
  tft.setSPISpeed(40000000);
  tft.setRotation(3);          // Landscape: 320x240
  // Arranca con el AVISO DE SEGURIDAD (pendingAviso = 1). Despues Python
  // pide la bienvenida, luego el menu de guias, y al final la cara del guia
  // que el niño haya elegido.
  consumePendingAviso();

  // GFXcanvas16 pide la RAM con malloc() y no avisa si falla: si algun canvas
  // no se pudo crear, mejor saberlo por el monitor que ver media pantalla.
  if (!verCanvas.ready() || !wifiCanvas.ready() || !menuCanvas.ready()) {
    Serial.println("[chat-bang] ERROR: no alcanzo la RAM para los canvas");
  }

  nextAutoBlinkMs = millis() + random(BLINK_IDLE_MIN_MS, BLINK_IDLE_MAX_MS);
  statsWindowStart = millis();

  // Servos: arrancan en reposo (90°) y se sueltan solos poco despues (mismo
  // camino que updateServos() usa al volver de hablar a REST).
  writeServos(SERVO_REST_ANGLE);
  servoRestSettling = true;
  servoRestSettleUntil = millis() + SERVO_REST_SETTLE_MS;

  Bridge.begin();
  Bridge.provide_safe("face_gesture", face_gesture);
  Bridge.provide_safe("mouth_level", mouth_level);
  Bridge.provide_safe("viseme", viseme);
  Bridge.provide_safe("arm_step", arm_step);
  Bridge.provide_safe("splash", splash);
  Bridge.provide_safe("card", card);
  Bridge.provide_safe("menu", menu);
  Bridge.provide_safe("aviso", aviso);
  Bridge.provide_safe("wifi", wifi);
  Bridge.provide_safe("qr", qr);
  Bridge.provide_safe("wifi_sync", wifi_sync);

  Serial.println("[chat-bang] sketch de la carita listo");
}

void loop() {
  consumePendingAviso();   // el aviso va antes que todo lo demas
  consumePendingSplash();  // pueden repintar la pantalla entera
  consumePendingQr();
  consumePendingMenu();
  consumePendingCard();
  consumePendingGesture();

  // La animacion de WiFi sincronizado bloquea ~1,4 s a proposito: es un
  // evento unico y se tiene que ver entero. Al terminar, repinta lo que hubiera.
  if (wifiSyncPending) {
    wifiSyncPending = false;
    // El guia que esta en pantalla (overlayPersona solo se actualiza al
    // abrir otra pantalla encima de la cara).
    if (currentPersona < FACE_COUNT) overlayPersona = currentPersona;
    playWifiSync();
    currentPersona = 255;
    if (qrOn) drawQr();
    else if (menuOn) drawMenu(menuIndex, true);
    else applyPersonaColors(overlayPersona);
  }

  // El nivel de WiFi se repinta solo cuando cambia (es una esquina chica).
  if (pendingWifi != NO_PENDING_WIFI) {
    const uint8_t nivel = pendingWifi;
    pendingWifi = NO_PENDING_WIFI;
    if (nivel != wifiLevel) {
      wifiLevel = nivel;
      if (!avisoOn) drawWifiBadge((splashOn || menuOn || cardOn) ? ST77XX_BLACK : BG_COLOR);
    }
  }
  consumeArmStep();

  updateAutoBlink();
  updateServos();

  unsigned long now = millis();
  if (now - lastFrame < FRAME_MS) {
    return;
  }
  lastFrame = now;

  uint32_t frameStartUs = micros();

  if (avisoOn) {
    renderAviso();
  } else if (qrOn) {
    // el QR es fijo: nada que animar
  } else if (splashOn) {
    renderSplash();
  } else if (menuOn) {
    // el menu es fijo: solo se repinta cuando cambia la seleccion
  } else if (cardOn) {
    // la tarjeta es fija: no hay nada que animar
  } else if (face && currentPersona < FACE_COUNT) {
    framePxLeft = FRAME_PX_BUDGET;
    renderFace();
  }
  // Si no, no hay cara valida en pantalla (recien salio la bienvenida y se
  // espera el menu, por ejemplo): nada que animar hasta el proximo gesto.

  uint32_t frameUs = micros() - frameStartUs;
  if (frameUs > statsWorstFrameUs) statsWorstFrameUs = frameUs;
  statsFrames++;

  reportStats();
}
