# Convierte los frames PNG de los guias (assets/img/<guia>/) en bitmaps para
# la pantalla TFT del MCU: genera sketch/<guia>_face.h.
#
# La TFT va por SPI a ~4 us/byte (ver sketch.ino), asi que NO se puede
# mandar una imagen entera por frame. Por eso la cara se parte en:
#   - base:   la pantalla completa (ojos abiertos, boca cerrada). Se manda
#             una sola vez, al cambiar de guia.
#   - ojos:   un sprite por ojo y por estado de parpadeo (abierto,
#             entrecerrado, cerrado), del tamano justo de lo que cambia.
#   - boca:   un sprite por nivel de apertura (0 = la sonrisa original).
#             Los PNG no traen boca hablando, asi que los niveles 1..N se
#             dibujan aqui a partir de la propia sonrisa: el labio de
#             arriba es el trazo original y el de abajo se "descuelga"
#             con el mismo grosor de linea, para que no desentone.
#
# Los pixeles se guardan en 2 bits y el sketch los pinta con los colores de
# faces_colors.h, asi que cambiar el color de la cara no exige volver a
# correr esto. Dos modos:
#   - una tinta (casi todos): 0 = fondo ... 3 = trazo lleno (antialiasing).
#   - dos tintas (Carmel, que tiene el bigote verde): 0 = fondo, 1 = medio
#     trazo, 2 = trazo (color de OJOS), 3 = segunda tinta (color de BOCA).
#
# Crispi tiene sus parametros medidos a mano (sus PNG son de otro tamano y
# ya estaba afinado). Los demas se calculan solos a partir de los trazos:
# los PNG de cada frame estan redibujados (cambian un poco de tamano y
# posicion, y hasta la nariz y la boca), asi que de cada frame se toman
# SOLO los ojos, alineados y escalados con la ceja del mismo lado, y todo
# lo demas sale del frame de ojos abiertos.
#
# Se corre DENTRO del contenedor de la App (ahi estan numpy, PIL y cv2):
#   docker exec robot-bang-3-main-1 python3 /app/tools/make_face_sprites.py [guia ...]

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

APP = Path(__file__).resolve().parent.parent
SCREEN_W, SCREEN_H = 320, 240
SCREEN_MARGIN = 6  # px libres alrededor de la cara (con la boca bien abierta)
MOUTH_FILL_ALPHA = 0.34  # interior de la boca abierta: un tinte claro del color
MOUTH_OPEN_SCREEN = [0, 8, 14, 20, 26]  # apertura de cada nivel, px de PANTALLA

# frames = (abierto, entrecerrado, cerrado). Si un guia no trae ojo
# entrecerrado se repite el cerrado: el parpadeo queda en abierto-cerrado.
PERSONAS = {
    "crispi": {
        "frames": ["crispi-01.png", "crispi-02.png", "crispi-03.png"],
        # (crispi-04 y crispi-05 son copias de 02 y 01: la secuencia de
        # parpadeo 1-2-3-4-5 la arma el sketch con estos 3 estados.)
        "manual": {
            # Escala y encuadre (en px del PNG original, 1059x786). La escala
            # deja la cara, con la boca en su maxima apertura, entera dentro
            # de los 240 px de alto de la pantalla.
            "scale": 0.38,
            "center": (528.5, 426.0),
            # Sonrisa: tramo "parseable" por columnas (a la derecha de x=675
            # empieza el ganchito del final, que se deja tal cual).
            "smile_x": (378, 672),
            "smile_y": (560, 660),
            "stroke": 21,  # grosor del trazo medido en la nariz y la sonrisa
            "open_px": [0, 20, 36, 52, 68],  # apertura de cada nivel, px del PNG
            # Zona de los ojos (x0, y0, x1, y1 en px del PNG). Los PNG se
            # exportaron por separado y su antialiasing difiere un poco en
            # TODA la imagen (nariz, cejas): de cada frame solo se toma esta
            # zona, y el resto sale del frame 1.
            "eye_zone": (250, 188, 815, 420),
        },
    },
    # Carmel: 01 trae otro bigote; 02 = abiertos, 03 = parpado a media asta,
    # 05 = cerrados relajados (04 son cerrados "felices", mas arriba).
    "carmel": {"frames": ["carmel-02.png", "carmel-03.png", "carmel-05.png"], "two_inks": True},
    # Cesia, Cori y Cristal son el mismo dibujo en otro color, con los
    # frames en otro orden. Sin ojo entrecerrado (03 son ojos "felices" ^^).
    "cesia": {"frames": ["casia-04.png", "cesia-01.png", "cesia-01.png"]},
    "cori": {"frames": ["cori-04.png", "cori-01.png", "cori-01.png"]},
    "cristal": {"frames": ["cristal-04.png", "cristal-01.png", "cristal-01.png"]},
}


# --- Carga -------------------------------------------------------------------


def load_inks(persona, name, two_inks):
    """Devuelve (tinta1, tinta2): opacidad 0..1 de cada color del dibujo."""
    rgba = np.array(Image.open(APP / "assets/img" / persona / name).convert("RGBA")).astype(np.float32)
    a = rgba[..., 3] / 255.0
    if not two_inks:
        return a, np.zeros_like(a)
    # El verde del bigote contra el amarillo del resto: el amarillo tiene
    # rojo alto, el verde casi nada.
    green = (rgba[..., 1] - rgba[..., 0]) > 60
    return a * ~green, a * green


# --- Composicion automatica (todos menos Crispi) -----------------------------


def components(a):
    """Trazos separados del dibujo: lista de (bbox x0, y0, x1, y1, mascara)."""
    m = (a > 0.5).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if area < 300:  # motas (el brillo del ojo, si quedara suelto, va con el ojo)
            continue
        out.append(((x, y, x + w, y + h), lab == i))
    return out


def classify(ink1, ink2):
    """Separa los trazos de un frame en cejas (izq, der), ojos y "el resto".

    Cejas = los dos trazos de mas arriba. Boca = el mas bajo. Nariz = el del
    medio. Lo que queda (entre cejas y boca, a los costados) son los ojos,
    con pestanas, pupila y brillo incluidos.
    """
    h, w = ink1.shape
    comps = components(np.maximum(ink1, ink2))
    comps.sort(key=lambda c: c[0][1])
    brows = sorted(comps[:2], key=lambda c: c[0][0])
    assert brows[0][0][2] < w / 2 < brows[1][0][0], "no encontre las dos cejas"
    ink1_only = [c for c in comps[2:] if (ink1[c[1]] > 0.5).mean() > 0.5]
    mouth = max(ink1_only, key=lambda c: c[0][3])
    eyes = np.zeros((h, w), dtype=bool)
    rest = np.zeros((h, w), dtype=bool)
    for c in comps:
        (x0, y0, x1, y1), m = c
        cx = (x0 + x1) / 2
        is_eye = (
            c is not mouth
            and all(c is not b for b in brows)
            and abs(cx - w / 2) > 0.1 * w  # la nariz va al medio
            and (ink1[m] > 0.5).mean() > 0.5  # el bigote (tinta 2) no es ojo
            and y1 < mouth[0][1]
        )
        (eyes if is_eye else rest)[m] = True
    # Las mascaras se agrandan un poco para llevarse el antialiasing del borde.
    k = np.ones((7, 7), np.uint8)
    grow = lambda m: cv2.dilate(m.astype(np.uint8), k).astype(bool)
    return brows, grow(eyes), grow(rest), mouth


def warp_to(layer, src_brow, dst_brow, x_range):
    """Lleva la mitad x_range de `layer` de la ceja src a la ceja dst
    (escala por el ancho de la ceja + traslacion de su centro)."""
    (sx0, sy0, sx1, sy1), _ = src_brow
    (dx0, dy0, dx1, dy1), _ = dst_brow
    s = (dx1 - dx0) / (sx1 - sx0)
    tx = (dx0 + dx1) / 2 - s * (sx0 + sx1) / 2
    ty = (dy0 + dy1) / 2 - s * (sy0 + sy1) / 2
    half = np.zeros_like(layer)
    half[:, x_range[0] : x_range[1]] = layer[:, x_range[0] : x_range[1]]
    M = np.float32([[s, 0, tx], [0, s, ty]])
    return cv2.warpAffine(half, M, (layer.shape[1], layer.shape[0]), flags=cv2.INTER_LINEAR)


def compose_auto(persona, cfg):
    """Frames (tinta1, tinta2) listos: ojos de cada frame sobre la base."""
    two = cfg.get("two_inks", False)
    frames = [load_inks(persona, n, two) for n in cfg["frames"]]
    base1, base2 = frames[0]
    brows0, eyes0, rest0, mouth0 = classify(base1, base2)
    rest1, rest2 = base1 * rest0, base2 * rest0
    w = base1.shape[1]
    out = []
    for ink1, ink2 in frames:
        brows, eyes, _, _ = classify(ink1, ink2)
        e1, e2 = ink1 * eyes, ink2 * eyes
        f1, f2 = rest1.copy(), rest2.copy()
        for side, xr in ((0, (0, w // 2)), (1, (w // 2, w))):
            f1 = np.maximum(f1, warp_to(e1, brows[side], brows0[side], xr))
            f2 = np.maximum(f2, warp_to(e2, brows[side], brows0[side], xr))
        out.append((f1, f2))
    return out, mouth0


def smile_params(ink1, mouth):
    """Tramo de la sonrisa que se puede leer columna por columna (un solo
    trazo por columna: sin el ganchito del final) y grosor del trazo."""
    (x0, y0, x1, y1), m = mouth
    runs = []
    for x in range(x0, x1):
        col = m[y0:y1, x]
        edges = np.flatnonzero(np.diff(np.concatenate([[0], col.astype(np.int8), [0]])))
        n = len(edges) // 2
        runs.append((edges[1::2] - edges[::2]).sum() if n == 1 else -1)
    # El tramo mas largo de columnas con un solo trazo.
    best, cur = (0, 0), None
    for i, r in enumerate(runs + [-1]):
        if r > 0 and cur is None:
            cur = i
        elif r <= 0 and cur is not None:
            if i - cur > best[1] - best[0]:
                best = (cur, i)
            cur = None
    lens = np.array(runs[best[0] : best[1]])
    stroke = int(round(np.percentile(lens, 20)))
    return (x0 + best[0], x0 + best[1] - 1), (y0, y1), stroke


# --- Boca ----------------------------------------------------------------------


def smile_centerline(a, smile_x, smile_y, mask=None):
    xs = np.arange(smile_x[0], smile_x[1] + 1)
    idx = np.arange(smile_y[0], smile_y[1])
    ys = []
    for x in xs:
        col = a[smile_y[0] : smile_y[1], x]
        if mask is not None:
            col = col * mask[smile_y[0] : smile_y[1], x]
        ys.append(float((col * idx).sum() / col.sum()))
    return xs, np.array(ys)


def mouth_frame(a, open_px, smile_x, smile_y, stroke, mask=None):
    """Frame 'ojos abiertos' con la boca abierta open_px (en px del PNG)."""
    if open_px == 0:
        return a
    xs, ytop = smile_centerline(a, smile_x, smile_y, mask)
    t = (xs - xs[0]) / (xs[-1] - xs[0])
    ybot = ytop + open_px * np.sin(np.pi * t) ** 0.85

    h, w = a.shape
    # Se dibuja a 4x y se reduce, para que el labio nuevo tenga el mismo
    # antialiasing que el trazo original.
    k = 4
    fill = Image.new("L", (w * k, h * k), 0)
    lip = Image.new("L", (w * k, h * k), 0)
    poly = [(x * k, y * k) for x, y in zip(xs, ytop)] + [(x * k, y * k) for x, y in zip(xs[::-1], ybot[::-1])]
    ImageDraw.Draw(fill).polygon(poly, fill=255)
    d = ImageDraw.Draw(lip)
    pts = [(x * k, y * k) for x, y in zip(xs, ybot)]
    d.line(pts, fill=255, width=stroke * k, joint="curve")
    r = stroke * k / 2
    for px, py in (pts[0], pts[-1]):  # puntas redondas, como el resto del dibujo
        d.ellipse((px - r, py - r, px + r, py + r), fill=255)
    fill = np.array(fill.resize((w, h), Image.BOX), dtype=np.float32) / 255.0
    lip = np.array(lip.resize((w, h), Image.BOX), dtype=np.float32) / 255.0
    return np.maximum.reduce([a, fill * MOUTH_FILL_ALPHA, lip])


# --- Pantalla ------------------------------------------------------------------


def to_screen(a, scale, center):
    """Escala al tamano de la pantalla (opacidad 0..1, sin cuantizar)."""
    h, w = a.shape
    sw, sh = round(w * scale), round(h * scale)
    img = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8), "L").resize((sw, sh), Image.LANCZOS)
    ox = round(center[0] * scale - SCREEN_W / 2)
    oy = round(center[1] * scale - SCREEN_H / 2)
    img = img.crop((ox, oy, ox + SCREEN_W, oy + SCREEN_H))
    return np.array(img, dtype=np.float32) / 255.0


def quantize(s1, s2, two_inks):
    if not two_inks:
        return np.clip(np.rint(s1 * 3), 0, 3).astype(np.uint8)
    q = np.clip(np.rint(s1 * 2), 0, 2).astype(np.uint8)
    q[s2 >= 0.5] = 3
    return q


def fit(frames_hi):
    """Escala y centro (px del PNG) para que todo entre en la pantalla."""
    ys, xs = np.nonzero(np.maximum.reduce([np.maximum(a, b) for a, b in frames_hi]) > 0.05)
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    scale = min((SCREEN_W - 2 * SCREEN_MARGIN) / (x1 - x0), (SCREEN_H - 2 * SCREEN_MARGIN) / (y1 - y0))
    return scale, ((x0 + x1) / 2, (y0 + y1) / 2)


def diff_bbox(frames, x_range):
    """Rectangulo minimo (x, y, w, h) que cubre todo lo que cambia entre frames."""
    x0, x1 = x_range
    changed = np.zeros_like(frames[0], dtype=bool)
    for f in frames[1:]:
        changed |= f != frames[0]
    changed[:, :x0] = False
    changed[:, x1:] = False
    ys, xs = np.nonzero(changed)
    # +1 px de margen, recortado a la pantalla.
    bx0, by0 = max(0, xs.min() - 1), max(0, ys.min() - 1)
    bx1, by1 = min(SCREEN_W, xs.max() + 2), min(SCREEN_H, ys.max() + 2)
    return int(bx0), int(by0), int(bx1 - bx0), int(by1 - by0)


def pack(q):
    """4 pixeles por byte, el primero en los 2 bits bajos."""
    h, w = q.shape
    stride = (w + 3) // 4
    padded = np.zeros((h, stride * 4), dtype=np.uint8)
    padded[:, :w] = q
    p = padded.reshape(h, stride, 4)
    return (p[..., 0] | (p[..., 1] << 2) | (p[..., 2] << 4) | (p[..., 3] << 6)).astype(np.uint8).ravel()


def c_array(name, data):
    lines = [f"static const uint8_t {name}[{len(data)}] = {{"]
    for i in range(0, len(data), 24):
        lines.append("  " + ",".join(f"0x{b:02x}" for b in data[i : i + 24]) + ",")
    lines.append("};")
    return "\n".join(lines)


# --- Armado --------------------------------------------------------------------


def build_crispi(persona, cfg):
    """El camino original de Crispi, con sus medidas a mano."""
    p = cfg["manual"]
    eyes_hi = [load_inks(persona, n, False)[0] for n in cfg["frames"]]
    x0, y0, x1, y1 = p["eye_zone"]
    for a in eyes_hi[1:]:
        clean = eyes_hi[0].copy()
        clean[y0:y1, x0:x1] = a[y0:y1, x0:x1]
        a[:] = clean
    sc = lambda a: to_screen(a, p["scale"], p["center"])
    eyes = [quantize(sc(a), None, False) for a in eyes_hi]
    mouths = [quantize(sc(mouth_frame(eyes_hi[0], o, p["smile_x"], p["smile_y"], p["stroke"])), None, False) for o in p["open_px"]]
    return eyes, mouths


def build_auto(persona, cfg):
    two = cfg.get("two_inks", False)
    frames_hi, mouth = compose_auto(persona, cfg)
    base1, base2 = frames_hi[0]
    smile_x, smile_y, stroke = smile_params(base1, mouth)
    # La escala depende de la boca bien abierta y la apertura depende de la
    # escala: se ajusta con la apertura maxima estimada y se verifica abajo.
    scale, center = fit(frames_hi)
    for _ in range(3):
        open_px = [o / scale for o in MOUTH_OPEN_SCREEN]
        widest = mouth_frame(base1, open_px[-1], smile_x, smile_y, stroke, mouth[1])
        scale, center = fit(frames_hi + [(widest, base2)])
    open_px = [o / scale for o in MOUTH_OPEN_SCREEN]
    sc = lambda a: to_screen(a, scale, center)
    eyes = [quantize(sc(f1), sc(f2), two) for f1, f2 in frames_hi]
    s2 = sc(base2)
    mouths = [quantize(sc(mouth_frame(base1, o, smile_x, smile_y, stroke, mouth[1])), s2, two) for o in open_px]
    print(f"{persona}: escala {scale:.3f}, sonrisa x={smile_x} trazo={stroke}px")
    return eyes, mouths


def ink_rgb(persona, cfg):
    """Colores reales del PNG, para la vista previa."""
    rgba = np.array(Image.open(APP / "assets/img" / persona / cfg["frames"][0]).convert("RGBA"))
    solid = rgba[rgba[..., 3] > 200][:, :3].astype(np.int32)
    green = ((solid[:, 1] - solid[:, 0]) > 60) & cfg.get("two_inks", False)
    c1 = solid[~green].mean(0)
    c2 = solid[green].mean(0) if green.any() else c1
    return c1, c2


def main(persona):
    cfg = PERSONAS[persona]
    two = cfg.get("two_inks", False)
    eyes, mouths = (build_crispi if "manual" in cfg else build_auto)(persona, cfg)
    # Boca y ojos comparten la base (la boca cerrada son los ojos abiertos).
    assert (mouths[0] == eyes[0]).all()

    mid = SCREEN_W // 2
    eye_l = diff_bbox(eyes, (0, mid))
    eye_r = diff_bbox(eyes, (mid, SCREEN_W))
    mouth = diff_bbox(mouths, (0, SCREEN_W))
    for a, b in ((eye_l, mouth), (eye_r, mouth)):
        assert a[1] + a[3] <= b[1], "los sprites de ojos y boca no pueden solaparse"

    up = persona.upper()
    out = [
        "/*",
        f"  GENERADO por tools/make_face_sprites.py a partir de assets/img/{persona}/.",
        "  No editar a mano: cambiar las imagenes (o el script) y volver a generarlo.",
        "",
        "  Pixeles de 2 bits, 4 por byte, el primero en los bits bajos.",
        (
            "  DOS tintas: 0 = fondo, 1 = medio trazo, 2 = trazo (color de ojos),"
            if two
            else "  Una tinta: 0 = fondo ... 3 = trazo lleno."
        ),
    ]
    if two:
        out.append("  3 = segunda tinta (color de boca). Los colores salen de faces_colors.h.")
    else:
        out.append("  Los colores salen de faces_colors.h.")
    out += ["*/", "", "#pragma once", "", '#include "face_sprite.h"', ""]

    sprites = {}
    arrays = {}  # bytes -> nombre: los frames repetidos se guardan una vez

    def add(var, rect, q):
        x, y, w, h = rect
        data = pack(q[y : y + h, x : x + w])
        key = (w, h, data.tobytes())
        if key not in arrays:
            arrays[key] = var + "_PX"
            out.append(c_array(var + "_PX", data))
            out.append("")
        sprites[var] = (rect, arrays[key])

    add(f"{up}_BASE", (0, 0, SCREEN_W, SCREEN_H), eyes[0])
    for i, q in enumerate(eyes):
        add(f"{up}_EYE_L{i}", eye_l, q)
        add(f"{up}_EYE_R{i}", eye_r, q)
    for i, q in enumerate(mouths):
        add(f"{up}_MOUTH{i}", mouth, q)

    def sprite(var):
        rect, arr = sprites[var]
        return "{ %d, %d, %d, %d, %s }" % (*rect, arr)

    out.append(f"static const FaceSprite {up}_BASE = {sprite(up + '_BASE')};")
    out.append(f"static const FaceSprite {up}_EYES_L[FACE_EYE_STATES] = {{")
    out += [f"  {sprite(f'{up}_EYE_L{i}')}," for i in range(len(eyes))]
    out.append("};")
    out.append(f"static const FaceSprite {up}_EYES_R[FACE_EYE_STATES] = {{")
    out += [f"  {sprite(f'{up}_EYE_R{i}')}," for i in range(len(eyes))]
    out.append("};")
    out.append(f"static const FaceSprite {up}_MOUTHS[FACE_MOUTH_LEVELS] = {{")
    out += [f"  {sprite(f'{up}_MOUTH{i}')}," for i in range(len(mouths))]
    out.append("};")
    out.append(
        f"static const FaceSpriteSet {up}_SPRITES = {{ &{up}_BASE, {up}_EYES_L, {up}_EYES_R, {up}_MOUTHS, {'true' if two else 'false'} }};"
    )
    out.append("")

    dst = APP / "sketch" / f"{persona}_face.h"
    dst.write_text("\n".join(out))
    total = sum(len(k[2]) for k in arrays)
    print(f"{dst}: ojo izq {eye_l}, ojo der {eye_r}, boca {mouth}, {total} bytes")

    # Vista previa para revisar sin flashear: fila de arriba = parpadeo,
    # fila de abajo = niveles de boca.
    c1, c2 = ink_rgb(persona, cfg)
    white = np.full(3, 255.0)
    if two:
        lut = np.array([white, (white + c1) / 2, c1, c2])
    else:
        lut = np.array([white * (1 - a / 3) + c1 * a / 3 for a in range(4)])
    rgb = lambda q: lut[q].astype(np.uint8)
    top = np.concatenate([rgb(q) for q in eyes + [eyes[0], eyes[0]]], axis=1)
    bot = np.concatenate([rgb(q) for q in mouths], axis=1)
    Image.fromarray(np.concatenate([top, bot], axis=0)).save(APP / "tools" / f"{persona}_preview.png")


if __name__ == "__main__":
    for p in sys.argv[1:] or PERSONAS:
        main(p)
