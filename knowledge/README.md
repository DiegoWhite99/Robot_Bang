# Conocimiento del modo ESSENTIALS (RAG)

Esta carpeta la lee `python/rag.py` al arrancar la App. De aquí sale la
"pista" corta (unos 200 caracteres) que el modelo local recibe en cada
turno, la lectura de cada tarjeta y las palabras que eligen guía cuando se
dice "robot". Este README NO se indexa.

Cómo editar (sin tocar código):

- Un archivo `.md` por tema. `comportamiento.md` y `bang_metodologia.md`
  valen para todos los guías; `guias/<clave>.md` (crispi, carmel, cesia,
  cori, cristal) solo para ese guía.
- Cada viñeta `- ...` es un fragmento. Escríbelo corto (una o dos frases,
  menos de 220 caracteres), en español sencillo, como se lo dirías a un
  niño. Un párrafo suelto también cuenta como fragmento.
- El título `## ...` le pone etiquetas a sus viñetas:
  - si nombra una fase (`sólida`, `gaseosa`, `líquida`), esas viñetas solo
    se usan en esa fase;
  - `## Tarjetas`: una viñeta por carta, con el formato
    `- «Texto exacto de la carta»: cómo se usa`. El texto tiene que ser el
    mismo de `CARDS` en `python/bang.py`, que es la fuente de verdad;
  - `## Cuándo elegirme`: palabras y temas para elegir a ese guía.
- Los cambios se ven al reiniciar la App. Para probar una búsqueda:
  `docker exec robot-bang-stable-main-1 python3 /app/python/rag.py "mi reto es..." crispi solida`
