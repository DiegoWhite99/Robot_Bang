# Convierte los SVG de las caras de los guias (assets/img/<guia>/*.svg) en
# sprites a color para la pantalla TFT del MCU: genera sketch/<guia>_face.h.
#
# Cada guia trae 20 dibujos a todo color (fondo de color + cara):
#   - Cara 1..6: parpadeo (1 = abierto ... 6 = cerrado), boca en reposo.
#   - 10 visemas (la boca de cada grupo de sonidos), ojos abiertos:
#       a,e,i  b,m,p  c,d,g,k,n,s,t,x,y,z  ch,sh,j  F  L  O  q,w  R  u
#   - 4 emociones: enojada, feliz, sorpresa, triste (cambian ojos Y boca).
#
# La TFT va por SPI a ~4 us/byte (ver sketch.ino), asi que NO se puede
# mandar una imagen entera por frame. Por eso la cara se parte en capas:
#   - BASE:  la Cara 1 a pantalla completa. Se manda una sola vez, al
#            cambiar de guia.
#   - ARRIBA (ojos/cejas, filas < split): parpadeo + ojos de cada emocion
#            (FRUSTRADO reutiliza los ojos entrecerrados de la Cara 3).
#   - BOCA   (filas >= split): los 10 visemas + la boca de cada emocion
#            (FRUSTRADO reutiliza la boca de la F).
#   - EXTRA  (filas >= split, atado a la capa de ARRIBA): lo que una
#            emocion cambia por debajo del split lejos de la boca
#            (cachetes, lagrimas). Se queda toda la frase, aunque la boca
#            vaya cambiando de visema.
#   ARRIBA y BOCA nunca se pisan (split), asi que una fila de pantalla es
#   la base + la capa de arriba, o la base + el extra + la boca.
#
# Pasos:
#   1. Rasteriza con librsvg + cairo por ctypes (ya vienen en el
#      contenedor; no hace falta instalar nada).
#   2. Alinea cada dibujo con la Cara 1: algunos estan re-exportados con
#      1-3 px de corrimiento, y sin esto se verian costuras. Se busca el
#      corrimiento sub-pixel que deja menos pixeles distintos y se vuelve a
#      rasterizar el vector ya corrido (sin re-muestrear el bitmap).
#   3. Paleta de 15 colores por guia (median cut sobre los 20 dibujos), el
#      fondo exacto es uno de ellos; el indice 15 es "transparente".
#   4. Cada estado guarda solo lo que cambia contra la base: su rectangulo,
#      con lo que no cambia transparente.
#   5. Codifica cada sprite en RLE por fila (ver "Formato" abajo) y mete todo
#      el guia en UN blob + un descriptor (pocos simbolos y relocaciones:
#      en el modo de link dinamico del core Zephyr eso tambien ocupa flash).
#   6. Reconstruye cada estado decodificando el blob igual que el MCU,
#      mide el error contra el SVG y deja tools/<guia>_preview.png.
#
# Formato del RLE (lo decodifica faceDecodeRow() en sketch.ino):
#   token = indice << 4 | (largo - 1); largo 1..15. Si los 4 bits bajos
#   valen 15, el largo es 16 + el byte siguiente (16..271). Indice 15 =
#   transparente (se deja lo de abajo). Cada sprite tiene una tabla de
#   filas (uint16 little endian por fila, offset desde `data`), y las filas
#   repetidas apuntan a los mismos bytes.
#
# Se corre DENTRO del contenedor de la App (ahi estan numpy, PIL, cv2 y
# librsvg):
#   docker exec robot-bang-stable-main-1 python3 /app/tools/make_face_sprites.py [guia ...]

import ctypes
import re
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

APP = Path(__file__).resolve().parent.parent
SCREEN_W, SCREEN_H = 320, 240

# Orden = PERSONA_IDS de python/gestures.py (y P_* de sketch.ino).
GUIAS = ["crispi", "carmel", "cesia", "cori", "cristal"]

# Cuadros del parpadeo que se guardan (la Cara 1 es la base). Tiene que
# coincidir con FACE_BLINK_FRAMES / FACE_BLINK_HALF de sketch/face_sprite.h
# (se verifica abajo). Con flash de sobra van todos: 2, 3, 4, 5, 6. Si no
# entra, (3, 5, 6) ahorra ~30 KB.
BLINK_FRAMES = (2, 3, 4, 5, 6)
FRUSTRATED_EYES = 3  # los ojos de FRUSTRADO son los de la Cara 3

# Visemas en el orden del Bridge "viseme" (1..10; 0 = boca de reposo).
VISEMES = ["AEI", "BMP", "CDG", "CHJ", "F", "L", "O", "QW", "R", "U"]
# Emociones en el orden de FACE_TOP_ANGRY.. / FACE_MOUTH_ANGRY.. de face_sprite.h.
EMOTIONS = ["ENOJADA", "FELIZ", "SORPRESA", "TRISTE"]

TRANSPARENT = 15
PALETTE_COLORS = 15
# Lo que se aleja del fondo menos que esto (max por canal) cuenta como fondo
# al armar la paleta.
BG_TOLERANCE = 6

# Un pixel es "cambio" si su color se aleja de la base al menos esto (max
# por canal, 0-255)...
CHANGE_CORE = 40
# ...o si toca un cambio (antialiasing del borde) y su indice de paleta es
# otro. Lo aislado y tenue (ruido de exportacion) se queda con la base.
CHANGE_GROW_PX = 2
# Manchas de cambio mas chicas que esto son ruido de exportacion.
MIN_BLOB_PX = 6


def _files(suf, **over):
    """Nombres de archivo de un guia: "Cara <clave><suf>.svg", salvo `over`."""
    names = {str(n): f"Cara {n}" for n in range(1, 7)}
    names.update(
        AEI="Cara a,e,i", BMP="Cara b,m,p", CDG="Cara c,d,g,k,n,s,t,x,y,z",
        CHJ="Cara ch,sh,j", F="Cara F", L="Cara L", O="Cara O", QW="Cara q,w",
        R="Cara R", U="Cara u", ENOJADA="Cara enojada", FELIZ="Cara feliz",
        SORPRESA="Cara sorpresa", TRISTE="Cara triste",
    )
    out = {k: v + suf + ".svg" for k, v in names.items()}
    out.update(over)
    return out


# Mapa explicito: los nombres de los SVG no son parejos entre guias.
FILES = {
    "crispi": _files("", CHJ="Cara ch,sh,i.svg"),  # sic: "i" en vez de "j"
    "carmel": _files(" Carmel"),
    "cesia": _files(" Cesia", AEI="Cara a,e,i_1.svg", U="Cara U Cesia.svg"),
    "cori": _files(" Cori", L="CARA L Cori.svg", CHJ="Cara ch, sh,j Cori.svg"),
    "cristal": _files(" Cristal", F="Cara f Cristal.svg", U="Cara U Cristal.svg"),
}


# ---------------------------------------------------------------- rasterizado
_rsvg = ctypes.CDLL("librsvg-2.so.2")
_cairo = ctypes.CDLL("libcairo.so.2")
_gobj = ctypes.CDLL("libgobject-2.0.so.0")


class _Rect(ctypes.Structure):
    _fields_ = [(n, ctypes.c_double) for n in ("x", "y", "width", "height")]


_rsvg.rsvg_handle_new_from_file.restype = ctypes.c_void_p
_rsvg.rsvg_handle_new_from_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
_rsvg.rsvg_handle_render_document.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Rect), ctypes.c_void_p]
_gobj.g_object_unref.argtypes = [ctypes.c_void_p]
_cairo.cairo_image_surface_create.restype = ctypes.c_void_p
_cairo.cairo_create.restype = ctypes.c_void_p
_cairo.cairo_create.argtypes = [ctypes.c_void_p]
_cairo.cairo_destroy.argtypes = [ctypes.c_void_p]
_cairo.cairo_surface_destroy.argtypes = [ctypes.c_void_p]
_cairo.cairo_surface_flush.argtypes = [ctypes.c_void_p]
_cairo.cairo_image_surface_get_data.restype = ctypes.POINTER(ctypes.c_uint8)
_cairo.cairo_image_surface_get_data.argtypes = [ctypes.c_void_p]
_cairo.cairo_image_surface_get_stride.argtypes = [ctypes.c_void_p]


class Svg:
    """Un SVG abierto una vez y rasterizado a 320x240 con un corrimiento."""

    def __init__(self, path):
        self.path = path
        self.handle = _rsvg.rsvg_handle_new_from_file(str(path).encode(), None)
        if not self.handle:
            raise SystemExit(f"no se pudo abrir {path}")

    def render(self, dx=0.0, dy=0.0):
        # ARGB32 de cairo = BGRA premultiplicado; el fondo es opaco, asi que
        # basta con reordenar los canales. Todo se libera: sin esto la
        # busqueda del corrimiento (cientos de renders) se queda sin RAM.
        surf = _cairo.cairo_image_surface_create(0, SCREEN_W, SCREEN_H)
        cr = _cairo.cairo_create(surf)
        _rsvg.rsvg_handle_render_document(self.handle, cr, ctypes.byref(_Rect(dx, dy, SCREEN_W, SCREEN_H)), None)
        _cairo.cairo_surface_flush(surf)
        stride = _cairo.cairo_image_surface_get_stride(surf)
        raw = np.ctypeslib.as_array(_cairo.cairo_image_surface_get_data(surf), shape=(SCREEN_H * stride,))
        rgb = raw.reshape(SCREEN_H, stride)[:, : SCREEN_W * 4].reshape(SCREEN_H, SCREEN_W, 4)[..., [2, 1, 0]].copy()
        _cairo.cairo_destroy(cr)
        _cairo.cairo_surface_destroy(surf)
        return rgb

    def close(self):
        _gobj.g_object_unref(self.handle)


def diff_count(a, b):
    return int((np.abs(a.astype(np.int16) - b).max(-1) > CHANGE_CORE).sum())


def align(svg, base):
    """Corrimiento (dx, dy) que deja `svg` lo mas parecido posible a `base`.

    Se minimiza la CANTIDAD de pixeles distintos (no la suma): lo que el
    dibujo cambia a proposito (boca, ojos) cuenta mas o menos igual con
    cualquier corrimiento, y lo que no cambia baja a ~0 cuando calza.
    """
    best = (diff_count(svg.render(), base), 0.0, 0.0)
    for step in (1.0, 0.5, 0.25):
        cx, cy = best[1], best[2]
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                x, y = cx + dx * step, cy + dy * step
                if (x, y) == (cx, cy):
                    continue
                c = diff_count(svg.render(x, y), base)
                if c < best[0]:
                    best = (c, x, y)
    return best[1], best[2]


# ---------------------------------------------------------------- paleta
def to565(rgb):
    r, g, b = (int(v) for v in rgb)
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def from565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return np.array([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], np.uint8)


def build_palette(base, changed, bg):
    """15 colores: el fondo exacto (indice 0) + 14 por median cut.

    Se cuantiza la base + SOLO lo que cambia en cada estado: con los 20
    cuadros enteros, la cara repetida 20 veces se come la paleta y los
    colores chicos de la boca (lengua, dientes) se pierden. El fondo se saca
    antes (es la mayoria de la base): si no, median cut lo parte en varias
    cajas casi iguales que en RGB565 quedan repetidas.
    """
    px = np.concatenate([base.reshape(-1, 3)] + [c.reshape(-1, 3) for c in changed], axis=0)
    px = px[np.abs(px.astype(np.int32) - bg).max(-1) > BG_TOLERANCE]
    w = 512  # PIL quiere una imagen: se arma una tira de `w` de ancho
    px = np.concatenate([px, np.repeat(px[:1], (-len(px)) % w, axis=0)], axis=0)
    mont = Image.fromarray(px.reshape(-1, w, 3).astype(np.uint8))
    bg565 = to565(bg)
    want = PALETTE_COLORS - 1

    def colors565(n):
        # Colores distintos en RGB565 (sin el fondo), en el orden de median cut.
        pal_img = mont.quantize(n, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        pal = np.array(pal_img.getpalette()[: n * 3], np.int32).reshape(-1, 3)
        out = []
        for c in pal:
            v = to565(c)
            if v != bg565 and v not in out:
                out.append(v)
        return out

    # Si al redondear a RGB565 se repiten colores, se pide alguno mas hasta
    # llenar los 14 lugares (sin pasarse).
    best = colors565(want)
    for n in range(want + 1, 4 * want):
        c = colors565(n)
        if len(c) > want:
            break
        best = c
        if len(c) == want:
            break
    # El fondo exacto: los badges (WiFi, version) se pintan sobre BG y no
    # pueden quedar de otro tono. Se cuantiza con la paleta YA pasada por
    # RGB565: lo que se compara y se previsualiza es lo que va a mostrar la
    # pantalla.
    pal = np.array([from565(v) for v in [bg565] + best], np.uint8)
    return pal, 0


def quantize(rgb, pal):
    d = ((rgb[:, :, None, :].astype(np.int32) - pal[None, None].astype(np.int32)) ** 2).sum(-1)
    return d.argmin(-1).astype(np.uint8)


# ---------------------------------------------------------------- cambios
def change_mask(img, q, base_img, base_q):
    """Pixeles del estado que difieren de la base de verdad (no ruido)."""
    core = np.abs(img.astype(np.int16) - base_img).max(-1) > CHANGE_CORE
    n, lab, stats, _ = cv2.connectedComponentsWithStats(core.astype(np.uint8), connectivity=8)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < MIN_BLOB_PX:
            core[lab == i] = False
    k = np.ones((2 * CHANGE_GROW_PX + 1,) * 2, np.uint8)
    near = cv2.dilate(core.astype(np.uint8), k).astype(bool)
    return core | (near & (q != base_q))


def bbox(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


# ---------------------------------------------------------------- RLE
def rle_row(row):
    out = bytearray()
    i, n = 0, len(row)
    while i < n:
        v = int(row[i])
        j = i
        while j < n and row[j] == v and j - i < 271:
            j += 1
        run = j - i
        if run <= 15:
            out.append((v << 4) | (run - 1))
        else:
            out += bytes(((v << 4) | 15, run - 16))
        i = j
    return bytes(out)


class Blob:
    """Los bytes de un guia: tablas de filas + filas RLE (deduplicadas)."""

    def __init__(self):
        self.data = bytearray()
        self.rows = {}  # bytes de fila -> offset (para reutilizar)

    def add_sprite(self, px):
        """Agrega un sprite (matriz de indices); devuelve (tabla, data).

        Primero van las filas nuevas y despues la tabla de filas. Los
        offsets de la tabla son de 16 bits desde `data`: 0 (absolutos, y
        las filas se comparten entre sprites) mientras el blob no pase de
        ~64 KB; si no, desde el comienzo de este sprite.
        """
        data_base = 0 if len(self.data) < 60000 else len(self.data)
        offs = []
        for r in (rle_row(row) for row in px):
            o = self.rows.get(r)
            if o is None or o < data_base:
                o = len(self.data)
                self.data += r
                self.rows[r] = o
            offs.append(o - data_base)
        table = len(self.data)
        for rel in offs:
            assert 0 <= rel < 65536, "fila fuera del alcance de 16 bits"
            self.data += bytes((rel & 0xFF, rel >> 8))
        return table, data_base


def decode_row(blob, table, data_base, row, w):
    """Lo mismo que faceDecodeRow() del sketch (para verificar)."""
    t = table + 2 * row
    p = data_base + blob[t] + (blob[t + 1] << 8)
    out = []
    while len(out) < w:
        tok = blob[p]
        p += 1
        v, n = tok >> 4, tok & 15
        if n < 15:
            n += 1
        else:
            n = 16 + blob[p]
            p += 1
        out += [v] * n
    return out[:w]


def compose(blob, layers, top, extra, mouth, split):
    """Pantalla de indices como la arma el MCU (faceComposeRow()): base +
    arriba la capa de los ojos, abajo el extra de esos ojos y la boca."""
    scr = np.zeros((SCREEN_H, SCREEN_W), np.uint8)
    base = layers["base"]
    for y in range(SCREEN_H):
        line = np.array(decode_row(blob, base[4], base[5], y, SCREEN_W), np.uint8)
        for s in ((top,) if y < split else (extra, mouth)):
            if s[2] and s[1] <= y < s[1] + s[3]:
                r = np.array(decode_row(blob, s[4], s[5], y - s[1], s[2]), np.uint8)
                seg = line[s[0] : s[0] + s[2]]
                seg[r != TRANSPARENT] = r[r != TRANSPARENT]
        scr[y] = line
    return scr


# ---------------------------------------------------------------- principal
def check_face_sprite_h():
    """BLINK_FRAMES tiene que coincidir con lo que espera el sketch."""
    txt = (APP / "sketch" / "face_sprite.h").read_text()
    want = {
        "FACE_BLINK_FRAMES": len(BLINK_FRAMES),
        "FACE_BLINK_HALF": BLINK_FRAMES.index(FRUSTRATED_EYES),
        "FACE_MOUTH_STATES": 1 + len(VISEMES) + len(EMOTIONS),
    }
    for name, val in want.items():
        m = re.search(rf"\b{name}\s*=\s*(\d+)", txt)
        if not m or int(m.group(1)) != val:
            raise SystemExit(f"face_sprite.h: {name} deberia ser {val} (ver BLINK_FRAMES)")


def c_bytes(name, data, per_line=24):
    lines = [f"static const uint8_t {name}[{len(data)}] = {{"]
    for i in range(0, len(data), per_line):
        lines.append("  " + ",".join(f"0x{b:02x}" for b in data[i : i + per_line]) + ",")
    lines.append("};")
    return lines


def main(guia):
    files = FILES[guia]
    folder = APP / "assets" / "img" / guia
    svgs = {k: Svg(folder / f) for k, f in files.items()}
    for k, s in svgs.items():
        if not s.path.exists():
            raise SystemExit(f"falta {s.path}")

    # 1-2. Rasterizar y alinear con la Cara 1.
    base_rgb = svgs["1"].render()
    base_i16 = base_rgb.astype(np.int16)
    imgs, shifts = {"1": base_rgb}, {}
    for k, s in svgs.items():
        if k == "1":
            continue
        dx, dy = align(s, base_i16)
        shifts[k] = (dx, dy)
        imgs[k] = s.render(dx, dy)
    for s in svgs.values():
        s.close()

    # 3. Paleta y cuantizado.
    bg = base_rgb[0, 0].astype(np.int32)
    changed = []
    for k, v in imgs.items():
        if k != "1":
            changed.append(v[np.abs(v.astype(np.int16) - base_i16).max(-1) > CHANGE_CORE])
    pal, bg_idx = build_palette(base_rgb, changed, bg)
    q = {k: quantize(v, pal) for k, v in imgs.items()}
    base_q = q["1"]

    # 4. Que cambia en cada estado, y donde se cortan las capas.
    masks = {k: change_mask(imgs[k], q[k], base_i16, base_q) for k in imgs if k != "1"}
    blink_keys = [str(n) for n in BLINK_FRAMES]
    blink_bottom = max(bbox(masks[k])[1] + bbox(masks[k])[3] for k in blink_keys)
    # El split es la fila que deja afuera menos pixeles: lo del parpadeo
    # por debajo + lo de los visemas por arriba (ruido, salvo que el dibujo
    # mueva de verdad algo de la otra capa). Si hay varias, la del medio.
    rows_blink = sum(masks[k].sum(1) for k in blink_keys)
    rows_vis = sum(masks[k].sum(1) for k in VISEMES)
    lost = [int(rows_blink[s:].sum() + rows_vis[:s].sum()) for s in range(SCREEN_H + 1)]
    best = [s for s, v in enumerate(lost) if v == min(lost)]
    split = best[len(best) // 2]
    stray = {}
    for k in blink_keys:  # parpadeo: solo arriba
        stray[k] = int(masks[k][split:].sum())
        masks[k][split:] = False
    for k in VISEMES:  # visemas: solo la boca
        stray[k] = int(masks[k][:split].sum())
        masks[k][:split] = False

    # Lo que una emocion cambia por debajo del split se parte en dos: la
    # boca (las manchas que tocan la zona de los visemas) va en la capa de
    # la boca, y el resto (cachetes, lagrimas) en un sprite "extra" atado a
    # los ojos: asi no se prende y apaga en cada pausa mientras habla.
    vis_box = np.zeros_like(masks[VISEMES[0]])
    for k in VISEMES:
        r = bbox(masks[k])
        if r:
            vis_box[r[1] : r[1] + r[3], r[0] : r[0] + r[2]] = True
    for e in EMOTIONS:
        low = masks[e].copy()
        low[:split] = False
        n, lab = cv2.connectedComponents(low.astype(np.uint8), connectivity=8)
        extra = np.zeros_like(low)
        for i in range(1, n):
            comp = lab == i
            if not (comp & vis_box).any():
                extra |= comp
        masks["X_" + e] = extra
        masks[e] = masks[e] & ~extra

    def sprite_px(k, rows, src=None):
        m = masks[k].copy()
        keep = np.zeros_like(m)
        keep[rows] = True
        m &= keep
        r = bbox(m)
        if r is None:
            return None
        x, y, w, h = r
        px = np.where(m, q[src or k], TRANSPARENT)[y : y + h, x : x + w]
        return r, px

    blob = Blob()
    layers = {}

    def add(name, k, rows, src=None):
        sp = sprite_px(k, rows, src)
        if sp is None:
            layers[name] = (0, 0, 0, 0, 0, 0)
            return
        (x, y, w, h), px = sp
        table, data_base = blob.add_sprite(px)
        layers[name] = (x, y, w, h, table, data_base)

    table, data_base = blob.add_sprite(base_q)
    layers["base"] = (0, 0, SCREEN_W, SCREEN_H, table, data_base)
    top_names = ["OPEN"]
    layers["OPEN"] = (0, 0, 0, 0, 0, 0)
    for k in blink_keys:
        add("B" + k, k, slice(0, split))
        top_names.append("B" + k)
    for e in EMOTIONS:
        add("T_" + e, e, slice(0, split))
        top_names.append("T_" + e)
    layers["T_FRUSTRADO"] = layers["B%d" % FRUSTRATED_EYES]
    top_names.append("T_FRUSTRADO")
    # Extra de cada estado de arriba (filas >= split); solo las emociones.
    extra_names = []
    for n in top_names:
        e = n[2:] if n.startswith("T_") and n[2:] in EMOTIONS else None
        if e:
            add("X_" + e, "X_" + e, slice(split, SCREEN_H), e)
            extra_names.append("X_" + e)
        else:
            extra_names.append("NONE")
    layers["NONE"] = (0, 0, 0, 0, 0, 0)
    mouth_names = ["REST"]
    layers["REST"] = (0, 0, 0, 0, 0, 0)
    for k in VISEMES:
        add("V_" + k, k, slice(split, SCREEN_H))
        mouth_names.append("V_" + k)
    for e in EMOTIONS:
        add("M_" + e, e, slice(split, SCREEN_H))
        mouth_names.append("M_" + e)
    data = bytes(blob.data)

    # 6. Verificacion: reconstruir cada estado desde el blob, como el MCU.
    states = [("Cara 1", "OPEN", "REST", "1")]
    states += [(f"Cara {k}", "B" + k, "REST", k) for k in blink_keys]
    states += [(k, "OPEN", "V_" + k, k) for k in VISEMES]
    states += [(e.lower(), "T_" + e, "M_" + e, e) for e in EMOTIONS]
    states += [("frustrado", "T_FRUSTRADO", "V_F", None)]
    # Hablando con una emocion: el visema encima de los ojos + extra.
    states += [(e.lower() + "+O", "T_" + e, "V_O", None) for e in EMOTIONS]
    lut = pal.astype(np.uint8)
    tiles, report = [], []
    for label, t, m, src in states:
        x = layers[extra_names[top_names.index(t)]]
        scr = compose(data, layers, layers[t], x, layers[m], split)
        rgb = lut[scr]
        if src is not None:
            err = np.abs(rgb.astype(np.int16) - imgs[src]).max(-1)
            report.append((label, float(err.mean()), float((err > 48).mean() * 100)))
        tiles.append((label, rgb))
    cols = 6
    rows_n = (len(tiles) + cols - 1) // cols
    sheet = np.full((rows_n * (SCREEN_H + 4), cols * (SCREEN_W + 4), 3), 255, np.uint8)
    for i, (label, rgb) in enumerate(tiles):
        r, c = divmod(i, cols)
        sheet[r * (SCREEN_H + 4) : r * (SCREEN_H + 4) + SCREEN_H, c * (SCREEN_W + 4) : c * (SCREEN_W + 4) + SCREEN_W] = rgb
    for i in range(cols * rows_n):  # la linea del split, para revisar el corte
        r, c = divmod(i, cols)
        y0, x0 = r * (SCREEN_H + 4) + split, c * (SCREEN_W + 4)
        sheet[y0, x0 : x0 + 6] = (255, 0, 0)
    Image.fromarray(sheet).save(APP / "tools" / f"{guia}_preview.png", optimize=True)

    # 5. Header.
    up = guia.upper()
    lut565 = [to565(c) for c in pal] + [0] * (16 - len(pal))
    swapped = [((c & 0xFF) << 8) | (c >> 8) for c in lut565]
    bg565 = to565(pal[bg_idx])

    def layer(name):
        x, y, w, h, t, d = layers[name]
        return "{ %d, %d, %d, %d, %d, %d }" % (x, y, w, h, t, d)

    out = [
        "/*",
        f"  Cara de {guia.capitalize()} a color: GENERADO por tools/make_face_sprites.py",
        f"  desde los SVG de assets/img/{guia}/. No editar a mano: volver a correr la",
        "  herramienta. Formato y capas: ver face_sprite.h.",
        "",
        f"  split = {split} (filas < split: ojos/cejas; >= split: boca)",
        f"  blob = {len(data)} bytes, parpadeo = Cara 1 + {', '.join(blink_keys)}",
        "*/",
        "",
        "#pragma once",
        "",
        '#include "face_sprite.h"',
        "",
        "// RGB565 con el byte swap ya hecho (listo para SPI); el 15 no se usa (transparente).",
        f"static const uint16_t {up}_FACE_LUT[16] = {{ " + ", ".join(f"0x{c:04x}" for c in swapped) + " };",
        "",
    ]
    out += c_bytes(f"{up}_FACE_BLOB", data)
    out += [
        "",
        f"static const ColorFace {up}_FACE = {{",
        f"  {up}_FACE_BLOB, {up}_FACE_LUT, 0x{bg565:04x}, {split},",
        f"  {layer('base')},",
        "  {",
    ]
    out += [f"    {layer(n)},  // {n}" for n in top_names]
    out += ["  },", "  {"]
    out += [f"    {layer(x)},  // {n}" + (f" ({x})" if x != "NONE" else "") for n, x in zip(top_names, extra_names)]
    out += ["  },", "  {"]
    out += [f"    {layer(n)},  // {n}" for n in mouth_names]
    out += ["  },", "};", ""]
    dst = APP / "sketch" / f"{guia}_face.h"
    dst.write_text("\n".join(out))

    worst = max(report, key=lambda r: r[2])
    mean = sum(r[1] for r in report) / len(report)
    print(f"{dst.name}: blob {len(data)} B, split {split}, parpadeo hasta y={blink_bottom}, "
          f"error medio {mean:.2f}/255, peor {worst[0]} {worst[2]:.2f}% px > 48")
    big = {k: v for k, v in stray.items() if v}
    if big:
        print(f"  descartado fuera de su capa (px): {big}")
    moved = {k: v for k, v in shifts.items() if v != (0.0, 0.0)}
    print(f"  corrimientos: {moved}")
    sizes = {n: layers[n][2] * layers[n][3] for n in top_names + extra_names + mouth_names if layers[n][2]}
    print(f"  rects (px): {sizes}")
    return len(data), report


if __name__ == "__main__":
    check_face_sprite_h()
    total = 0
    for g in sys.argv[1:] or GUIAS:
        if g not in FILES:
            raise SystemExit(f"guia desconocido: {g} (validos: {', '.join(GUIAS)})")
        total += main(g)[0]
    print(f"total {total} bytes")
