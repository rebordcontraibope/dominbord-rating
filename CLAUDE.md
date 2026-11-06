# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

Página estática (un solo `index.html`) que muestra el rating IBOPE de **Dominbord** emisión por emisión y si le ganó a los canales de aire. Sin framework, sin bundler, sin `package.json`. La única dependencia externa es Google Fonts.

## Comandos

```bash
# Levantar local (file:// también funciona: los datos se cargan con <script src>, no con fetch)
python3 -m http.server 8080        # → http://localhost:8080

# Estados de la página para probar diseño sin esperar la fecha
#   ?demo=1            → 3 emisiones inventadas, fecha simulada 2026-12-01
#   ?estado=aire       → simula programa al aire
#   ?estado=pendiente  → simula "Esperando a IBOPE"

# Cargar rating de una emisión (Python 3.10+, solo stdlib). Siempre probar primero con --dry-run
python3 scripts/actualizar_rating.py --dry-run                       # scrapea television.com.ar, último domingo
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --x-post https://x.com/ismael_mon129/status/XXXX
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --rating 4.2 --comp "Telefe=7.9" --comp "eltrece=3.1"
```

No hay tests ni linter.

## Arquitectura

**`data/emisiones.js` es la única fuente de verdad.** Es JSON válido envuelto en `window.DOMINGO_DATA = {...};`. Lo leen dos consumidores con contratos distintos:

- `index.html` lo carga con `<script src>` y lee `window.DOMINGO_DATA` (por eso funciona con `file://`).
- `scripts/actualizar_rating.py` lo parsea con una regex sobre esa misma asignación (`leer_datos`) y lo reescribe completo con `json.dumps` más una cabecera de comentarios fija (`escribir_datos`). Si cambiás el formato del archivo (nombre de la variable, comentarios adentro del objeto, trailing commas), rompés el scraper.

**Modelo de datos:** `config` (programa, canal, estreno ISO con offset -03:00, duración, `competencia[]` con `{canal, programa, hashtag}`) y `emisiones[]` con `{numero, fecha, rating, youtube, estado, competencia[]}`. `estado` es `confirmado` o `provisorio` (algún rival con `rating: null`); la página muestra una etiqueta en ese caso. El script renumera `numero` por orden de fecha en cada escritura y hace upsert por `fecha`.

**Lógica de estados en `index.html`:** todo se calcula en el cliente a partir de `config.estreno` + semanas transcurridas, siempre en hora Argentina. Tres estados: cuenta regresiva (antes del estreno), al aire / esperando dato (sin emisión cargada para la última fecha programada) y con dato. `?demo=1` y `?estado=` solo pisan `now` y `E` para forzar estos estados.

**Scraper, tres fuentes en orden de prioridad:** manual (`--rating/--comp`) > X vía `api.fxtwitter.com` (`--x-post`) o timeline de syndication (`--x-auto`, suele dar 429) > planilla de television.com.ar (`buscar_nota` + `parsear_planilla` sobre texto plano, no sobre markup). Los nombres de canal se normalizan con `ALIAS`/`canal_norm`; los hashtags de X con `hashtag_norm` (sin acentos, espacios ni mayúsculas). El parser de television.com.ar nunca se probó contra el sitio real.

## Convenciones

- Todo en español (código, comentarios, README, mensajes del script). Mantenerlo así.
- Nombres de canal tal como los escribe television.com.ar: `Telefe`, `eltrece`, `América`, `elnueve`, `TV Pública`.
- `.github/workflows/actualizar.yml` corre el scraper los lunes 14:00 UTC y commitea `data/emisiones.js` si cambió. `Claude outputs/actualizar.yml` es una copia idéntica y no se usa.

## Git y deploy

- Este repo se pushea SIEMPRE con la cuenta personal de GitHub **FedeRoma**, nunca con la cuenta de trabajo (`federicork-q`). Antes de cualquier push: `gh auth status` debe mostrar FedeRoma como cuenta activa (`gh auth switch -u FedeRoma` si no lo está). La identidad de commit está seteada a nivel local del repo (`git config user.name/user.email`), no global.
- Hosting: GitHub Pages desde la rama `main`, raíz del repo. El workflow de los lunes commitea `data/emisiones.js` y Pages republica solo con cada push.
