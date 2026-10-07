# Recuperacion de conocimiento (RAG) para el modo ESSENTIALS.
#
# El modelo local (Qwen 0.8B en la placa) lee el prompt a ~9 tokens/s: cada
# 100 caracteres de mas son ~3 s de espera. Por eso NO se le pasa toda la
# metodologia: en cada turno se busca la "pista" mas util (<= ~200
# caracteres) y va en el mensaje del usuario, nunca en el system prompt (que
# se queda fijo por guia para que llama.cpp reuse su cache de prefijo).
#
# BM25 en Python puro, sin dependencias (en el contenedor no hay sklearn ni
# rank_bm25, y no hace falta). Fuentes, leidas una vez al importar:
#   - knowledge/*.md y knowledge/guias/<guia>.md  (versionado; ver su README)
#   - identty/0X_<Guia>.txt: perfil, rasgos, como te ayuda y tono
#   - identty/00_...txt: pasos y principios (secciones 4 y 5)
# De identty/ NO se toman las cartas ni su lectura: ahi el texto de algunas
# cartas no coincide con bang.CARDS, que es la fuente de verdad.
#
# Tambien sirve, sin llamar a ningun LLM, para:
#   - classify_persona(): que guia acompana mejor un reto ("robot, ...").
#   - card_reading(): como se usa una tarjeta (knowledge/guias, ## Tarjetas).
#
# Prueba rapida:  python3 python/rag.py "mi reto es reciclar" crispi solida

import math
import random
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_KNOWLEDGE = _ROOT / "knowledge"
_IDENTTY = _ROOT / "identty"

PERSONA_KEYS = ("crispi", "carmel", "cesia", "cori", "cristal")
PHASES = ("solida", "gaseosa", "liquida")

MAX_CHUNK_CHARS = 220  # ~60 tokens: un fragmento nunca se come la pista entera
K1, B = 1.5, 0.75  # parametros clasicos de BM25

_STOPWORDS = set(
    """
    a al algo algun alguna algunas alguno algunos ante antes asi aun aunque bien cada como con contra cual
    cuales cuando de del desde donde dos e el ella ellas ello ellos en entre era eran eres es esa esas ese
    eso esos esta estaba estan estar estas este esto estos fue fueron ha hace hacen hacer hacia han hay
    hasta la las le les lo los mas me mi mis mucho muy nada ni no nos nosotros o otra otras otro otros
    para pero poco por porque que quien se sea ser si sin sobre solo son su sus tal tambien te ti tiene
    tienen tu tus un una unas uno unos usted y ya yo eh pues entonces oye mira bueno vale dale ok
    quiero quisiera reto dice digo
    """.split()
)


def fold(text):
    """Minusculas y sin tildes (la n de "nino" incluida)."""
    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def _stem(tok):
    """Raiz pobre pero suficiente: sin plural y cortada a 6 letras."""
    if len(tok) > 4 and tok.endswith("es"):
        tok = tok[:-2]
    elif len(tok) > 3 and tok.endswith("s"):
        tok = tok[:-1]
    return tok[:6]


def tokens(text):
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", fold(text)) if len(w) > 2 and w not in _STOPWORDS]


@dataclass
class Chunk:
    text: str
    persona: str = None  # None = vale para todos los guias
    phase: str = None  # None = cualquier fase
    kind: str = "pista"  # pista | perfil | tarjeta | clasificar
    source: str = ""


def _split_long(text, limit=MAX_CHUNK_CHARS):
    """Parte un texto largo por frases, en trozos de hasta `limit`."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return [text] if text else []
    out, cur = [], ""
    for frase in re.split(r"(?<=[.!?;:])\s+", text):
        if cur and len(cur) + 1 + len(frase) > limit:
            out.append(cur)
            cur = frase
        else:
            cur = f"{cur} {frase}".strip()
    if cur:
        out.append(cur)
    return [o[:limit] for o in out]


def _heading_tags(heading):
    h = fold(heading)
    phase = next((p for p in PHASES if p in h), None)
    if "tarjeta" in h:
        kind = "tarjeta"
    elif "elegir" in h:
        kind = "clasificar"
    else:
        kind = "pista"
    return phase, kind


def _parse_markdown(path, persona):
    """Cada vineta (o parrafo) bajo un '## titulo' es un fragmento."""
    chunks = []
    phase, kind = None, "pista"
    buf = []

    def flush():
        if buf:
            text = " ".join(buf)
            for part in _split_long(text, 400 if kind == "tarjeta" else MAX_CHUNK_CHARS):
                chunks.append(Chunk(part, persona, phase, kind, path.name))
            buf.clear()

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("<!--"):
            continue
        if line.startswith("#"):
            flush()
            if line.startswith("## "):
                phase, kind = _heading_tags(line[3:])
            continue
        if not line:
            flush()
            continue
        if line.startswith(("- ", "* ")):
            flush()
            buf.append(line[2:].strip())
        else:
            buf.append(line)
    flush()
    return chunks


_IDENTTY_KEEP = ("PERFIL", "ARQUETIPO", "RASGOS", "COMO TE AYUDA", "TONO")


def _identty_sections(path):
    """{titulo: [lineas]} de un reporte de identty/ (titulos subrayados con ----)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    sections, title = {}, None
    for i, line in enumerate(lines):
        if set(line.strip()) == {"="}:
            title = None
            continue
        if set(line.strip()) == {"-"}:
            continue
        if i + 1 < len(lines) and set(lines[i + 1].strip()) == {"-"} and line.strip():
            title = fold(line.strip()).upper()
            sections[title] = []
            continue
        if title is not None:
            sections[title].append(line)
    return sections


def _items(lines):
    """Vinetas ('- ', 'PASO n') con sus lineas de continuacion; si no hay, un parrafo."""
    items, cur = [], []
    for line in lines:
        s = line.strip()
        if not s:
            if cur:
                items.append(" ".join(cur))
                cur = []
            continue
        if s.startswith("- ") or re.match(r"PASO \d", s):
            if cur:
                items.append(" ".join(cur))
            cur = [s.lstrip("- ").strip()]
        else:
            cur.append(s)
    if cur:
        items.append(" ".join(cur))
    return items


def _parse_identty():
    chunks = []
    if not _IDENTTY.is_dir():
        return chunks
    for path in sorted(_IDENTTY.glob("*.txt")):
        key = fold(path.stem.split("_", 1)[-1])
        sections = _identty_sections(path)
        if key in PERSONA_KEYS:
            for title, lines in sections.items():
                if not title.startswith(_IDENTTY_KEEP):
                    continue
                kind = "clasificar" if title.startswith(("COMO TE AYUDA", "RASGOS")) else "perfil"
                for item in _items(lines):
                    for part in _split_long(item):
                        chunks.append(Chunk(part, key, None, kind, path.name))
        else:
            # Informe general: solo los pasos (4) y los principios (5).
            for title, lines in sections.items():
                if title.startswith(("4.", "5.")):
                    for item in _items(lines):
                        for part in _split_long(item):
                            chunks.append(Chunk(part, None, None, "pista", path.name))
    return chunks


def _load():
    chunks = []
    if _KNOWLEDGE.is_dir():
        for path in sorted(_KNOWLEDGE.glob("*.md")):
            if path.name.lower() != "readme.md":
                chunks += _parse_markdown(path, None)
        for path in sorted((_KNOWLEDGE / "guias").glob("*.md")):
            key = fold(path.stem)
            if key in PERSONA_KEYS:
                chunks += _parse_markdown(path, key)
    return chunks + _parse_identty()


class _BM25:
    def __init__(self, docs):
        self.docs = [Counter(d) for d in docs]
        self.lens = [len(d) for d in docs]
        self.avg = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
        df = Counter(t for d in self.docs for t in d)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query_tokens, i):
        d, dl, s = self.docs[i], self.lens[i], 0.0
        for t in set(query_tokens):
            f = d.get(t)
            if f:
                s += self.idf[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / (self.avg or 1)))
        return s


CHUNKS = _load()
_INDEX = _BM25([tokens(c.text) for c in CHUNKS])
_TEXTS = {c.text for c in CHUNKS}


# Lo de la fase en curso y lo del propio guia pesa mas que lo general: con
# frases de niño muy cortas, BM25 solo no distingue bien.
PHASE_BOOST, PERSONA_BOOST = 1.6, 1.2


def _boost(c, persona, phase):
    return (PHASE_BOOST if phase and c.phase == phase else 1.0) * (PERSONA_BOOST if persona and c.persona == persona else 1.0)


def retrieve(query, persona=None, phase=None, k=2, max_chars=200, kinds=("pista", "perfil"), exclude=(), used=None):
    """Los fragmentos mas utiles para esta frase, juntos en <= max_chars.

    Filtra por guia (los suyos + los generales), fase (los de esa fase + los
    que valen siempre) y tipo. Si nada coincide, devuelve la pista de la fase
    del propio guia, para que el modelo local siempre tenga un empujon.

    exclude: textos de fragmentos que NO se quieren (los que ya se usaron hace
    poco en esta conversacion). Es lo que evita que el modelo local repita:
    con el mismo reto, BM25 devolvia el MISMO fragmento turno tras turno, Qwen
    recibia casi el mismo mensaje y contestaba casi lo mismo. Si al excluir no
    queda nada, se ignora exclude (mejor una pista repetida que ninguna).
    used: si es una lista, se le agregan los fragmentos elegidos.
    """
    q = tokens(query)
    excluir = set(exclude or ())
    base = [
        i for i, c in enumerate(CHUNKS)
        if c.kind in kinds and c.persona in (None, persona) and (c.phase is None or c.phase == phase)
    ]
    pool = [i for i in base if CHUNKS[i].text not in excluir] or base
    ranked = sorted(((_INDEX.score(q, i) * _boost(CHUNKS[i], persona, phase), i) for i in pool), reverse=True)
    picks = [CHUNKS[i].text for s, i in ranked if s > 0][: max(k, 1) * 3]
    if not picks:
        picks = [c.text for c in CHUNKS if c.kind == "pista" and c.phase == phase and c.persona == persona]
        picks += [c.text for c in CHUNKS if c.kind == "pista" and c.phase == phase and c.persona is None][:1]
        # Tambien aqui, que es justo el caso de las frases cortas o mal
        # entendidas ("escuche respondeme"): sin esto salia siempre la misma.
        picks = [t for t in picks if t not in excluir] or picks
        random.shuffle(picks)
    out = []
    for text in picks:
        if len(out) >= k:
            break
        if len(" ".join(out + [text])) <= max_chars:
            out.append(text)
    if not out and picks:
        out = [picks[0][:max_chars].rsplit(" ", 1)[0]]
    if used is not None:
        used.extend(t for t in out if t in _TEXTS)
    return " ".join(out)


_QUESTION = re.compile(r"¿[^?¿]{8,140}\?")
# De donde NO sacar preguntas para decirle al niño: los ejemplos son de OTROS
# retos ("¿y si en vez de sonar, se apagara la tele?" no viene al caso del
# suyo) y el informe de tarjetas esta escrito para adultos ("¿Necesitas
# estrategia y foco de valor?"). Sirven como pista para el modelo, no para
# decirlas tal cual.
_NO_DECIR = ("ejemplos_retos", "Informe_general")
# Y preguntas que, sacadas de su parrafo, no se entienden: "¿Que haria ELLA
# aqui?" (ella era "la persona que mas admiras", una frase antes) o "¿que idea
# aparece para MI reto?" (lo dice el niño mirando una tarjeta, no el guia).
_SUELTA_NO = re.compile(r"(?i)\b(ella|él|ellos|ellas|eso mismo|mi reto)\b")


def fresh_question(query, persona, phase, exclude=()):
    """Una PREGUNTA de knowledge/ lista para decir en voz alta, que no se haya
    usado hace poco: (pregunta, texto del fragmento), o ("", "").

    Es el plan B cuando el modelo local se repite: en vez de volver a esperarlo
    15-30 s, el guia sigue con una pregunta de la metodologia, elegida por
    parecido con lo que dijo el niño y siempre distinta a las ultimas.
    """
    q = tokens(query)
    excluir = set(exclude or ())
    cands = []
    for i, c in enumerate(CHUNKS):
        if c.kind != "pista" or c.persona not in (None, persona) or (c.phase not in (None, phase)):
            continue
        if c.text in excluir or any(x in c.source for x in _NO_DECIR):
            continue
        m = _QUESTION.search(c.text)
        if m and "..." not in m.group(0) and "…" not in m.group(0) and not _SUELTA_NO.search(m.group(0)):
            # ("¿Como podriamos...?" es una plantilla a medio escribir.)
            pregunta = m.group(0)
            pregunta = "¿" + pregunta[1:2].upper() + pregunta[2:]
            cands.append((_INDEX.score(q, i) * _boost(c, persona, phase) + random.random() * 0.01, pregunta, c.text))
    if not cands:
        return "", ""
    cands.sort(reverse=True)
    # Entre las 3 mas parecidas, una al azar: relevante pero no siempre igual.
    _, pregunta, texto = random.choice(cands[:3])
    return pregunta, texto


def card_reading(persona, card_text):
    """Como se usa una tarjeta ('' si knowledge/ no la trae)."""
    want = tokens(card_text)
    for c in CHUNKS:
        if c.kind == "tarjeta" and c.persona == persona:
            m = re.match(r"«(.+?)»\s*:?\s*(.*)", c.text)
            if m and tokens(m.group(1)) == want:
                return m.group(2).strip()
    return ""


def _persona_docs():
    docs = {k: [] for k in PERSONA_KEYS}
    for c in CHUNKS:
        if c.persona in docs and c.kind in ("clasificar", "perfil"):
            docs[c.persona] += tokens(c.text)
    return docs


_PERSONA_DOCS = _persona_docs()
_PERSONA_INDEX = _BM25([_PERSONA_DOCS[k] for k in PERSONA_KEYS])


def classify_persona(text, candidates=None, default="crispi"):
    """Que guia acompana mejor este reto, sin LLM (BM25 sobre 'Cuando elegirme',
    rasgos y perfil de cada guia). Si nada coincide, `default`."""
    q = tokens(text)
    best, best_score = default, 0.0
    for i, key in enumerate(PERSONA_KEYS):
        if candidates and key not in candidates:
            continue
        s = _PERSONA_INDEX.score(q, i)
        if s > best_score:
            best, best_score = key, s
    return best


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "mi reto es que en el salon nadie recicla"
    persona = sys.argv[2] if len(sys.argv) > 2 else "crispi"
    phase = sys.argv[3] if len(sys.argv) > 3 else "solida"
    print(f"{len(CHUNKS)} fragmentos indexados")
    print("pista:", retrieve(query, persona, phase))
    print("guia sugerido:", classify_persona(query))
