#!/usr/bin/env python3
"""
Actualiza data/emisiones.js con el rating de una emisión.

Dos modos:

  1) Automático (lo que corre el cron): consulta TRES fuentes.
       - television.com.ar: planilla diaria, IBOPE AMBA. Manda.
       - poneleshow.blogspot.com: un post por canal, IBOPE AMBA (misma escala);
         completa lo que falte en la planilla. Se informan las diferencias.
       - X @ismael_mon129: otra escala (promedio nacional). NUNCA se mezcla; se
         usa solo si no hay nada AMBA, y la emisión queda provisoria.
     Lo que ya estaba cargado para esa fecha se conserva si hoy nada lo trae.

       python3 scripts/actualizar_rating.py --fecha 2026-11-15 --youtube https://youtu.be/XXXX
       (--solo-television / --solo-x para limitar las fuentes)

  2) Desde X (cuenta @ismael_mon129 "ARGENTINA EN PANTALLA" u otra que postee
     "#Programa 13.2" por línea). Dos variantes:

       # pegando el link del post (usa api.fxtwitter.com, sin login, confiable)
       python3 scripts/actualizar_rating.py --fecha 2026-11-15 --x-post https://x.com/ismael_mon129/status/123...

       # buscando solo el último post con "-DOMINGO-" (usa el timeline de
       # syndication.twitter.com, que a veces responde 429; si falla, usá --x-post)
       python3 scripts/actualizar_rating.py --fecha 2026-11-15 --x-auto

     Los hashtags se matchean contra config.programa / config.competencia[].programa
     (o el campo opcional "hashtag" de cada uno).

  3) Manual: cargás los números a mano (siempre funciona, aunque el sitio cambie).

       python3 scripts/actualizar_rating.py --fecha 2026-11-15 --rating 4.2 \
           --comp "Telefe=7.9" --comp "eltrece=3.1" --comp "América=2.0" \
           --comp "elnueve=1.4" --comp "TV Pública=0.6" --youtube https://youtu.be/XXXX

Sin --fecha usa el último domingo. Sin dependencias externas (solo stdlib).
"""
import argparse
import datetime as dt
import html
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "emisiones.js"
LISTADO = "https://television.com.ar/noticias/?categoria=ratings"
UA = {"User-Agent": "Mozilla/5.0 (compatible; DomingoRating/1.0)"}

# Alias para matchear nombres de canal tal como los escribe television.com.ar
ALIAS = {
    "telefe": "Telefe",
    "eltrece": "eltrece", "el trece": "eltrece",
    "america": "América", "américa": "América",
    "elnueve": "elnueve", "el nueve": "elnueve",
    "canal siete": "TV Pública", "tv publica": "TV Pública", "tv pública": "TV Pública", "television publica": "TV Pública",
    "net tv": "NET TV", "alpha tv": "Alpha TV",
    "canal 9": "elnueve", "canal 13": "eltrece", "canal 7": "TV Pública", "television publica": "TV Pública",
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def canal_norm(s: str) -> str:
    return ALIAS.get(norm(s), s.strip())


# ---------------------------------------------------------------- datos

def leer_datos() -> dict:
    txt = DATA.read_text(encoding="utf-8")
    m = re.search(r"^window\.DOMINGO_DATA\s*=\s*(\{.*\})\s*;?\s*$", txt, re.S | re.M)
    if not m:
        sys.exit(f"No pude encontrar window.DOMINGO_DATA en {DATA}")
    return json.loads(m.group(1))


def escribir_datos(d: dict) -> None:
    cabecera = (
        "// ==========================================================\n"
        "//  ÚNICA FUENTE DE VERDAD DE LA PÁGINA\n"
        "//  Lo edita a mano o lo reescribe scripts/actualizar_rating.py\n"
        "//  Formato: JSON válido adentro de window.DOMINGO_DATA = {...};\n"
        "// ==========================================================\n"
    )
    DATA.write_text(cabecera + "window.DOMINGO_DATA = " + json.dumps(d, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")


# ---------------------------------------------------------------- scraping

def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def buscar_nota(fecha: dt.date) -> str | None:
    """Busca en el listado de ratings la nota 'programas más vistos de cada canal' de esa fecha."""
    pagina = fetch(LISTADO)
    links = re.findall(r'href="(https?://television\.com\.ar/noticias/ratings/[^"]+)"', pagina)
    sufijo = fecha.strftime("%Y-%m-%d")
    candidatos = [u for u in dict.fromkeys(links) if "mas-vistos" in u and sufijo in u]
    if not candidatos:
        # algunas notas no llevan la fecha en el slug: probar por día y número
        dia = fecha.day
        candidatos = [u for u in dict.fromkeys(links) if "mas-vistos" in u and re.search(rf"-{dia}-de-", u)]
    return candidatos[0] if candidatos else None


def parsear_planilla(html_txt: str) -> dict[str, list[tuple[str, float]]]:
    """
    Devuelve {canal: [(programa, rating), ...]} a partir del HTML (o texto) de la nota.
    La nota tiene un encabezado por canal ("Los cinco más vistos Telefe") y después
    una lista donde el puesto ("1."), el nombre y el rating ("13.3") pueden venir
    en la misma línea o en líneas separadas. Se trabaja sobre texto plano.
    """
    texto = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_txt, flags=re.S | re.I)
    texto = re.sub(r"<br\s*/?>|</?(p|li|ol|ul|h\d|div|tr|td|span|strong|b)\b[^>]*>", "\n", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    lineas = [re.sub(r"[ \t\u00a0]+", " ", l).strip() for l in texto.splitlines()]
    lineas = [l for l in lineas if l]

    RE_CANAL = re.compile(r"^(?:los\s+)?(?:cinco|5|diez|10)?\s*m[aá]s\s+vistos?\s+(?:de\s+)?(.+)$", re.I)
    RE_PUESTO = re.compile(r"^\d{1,2}\s*[\.\)]?$")
    RE_RATING = re.compile(r"^(\d{1,2}[\.,]\d)$")
    RE_LINEA = re.compile(r"^(?:\d{1,2}[\.\)]?\s+)?(.+?)\s+(\d{1,2}[\.,]\d)\s*$")
    RE_FECHA = re.compile(r"^\d{1,2}\s+(de\s+)?[a-záéíóú]+(\s+\d{4})?$", re.I)

    canales: dict[str, list[tuple[str, float]]] = {}
    actual, pendiente = None, None
    for l in lineas:
        m = RE_CANAL.match(l)
        if m:
            actual, pendiente = canal_norm(m.group(1)), None
            canales.setdefault(actual, [])
            continue
        if actual is None:
            continue
        if l.lower().startswith("fuente"):
            actual, pendiente = None, None
            continue
        if RE_PUESTO.match(l) or RE_FECHA.match(l):
            continue
        m = RE_RATING.match(l)
        if m:
            if pendiente:
                canales[actual].append((pendiente, float(m.group(1).replace(",", "."))))
                pendiente = None
            continue
        m = RE_LINEA.match(l)
        if m:
            canales[actual].append((m.group(1).strip(" -–·"), float(m.group(2).replace(",", "."))))
            pendiente = None
            continue
        pendiente = l.strip(" -–·")
    return {k: v for k, v in canales.items() if v}


def buscar_programa(planilla: dict, canal: str, programa: str) -> float | None:
    if not programa:
        return None
    objetivo = norm(programa)
    for c, items in planilla.items():
        if canal and norm(canal_norm(c)) != norm(canal_norm(canal)):
            continue
        for prog, val in items:
            p = norm(prog)
            if p == objetivo or objetivo in p or p in objetivo:
                return val
    return None


# ---------------------------------------------------------------- Ponele Show (blog)

PONELE_FEED = "https://poneleshow.blogspot.com/feeds/posts/default?alt=json&max-results={n}"


def obtener_poneleshow(fecha: dt.date, cfg: dict) -> tuple[float | None, dict[str, float | None]] | None:
    """Rating propio y rivales desde el blog de Ponele Show (poneleshow.blogspot.com),
    que publica un post por canal 'Rating DD/MM: Los más vistos de <Canal>' con los
    números de IBOPE AMBA (misma escala que television.com.ar). Usa el feed JSON de Blogger."""
    try:
        feed = json.loads(fetch(PONELE_FEED.format(n=60)))
    except Exception as e:
        print(f"[poneleshow] no pude leer el feed: {e}")
        return None
    pat = re.compile(rf"^rating\s+{fecha.day:02d}/{fecha.month:02d}\s*:\s*los\s+m[aá]s\s+vistos\s+de\s+(.+)$", re.I)
    planilla: dict[str, list[tuple[str, float]]] = {}
    for e in feed.get("feed", {}).get("entry", []):
        titulo = e.get("title", {}).get("$t", "").strip()
        m = pat.match(titulo)
        if not m:
            continue
        canal = canal_norm(m.group(1))
        contenido = e.get("content", {}).get("$t", "")
        texto = re.sub(r"<br\s*/?>|</?(p|div|li|h\d|tr)\b[^>]*>", "\n", contenido, flags=re.I)
        texto = html.unescape(re.sub(r"<[^>]+>", "", texto))
        items = []
        for l in texto.splitlines():
            l = re.sub(r"[ \t\u00a0]+", " ", l).strip()
            if not l or l.lower().startswith(("promedio", "programa destacado", "los destacados")):
                continue
            mm = re.match(r"^(.+?)\s+(\d{1,2}[\.,]\d)\s*$", l)
            if mm:
                items.append((mm.group(1).strip(" -–·").title(), float(mm.group(2).replace(",", "."))))
        if items:
            planilla[canal] = items
    if not planilla:
        print(f"[poneleshow] no hay posts 'Rating {fecha.day:02d}/{fecha.month:02d}' en el feed todavía")
        return None
    for canal, items in planilla.items():
        print(f"[poneleshow] {canal}: " + ", ".join(f"{p} {v}" for p, v in items))
    rating = buscar_programa(planilla, cfg.get("canal"), cfg.get("programa"))
    if rating is None:
        rating = buscar_programa(planilla, "", cfg.get("programa"))
    comp = {canal_norm(c["canal"]): buscar_programa(planilla, c["canal"], c.get("programa", "")) for c in cfg.get("competencia", [])}
    return rating, comp


# ---------------------------------------------------------------- X (Twitter)

FX_API = "https://api.fxtwitter.com"
SYNDICATION = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{user}"
DIAS_ES = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]


def hashtag_norm(s: str) -> str:
    """'#Otro Día Perdido' -> 'otrodiaperdido' para comparar con el hashtag."""
    return re.sub(r"[^a-z0-9]", "", norm(s))


def parsear_post_x(texto: str) -> dict[str, float]:
    """'#MasterChefCelebrity 13.2🔥' -> {'masterchefcelebrity': 13.2}"""
    out = {}
    for m in re.finditer(r"#([^\s#]+)\s+(\d{1,2}[\.,]\d)", texto):
        out[hashtag_norm(m.group(1))] = float(m.group(2).replace(",", "."))
    return out


def texto_post_x(url_o_id: str, user: str) -> tuple[str, str]:
    """Devuelve (texto, fecha_publicacion) de un post usando api.fxtwitter.com."""
    m = re.search(r"(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)", url_o_id)
    if m:
        user, pid = m.group(1), m.group(2)
    else:
        pid = re.sub(r"\D", "", url_o_id)
    data = json.loads(fetch(f"{FX_API}/{user}/status/{pid}"))
    if data.get("code") != 200:
        sys.exit(f"fxtwitter devolvió {data.get('code')}: {data.get('message')}")
    t = data["tweet"]
    return t.get("text", ""), t.get("created_at", "")


def _tweets_de(obj, out: list) -> None:
    """Recorre el JSON de syndication y junta dicts con full_text + created_at."""
    if isinstance(obj, dict):
        if "full_text" in obj and "created_at" in obj:
            out.append(obj)
        for v in obj.values():
            _tweets_de(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _tweets_de(v, out)


def buscar_post_x_timeline(user: str, fecha: dt.date) -> str | None:
    """Best effort: junta todos los posts '#Rating ... -DIA-' del timeline público (syndication),
    publicados entre el día de la emisión y dos días después. La cuenta suele publicar
    varios posts por día (uno por grupo de programas) y a veces con demora."""
    try:
        page = fetch(SYNDICATION.format(user=user))
    except Exception as e:  # 429 frecuente
        print(f"  ! syndication.twitter.com no respondió ({e}); usá --x-post con el link del post")
        return None
    dia = DIAS_ES[fecha.weekday()]
    tweets: list[dict] = []
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if m:
        try:
            _tweets_de(json.loads(m.group(1)), tweets)
        except json.JSONDecodeError:
            pass
    if not tweets:  # fallback: regex sobre el texto crudo
        for t in re.findall(r'"full_text":"((?:[^"\\]|\\.)*)"', page):
            tweets.append({"full_text": json.loads(f'"{t}"'), "created_at": ""})

    def fecha_ok(created: str) -> bool:
        if not created:
            return True
        for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                f = dt.datetime.strptime(created, fmt).date()
                return fecha <= f <= fecha + dt.timedelta(days=2)
            except ValueError:
                continue
        return True

    encontrados = [t["full_text"] for t in tweets
                   if "#rating" in t["full_text"].lower() and f"-{dia}-" in norm(t["full_text"]).upper()
                   and fecha_ok(t.get("created_at", ""))]
    return "\n".join(encontrados) if encontrados else None


def aplicar_post_x(texto: str, cfg: dict) -> tuple[float | None, dict[str, float | None]]:
    vals = parsear_post_x(texto)
    print("  Post X:", ", ".join(f"{k} {v}" for k, v in vals.items()) or "(sin números)")

    def match(nombre: str, hashtag: str = "") -> float | None:
        claves = [hashtag_norm(hashtag)] if hashtag else []
        claves.append(hashtag_norm(nombre))
        for k in claves:
            if k and k in vals:
                return vals[k]
        n = claves[-1]
        for k, v in vals.items():  # match parcial: solo por prefijo, para que "Domingo" no agarre "CineDelDomingo"
            if n and len(n) >= 5 and (k.startswith(n) or n.startswith(k)):
                return v
        return None

    rating = match(cfg.get("programa", ""), cfg.get("hashtag", ""))
    comp = {canal_norm(c["canal"]): match(c.get("programa", ""), c.get("hashtag", "")) for c in cfg.get("competencia", [])}
    return rating, comp


# ---------------------------------------------------------------- main

def ultimo_domingo(hoy: dt.date) -> dt.date:
    return hoy - dt.timedelta(days=(hoy.weekday() + 1) % 7)


def obtener_television(fecha: dt.date, cfg: dict, url: str | None) -> tuple[float | None, dict[str, float | None]] | None:
    """Rating propio y rivales desde la planilla de television.com.ar. None si no hay planilla."""
    try:
        url = url or buscar_nota(fecha)
    except Exception as e:
        print(f"[television.com.ar] no pude leer el listado: {e}")
        return None
    if not url:
        print(f"[television.com.ar] no encontré la planilla del {fecha}")
        return None
    print(f"[television.com.ar] planilla: {url}")
    try:
        planilla = parsear_planilla(fetch(url))
    except Exception as e:
        print(f"[television.com.ar] no pude bajar la nota: {e}")
        return None
    if not planilla:
        print("[television.com.ar] bajé la nota pero no pude parsear ninguna lista")
        return None
    for canal, items in planilla.items():
        print(f"  {canal}: " + ", ".join(f"{p} {v}" for p, v in items))
    rating = buscar_programa(planilla, cfg.get("canal"), cfg.get("programa"))
    if rating is None:  # si no está en el top 5 de su canal, quizás esté en cualquier canal
        rating = buscar_programa(planilla, "", cfg.get("programa"))
    comp = {canal_norm(c["canal"]): buscar_programa(planilla, c["canal"], c.get("programa", "")) for c in cfg.get("competencia", [])}
    return rating, comp


def obtener_x(fecha: dt.date, cfg: dict, post: str | None) -> tuple[float | None, dict[str, float | None]] | None:
    """Rating propio y rivales desde un post de X. None si no hay post."""
    user = cfg.get("x_cuenta", "ismael_mon129")
    try:
        if post:
            texto, publicado = texto_post_x(post, user)
            print(f"[X] post de @{user} publicado: {publicado}")
        else:
            texto = buscar_post_x_timeline(user, fecha)
            if not texto:
                print(f"[X] no encontré un post '#Rating ... -{DIAS_ES[fecha.weekday()]}-' en @{user}")
                return None
            print(f"[X] post encontrado en el timeline de @{user}")
    except Exception as e:
        print(f"[X] falló: {e}")
        return None
    return aplicar_post_x(texto, cfg)


def combinar(tv, ps, x, cfg: dict) -> tuple[float | None, dict[str, float | None], list[str]]:
    """Escala IBOPE AMBA: television.com.ar manda, poneleshow completa lo que falte (misma medición).
    X (@ismael_mon129) mide otra cosa (promedio nacional): NUNCA se mezcla; se usa solo si no hay nada AMBA."""
    prog = cfg.get("programa", "")
    canales = [canal_norm(c["canal"]) for c in cfg.get("competencia", [])]

    def difs(nombre_a, a, nombre_b, b):
        if not a or not b:
            return
        for nombre, va, vb in [(prog, a[0], b[0])] + [(k, a[1].get(k), b[1].get(k)) for k in canales]:
            if va is not None and vb is not None and abs(va - vb) >= 0.05:
                print(f"  ≠ {nombre}: {nombre_a} {va} vs {nombre_b} {vb} → me quedo con {va}")

    difs("television.com.ar", tv, "poneleshow", ps)
    difs("television.com.ar", tv, "X", x)
    difs("poneleshow", ps, "X", x)

    rating, comp, fuentes = None, {}, []
    for nombre, src in (("television.com.ar", tv), ("poneleshow", ps)):
        if not src:
            continue
        usado = False
        if rating is None and src[0] is not None:
            rating, usado = src[0], True
        for k in canales:
            if comp.get(k) is None and src[1].get(k) is not None:
                comp[k], usado = src[1][k], True
        if usado:
            fuentes.append(nombre)
    if rating is not None:
        return rating, {k: comp.get(k) for k in canales}, fuentes
    if x and x[0] is not None:
        print("  ! uso X (promedio nacional, no AMBA): la emisión queda provisoria hasta que salga la planilla")
        return x[0], {k: x[1].get(k) for k in canales}, ["x"]
    return None, {}, []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fecha", help="YYYY-MM-DD de la emisión (default: último domingo)")
    ap.add_argument("--rating", type=float, help="Rating propio (modo manual)")
    ap.add_argument("--comp", action="append", default=[], help='"Canal=rating" (repetible, modo manual)')
    ap.add_argument("--youtube", help="Link al programa subido")
    ap.add_argument("--url", help="URL exacta de la nota de television.com.ar (si el buscador no la encuentra)")
    ap.add_argument("--x-post", help="Link (o id) de un post de X con '#Programa 13.2' por línea")
    ap.add_argument("--x-auto", action="store_true", help="(default) buscar el post del día en el timeline público de config.x_cuenta")
    ap.add_argument("--solo-television", action="store_true", help="No consultar X")
    ap.add_argument("--solo-x", action="store_true", help="No consultar television.com.ar")
    ap.add_argument("--dry-run", action="store_true", help="Muestra lo que haría sin escribir")
    a = ap.parse_args()

    d = leer_datos()
    cfg = d["config"]
    fecha = dt.date.fromisoformat(a.fecha) if a.fecha else ultimo_domingo(dt.date.today())
    comp_cfg = cfg.get("competencia", [])
    emisiones = d.setdefault("emisiones", [])
    existente = next((e for e in emisiones if e.get("fecha") == fecha.isoformat()), None)

    rating = a.rating
    comp_vals: dict[str, float | None] = {}
    fuentes: list[str] = []
    for c in a.comp:
        k, _, v = c.partition("=")
        comp_vals[canal_norm(k)] = float(v.replace(",", "."))
    if rating is not None or a.comp:
        fuentes.append("manual")

    if rating is None:
        tv = None if a.solo_x else obtener_television(fecha, cfg, a.url)
        if tv is None and existente and "television.com.ar" in existente.get("fuentes", []):
            # la planilla no está disponible hoy, pero ya la habíamos leído: sigue mandando
            print("[television.com.ar] no disponible ahora; conservo lo que ya se cargó de esa fuente")
            tv = (existente.get("rating"), {canal_norm(c["canal"]): c.get("rating") for c in existente.get("competencia", [])})
        ps = None if (a.solo_x or a.solo_television) else obtener_poneleshow(fecha, cfg)
        x = None if a.solo_television else obtener_x(fecha, cfg, a.x_post)
        rating, comp_auto, fuentes_auto = combinar(tv, ps, x, cfg)
        fuentes += fuentes_auto
        for k, v in comp_auto.items():
            comp_vals.setdefault(k, v)
        if rating is None:
            if existente and existente.get("rating") is not None:
                print(f"\nNinguna fuente tiene '{cfg.get('programa')}' hoy; dejo la emisión del {fecha} como estaba.")
                return
            sys.exit(f"\nNinguna fuente tiene '{cfg.get('programa')}' para el {fecha}. "
                     "Probá más tarde, pasá --x-post con el link, o cargá a mano con --rating/--comp.")

    competencia = []
    for c in comp_cfg:
        k = canal_norm(c["canal"])
        v = comp_vals.get(k)
        if v is None and existente and set(existente.get("fuentes", [])) <= {"television.com.ar", "poneleshow"} and set(fuentes) <= {"television.com.ar", "poneleshow"}:  # conservar solo si es la misma fuente
            v = next((e.get("rating") for e in existente.get("competencia", []) if canal_norm(e["canal"]) == k), None)
        if v is None:
            print(f"  ! {c['canal']}: sin dato de '{c.get('programa') or '(sin programa configurado)'}'; queda pendiente")
        competencia.append({"canal": c["canal"], "programa": c.get("programa", ""), "rating": v})
    for k, v in comp_vals.items():  # canales cargados a mano que no estaban en config
        if not any(canal_norm(c["canal"]) == k for c in comp_cfg):
            competencia.append({"canal": k, "programa": "", "rating": v})
    estado = "provisorio" if (any(c["rating"] is None for c in competencia) or fuentes == ["x"]) else "confirmado"

    nueva = {
        "numero": existente.get("numero") if existente else len(emisiones) + 1,
        "fecha": fecha.isoformat(),
        "rating": rating,
        "youtube": a.youtube or (existente or {}).get("youtube", ""),
        "estado": estado,
        "fuentes": fuentes,
        "competencia": competencia,
    }
    if existente:
        if {k: existente.get(k) for k in nueva} == nueva:
            print("\nSin cambios respecto a lo que ya estaba cargado.")
            return
        existente.update(nueva)
    else:
        emisiones.append(nueva)
    emisiones.sort(key=lambda e: e["fecha"])
    for i, e in enumerate(emisiones, 1):
        e["numero"] = i

    print("\nEmisión:", json.dumps(nueva, ensure_ascii=False, indent=2))
    if a.dry_run:
        print("(dry-run, no escribí nada)")
        return
    escribir_datos(d)
    print(f"\nListo: {DATA}")


if __name__ == "__main__":
    main()
