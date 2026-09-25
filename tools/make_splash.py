# Convierte assets/img/menu/Bang.gif (la bienvenida al BANG) en
# sketch/bang_splash.h para la pantalla TFT del MCU.
#
# Mismo problema de bus que las caras (ver make_face_sprites.py y sketch.ino):
# la pantalla completa son 153.600 bytes (~0,6 s de SPI), asi que se manda
# UNA vez y despues, cuadro a cuadro, solo lo que cambia (el avioncito de
# papel y su estela). El GIF es de 836x470 (16:9): se recorta al centro a 4:3
# (el texto entra justo) y se escala a 320x240.
#
# Formato: indices de 8 bits a una paleta de 256 colores (RGB565), comun a
# todos los cuadros para que el fondo no "titile" entre uno y otro.
#
#   docker exec robot-bang-3-main-1 /app/.cache/.venv/bin/python /app/tools/make_splash.py
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "img", "menu", "Bang.gif")
OUT = os.path.join(ROOT, "sketch", "bang_splash.h")
PREVIEW = os.path.join(ROOT, "tools", "bang_splash_preview.png")

W, H = 320, 240
BAND_H = 6  # alto de las franjas en que se parte cada cambio (ver bands())


def load_frames():
    im = Image.open(SRC)
    cw = round(im.height * W / H)
    x0 = (im.width - cw) // 2
    frames, durations = [], []
    for i in range(im.n_frames):
        im.seek(i)
        durations.append(im.info.get("duration", 70))
        f = im.convert("RGB").crop((x0, 0, x0 + cw, im.height))
        frames.append(f.resize((W, H), Image.LANCZOS))
    return frames, durations


def bands(changed):
    """Rectangulos (x, y, w, h) que cubren los pixeles cambiados: franjas de
    BAND_H filas, cada una con su propio ancho. Un rectangulo unico sobre la
    estela diagonal del avion mandaria 8 veces mas pixeles."""
    rects = []
    for y in range(0, H, BAND_H):
        cols = np.nonzero(changed[y:y + BAND_H].any(0))[0]
        if len(cols) == 0:
            continue
        rows = np.nonzero(changed[y:y + BAND_H].any(1))[0]
        x, x1 = int(cols.min()), int(cols.max()) + 1
        ry, ry1 = y + int(rows.min()), y + int(rows.max()) + 1
        rects.append((x, ry, x1 - x, ry1 - ry))
    return rects


def rgb565(c):
    r, g, b = (int(v) for v in c)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def c_bytes(name, data):
    lines = [f"static const uint8_t {name}[{len(data)}] = {{"]
    for i in range(0, len(data), 24):
        lines.append("  " + ",".join(f"0x{v:02x}" for v in data[i:i + 24]) + ",")
    lines.append("};")
    return lines


def main():
    frames, durations = load_frames()
    pal_img = frames[0].quantize(256, method=Image.Quantize.MEDIANCUT)
    idx = [np.array(f.quantize(palette=pal_img, dither=Image.Dither.NONE)) for f in frames]
    pal = np.array(pal_img.getpalette()[:768], dtype=np.uint8).reshape(-1, 3)
    pal = np.vstack([pal, np.zeros((256 - len(pal), 3), np.uint8)])

    out = [
        "/*",
        "  GENERADO por tools/make_splash.py a partir de assets/img/menu/Bang.gif.",
        "  No editar a mano: cambiar el GIF (o el script) y volver a generarlo.",
        "",
        "  Pantalla de bienvenida: indices de 8 bits a SPLASH_PALETTE (RGB565).",
        "  SPLASH_BASE es la pantalla entera (cuadro 0); cada cuadro siguiente",
        "  trae solo los rectangulos que cambian respecto del anterior (el ultimo",
        "  vuelve al cuadro 0, asi el bucle cierra).",
        "*/",
        "",
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        "struct SplashRect {",
        "  int16_t x, y, w, h;",
        "  uint32_t offset; // en SPLASH_PX: w*h indices, fila por fila",
        "};",
        "",
        "struct SplashFrame {",
        "  uint16_t firstRect, rectCount;",
        "  uint16_t durationMs; // cuanto se muestra este cuadro",
        "};",
        "",
        f"const int16_t SPLASH_W = {W};",
        f"const int16_t SPLASH_H = {H};",
        "",
        "static const uint16_t SPLASH_PALETTE[256] = {",
    ]
    for i in range(0, 256, 12):
        out.append("  " + ",".join(f"0x{rgb565(c):04x}" for c in pal[i:i + 12]) + ",")
    out.append("};")
    out.append("")
    out += c_bytes("SPLASH_BASE", idx[0].astype(np.uint8).ravel().tolist())
    out.append("")

    px, rects, frame_rows = [], [], []
    n = len(idx)
    worst = 0
    for i in range(n):
        # El cuadro i se dibuja encima del i-1; el "cuadro n" es volver al 0.
        cur, prev = idx[(i + 1) % n], idx[i]
        first = len(rects)
        cost = 0
        for (x, y, w, h) in bands(cur != prev):
            rects.append((x, y, w, h, len(px)))
            px += cur[y:y + h, x:x + w].astype(np.uint8).ravel().tolist()
            cost += w * h
        worst = max(worst, cost)
        frame_rows.append((first, len(rects) - first, durations[(i + 1) % n]))

    out += c_bytes("SPLASH_PX", px or [0])
    out.append("")
    out.append(f"static const SplashRect SPLASH_RECTS[{max(len(rects), 1)}] = {{")
    for r in rects or [(0, 0, 0, 0, 0)]:
        out.append("  {%d, %d, %d, %d, %d}," % r)
    out.append("};")
    out.append("")
    out.append("// SPLASH_FRAMES[i] lleva la pantalla del cuadro i al i+1 del GIF.")
    out.append(f"static const SplashFrame SPLASH_FRAMES[{n}] = {{")
    for f in frame_rows:
        out.append("  {%d, %d, %d}," % f)
    out.append("};")
    out.append(f"const uint16_t SPLASH_FRAME_COUNT = {n};")
    out.append(f"const uint32_t SPLASH_WORST_FRAME_PX = {worst}; // referencia, no se usa")
    out.append("")

    with open(OUT, "w") as fh:
        fh.write("\n".join(out))

    # Vista previa: cuadro 0 y cuadro del medio tal como quedan en la TFT
    # (paleta RGB565 incluida), reconstruidos a partir de los datos.
    lut = np.array([[((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31]
                    for v in (rgb565(c) for c in pal)], np.uint8)
    screen = idx[0].copy()
    shots = [lut[screen]]
    for i in range(n // 2):
        first, count, _ = frame_rows[i]
        for (x, y, w, h, off) in rects[first:first + count]:
            screen[y:y + h, x:x + w] = np.array(px[off:off + w * h]).reshape(h, w)
    assert (screen == idx[n // 2]).all(), "los diffs no reconstruyen el cuadro"
    shots.append(lut[screen])
    Image.fromarray(np.hstack(shots)).save(PREVIEW)

    total = 256 * 2 + W * H + len(px) + len(rects) * 12 + n * 6
    print(f"{OUT}: {n} cuadros, {len(rects)} rectangulos, peor cuadro {worst} px, "
          f"{total} bytes de flash")


if __name__ == "__main__":
    main()
