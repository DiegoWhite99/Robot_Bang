/*
  Chat BANG - carita animada en pantalla TFT + 2 servos (lado MCU).

  El MPU (Python) lleva la conversacion; aqui animamos la carita en la
  pantalla Y movemos 2 servos SG90 sincronizados, ambos mientras el celular
  lee la respuesta en voz alta.

  Python avisa con Bridge.notify("face_gesture", <valor>), donde <valor> es
  el gesto y el personaje empaquetados en un solo entero:
    valor = personaId * 4 + gesto

  Gesto (0-3):
    0 REST      boca cerrada, parpadeo normal, servos en reposo (90°, sueltos)
    1 TALK      boca se mueve + servos en vaiven, como si hablara
    2 HAPPY     boca se mueve + cejas se levantan + servos barren amplio x2
    3 SURPRISE  boca se mueve + cejas se levantan + servos dan un respingo

  Personaje: cada uno tiene su propia paleta (fondo, ojos, cejas, boca),
  definida en faces_colors.h — ver paletteFor(). Para cambiar los colores
  de un guia, edita ese archivo; no hace falta tocar este.

  Cara dibujada: los guias que tienen PNG en assets/img/<guia>/ (hoy los
  5) no usan la cara geometrica sino esos dibujos, convertidos a
  sprites por tools/make_face_sprites.py (-> <guia>_face.h). El parpadeo
  repite la secuencia de los PNG (abierto, entrecerrado, cerrado,
  entrecerrado, abierto) y la boca se abre segun el volumen real de la voz.

  Baile de celebracion (de Diome-chan): al pasar de fase BANG, Python toca
  una cancioncita y en cada nota manda Bridge.notify("arm_step", n); el
  sketch alterna los dos brazos entre dos poses espejadas (ver consumeArmStep()).

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
  Bridge.notify("mouth_level", 0..4) con el volumen de lo que esta sonando
  (ver python/voice.py). Vale para las dos caras, la dibujada y la geometrica.

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

  Redibujado parcial: cada parte movil de la cara (cada ojo, cada ceja, la
  boca) tiene su propio framebuffer chico en RAM, y solo se vuelve a mandar
  por SPI la que realmente cambio. Hablando sin parpadear, por ejemplo, solo
  viaja la boca.
*/

#include "Arduino_RouterBridge.h"
#include "faces_colors.h"
#include "face_bands.h"
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

// --- Geometria: todo derivado de SCREEN_W/SCREEN_H, nunca literales sueltos ---
// Medidas pensadas para que cada parte movil ocupe la region mas chica posible:
// lo que se dibuja es lo que viaja por SPI en cada frame.
// EYE_R chico a proposito: el radio de la esquina se suma al alto de las
// bandas que hay que reenviar al parpadear (ver bandsFor()), y a ~4 us/byte
// cada fila extra cuesta tiempo de frame.
const int16_t EYE_W = 64, EYE_H = 64, EYE_R = 6, EYE_SPACE = 40;
const int16_t FACE_CY = SCREEN_H / 2;                               // 120
const int16_t EYE_CY = FACE_CY - 10;                                // 110
const int16_t EYE_L_CX = SCREEN_W / 2 - EYE_SPACE / 2 - EYE_W / 2;  // 108
const int16_t EYE_R_CX = SCREEN_W / 2 + EYE_SPACE / 2 + EYE_W / 2;  // 212
const int16_t BROW_H = 14, BROW_GAP = 8;
const int16_t BROW_W = EYE_W - 12;                                  // 52
const int16_t EYE_MIN_H = 6; // alto del ojo completamente cerrado (parpadeo)

const int16_t MOUTH_CX = SCREEN_W / 2;             // 160
const int16_t MOUTH_CY = FACE_CY + EYE_H / 2 + 45; // 197, debajo de los ojos
const int16_t MOUTH_W = 120;

const unsigned long FRAME_MS = 33; // 30 fps

// --- Paleta RGB565: cambia por personaje, asi que ya no son const ---
uint16_t BG_COLOR    = ST77XX_BLACK;
uint16_t EYE_COLOR   = tft.color565(30, 100, 255);  // azul (por defecto, antes del primer gesto)
uint16_t BROW_COLOR  = tft.color565(30, 100, 255);
uint16_t MOUTH_COLOR = tft.color565(30, 100, 255);

// --- Gestos recibidos desde Python. Deben coincidir con python/gestures.py ---
const uint8_t G_REST = 0;
const uint8_t G_TALK = 1;
const uint8_t G_HAPPY = 2;
const uint8_t G_SURPRISE = 3;
const uint8_t GESTURE_COUNT = 4; // para desempaquetar el valor combinado

// --- Personajes. Deben coincidir con PERSONA_IDS de python/gestures.py ---
const uint8_t P_CRISPI = 0;
const uint8_t P_CARMEL = 1;
const uint8_t P_CESIA = 2;
const uint8_t P_CORI = 3;
const uint8_t P_CRISTAL = 4;

uint8_t currentPersona = 255; // invalido a proposito: fuerza la 1ra paleta

struct Palette {
  uint16_t bg;    // fondo de pantalla
  uint16_t eye;   // ojos
  uint16_t brow;  // cejas
  uint16_t mouth; // boca
};

// Colores por personaje: se editan en faces_colors.h, no aqui.
Palette paletteFor(uint8_t personaId) {
  if (personaId >= FACE_PALETTES_COUNT) personaId = 0; // por si llega un id invalido
  const FaceColors &c = FACE_PALETTES[personaId];
  return {
    tft.color565(c.bgR, c.bgG, c.bgB),
    tft.color565(c.eyeR, c.eyeG, c.eyeB),
    tft.color565(c.browR, c.browG, c.browB),
    tft.color565(c.mouthR, c.mouthG, c.mouthB),
  };
}

bool talking = false; // true entre un gesto != REST y el siguiente REST

// --- Cara dibujada (sprites de los PNG). nullptr = cara geometrica ---
// (los <GUIA>_SPRITES vienen armados en cada <guia>_face.h)
const FaceSpriteSet *spriteSetFor(uint8_t personaId) {
  switch (personaId) {
    case P_CRISPI:  return &CRISPI_SPRITES;
    case P_CARMEL:  return &CARMEL_SPRITES;
    case P_CESIA:   return &CESIA_SPRITES;
    case P_CORI:    return &CORI_SPRITES;
    case P_CRISTAL: return &CRISTAL_SPRITES;
    default:        return nullptr; // sin PNG: cara geometrica
  }
}

const FaceSpriteSet *spriteFace = nullptr;
uint16_t spriteLut[4];            // 2 bits -> color de pantalla, ya con el byte swap
uint8_t drawnEyeState = FACE_EYE_OPEN;
uint8_t drawnMouthLevel = 0;
unsigned long eyeDrawnMs = 0;     // cuando se termino de dibujar el estado actual
uint8_t eyesWaitFrames = 0;       // frames seguidos que los ojos no entraron

// Parpadeo de la cara dibujada: la misma secuencia de los PNG (02, 03, 04;
// el 01 y el 05 son los ojos abiertos de reposo). Cada paso se sostiene su
// tiempo DESDE QUE TERMINA DE DIBUJARSE, asi ningun frame se saltea aunque
// el SPI se atrase (un paso de ojos cuesta 2-3 frames de bus).
const uint8_t SPRITE_BLINK_SEQ[] = { FACE_EYE_HALF, FACE_EYE_CLOSED, FACE_EYE_HALF };
const unsigned long SPRITE_BLINK_HOLD_MS[] = { 40, 90, 40 };
const int8_t SPRITE_BLINK_STEPS = sizeof(SPRITE_BLINK_SEQ);
int8_t spriteBlinkStep = -1;      // -1 = ojos abiertos, sin parpadeo en curso

// --- Parpadeo automatico (de vez en cuando, un poco mas seguido al hablar) ---
enum BlinkPhase : uint8_t { PHASE_OPEN, PHASE_CLOSING, PHASE_CLOSED, PHASE_OPENING };
// Parpadeo mas largo que el original (90 ms): reparte el mismo recorrido en
// mas frames, asi cada frame mueve menos filas. Y 170/190 ms es igual o mas
// natural que 90 ms para un parpadeo humano.
const unsigned long BLINK_CLOSE_MS = 170, BLINK_HOLD_MS = 60, BLINK_OPEN_MS = 190;
const unsigned long BLINK_IDLE_MIN_MS = 7000, BLINK_IDLE_MAX_MS = 14000;
const unsigned long BLINK_TALK_MIN_MS = 5000, BLINK_TALK_MAX_MS = 9000;

BlinkPhase blinkPhase = PHASE_OPEN;
unsigned long blinkPhaseStartMs = 0;
unsigned long nextAutoBlinkMs = 0;
unsigned long lastFrame = 0;

// --- Cejas: pequeno levantamiento al recibir HAPPY/SURPRISE ---
const unsigned long BROW_PULSE_MS = 400;
const int16_t BROW_PULSE_LIFT_PX = 6;
unsigned long browPulseUntil = 0;

// --- Boca: se abre segun el volumen de la voz (nivel que manda Python) ---
const int16_t MOUTH_REST_H     = 8;   // cerrada/neutra en reposo
const int16_t MOUTH_MIN_TALK_H = 14;  // abierta con el nivel 1
const int16_t MOUTH_MAX_TALK_H = 48;  // abierta con el nivel maximo
const float MOUTH_LERP_ALPHA = 0.22f; // suavizado: mas bajo = menos filas por frame
const int16_t MOUTH_R = 4;            // radio chico, por lo mismo que EYE_R

float mouthCurrentH = MOUTH_REST_H;
int16_t mouthTargetH = MOUTH_REST_H;

// Ultimo nivel recibido por Bridge.notify("mouth_level", ...): 0 = cerrada,
// FACE_MOUTH_LEVELS - 1 = lo mas abierta. Lo escribe el hilo del Bridge
// (1 byte, atomico) y lo lee loop(), igual que pendingEncoded.
volatile uint8_t mouthLevel = 0;

// --- Servos: 2 SG90 sincronizados, se mueven junto con la boca ---
const uint8_t SERVO1_PIN = 5;
const uint8_t SERVO2_PIN = 6;
Servo servo1, servo2;

const int SERVO_REST_ANGLE   = 90;
const int SERVO_TALK_MIN     = 80;
const int SERVO_TALK_MAX     = 100;
const int SERVO_HAPPY_MIN    = 45;
const int SERVO_HAPPY_MAX    = 145;
const int SERVO_SURPRISE_ANGLE = 160;

const unsigned long SERVO_TALK_STEP_MS     = 150; // medio ciclo del vaiven al hablar
const unsigned long SERVO_HAPPY_LEG_MS     = 180; // duracion de cada tramo del barrido HAPPY
const uint8_t        SERVO_HAPPY_LEGS      = 4;    // 45->145->45->145 = barrido amplio x2
const unsigned long SERVO_SURPRISE_HOLD_MS = 200; // cuanto se sostiene el respingo
const unsigned long SERVO_REST_SETTLE_MS   = 350; // tiempo para llegar a 90 antes de soltar (detach)

enum ServoPhase : uint8_t { SERVO_PHASE_TALK, SERVO_PHASE_HAPPY, SERVO_PHASE_SURPRISE };
ServoPhase servoPhase = SERVO_PHASE_TALK;
unsigned long servoPhaseStepUntil = 0; // fin del tramo actual de HAPPY/SURPRISE
uint8_t servoHappyLeg = 0;             // que tramo del barrido HAPPY va

bool servoTalkHigh = false;            // hacia que extremo va el vaiven (TALK)
unsigned long servoNextStepMs = 0;

// Baile (arm_step): cada nota alterna entre dos poses espejadas alrededor de
// 90°, como Diome-chan alternaba 30/100 y 0/130 en sus dos brazos. Mientras
// dura, updateServos() no toca los servos.
const int DANCE_SWING_SMALL = 30;
const int DANCE_SWING_BIG = 60;
const unsigned long DANCE_HOLD_MS = 450; // sin notas nuevas en este tiempo, se termina el baile
const uint8_t NO_PENDING_STEP = 0xFF;
volatile uint8_t pendingArmStep = NO_PENDING_STEP;
unsigned long danceUntil = 0;

bool servosAttached = false;
bool servoRestSettling = false;        // esperando llegar a 90 antes de soltar
unsigned long servoRestSettleUntil = 0;

// --- Regiones de pantalla para el redibujado parcial ---
// Una region por parte movil, cada una del tamano justo de lo que dibuja: los
// ojos no arrastran la banda de cejas (que no se mueve al parpadear) ni el
// hueco entre los dos ojos.
const int16_t REGION_MARGIN = 4;

const int16_t EYE_REGION_W = EYE_W + 2 * REGION_MARGIN;             // 72
const int16_t EYE_REGION_H = EYE_H + 2 * REGION_MARGIN;             // 72
const int16_t EYE_REGION_Y = EYE_CY - EYE_H / 2 - REGION_MARGIN;    // 74
const int16_t EYE_L_REGION_X = EYE_L_CX - EYE_W / 2 - REGION_MARGIN; // 72
const int16_t EYE_R_REGION_X = EYE_R_CX - EYE_W / 2 - REGION_MARGIN; // 176

// La ceja se mueve entre "levantada" (lift maximo) y "en reposo" (lift 0): la
// region cubre justo ese recorrido.
const int16_t BROW_TOP_LIFTED  = EYE_CY - EYE_H / 2 - BROW_GAP - BROW_H - BROW_PULSE_LIFT_PX; // 50
const int16_t BROW_BOTTOM_REST = EYE_CY - EYE_H / 2 - BROW_GAP;                               // 70
const int16_t BROW_REGION_W = BROW_W + 2 * REGION_MARGIN;                       // 60
const int16_t BROW_REGION_H = (BROW_BOTTOM_REST - BROW_TOP_LIFTED) + 2 * REGION_MARGIN; // 28
const int16_t BROW_REGION_Y = BROW_TOP_LIFTED - REGION_MARGIN;                  // 46
const int16_t BROW_L_REGION_X = EYE_L_CX - BROW_W / 2 - REGION_MARGIN;          // 78
const int16_t BROW_R_REGION_X = EYE_R_CX - BROW_W / 2 - REGION_MARGIN;          // 182

const int16_t MOUTH_REGION_X = MOUTH_CX - MOUTH_W / 2 - REGION_MARGIN;          // 96
const int16_t MOUTH_REGION_Y = MOUTH_CY - MOUTH_MAX_TALK_H / 2 - REGION_MARGIN; // 169
const int16_t MOUTH_REGION_W = MOUTH_W + 2 * REGION_MARGIN;                     // 128
const int16_t MOUTH_REGION_H = MOUTH_MAX_TALK_H + 2 * REGION_MARGIN;            // 56

RegionCanvas eyeLCanvas(EYE_REGION_W, EYE_REGION_H, EYE_L_REGION_X, EYE_REGION_Y, tft);
RegionCanvas eyeRCanvas(EYE_REGION_W, EYE_REGION_H, EYE_R_REGION_X, EYE_REGION_Y, tft);
RegionCanvas browLCanvas(BROW_REGION_W, BROW_REGION_H, BROW_L_REGION_X, BROW_REGION_Y, tft);
RegionCanvas browRCanvas(BROW_REGION_W, BROW_REGION_H, BROW_R_REGION_X, BROW_REGION_Y, tft);
RegionCanvas mouthCanvas(MOUTH_REGION_W, MOUTH_REGION_H, MOUTH_REGION_X, MOUTH_REGION_Y, tft);

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
RegionCanvas wifiCanvas(WIFI_W, WIFI_H, SCREEN_W - WIFI_W - 3, 3, tft);

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
  if (data.size() < (size_t)(1 + need) || need > QR_BYTES) return;
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

// Ultimo valor efectivamente dibujado: si no cambio, nos ahorramos el
// redibujado + envio de esa region (arranca en -1 para forzar el primer
// dibujo).
int16_t lastDrawnEyeH = -1;
int16_t lastDrawnBrowLift = -1;
int16_t lastDrawnMouthH = -1;

// Presupuesto de pixeles por frame. A ~4 us/byte, 3200 px = ~25 ms, que entra
// en los 33 ms del frame. Si dos partes quieren actualizarse en el mismo frame
// y no alcanza, una espera al siguiente: asi el frame NUNCA se pasa y los
// 30 fps son estables POR CONSTRUCCION (se degrada la suavidad, no el fps).
const uint32_t FRAME_PX_BUDGET = 3200;
uint32_t framePxLeft = 0;
// Arranca en true: el primer frame tiene que dibujar la cara completa, y eso
// no entra en el presupuesto (region entera de cada parte). Un frame lento al
// arrancar y en cada cambio de personaje, nada mas.
bool forceFullRedraw = true;


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
const uint8_t NO_PENDING = 0xFF; // los validos son 0..19 (5 personajes * 4 gestos)
volatile uint8_t pendingEncoded = NO_PENDING;

// --- Medicion de rendimiento: se lee con `arduino-app-cli monitor` ---
const unsigned long STATS_PERIOD_MS = 1000;
unsigned long statsWindowStart = 0;
uint16_t statsFrames = 0;
uint32_t statsWorstFrameUs = 0;

// Pone la paleta del personaje que esta hablando. Si es el mismo de antes no
// hace nada, para no repintar la pantalla completa en cada frase.
// OJO: toca el SPI (fillScreen), asi que solo puede llamarse desde loop().
void applyPersonaColors(uint8_t personaId) {
  if (personaId == currentPersona) return;
  currentPersona = personaId;

  Palette p = paletteFor(personaId);
  BG_COLOR = p.bg;
  EYE_COLOR = p.eye;
  BROW_COLOR = p.brow;
  MOUTH_COLOR = p.mouth;

  spriteFace = spriteSetFor(personaId);
  if (spriteFace) {
    // La base es la pantalla entera (fondo + cara con ojos abiertos y boca
    // cerrada): reemplaza al fillScreen, un solo frame lento (~0,6 s).
    buildSpriteLut(BG_COLOR, EYE_COLOR, MOUTH_COLOR, spriteFace->twoInks);
    const FaceSprite &base = *spriteFace->base;
    pushSpriteRect(base, 0, 0, base.w, base.h);
    drawVersionBadge();  // la cara acaba de repintar la pantalla entera
    drawWifiBadge(BG_COLOR);
    drawnEyeState = FACE_EYE_OPEN;
    drawnMouthLevel = 0;
    spriteBlinkStep = -1;
    eyesWaitFrames = 0;
    return;
  }

  tft.fillScreen(BG_COLOR); // fondo completo, una sola vez por cambio de personaje
  drawVersionBadge();
  drawWifiBadge(BG_COLOR);

  // El redibujado parcial solo se dispara cuando el valor dibujado cambia;
  // forzamos eso para que ojos, cejas y boca tomen los colores nuevos ya.
  lastDrawnEyeH = -1;
  lastDrawnBrowLift = -1;
  lastDrawnMouthH = -1;
  forceFullRedraw = true; // este frame se pasa del presupuesto a proposito
}

void triggerBlink() {
  if (blinkPhase == PHASE_OPEN) {
    blinkPhase = PHASE_CLOSING;
    blinkPhaseStartMs = millis();
  }
  // La cara dibujada tiene su propia secuencia (ver SPRITE_BLINK_SEQ); la
  // fase de arriba sigue corriendo igual, porque marca cada cuanto parpadear.
  if (spriteBlinkStep < 0) spriteBlinkStep = 0;
}

void updateBlinkPhase() {
  if (blinkPhase == PHASE_OPEN) return;
  unsigned long elapsed = millis() - blinkPhaseStartMs;
  if (blinkPhase == PHASE_CLOSING && elapsed >= BLINK_CLOSE_MS) {
    blinkPhase = PHASE_CLOSED;
    blinkPhaseStartMs = millis();
  } else if (blinkPhase == PHASE_CLOSED && elapsed >= BLINK_HOLD_MS) {
    blinkPhase = PHASE_OPENING;
    blinkPhaseStartMs = millis();
  } else if (blinkPhase == PHASE_OPENING && elapsed >= BLINK_OPEN_MS) {
    blinkPhase = PHASE_OPEN;
  }
}

int16_t currentEyeHeight() {
  unsigned long e = millis() - blinkPhaseStartMs;
  switch (blinkPhase) {
    case PHASE_CLOSING:
      return EYE_H - (int16_t)(min(1.0f, (float)e / BLINK_CLOSE_MS) * (EYE_H - EYE_MIN_H));
    case PHASE_CLOSED:
      return EYE_MIN_H;
    case PHASE_OPENING:
      return EYE_MIN_H + (int16_t)(min(1.0f, (float)e / BLINK_OPEN_MS) * (EYE_H - EYE_MIN_H));
    default: // PHASE_OPEN
      return EYE_H;
  }
}

void updateAutoBlink() {
  if (blinkPhase == PHASE_OPEN && millis() >= nextAutoBlinkMs) {
    triggerBlink();
    nextAutoBlinkMs = millis() + (talking
      ? random(BLINK_TALK_MIN_MS, BLINK_TALK_MAX_MS)
      : random(BLINK_IDLE_MIN_MS, BLINK_IDLE_MAX_MS));
  }
}

// Nivel de boca que toca mostrar: el que manda Python, solo mientras habla.
uint8_t currentMouthLevel() {
  if (!talking) return 0;
  const uint8_t level = mouthLevel;
  return level < FACE_MOUTH_LEVELS ? level : FACE_MOUTH_LEVELS - 1;
}

// Boca de la cara geometrica: el alto sigue el volumen de la voz (nivel 0 =
// cerrada, en los silencios entre palabras tambien); en reposo vuelve
// suavemente a casi cerrada.
void updateMouthTalk() {
  const uint8_t level = currentMouthLevel();
  if (level == 0) {
    mouthTargetH = MOUTH_REST_H;
  } else {
    mouthTargetH = MOUTH_MIN_TALK_H +
      (int16_t)((MOUTH_MAX_TALK_H - MOUTH_MIN_TALK_H) * (level - 1) / (FACE_MOUTH_LEVELS - 2));
  }
  mouthCurrentH += (mouthTargetH - mouthCurrentH) * MOUTH_LERP_ALPHA;
}

// Escribe el mismo angulo en los 2 servos (siempre sincronizados). Los
// vuelve a enganchar (attach) si estaban sueltos por un REST anterior.
void writeServos(int angle) {
  if (!servosAttached) {
    servo1.attach(SERVO1_PIN);
    servo2.attach(SERVO2_PIN);
    servosAttached = true;
  }
  servo1.write(angle);
  servo2.write(angle);
}

// Los dos servos a angulos distintos (el baile los mueve espejados).
void writeServoPair(int angle1, int angle2) {
  writeServos(angle1); // engancha si hacia falta
  servo2.write(angle2);
}

// Un paso del baile, ya en el hilo de loop() (dueño de los servos).
void consumeArmStep() {
  const uint8_t step = pendingArmStep;
  if (step == NO_PENDING_STEP) return;
  pendingArmStep = NO_PENDING_STEP;

  const int swing = (step % 2 == 0) ? DANCE_SWING_SMALL : DANCE_SWING_BIG;
  writeServoPair(SERVO_REST_ANGLE - swing, SERVO_REST_ANGLE + swing);
  danceUntil = millis() + DANCE_HOLD_MS;
  servoRestSettling = false; // al terminar, updateServos() vuelve a reposo solo
}

// Arranca el barrido amplio de HAPPY o el respingo de SURPRISE. Se llama una
// sola vez, al consumir el gesto (ver consumePendingGesture()); despues de su
// tiempo corto, updateServos() vuelve sola a la fase TALK.
void startServoFlourish(uint8_t gesture) {
  servoRestSettling = false; // por si veniamos de REST, cancelar el soltado
  if (gesture == G_HAPPY) {
    servoPhase = SERVO_PHASE_HAPPY;
    servoHappyLeg = 0;
    servoPhaseStepUntil = millis() + SERVO_HAPPY_LEG_MS;
    writeServos(SERVO_HAPPY_MIN);
  } else if (gesture == G_SURPRISE) {
    servoPhase = SERVO_PHASE_SURPRISE;
    servoPhaseStepUntil = millis() + SERVO_SURPRISE_HOLD_MS;
    writeServos(SERVO_SURPRISE_ANGLE);
  }
  // TALK no necesita flourish: updateServos() ya hace el vaiven mientras
  // talking == true.
}

// Mueve los servos segun la fase actual. Se llama en cada loop(), igual que
// updateMouthTalk(): nada de delay(), todo temporizado con millis().
void updateServos() {
  unsigned long now = millis();
  if (now < danceUntil) return; // bailando: los mueve consumeArmStep()

  if (!talking) {
    // REST: volver a 90 y, tras un rato de sobra para llegar, soltar el
    // servo (detach) para que no zumbe en reposo — igual que describia el
    // README original ("vuelve a 90° y se suelta, no zumba").
    if (servoPhase != SERVO_PHASE_TALK) {
      servoPhase = SERVO_PHASE_TALK; // corta cualquier flourish en curso
    }
    if (!servoRestSettling && servosAttached) {
      writeServos(SERVO_REST_ANGLE);
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

  switch (servoPhase) {
    case SERVO_PHASE_HAPPY:
      if (now >= servoPhaseStepUntil) {
        servoHappyLeg++;
        if (servoHappyLeg >= SERVO_HAPPY_LEGS) {
          servoPhase = SERVO_PHASE_TALK; // barrido terminado, sigue el vaiven normal
        } else {
          writeServos((servoHappyLeg % 2 == 0) ? SERVO_HAPPY_MIN : SERVO_HAPPY_MAX);
          servoPhaseStepUntil = now + SERVO_HAPPY_LEG_MS;
        }
      }
      break;

    case SERVO_PHASE_SURPRISE:
      if (now >= servoPhaseStepUntil) {
        servoPhase = SERVO_PHASE_TALK; // respingo terminado, sigue el vaiven normal
      }
      break;

    case SERVO_PHASE_TALK:
      if (now >= servoNextStepMs) {
        servoTalkHigh = !servoTalkHigh;
        writeServos(servoTalkHigh ? SERVO_TALK_MAX : SERVO_TALK_MIN);
        servoNextStepMs = now + SERVO_TALK_STEP_MS;
      }
      break;
  }
}

// --- Dibujo: componer en RAM (barato) y mandar SOLO las filas que cambiaron ---
//
// Medido en esta placa: el driver SPI cuesta ~4 us por byte (una ISR por byte,
// el bus va al 5% de su capacidad). O sea ~253 KB/s reales: un flush de region
// completa de la boca (7.168 px) son 57 ms, casi dos frames. Por eso NO se
// puede mandar la region entera en cada frame.
//
// Las partes de la cara crecen y encogen centradas, asi que entre un frame y
// el siguiente solo cambian dos bandas de filas: una arriba y otra abajo del
// borde. Se manda solo eso, y lo que sale por pantalla es identico.


ChangeBands bandsFor(int16_t H, int16_t hOld, int16_t hNew, int16_t r) {
  ChangeBands b;
  if (hOld < 0) { // primera vez / cambio de personaje: toda la region
    b.topY = 0; b.topH = H; b.botY = 0; b.botH = 0;
    return b;
  }

  const int16_t t0 = (H - hOld) / 2, t1 = (H - hNew) / 2;
  const int16_t d0 = t0 + hOld,      d1 = t1 + hNew;

  // +-1 de holgura para no cortar bordes por redondeo de la division entera.
  int16_t topFrom = max((int16_t)0, (int16_t)(min(t0, t1) - 1));
  int16_t topTo   = min(H, (int16_t)(max(t0, t1) + r + 1));
  int16_t botFrom = max((int16_t)0, (int16_t)(min(d0, d1) - r - 1));
  int16_t botTo   = min(H, (int16_t)(max(d0, d1) + 1));

  if (botFrom <= topTo) { // se solapan: una sola banda
    b.topY = topFrom; b.topH = botTo - topFrom; b.botY = 0; b.botH = 0;
  } else {
    b.topY = topFrom; b.topH = topTo - topFrom;
    b.botY = botFrom; b.botH = botTo - botFrom;
  }
  return b;
}

void flushBands(RegionCanvas &c, const ChangeBands &b) {
  if (b.topH > 0) c.flushRect(0, b.topY, c.width(), b.topH);
  if (b.botH > 0) c.flushRect(0, b.botY, c.width(), b.botH);
}

void composeEye(RegionCanvas &canvas, int16_t eyeH) {
  canvas.fillScreen(BG_COLOR);
  // Centrado en su region: la region tiene el ancho/alto del ojo + margen.
  canvas.fillRoundRect(REGION_MARGIN, (EYE_REGION_H - eyeH) / 2,
                       EYE_W, eyeH, min(EYE_R, (int16_t)(eyeH / 2)), EYE_COLOR);
}

void composeBrow(RegionCanvas &canvas, int16_t browLift) {
  canvas.fillScreen(BG_COLOR);
  // La ceja "en reposo" (lift 0) queda abajo de su region; al levantarse sube.
  int16_t y = REGION_MARGIN + (BROW_PULSE_LIFT_PX - browLift);
  canvas.fillRoundRect(REGION_MARGIN, y, BROW_W, BROW_H, BROW_H / 2, BROW_COLOR);
}

void composeMouth(int16_t mouthH) {
  mouthCanvas.fillScreen(BG_COLOR);
  if (mouthH >= 2) {
    mouthCanvas.fillRoundRect(REGION_MARGIN, (MOUTH_REGION_H - mouthH) / 2,
                              MOUTH_W, mouthH,
                              min((int16_t)(mouthH / 2), MOUTH_R), MOUTH_COLOR);
  }
}

// --- Cara dibujada: sprites de 2 bits de los PNG (ver face_sprite.h) ---
//
// Mismo problema de bus que la cara geometrica, peor: un ojo dibujado son
// ~7.400 px, mas de dos frames de SPI. Por eso de un sprite al siguiente
// (ojo abierto -> entrecerrado, boca nivel 1 -> 3...) se manda solo lo que
// cambia: se comparan los dos sprites fila por fila y las filas que difieren
// se agrupan en pocos rectangulos (planSpriteDiff()).

// Cada rectangulo cuesta ademas su setAddrWindow() (~11 bytes de comando, de
// a uno por vez): en pixeles equivale a unos 32. Con eso se decide si
// conviene juntar dos filas en un mismo rectangulo o mandarlas por separado.
const uint32_t WINDOW_COST_PX = 32;
const uint8_t MAX_DIFF_RECTS = 24;
SpanRect diffRectsA[MAX_DIFF_RECTS], diffRectsB[MAX_DIFF_RECTS];

// Mezcla fondo -> trazo en los niveles de opacidad de los sprites. Con dos
// tintas (ver FaceSpriteSet::twoInks) son 3 niveles de `ink` y el ultimo
// valor es `ink2` lleno.
void buildSpriteLut(uint16_t bg, uint16_t ink, uint16_t ink2, bool twoInks) {
  const uint8_t br = (bg >> 11) & 0x1F, bgc = (bg >> 5) & 0x3F, bb = bg & 0x1F;
  const uint8_t ir = (ink >> 11) & 0x1F, ig = (ink >> 5) & 0x3F, ib = ink & 0x1F;
  const uint8_t steps = twoInks ? 2 : 3;
  for (uint8_t a = 0; a <= steps; a++) {
    const uint16_t r = (br * (steps - a) + ir * a) / steps;
    const uint16_t g = (bgc * (steps - a) + ig * a) / steps;
    const uint16_t b = (bb * (steps - a) + ib * a) / steps;
    spriteLut[a] = __builtin_bswap16((uint16_t)((r << 11) | (g << 5) | b));
  }
  if (twoInks) spriteLut[3] = __builtin_bswap16(ink2);
}

// Manda un sub-rectangulo de un sprite (coordenadas locales). Mismo camino
// rapido que RegionCanvas::flushRect(): se arma en spiScratch (nunca se le
// pasa otro buffer a SPI.transfer(), que lo sobrescribe) y va de a chunks.
void pushSpriteRect(const FaceSprite &s, int16_t rx, int16_t ry, int16_t rw, int16_t rh) {
  if (rw <= 0 || rh <= 0) return;
  const int16_t rowsPerChunk = (int16_t)(SCRATCH_PX / (uint32_t)rw);
  if (rowsPerChunk < 1) return; // no deberia pasar: rw <= SCREEN_W

  tft.startWrite();
  tft.setAddrWindow(s.x + rx, s.y + ry, rw, rh);

  for (int16_t row = 0; row < rh; ) {
    const int16_t rows = min((int16_t)(rh - row), rowsPerChunk);
    uint16_t *dst = spiScratch;

    uint32_t t0 = micros();
    for (int16_t r = 0; r < rows; r++) {
      for (int16_t c = 0; c < rw; c++) {
        *dst++ = spriteLut[s.at(rx + c, ry + row + r)];
      }
    }
    uint32_t t1 = micros();

    SPI.transfer(spiScratch, (size_t)rows * (size_t)rw * 2);
    uint32_t t2 = micros();

    statsCopyUs += t1 - t0;
    statsSpiUs += t2 - t1;
    row += rows;
  }

  tft.endWrite();
  statsPixels += (uint32_t)rw * rh;
}

// Arma los rectangulos que llevan la pantalla del sprite `from` al `to`
// (mismo rectangulo de pantalla, distintos pixeles). Devuelve cuantos son y
// deja en `cost` los pixeles a mandar, overhead de ventana incluido.
uint8_t planSpriteDiff(const FaceSprite &from, const FaceSprite &to, SpanRect *out, uint32_t &cost) {
  const uint16_t stride = (to.w + 3) / 4;
  uint8_t n = 0;
  cost = 0;

  for (int16_t row = 0; row < to.h; row++) {
    const uint8_t *ra = from.px + (uint32_t)row * stride;
    const uint8_t *rb = to.px + (uint32_t)row * stride;
    int16_t b0 = -1, b1 = -1;
    for (uint16_t i = 0; i < stride; i++) {
      if (ra[i] != rb[i]) {
        if (b0 < 0) b0 = i;
        b1 = i;
      }
    }
    if (b0 < 0) continue; // fila identica

    // Granularidad de byte (4 px): mas simple y el sobrante es minimo.
    const int16_t x0 = b0 * 4, x1 = min((int16_t)(to.w - 1), (int16_t)(b1 * 4 + 3));
    const int16_t rowW = x1 - x0 + 1;

    if (n > 0) {
      SpanRect &g = out[n - 1];
      if (g.y + g.h == row) { // fila contigua: juntarla si sale mas barato
        const int16_t ux0 = min(g.x, x0), ux1 = max((int16_t)(g.x + g.w - 1), x1);
        const uint32_t merged = (uint32_t)(ux1 - ux0 + 1) * (g.h + 1);
        const uint32_t apart = (uint32_t)g.w * g.h + rowW + WINDOW_COST_PX;
        if (merged <= apart || n == MAX_DIFF_RECTS) {
          cost += merged - (uint32_t)g.w * g.h;
          g.x = ux0; g.w = ux1 - ux0 + 1; g.h++;
          continue;
        }
      } else if (n == MAX_DIFF_RECTS) { // sin lugar: estirar el ultimo
        const int16_t ux0 = min(g.x, x0), ux1 = max((int16_t)(g.x + g.w - 1), x1);
        cost -= (uint32_t)g.w * g.h;
        g.x = ux0; g.w = ux1 - ux0 + 1; g.h = row - g.y + 1;
        cost += (uint32_t)g.w * g.h;
        continue;
      }
    }
    out[n++] = { x0, row, rowW, 1 };
    cost += rowW + WINDOW_COST_PX;
  }
  return n;
}

void pushSpriteRects(const FaceSprite &s, const SpanRect *rects, uint8_t n) {
  for (uint8_t i = 0; i < n; i++) {
    pushSpriteRect(s, rects[i].x, rects[i].y, rects[i].w, rects[i].h);
  }
}

// Descuenta del presupuesto del frame (sin pasarse de 0 cuando se forzo).
void spendFramePx(uint32_t cost) {
  framePxLeft = cost < framePxLeft ? framePxLeft - cost : 0;
}

// Lleva la boca al nivel pedido. `force`: mandarla aunque no entre en el
// presupuesto (solo si en este frame no salio nada todavia, para que nunca
// quede trabada). Devuelve true si mando algo.
bool stepSpriteMouth(uint8_t level, bool force) {
  if (level == drawnMouthLevel) return false;
  const FaceSprite &from = spriteFace->mouths[drawnMouthLevel];
  const FaceSprite &to = spriteFace->mouths[level];
  uint32_t cost;
  const uint8_t n = planSpriteDiff(from, to, diffRectsA, cost);
  if (cost > framePxLeft && !force) return false;
  spendFramePx(cost);
  pushSpriteRects(to, diffRectsA, n);
  drawnMouthLevel = level;
  return true;
}

// Los dos ojos se mandan juntos o ninguno, para que no queden desparejos.
bool stepSpriteEyes(uint8_t state, bool force) {
  if (state == drawnEyeState) return false;
  uint32_t costL, costR;
  const uint8_t nL = planSpriteDiff(spriteFace->eyesL[drawnEyeState], spriteFace->eyesL[state], diffRectsA, costL);
  const uint8_t nR = planSpriteDiff(spriteFace->eyesR[drawnEyeState], spriteFace->eyesR[state], diffRectsB, costR);
  if (costL + costR > framePxLeft && !force) return false;
  spendFramePx(costL + costR);
  pushSpriteRects(spriteFace->eyesL[state], diffRectsA, nL);
  pushSpriteRects(spriteFace->eyesR[state], diffRectsB, nR);
  drawnEyeState = state;
  eyeDrawnMs = millis();
  return true;
}

// Avanza el parpadeo de la cara dibujada: el siguiente paso de la secuencia
// arranca cuando el actual ya se dibujo y cumplio su tiempo.
uint8_t spriteEyeTarget() {
  if (spriteBlinkStep >= 0 &&
      drawnEyeState == SPRITE_BLINK_SEQ[spriteBlinkStep] &&
      millis() - eyeDrawnMs >= SPRITE_BLINK_HOLD_MS[spriteBlinkStep]) {
    spriteBlinkStep++;
    if (spriteBlinkStep >= SPRITE_BLINK_STEPS) spriteBlinkStep = -1;
  }
  return spriteBlinkStep < 0 ? FACE_EYE_OPEN : SPRITE_BLINK_SEQ[spriteBlinkStep];
}

// Si los ojos esperan tantos frames seguidos, pasan antes que la boca: con
// la boca cambiando casi en cada frame al hablar, si no, nunca entrarian.
const uint8_t EYES_MAX_WAIT_FRAMES = 2;

void renderSpriteFace() {
  const uint8_t eyeTarget = spriteEyeTarget();
  const uint8_t mouthTarget = currentMouthLevel();
  bool sent = false;

  // Presupuesto del frame: si lo primero ya lo gasta entero, lo segundo
  // espera. Nada se pierde, solo se atrasa (se dibuja el ultimo estado pedido).
  const bool eyesFirst = eyesWaitFrames >= EYES_MAX_WAIT_FRAMES;
  if (!eyesFirst) sent = stepSpriteMouth(mouthTarget, true);

  if (eyeTarget != drawnEyeState) {
    if (stepSpriteEyes(eyeTarget, !sent)) {
      sent = true;
      eyesWaitFrames = 0;
    } else {
      eyesWaitFrames++;
    }
  }

  if (eyesFirst) stepSpriteMouth(mouthTarget, !sent);
}

// Manda un rectangulo de una imagen paletizada (indices de 8 bits, w*h
// seguidos) con el camino rapido de pushSpriteRect(). Lo usan la bienvenida y
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
  if (want == NO_PENDING_AVISO && avisoOn && millis() - avisoStartMs >= AVISO_MAX_MS) {
    want = 0;  // Python no lo apago: se sigue adelante igual
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
    // la pantalla entera por su cuenta.
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
    if (currentPersona < FACE_PALETTES_COUNT) overlayPersona = currentPersona;
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
    if (!menuOn && currentPersona < FACE_PALETTES_COUNT) overlayPersona = currentPersona;
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
  qrPending = false;
  qrSize = qrSizePending;
  if (qrSize > 0) {
    if (splashOn || avisoOn) return;  // el aviso y la bienvenida mandan
    if (!qrOn && currentPersona < FACE_PALETTES_COUNT) overlayPersona = currentPersona;
    qrOn = true;
    drawQr();
  } else if (qrOn) {
    qrOn = false;
    currentPersona = 255;
    applyPersonaColors(overlayPersona);
  }
}

// Muestra/saca la tarjeta, ya en el hilo de loop() (dueño del SPI).
void consumePendingCard() {
  const uint8_t want = pendingCard;
  if (want == NO_PENDING_CARD) return;
  pendingCard = NO_PENDING_CARD;

  if (want < CARD_COUNT) {
    if (splashOn) return; // con la bienvenida en pantalla no se tapa
    if (!cardOn && currentPersona < FACE_PALETTES_COUNT) overlayPersona = currentPersona;
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

// Llamada desde Python con Bridge.notify("mouth_level", 0..4) mientras suena
// la voz. Misma regla que face_gesture(): solo anota el byte, loop() dibuja.
void mouth_level(uint8_t level) {
  mouthLevel = level;
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

  const uint8_t personaId = encoded / GESTURE_COUNT;
  const uint8_t gesture = encoded % GESTURE_COUNT;

  // Con la bienvenida, una tarjeta o el menu en pantalla, el guia se anota y
  // aparece al salir de ahi (los servos y lo demas siguen igual).
  if (splashOn || cardOn || menuOn) overlayPersona = personaId < FACE_PALETTES_COUNT ? personaId : 0;
  else applyPersonaColors(personaId);

  talking = (gesture != G_REST);
  if (!talking) mouthLevel = 0; // por si el ultimo "mouth_level" no fue 0
  if (gesture == G_HAPPY || gesture == G_SURPRISE) {
    triggerBlink();
    browPulseUntil = millis() + BROW_PULSE_MS;
  }
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
  // no se pudo crear, mejor saberlo por el monitor que ver media cara.
  if (!eyeLCanvas.ready() || !eyeRCanvas.ready() || !browLCanvas.ready() ||
      !browRCanvas.ready() || !mouthCanvas.ready()) {
    Serial.println("[chat-bang] ERROR: no alcanzo la RAM para los canvas de la cara");
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

  updateBlinkPhase();
  updateAutoBlink();
  updateMouthTalk();
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
  } else if (spriteFace) {
    framePxLeft = FRAME_PX_BUDGET;
    renderSpriteFace();
  } else {
    renderGeometricFace();
  }

  uint32_t frameUs = micros() - frameStartUs;
  if (frameUs > statsWorstFrameUs) statsWorstFrameUs = frameUs;
  statsFrames++;

  reportStats();
}

// La cara de siempre (rectangulos redondeados), para los guias sin PNG.
void renderGeometricFace() {
  // Presupuesto del frame. Lo que no entra no se pierde: la parte queda
  // "sucia" (no se actualiza su lastDrawn*) y se manda en el frame siguiente.
  framePxLeft = forceFullRedraw ? 0xFFFFFFFFu : FRAME_PX_BUDGET;

  // La boca va primero: es la que se mueve todo el tiempo mientras habla, y
  // es la que mas se nota si se atrasa.
  int16_t mouthH = (int16_t)(mouthCurrentH + 0.5f);
  if (mouthH != lastDrawnMouthH) {
    ChangeBands b = bandsFor(MOUTH_REGION_H, lastDrawnMouthH, mouthH, MOUTH_R);
    if (b.px(MOUTH_REGION_W) <= framePxLeft) {
      framePxLeft -= b.px(MOUTH_REGION_W);
      composeMouth(mouthH);
      flushBands(mouthCanvas, b);
      lastDrawnMouthH = mouthH;
    }
  }

  // Los dos ojos se mandan juntos o ninguno, para que no queden desparejos.
  int16_t eyeH = currentEyeHeight();
  if (eyeH != lastDrawnEyeH) {
    ChangeBands b = bandsFor(EYE_REGION_H, lastDrawnEyeH, eyeH, EYE_R);
    const uint32_t cost = 2u * b.px(EYE_REGION_W);
    if (cost <= framePxLeft) {
      framePxLeft -= cost;
      composeEye(eyeLCanvas, eyeH);
      composeEye(eyeRCanvas, eyeH);
      flushBands(eyeLCanvas, b);
      flushBands(eyeRCanvas, b);
      lastDrawnEyeH = eyeH;
    }
  }

  // Las cejas cambian solo en el pulso de HAPPY/SURPRISE (2 veces por frase),
  // asi que se manda la region entera; una por frame si no entran las dos.
  int16_t browLift = (millis() < browPulseUntil) ? BROW_PULSE_LIFT_PX : 0;
  if (browLift != lastDrawnBrowLift) {
    const uint32_t one = (uint32_t)BROW_REGION_W * BROW_REGION_H;
    if (2u * one <= framePxLeft) {
      framePxLeft -= 2u * one;
      composeBrow(browLCanvas, browLift);
      composeBrow(browRCanvas, browLift);
      browLCanvas.flush();
      browRCanvas.flush();
      lastDrawnBrowLift = browLift;
    }
  }

  forceFullRedraw = false;
}
