# Convierte las tarjetas BANG (assets/img/tarjetas/<Guia>/<guia>-N.png, 50 en
# total) en sketch/bang_cards.h, para mostrarlas en la pantalla TFT.
#
# Las tarjetas son verticales (828x1536) y la pantalla es horizontal (320x240):
# achicada entera, la tarjeta queda de 129 px de ancho y el texto no se lee.
# Por eso se rearma en horizontal: arriba la franja de la cabecera (nombre +
# dibujo del guia), el color del cuerpo de fondo y en el centro el texto,
# recortado del PNG y ampliado todo lo que entre.
#
# Para que las 50 entren en la flash (crudas serian 3,8 MB):
#   - La cabecera es igual en las 10 tarjetas de un guia: va UNA vez por guia,
#     16 colores + RLE de 4 bits (token: nibble alto = color, nibble bajo n:
#     n < 15 -> n + 1 pixeles; n == 15 -> 16 + el byte siguiente).
#   - El cuerpo es color de fondo + texto de un solo color: basta la opacidad
#     del texto en 4 niveles, con RLE de 2 bits (token: 2 bits altos = nivel,
#     6 bajos n: n < 63 -> n + 1 pixeles; n == 63 -> 64 + el byte siguiente).
# Las corridas siguen de una fila a la otra.
#
#   docker exec robot-bang-3-main-1 /app/.cache/.venv/bin/python /app/tools/make_cards.py
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "assets", "img", "tarjetas")
OUT = os.path.join(ROOT, "sketch", "bang_cards.h")
PREVIEW = os.path.join(ROOT, "tools", "cards_preview.png")

# Mismo orden que PERSONA_IDS de python/gestures.py (y P_* del sketch).
GUIAS = ["crispi", "carmel", "cesia", "cori", "cristal"]
CARDS_PER_GUIDE = 10
W, H = 320, 240
HEADER_FRAC = 0.075  # alto de la cabecera en el PNG original
TEXT_BOX = (296, 150)  # lo maximo que puede ocupar el texto en pantalla
MARGIN = 20  # px del PNG que se ignoran en los bordes del cuerpo


def load(persona, n):
    """El PNG sobre el color de su propio cuerpo (las esquinas son
    transparentes) y ese color."""
    rgba = Image.open(os.path.join(SRC, persona.capitalize(), f"{persona}-{n}.png")).convert("RGBA")
    a = np.asarray(rgba)
    body = a[a.shape[0] // 2 - 200 : a.shape[0] // 2 - 150, 40:-40, :3].reshape(-1, 3)
    bg = tuple(int(v) for v in np.median(body, axis=0))
    flat = Image.new("RGBA", rgba.size, bg + (255,))
    return Image.alpha_composite(flat, rgba).convert("RGB"), np.array(bg)


def header_of(card):
    w, h = card.size
    head_h = int(h * HEADER_FRAC)
    hh = round(head_h * W / w)
    return card.crop((0, 0, w, head_h)).resize((W, hh), Image.LANCZOS)


def body_alpha(card, bg, hh):
    """Opacidad del texto (0..3) en el cuerpo de la pantalla (H - hh filas) y
    el color del texto."""
    a = np.asarray(card).astype(np.float32)
    h, w = a.shape[:2]
    head_h = int(h * HEADER_FRAC)
    body = a[head_h + MARGIN : h - 3 * MARGIN, MARGIN : w - MARGIN]
    dist = np.abs(body - bg).sum(2)
    ys, xs = np.nonzero(dist > 60)
    out = np.zeros((H - hh, W), np.uint8)
    if len(ys) == 0:
        return out, bg
    # El color del texto: el pixel mas lejos del fondo.
    ink = body[ys, xs][dist[ys, xs].argmax()]
    pad = 6
    x0, x1 = xs.min() + MARGIN - pad, xs.max() + MARGIN + 1 + pad
    y0, y1 = ys.min() + head_h + MARGIN - pad, ys.max() + head_h + MARGIN + 1 + pad
    text = card.crop((x0, y0, x1, y1))
    k = min(TEXT_BOX[0] / text.width, TEXT_BOX[1] / text.height)
    text = text.resize((max(1, round(text.width * k)), max(1, round(text.height * k))), Image.LANCZOS)
    t = np.asarray(text).astype(np.float32)
    d = ink - bg
    alpha = np.clip(((t - bg) @ d) / float(d @ d), 0, 1)
    top = (H - hh - text.height) // 2
    left = (W - text.width) // 2
    out[top : top + text.height, left : left + text.width] = np.rint(alpha * 3).astype(np.uint8)
    return out, ink


def runs(flat):
    """(valor, largo) de cada corrida."""
    edges = np.flatnonzero(np.diff(flat)) + 1
    starts = np.concatenate([[0], edges])
    ends = np.concatenate([edges, [len(flat)]])
    return [(int(flat[s]), int(e - s)) for s, e in zip(starts, ends)]


def rle(flat, value_bits):
    """Tokens de 1 byte: `value_bits` altos = valor, el resto = largo - 1; el
    largo maximo del token significa "sigue un byte extra"."""
    len_bits = 8 - value_bits
    esc = (1 << len_bits) - 1
    top = esc + 1 + 255
    data = []
    for v, n in runs(flat):
        while n:
            r = min(n, top)
            if r <= esc:
                data.append((v << len_bits) | (r - 1))
            else:
                data += [(v << len_bits) | esc, r - esc - 1]
            n -= r
    return data


def rgb565(c):
    r, g, b = (int(v) for v in c)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def from565(v):
    return ((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63, (v & 31) * 255 // 31


def c_bytes(name, data):
    lines = [f"static const uint8_t {name}[{len(data)}] = {{"]
    for i in range(0, len(data), 24):
        lines.append("  " + ",".join(f"0x{v:02x}" for v in data[i : i + 24]) + ",")
    lines.append("};")
    return lines


def main():
    headers, cards, blob, previews = [], [], [], []
    for persona in GUIAS:
        first, _ = load(persona, 1)
        head = header_of(first)
        q = head.quantize(16, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        pal = np.array(q.getpalette()[:48], np.uint8).reshape(-1, 3)
        pal = np.vstack([pal, np.zeros((16 - len(pal), 3), np.uint8)])
        idx = np.asarray(q)
        data = rle(idx.ravel(), 4)
        headers.append(([rgb565(c) for c in pal], head.height, len(blob), len(data)))
        blob += data
        head_rgb = np.array([from565(rgb565(c)) for c in pal], np.uint8)[idx]

        for n in range(1, CARDS_PER_GUIDE + 1):
            card, bg = load(persona, n)
            alpha, ink = body_alpha(card, bg, head.height)
            data = rle(alpha.ravel(), 2)
            cards.append((len(headers) - 1, rgb565(bg), rgb565(ink), len(blob), len(data)))
            blob += data
            # Vista previa con la misma mezcla que hace el sketch.
            b, i = np.array(from565(rgb565(bg)), float), np.array(from565(rgb565(ink)), float)
            lut = np.array([b + (i - b) * k / 3 for k in range(4)], np.uint8)
            previews.append(Image.fromarray(np.vstack([head_rgb, lut[alpha]])))

    out = [
        "/*",
        "  GENERADO por tools/make_cards.py a partir de assets/img/tarjetas/.",
        "  No editar a mano: cambiar las imagenes (o el script) y volver a generarlo.",
        "",
        "  Tarjetas BANG rearmadas en horizontal para la TFT (320x240). Cada una es",
        "  la cabecera de su guia (16 colores, RLE de 4 bits) + el cuerpo (fondo y",
        "  texto, opacidad en 4 niveles, RLE de 2 bits); ver el script.",
        "  Indice = guia * 10 + (numero - 1), con el orden de PERSONA_IDS de",
        "  python/gestures.py.",
        "*/",
        "",
        "#pragma once",
        "",
        "#include <stdint.h>",
        "",
        "struct CardHeader {",
        "  uint16_t palette[16];",
        "  int16_t h;             // alto en pantalla (ancho = CARD_W)",
        "  uint32_t offset, size; // en CARDS_RLE",
        "};",
        "",
        "struct BangCard {",
        "  uint8_t header;        // en CARD_HEADERS",
        "  uint16_t bg, ink;      // fondo y texto del cuerpo",
        "  uint32_t offset, size; // en CARDS_RLE",
        "};",
        "",
        f"const int16_t CARD_W = {W};",
        f"const int16_t CARD_H = {H};",
        f"const uint8_t CARD_COUNT = {len(cards)};",
        "",
    ]
    out += c_bytes("CARDS_RLE", blob)
    out.append("")
    out.append(f"static const CardHeader CARD_HEADERS[{len(headers)}] = {{")
    for pal, hh, off, size in headers:
        out.append("  {{" + ",".join(f"0x{v:04x}" for v in pal) + f"}}, {hh}, {off}, {size}}},")
    out.append("};")
    out.append("")
    out.append(f"static const BangCard CARDS[{len(cards)}] = {{")
    for c in cards:
        out.append("  {%d, 0x%04x, 0x%04x, %d, %d}," % c)
    out.append("};")
    out.append("")
    with open(OUT, "w") as fh:
        fh.write("\n".join(out))

    sheet = Image.new("RGB", (W // 2 * 10, H // 2 * 5), "white")
    for i, p in enumerate(previews):
        sheet.paste(p.resize((W // 2, H // 2), Image.LANCZOS), ((i % 10) * W // 2, (i // 10) * H // 2))
    sheet.save(PREVIEW)
    biggest = max(c[4] for c in cards)
    print(f"{OUT}: {len(cards)} tarjetas, {len(blob)} bytes de RLE (cuerpo mas grande: {biggest})")


if __name__ == "__main__":
    main()
