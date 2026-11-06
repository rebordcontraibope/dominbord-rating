# Domingo · Rating

Página estática que muestra cómo le va a **Domingo** en el rating de IBOPE, emisión por emisión, y si le ganó a los demás canales de aire en su franja.

## Estructura

```
index.html                   la página (HTML + CSS + JS; solo carga las fuentes Anton y Barlow de Google Fonts)
assets/                      poster.webp (fondo del hero), rebord.webp (icono), estatica.webp (original)
data/emisiones.js            ÚNICA fuente de datos: config del programa + lista de emisiones
scripts/actualizar_rating.py scraper / cargador de rating (Python 3.10+, solo stdlib)
.github/workflows/           cron opcional para GitHub Pages
```

## Ver el prototipo

Abrí `index.html` en el navegador. Para ver cómo queda con datos, abrilo con `?demo=1` al final de la URL (datos inventados, no se guardan). Para ver los otros estados sin esperar: `?estado=aire` y `?estado=pendiente`.

La página tiene tres estados automáticos según la hora de Argentina:

1. Antes del estreno: cuenta regresiva al domingo 15/11 22:00.
2. Desde que arranca la emisión hasta que hay dato cargado: "Al aire" / "Esperando a IBOPE", con link al minuto a minuto de Real Time Rating.
3. Con dato: rating grande, veredicto contra cada canal de aire, botón a YouTube, tabla histórica y curva.

## Configurar

En `data/emisiones.js`, bloque `config`:

- `canal`: canal propio, tal como lo escribe television.com.ar (`Telefe`, `eltrece`, `América`, `elnueve`, `TV Pública`).
- `competencia`: un ítem por canal rival con el **nombre del programa que va en la misma franja** (domingo 22:00). Es lo que el scraper busca en el top 5 de cada canal. Si lo dejás vacío, ese canal queda pendiente y se carga a mano.
- `estreno`, `horario`, `duracion_min`, `youtube_canal`, `realtime_rating`.

## Cargar el rating de una emisión

El script tiene una lista `FUENTES` al principio de `scripts/actualizar_rating.py`, en orden de prioridad. Las consulta todas; si alguna falla o todavía no publicó, sigue con la siguiente.

| id | tipo | escala | Cuándo publica | Qué trae |
|---|---|---|---|---|
| `television.com.ar` | planilla_television | ibope_amba | día siguiente, mañana | top 5 por canal, "Fuente: Ibope" |
| `poneleshow` | blogger (feed JSON del blog de @PoneleShow) | ibope_amba | día siguiente, ~16:00 | un post por canal, más programas que el top 5; coincide con la planilla |
| `x:ismael_mon129` | x_cuenta | nacional | día siguiente, tarde, varios posts | `#Programa 13.2` por línea; **no coincide** con AMBA |

Cómo se combinan: las fuentes de escala `ibope_amba` se mezclan entre sí (la primera que tiene el programa manda, las siguientes completan rivales que falten, y se avisan diferencias). Las de otra escala nunca se mezclan con esas: se usan solo si ninguna AMBA tiene el dato, y la emisión queda `provisorio` hasta que una AMBA la reemplace entera. Lo ya cargado desde una AMBA no se pisa, y si nada responde se conserva lo que había. Cada emisión guarda en `fuentes` de dónde salió.

**Quién compite en la franja (automático).** El domingo a las 22:30 el cron corre `--grilla`: lee la grilla de TeleRed (`GRILLAS` en el script; solo responde para el día de hoy, por eso se corre esa noche) y anota en la emisión de ese domingo el programa de cada canal rival con más minutos dentro de la franja (22:00 + duración). Las corridas del lunes buscan esos nombres en las planillas. Si la grilla no responde, se usan los nombres fijos de `config.competencia`, que podés dejar vacíos o usar como respaldo. Si el nombre de la grilla no coincide con el de la planilla (pasa: "BTV" vs el nombre largo), ese rival queda pendiente y lo cargás a mano con `--comp`.

**Agregar una fuente:** sumá un dict a `FUENTES` con `id`, `tipo`, `escala` y los parámetros del tipo (otra cuenta de X: `"tipo": "x_cuenta", "cuenta": "..."`; otro blog de Blogger: `"tipo": "blogger", "feed": ..., "titulo": regex con {dd}/{mm}`). Si es un sitio distinto, escribí una función `obtener_<tipo>(fuente, fecha, cfg, args)` que devuelva `{canal: [(programa, rating), ...]}` y registrala en `TIPOS`. `--listar-fuentes` las muestra.

```bash
python3 scripts/actualizar_rating.py --listar-fuentes
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --dry-run            # todas las fuentes, sin escribir
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --youtube https://youtu.be/XXXX
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --fuente poneleshow  # solo una
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --x-post https://x.com/ismael_mon129/status/XXXX
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --rating 4.2 --comp "Telefe=7.9" --comp "eltrece=3.1"   # manual
```

Los hashtags de X se matchean contra `programa` o el campo opcional `hashtag` de la config (sin acentos ni espacios, exacto o por prefijo). La API oficial de X no sirve para esto (leer timelines es pago); el timeline público a veces responde 429, y ahí `--x-post` con el link lo resuelve.

## El cron (GitHub Actions)

`.github/workflows/actualizar.yml` corre el script seis veces por semana: domingo 22:30 (hora Argentina) con `--grilla` para anotar la competencia, lunes 10:00, 13:00, 17:00 y 20:00 para los ratings, y martes 11:00 como red de seguridad. Cada corrida consulta todas las fuentes; como el script es idempotente, las corridas sin novedades no escriben ni commitean. Cuando `data/emisiones.js` cambia, commitea y pushea, y GitHub Pages republica. Requiere: repo en GitHub, y Settings → Actions → General → Workflow permissions → "Read and write". Desde la pestaña Actions se puede correr a mano con fecha, link de X, link de YouTube y la opción "solo probar".

## Hosting

Es 100% estático: sirve GitHub Pages, Vercel o Netlify. Si va a GitHub Pages, el workflow en `.github/workflows/actualizar.yml` corre el scraper todos los lunes a las 11:00 (hora Argentina) y commitea `data/emisiones.js` si cambió.

## Cómo se mide el rating (para el footer y para explicarlo)

- Mide Kantar IBOPE Media con people meters (~5.000 aparatos en ~1.500 hogares). Universo de referencia: AMBA.
- 1 punto = 1% del universo ≈ 30.000 hogares ≈ 100.000–110.000 personas promedio por minuto.
- Desde el 15/10/2025 el minuto a minuto público (Real Time Rating en X) muestra también espectadores en miles. Las planillas diarias siguen en puntos.
- No hay API pública ni oficial. Fuentes: television.com.ar (planilla por canal, texto HTML), Rating Cero (C5N), La Nación, Teleshow, Primicias Ya; Real Time Rating en X para el vivo.
