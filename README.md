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

El script consulta **las dos fuentes** y las combina:

| Fuente | Cuándo publica | Qué trae |
|---|---|---|
| television.com.ar (planilla diaria, "Fuente: Ibope") | lunes a la mañana | top 5 por canal, dato AMBA |
| X @ismael_mon129 "ARGENTINA EN PANTALLA" | al día siguiente a la tarde | `#Programa 13.2` por línea; dice ser promedio de todas las provincias |

Reglas: las dos fuentes **no miden lo mismo** (la planilla es IBOPE AMBA; la cuenta de X dice publicar un promedio nacional y sus números difieren bastante), así que **nunca se mezclan dentro de una emisión**. Si television.com.ar tiene el programa propio, se usa esa planilla entera y los rivales que no estén en el top 5 quedan pendientes. X se usa solo si la planilla no tiene nada ese día, y la emisión queda `provisorio` hasta que salga la planilla, que la reemplaza. Lo cargado desde television.com.ar no se pisa con X en corridas posteriores, y si ninguna fuente responde se conserva lo que había. Cuando las dos tienen el dato, el script avisa las diferencias. En el timeline de X se juntan todos los posts del día (publican varios, uno por grupo de programas) publicados entre el día de la emisión y dos días después.

```bash
# automático (lo mismo que corre el cron)
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --youtube https://youtu.be/XXXX

# pegando el link del post de X (api.fxtwitter.com, sin login)
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --x-post https://x.com/ismael_mon129/status/XXXX

# limitar fuentes
python3 scripts/actualizar_rating.py --solo-television
python3 scripts/actualizar_rating.py --solo-x

# manual (siempre funciona)
python3 scripts/actualizar_rating.py --fecha 2026-11-15 --rating 4.2 \
  --comp "Telefe=7.9" --comp "eltrece=3.1" --comp "América=2.0" \
  --comp "elnueve=1.4" --comp "TV Pública=0.6" --youtube https://youtu.be/XXXX
```

Agregá `--dry-run` para ver qué haría sin escribir. Si falta algún rival, la emisión queda `provisorio` y la página lo muestra con una etiqueta. Cada emisión guarda en `fuentes` de dónde salieron los números.

Los hashtags de X se matchean contra `programa` o el campo opcional `hashtag` de la config (sin acentos ni espacios). La API oficial de X no sirve para esto: leer timelines requiere el plan pago. El timeline público (`syndication.twitter.com`) a veces responde 429; en ese caso pasá `--x-post` con el link.

> El scraper de television.com.ar todavía no se pudo probar contra el sitio real. La primera vez corrélo con `--dry-run`; si no encuentra las listas, pasá `--url` con la nota exacta o cargá a mano.

## El cron (GitHub Actions)

`.github/workflows/actualizar.yml` corre el script dos veces cada lunes: 11:00 ARG (ya está la planilla) y 20:00 ARG (ya está el post de X; completa o confirma). Si `data/emisiones.js` cambió, commitea y pushea, y GitHub Pages republica. Requiere: repo en GitHub, y Settings → Actions → General → Workflow permissions → "Read and write". Desde la pestaña Actions se puede correr a mano con fecha, link de X, link de YouTube y la opción "solo probar".

## Hosting

Es 100% estático: sirve GitHub Pages, Vercel o Netlify. Si va a GitHub Pages, el workflow en `.github/workflows/actualizar.yml` corre el scraper todos los lunes a las 11:00 (hora Argentina) y commitea `data/emisiones.js` si cambió.

## Cómo se mide el rating (para el footer y para explicarlo)

- Mide Kantar IBOPE Media con people meters (~5.000 aparatos en ~1.500 hogares). Universo de referencia: AMBA.
- 1 punto = 1% del universo ≈ 30.000 hogares ≈ 100.000–110.000 personas promedio por minuto.
- Desde el 15/10/2025 el minuto a minuto público (Real Time Rating en X) muestra también espectadores en miles. Las planillas diarias siguen en puntos.
- No hay API pública ni oficial. Fuentes: television.com.ar (planilla por canal, texto HTML), Rating Cero (C5N), La Nación, Teleshow, Primicias Ya; Real Time Rating en X para el vivo.
