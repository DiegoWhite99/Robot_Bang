# Convierte assets/img/aviso/robot-bang-videojuego.gif (el aviso de seguridad
# que sale ANTES de la bienvenida) en sketch/bang_aviso.h para la pantalla TFT.
#
# Mismo formato y mismo truco que make_splash.py: indices de 8 bits a una
# paleta comun de 256 colores en RGB565, una pantalla base y despues solo los
# rectangulos que cambian de un cuadro al siguiente. Ver la cabecera de
# sketch.ino para por que no se puede mandar la pantalla entera cada cuadro.
#
# Diferencias con make_splash.py:
#   - el GIF es VERTICAL (800x880) y la pantalla horizontal (320x240): se
#     escala entero y se centra, con bandas negras a los lados. Recortar
#     dejaria fuera parte del aviso, que es justo lo que hay que leer.
#   - se toma 1 de cada FRAME_STEP cuadros: 58 cuadros enteros no caben
#     comodos en el flash del MCU junto al GIF de bienvenida y las tarjetas.
#
#   docker exec robot-bang-stable-main-1 /app/.cache/.venv/bin/python /app/tools/make_aviso.py
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "img", "aviso", "robot-bang-videojuego.gif")
OUT = os.path.join(ROOT, "sketch", "bang_aviso.h")
PREVIEW = os.path.join(ROOT, "tools", "bang_aviso_preview.png")

SCREEN_W, SCREEN_H = 320, 240
BAND_H = 6
# 1 de cada 10 cuadros (6 de los 58). El flash del MCU son 768 KB y el sketch
# ya gasta la mayor parte en la bienvenida, las 50 tarjetas y las 5 caras.
# Medido: con FRAME_STEP=3 el binario llegaba al 105% (no entraba) y con 6 se
# quedaba a ~3 KB del limite. Con 10 entra con margen. El aviso se lee igual:
# lo que importa es el texto, no la fluidez.
FRAME_STEP = 10

# Tamaño y posicion reales de la imagen dentro de la pantalla. Se calculan en
# load_frames() y se emiten al .h: NO se guardan las bandas negras laterales,
# que en un GIF vertical sobre pantalla horizontal son un tercio de los
# pixeles. La pantalla se pinta de negro una vez y encima va solo la imagen.
IMG = {}


def load_frames():
    im = Image.open(SRC)
    scale = min(SCREEN_W / im.width, SCREEN_H / im.height)
    nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
    IMG.update(w=nw, h=nh, x=(SCREEN_W - nw) // 2, y=(SCREEN_H - nh) // 2)
    frames, durations = [], []
    for i in range(0, im.n_frames, FRAME_STEP):
        im.seek(i)
        durations.append(im.info.get("duration", 80) * FRAME_STEP)
        frames.append(im.convert("RGB").resize((nw, nh), Image.LANCZOS))
    return frames, durations


def bands(changed):
    H = IMG["h"]
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
        "  GENERADO por tools/make_aviso.py a partir de",
        "  assets/img/aviso/robot-bang-videojuego.gif.",
        "  No editar a mano: cambiar el GIF (o el script) y volver a generarlo.",
        "",
        "  Aviso de seguridad: es la PRIMERA pantalla al arrancar, antes de la",
        "  bienvenida de BANG. Mismo formato que bang_splash.h, pero aqui se",
        "  guarda SOLO la imagen (AVISO_W x AVISO_H) y no la pantalla entera:",
        "  el GIF es vertical y las bandas negras de los lados se pintan una",
        "  vez con fillScreen(). Las coordenadas van desplazadas AVISO_X/Y.",
        "*/",
        "",
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        "struct AvisoRect {",
        "  int16_t x, y, w, h;",
        "  uint32_t offset;",
        "};",
        "",
        "struct AvisoFrame {",
        "  uint16_t firstRect, rectCount;",
        "  uint16_t durationMs;",
        "};",
        "",
        f"const int16_t AVISO_W = {IMG['w']};",
        f"const int16_t AVISO_H = {IMG['h']};",
        f"const int16_t AVISO_X = {IMG['x']};  // donde se pega en la pantalla",
        f"const int16_t AVISO_Y = {IMG['y']};",
        "",
        "static const uint16_t AVISO_PALETTE[256] = {",
    ]
    for i in range(0, 256, 12):
        out.append("  " + ",".join(f"0x{rgb565(c):04x}" for c in pal[i:i + 12]) + ",")
    out.append("};")
    out.append("")
    out += c_bytes("AVISO_BASE", idx[0].astype(np.uint8).ravel().tolist())
    out.append("")

    px, rects, frame_rows = [], [], []
    n = len(idx)
    worst = 0
    for i in range(n):
        cur, prev = idx[(i + 1) % n], idx[i]
        first = len(rects)
        cost = 0
        for (x, y, w, h) in bands(cur != prev):
            rects.append((x, y, w, h, len(px)))
            px += cur[y:y + h, x:x + w].astype(np.uint8).ravel().tolist()
            cost += w * h
        worst = max(worst, cost)
        frame_rows.append((first, len(rects) - first, durations[(i + 1) % n]))

    out += c_bytes("AVISO_PX", px or [0])
    out.append("")
    out.append(f"static const AvisoRect AVISO_RECTS[{max(len(rects), 1)}] = {{")
    for r in rects or [(0, 0, 0, 0, 0)]:
        out.append("  {%d, %d, %d, %d, %d}," % r)
    out.append("};")
    out.append("")
    out.append(f"static const AvisoFrame AVISO_FRAMES[{n}] = {{")
    for f in frame_rows:
        out.append("  {%d, %d, %d}," % f)
    out.append("};")
    out.append(f"const uint16_t AVISO_FRAME_COUNT = {n};")
    out.append("")

    with open(OUT, "w") as fh:
        fh.write("\n".join(out))

    lut = np.array([[((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31]
                    for v in (rgb565(c) for c in pal)], np.uint8)
    Image.fromarray(lut[idx[0]]).save(PREVIEW)

    total = 256 * 2 + IMG["w"] * IMG["h"] + len(px) + len(rects) * 12 + n * 6
    print(f"{OUT}: {n} cuadros, {len(rects)} rectangulos, peor cuadro {worst} px, "
          f"{total} bytes de flash ({total / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
