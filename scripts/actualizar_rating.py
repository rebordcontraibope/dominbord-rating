#!/usr/bin/env python3
"""
Actualiza data/emisiones.js con el rating de una emisión, consultando una lista
de fuentes (ver FUENTES más abajo) y combinándolas.

Uso típico (lo que corre el cron):

    python3 scripts/actualizar_rating.py                       # último domingo
    python3 scripts/actualizar_rating.py --fecha 2026-11-15 --youtube https://youtu.be/XXXX
    python3 scripts/actualizar_rating.py --fecha 2026-11-15 --dry-run

Ayudas:
    --fuente ID            consultar solo esa fuente (repetible). IDs: ver --listar-fuentes
    --x-post LINK          usar un post de X concreto (api.fxtwitter.com) para la fuente de X
    --url LINK             nota exacta de television.com.ar si el buscador no la encuentra
    --rating 4.2 --comp "Telefe=7.9" ...   carga manual, sin consultar fuentes

Cómo se combinan:
  * Las fuentes se consultan en el orden de FUENTES. Si una falla o todavía no
    publicó, se sigue con la siguiente; no es un error.
  * Las de escala "ibope_amba" (la medición oficial del AMBA, que es la que se
    publica como "el rating") se combinan entre sí: la primera que tiene el
    programa propio manda, las siguientes completan rivales que falten, y se
    informan las diferencias entre ellas.
  * Las de otra escala (p. ej. "nacional") NUNCA se mezclan con las anteriores:
    se usan solo si ninguna fuente AMBA tiene el programa, y la emisión queda
    "provisorio" hasta que aparezca una AMBA, que la reemplaza entera.
  * Lo ya cargado desde una fuente AMBA no se pisa por una de otra escala en
    corridas posteriores. Si hoy ninguna fuente responde, se conserva lo que había.
  * El script es idempotente: correrlo muchas veces el mismo día es gratis; si no
    hay cambios no escribe nada (y el cron no commitea).

Grilla (quién compite en la franja):
    python3 scripts/actualizar_rating.py --grilla        # corre el DOMINGO a la noche
  Lee la grilla de TeleRed (solo responde para el día de hoy) y anota en la
  emisión de hoy qué programa va en cada canal rival en la franja (el de más
  minutos superpuestos). Al día siguiente, las corridas de rating buscan esos
  nombres. Si no hay grilla, se usan los nombres fijos de config.competencia.

Para agregar una fuente: sumá un dict a FUENTES. Si es de un tipo ya soportado
("planilla_television", "blogger", "x_cuenta") alcanza con eso. Si es un sitio
nuevo, escribí una función obtener_<tipo>(fuente, fecha, cfg, args) que devuelva
{canal: [(programa, rating), ...]} y registrala en TIPOS.
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
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def hoy_ar() -> dt.date:
    """La fecha en Argentina (el cron corre en UTC: a las 01:30 UTC del lunes todavía es domingo acá)."""
    return dt.datetime.now(TZ).date()

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "emisiones.js"
UA = {"User-Agent": "Mozilla/5.0 (compatible; DomingoRating/1.0)"}
ESCALA_PRINCIPAL = "ibope_amba"

# =====================================================================
#  FUENTES, en orden de prioridad. Agregá acá.
# =====================================================================
FUENTES = [
    {
        "id": "television.com.ar",
        "tipo": "planilla_television",
        "escala": "ibope_amba",
        "listado": "https://television.com.ar/noticias/?categoria=ratings",
        "nota": "Planilla diaria 'los programas más vistos de cada canal' (top 5 por canal). Sale el día siguiente a la mañana.",
    },
    {
        "id": "poneleshow",
        "tipo": "blogger",
        "escala": "ibope_amba",
        "feed": "https://poneleshow.blogspot.com/feeds/posts/default?alt=json&max-results=60",
        # título del post por canal; {dd} y {mm} se reemplazan por la fecha de la emisión
        "titulo": r"^rating\s+{dd}/{mm}\s*:\s*los\s+m[aá]s\s+vistos\s+de\s+(.+)$",
        "nota": "Blog de @PoneleShow. Un post por canal con más programas que el top 5. Sale el día siguiente ~16:00.",
    },
    {
        "id": "x:ismael_mon129",
        "tipo": "x_cuenta",
        "escala": "nacional",
        "cuenta": "ismael_mon129",
        "nota": "'ARGENTINA EN PANTALLA'. Posts '#Programa 13.2' por línea; dice ser promedio nacional, no coincide con AMBA. Solo respaldo.",
    },
]

# Grillas de programación (para saber quién compite en la franja). Solo sirven para "hoy".
GRILLAS = [
    {
        "id": "telered",
        "tipo": "grilla_telered",
        "url": "https://www.telered.com.ar/layout/grillaTVupd.php?prti={h1}&prtf={h2}&&wn=0&pack=D",
        "nota": "EPG del cable TeleRed. Devuelve la grilla de HOY entre dos horas, todos los canales.",
    },
]

# Alias para matchear nombres de canal tal como los escriben las fuentes
ALIAS = {
    "telefe": "Telefe",
    "eltrece": "eltrece", "el trece": "eltrece", "canal 13": "eltrece",
    "america": "América", "américa": "América", "america tv": "América",
    "elnueve": "elnueve", "el nueve": "elnueve", "canal 9": "elnueve",
    "canal siete": "TV Pública", "canal 7": "TV Pública", "tv publica": "TV Pública", "tv pública": "TV Pública",
    "television publica": "TV Pública", "tvp": "TV Pública",
    "net tv": "NET TV", "alpha tv": "Alpha TV",
}
DIAS_ES = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]

Planilla = dict[str, list[tuple[str, float]]]


# ---------------------------------------------------------------- utilidades

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def canal_norm(s: str) -> str:
    return ALIAS.get(norm(s), s.strip())


def hashtag_norm(s: str) -> str:
    """'#Otro Día Perdido' -> 'otrodiaperdido'"""
    return re.sub(r"[^a-z0-9]", "", norm(s))


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="ignore")


def html_a_lineas(h: str) -> list[str]:
    """HTML (o texto) -> líneas de texto plano, sin vacías."""
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|</?(p|li|ol|ul|h\d|div|tr|td|span|strong|b|i)\b[^>]*>", "\n", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    lineas = [re.sub(r"[ \t ]+", " ", l).strip() for l in t.splitlines()]
    return [l for l in lineas if l]


RE_PUESTO = re.compile(r"^\d{1,2}\s*[\.\)]?$")
RE_RATING = re.compile(r"^(\d{1,2}[\.,]\d)$")
RE_LINEA = re.compile(r"^(?:\d{1,2}[\.\)]?\s+)?(.+?)\s+(\d{1,2}[\.,]\d)\s*$")
RE_FECHA = re.compile(r"^\d{1,2}\s+(de\s+)?[a-záéíóú]+(\s+\d{4})?$", re.I)


def parsear_items(lineas: list[str]) -> list[tuple[str, float]]:
    """Lista de programas con rating, aceptando 'Programa 13.3' en una línea o
    puesto / nombre / rating en líneas separadas. Ignora puestos, fechas y cabeceras."""
    items, pendiente = [], None
    for l in lineas:
        low = l.lower()
        if RE_PUESTO.match(l) or RE_FECHA.match(l) or low.startswith(("fuente", "promedio", "programa destacado", "los destacados")):
            if low.startswith("fuente"):
                pendiente = None
            continue
        m = RE_RATING.match(l)
        if m:
            if pendiente:
                items.append((pendiente, float(m.group(1).replace(",", "."))))
                pendiente = None
            continue
        m = RE_LINEA.match(l)
        if m:
            items.append((m.group(1).strip(" -–·"), float(m.group(2).replace(",", "."))))
            pendiente = None
            continue
        pendiente = l.strip(" -–·")
    return items


def buscar_programa(planilla: Planilla, canal: str, programa: str) -> float | None:
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


def resultado_desde_planilla(planilla: Planilla, cfg: dict) -> tuple[float | None, dict[str, float | None]]:
    rating = buscar_programa(planilla, cfg.get("canal"), cfg.get("programa"))
    if rating is None:  # por si no está en el top de su canal pero sí en otro bloque
        rating = buscar_programa(planilla, "", cfg.get("programa"))
    comp = {canal_norm(c["canal"]): buscar_programa(planilla, c["canal"], c.get("programa", "")) for c in cfg.get("competencia", [])}
    return rating, comp


def imprimir_planilla(fid: str, planilla: Planilla) -> None:
    for canal, items in planilla.items():
        print(f"  [{fid}] {canal}: " + ", ".join(f"{p} {v}" for p, v in items))


# ---------------------------------------------------------------- tipos de fuente
# Cada función recibe (fuente, fecha, cfg, args) y devuelve una Planilla
# {canal: [(programa, rating), ...]} o None si no hay datos.

RE_CANAL = re.compile(r"^(?:los\s+)?(?:cinco|5|diez|10)?\s*m[aá]s\s+vistos?\s+(?:de\s+)?(.+)$", re.I)


def obtener_planilla_television(f: dict, fecha: dt.date, cfg: dict, args) -> Planilla | None:
    url = args.url
    if not url:
        pagina = fetch(f["listado"])
        links = list(dict.fromkeys(re.findall(r'href="(https?://television\.com\.ar/noticias/ratings/[^"]+)"', pagina)))
        cand = [u for u in links if "mas-vistos" in u and fecha.strftime("%Y-%m-%d") in u]
        if not cand:
            cand = [u for u in links if "mas-vistos" in u and re.search(rf"-{fecha.day}-de-", u)]
        if not cand:
            print(f"  [{f['id']}] no encontré la planilla del {fecha} en el listado")
            return None
        url = cand[0]
    print(f"  [{f['id']}] nota: {url}")
    lineas = html_a_lineas(fetch(url))
    planilla: Planilla = {}
    bloque, actual = [], None
    for l in lineas + ["Fuente: fin"]:
        m = RE_CANAL.match(l)
        if m or l.lower().startswith("fuente"):
            if actual and bloque:
                items = parsear_items(bloque)
                if items:
                    planilla[actual] = items
            actual = canal_norm(m.group(1)) if m else None
            bloque = []
            continue
        if actual:
            bloque.append(l)
    return planilla or None


def obtener_blogger(f: dict, fecha: dt.date, cfg: dict, args) -> Planilla | None:
    feed = json.loads(fetch(f["feed"]))
    pat = re.compile(f["titulo"].format(dd=f"{fecha.day:02d}", mm=f"{fecha.month:02d}"), re.I)
    planilla: Planilla = {}
    for e in feed.get("feed", {}).get("entry", []):
        m = pat.match(e.get("title", {}).get("$t", "").strip())
        if not m:
            continue
        items = parsear_items(html_a_lineas(e.get("content", {}).get("$t", "")))
        if items:
            planilla[canal_norm(m.group(1))] = [(p.title(), v) for p, v in items]
    if not planilla:
        print(f"  [{f['id']}] todavía no hay posts para el {fecha:%d/%m}")
    return planilla or None


def obtener_x_cuenta(f: dict, fecha: dt.date, cfg: dict, args) -> Planilla | None:
    """Posts '#Programa 13.2' por línea. Con --x-post usa ese post (api.fxtwitter.com);
    si no, junta los posts del día del timeline público (syndication, a veces 429)."""
    user = f["cuenta"]
    if args.x_post:
        m = re.search(r"(?:x\.com|twitter\.com)/([^/]+)/status/(\d+)", args.x_post)
        u, pid = (m.group(1), m.group(2)) if m else (user, re.sub(r"\D", "", args.x_post))
        data = json.loads(fetch(f"https://api.fxtwitter.com/{u}/status/{pid}"))
        if data.get("code") != 200:
            print(f"  [{f['id']}] fxtwitter devolvió {data.get('code')}")
            return None
        textos = [data["tweet"].get("text", "")]
    else:
        page = fetch(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{user}")
        tweets: list[dict] = []
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
        if m:
            try:
                _tweets_de(json.loads(m.group(1)), tweets)
            except json.JSONDecodeError:
                pass
        if not tweets:
            for t in re.findall(r'"full_text":"((?:[^"\\]|\\.)*)"', page):
                tweets.append({"full_text": json.loads(f'"{t}"'), "created_at": ""})
        dia = DIAS_ES[fecha.weekday()]
        textos = [t["full_text"] for t in tweets
                  if "#rating" in t["full_text"].lower() and f"-{dia}-" in norm(t["full_text"]).upper()
                  and _fecha_ok(t.get("created_at", ""), fecha)]
        if not textos:
            print(f"  [{f['id']}] no hay posts '#Rating ... -{dia}-' del {fecha:%d/%m} en el timeline")
            return None
    vals: dict[str, float] = {}
    for t in textos:
        for m in re.finditer(r"#([^\s#]+)\s+(\d{1,2}[\.,]\d)", t):
            vals[hashtag_norm(m.group(1))] = float(m.group(2).replace(",", "."))
    if not vals:
        return None
    # los hashtags no traen canal: se devuelven como un solo bloque "(X)" y el match es por nombre
    return {"(X)": [(k, v) for k, v in vals.items()]}


def _tweets_de(obj, out: list) -> None:
    if isinstance(obj, dict):
        if "full_text" in obj and "created_at" in obj:
            out.append(obj)
        for v in obj.values():
            _tweets_de(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _tweets_de(v, out)


def _fecha_ok(created: str, fecha: dt.date) -> bool:
    if not created:
        return True
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            f = dt.datetime.strptime(created, fmt).date()
            return fecha <= f <= fecha + dt.timedelta(days=2)
        except ValueError:
            continue
    return True


def buscar_programa_x(planilla: Planilla, nombre: str, hashtag: str = "") -> float | None:
    """Match por hashtag normalizado, exacto o por prefijo (para que 'Domingo' no agarre 'CineDelDomingo')."""
    vals = dict(planilla.get("(X)", []))
    claves = [hashtag_norm(hashtag)] if hashtag else []
    claves.append(hashtag_norm(nombre))
    for k in claves:
        if k and k in vals:
            return vals[k]
    n = claves[-1]
    for k, v in vals.items():
        if n and len(n) >= 5 and (k.startswith(n) or n.startswith(k)):
            return v
    return None


TIPOS = {
    "planilla_television": obtener_planilla_television,
    "blogger": obtener_blogger,
    "x_cuenta": obtener_x_cuenta,
}


def consultar(f: dict, fecha: dt.date, cfg: dict, args) -> tuple[float | None, dict[str, float | None]] | None:
    """Corre una fuente y devuelve (rating_propio, {canal: rating_rival}) o None."""
    try:
        planilla = TIPOS[f["tipo"]](f, fecha, cfg, args)
    except Exception as e:
        print(f"  [{f['id']}] falló: {e}")
        return None
    if not planilla:
        return None
    imprimir_planilla(f["id"], planilla)
    if f["tipo"] == "x_cuenta":
        rating = buscar_programa_x(planilla, cfg.get("programa", ""), cfg.get("hashtag", ""))
        comp = {canal_norm(c["canal"]): buscar_programa_x(planilla, c.get("programa", ""), c.get("hashtag", "")) for c in cfg.get("competencia", [])}
        return rating, comp
    return resultado_desde_planilla(planilla, cfg)


# ---------------------------------------------------------------- combinar

def combinar(resultados: list[tuple[dict, tuple]], cfg: dict) -> tuple[float | None, dict[str, float | None], list[str]]:
    """resultados: [(fuente, (rating, comp)), ...] en orden de prioridad, solo las que respondieron."""
    prog = cfg.get("programa", "")
    canales = [canal_norm(c["canal"]) for c in cfg.get("competencia", [])]

    # diferencias entre todas las fuentes que respondieron (informativo)
    for i, (fa, ra) in enumerate(resultados):
        for fb, rb in resultados[i + 1:]:
            for nombre, va, vb in [(prog, ra[0], rb[0])] + [(k, ra[1].get(k), rb[1].get(k)) for k in canales]:
                if va is not None and vb is not None and abs(va - vb) >= 0.05:
                    print(f"  ≠ {nombre}: {fa['id']} {va} vs {fb['id']} {vb}")

    rating, comp, fuentes = None, {}, []
    for f, (r, c) in resultados:
        if f["escala"] != ESCALA_PRINCIPAL:
            continue
        usado = False
        if rating is None and r is not None:
            rating, usado = r, True
        for k in canales:
            if comp.get(k) is None and c.get(k) is not None:
                comp[k], usado = c[k], True
        if usado:
            fuentes.append(f["id"])
    if rating is not None:
        return rating, {k: comp.get(k) for k in canales}, fuentes

    for f, (r, c) in resultados:  # otras escalas: solo una, entera, y provisoria
        if f["escala"] != ESCALA_PRINCIPAL and r is not None:
            print(f"  ! uso {f['id']} (escala '{f['escala']}', no AMBA): la emisión queda provisoria")
            return r, {k: c.get(k) for k in canales}, [f["id"]]
    return None, {}, []


# ---------------------------------------------------------------- grillas

def obtener_grilla_telered(g: dict, h1: int, h2: int) -> dict[str, list[tuple[int, int, str]]]:
    """{canal: [(min_desde, min_hasta, titulo), ...]} para hoy entre h1 y h2 (horas)."""
    page = fetch(g["url"].format(h1=h1, h2=h2))
    grilla: dict[str, list[tuple[int, int, str]]] = {}
    for bloque in re.split(r'<li class="chrow', page)[1:]:
        m = re.search(r'class="chname">\s*([^<]+?)\s*<', bloque)
        if not m:
            continue
        canal = canal_norm(html.unescape(m.group(1)))
        progs = []
        for pm in re.finditer(r'data-horadesdeex="(\d{1,2}):(\d{2})"[^>]*data-horahastaex="(\d{1,2}):(\d{2})"[^>]*>.*?class="programtitle">\s*([^<]*?)\s*<', bloque, re.S):
            d = int(pm.group(1)) * 60 + int(pm.group(2))
            h = int(pm.group(3)) * 60 + int(pm.group(4))
            if h <= d:
                h += 24 * 60  # cruza medianoche
            progs.append((d, h, html.unescape(pm.group(5))))
        if progs:
            grilla[canal] = progs
    return grilla


GRILLA_TIPOS = {"grilla_telered": obtener_grilla_telered}


def programa_en_franja(progs: list[tuple[int, int, str]], ini: int, fin: int) -> str | None:
    """El programa con más minutos dentro de [ini, fin) (minutos desde las 00:00)."""
    mejor, mejor_min = None, 0
    for d, h, t in progs:
        solap = min(h, fin) - max(d, ini)
        if solap > mejor_min and not re.search(r"fin de transmisi|cierre de transmisi", t, re.I):
            mejor, mejor_min = t, solap
    return mejor


def correr_grilla(d: dict, fecha: dt.date, args) -> None:
    cfg = d["config"]
    hh, mm = (cfg.get("horario") or "22:00").split(":")
    ini = int(hh) * 60 + int(mm)
    fin = ini + int(cfg.get("duracion_min") or 90)
    h1, h2 = ini // 60, min(23, (fin - 1) // 60)
    canales = [c["canal"] for c in cfg.get("competencia", [])]
    encontrados: dict[str, str] = {}
    for g in GRILLAS:
        print(f"→ grilla {g['id']}")
        try:
            grilla = GRILLA_TIPOS[g["tipo"]](g, h1, h2)
        except Exception as e:
            print(f"  [{g['id']}] falló: {e}")
            continue
        for canal in canales + [cfg.get("canal", "")]:
            k = canal_norm(canal)
            progs = next((v for c, v in grilla.items() if canal_norm(c) == k), None)
            if not progs:
                continue
            print(f"  [{g['id']}] {canal}: " + " | ".join(f"{a//60:02d}:{a%60:02d}-{(b%1440)//60:02d}:{b%60:02d} {t}" for a, b, t in progs))
            p = programa_en_franja(progs, ini, fin)
            if p and k not in encontrados:
                encontrados[k] = p
        if len(encontrados) >= len(canales):
            break
    if not encontrados:
        sys.exit("Ninguna grilla respondió. Las corridas de rating usarán los nombres fijos de config.competencia.")

    emisiones = d.setdefault("emisiones", [])
    existente = next((e for e in emisiones if e.get("fecha") == fecha.isoformat()), None)
    competencia = []
    for c in cfg.get("competencia", []):
        k = canal_norm(c["canal"])
        prev = next((e for e in (existente or {}).get("competencia", []) if canal_norm(e["canal"]) == k), {})
        nombre = encontrados.get(k) or prev.get("programa") or c.get("programa", "")
        if k in encontrados:
            print(f"  ✓ {c['canal']}: en la franja va '{encontrados[k]}'")
        else:
            print(f"  ! {c['canal']}: la grilla no lo trajo; uso '{nombre or '(nada)'}'")
        competencia.append({"canal": c["canal"], "programa": nombre, "rating": prev.get("rating")})
    propio_k = canal_norm(cfg.get("canal", ""))
    if propio_k in encontrados:
        print(f"  ✓ {cfg.get('canal')}: la grilla dice '{encontrados[propio_k]}' (el programa propio se sigue buscando como '{cfg.get('programa')}')")

    nueva = {
        "numero": existente.get("numero") if existente else len(emisiones) + 1,
        "fecha": fecha.isoformat(),
        "rating": (existente or {}).get("rating"),
        "youtube": args.youtube or (existente or {}).get("youtube", ""),
        "estado": (existente or {}).get("estado", "pendiente"),
        "fuentes": (existente or {}).get("fuentes", []),
        "competencia": competencia,
    }
    if existente:
        if {k: existente.get(k) for k in nueva} == nueva:
            print("\nSin cambios.")
            return
        existente.update(nueva)
    else:
        emisiones.append(nueva)
    emisiones.sort(key=lambda e: e["fecha"])
    for i, e in enumerate(emisiones, 1):
        e["numero"] = i
    print("\nEmisión:", json.dumps(nueva, ensure_ascii=False, indent=2))
    if args.dry_run:
        print("(dry-run, no escribí nada)")
        return
    escribir_datos(d)
    print(f"\nListo: {DATA}")


# ---------------------------------------------------------------- datos

def leer_datos() -> dict:
    txt = DATA.read_text(encoding="utf-8")
    m = re.search(r"^window\.DOMINGO_DATA\s*=\s*(\{.*\})\s*;?\s*$", txt, re.S | re.M)
    if not m:
        sys.exit(f"No pude encontrar window.DOMINGO_DATA en {DATA}")
    return json.loads(m.group(1))


def escribir_datos(d: dict) -> None:
    cab = ("// ==========================================================\n"
           "//  ÚNICA FUENTE DE VERDAD DE LA PÁGINA\n"
           "//  Lo edita a mano o lo reescribe scripts/actualizar_rating.py\n"
           "//  Formato: JSON válido adentro de window.DOMINGO_DATA = {...};\n"
           "// ==========================================================\n")
    DATA.write_text(cab + "window.DOMINGO_DATA = " + json.dumps(d, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")


def ultimo_domingo(hoy: dt.date) -> dt.date:
    return hoy - dt.timedelta(days=(hoy.weekday() + 1) % 7)


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fecha", help="YYYY-MM-DD de la emisión (default: último domingo)")
    ap.add_argument("--youtube", help="Link al programa subido")
    ap.add_argument("--fuente", action="append", default=[], help="Consultar solo esta fuente (id; repetible)")
    ap.add_argument("--listar-fuentes", action="store_true")
    ap.add_argument("--x-post", help="Link (o id) de un post de X para la fuente de X")
    ap.add_argument("--url", help="Nota exacta de television.com.ar")
    ap.add_argument("--rating", type=float, help="Rating propio (carga manual)")
    ap.add_argument("--comp", action="append", default=[], help='"Canal=rating" (repetible, carga manual)')
    ap.add_argument("--grilla", action="store_true", help="Solo anotar quién compite en la franja (grilla de HOY); correr el domingo a la noche")
    ap.add_argument("--dry-run", action="store_true", help="Muestra lo que haría sin escribir")
    a = ap.parse_args()

    if a.listar_fuentes:
        for f in FUENTES:
            print(f"{f['id']:<22} tipo={f['tipo']:<20} escala={f['escala']:<11} {f.get('nota', '')}")
        return

    d = leer_datos()
    cfg = d["config"]
    if a.grilla:
        correr_grilla(d, dt.date.fromisoformat(a.fecha) if a.fecha else hoy_ar(), a)
        return
    fecha = dt.date.fromisoformat(a.fecha) if a.fecha else ultimo_domingo(hoy_ar())
    emisiones = d.setdefault("emisiones", [])
    existente = next((e for e in emisiones if e.get("fecha") == fecha.isoformat()), None)
    # los nombres de los rivales: los que anotó la grilla para esta emisión, si no los fijos de config
    comp_cfg = [dict(c) for c in cfg.get("competencia", [])]
    for c in comp_cfg:
        anotado = next((e for e in (existente or {}).get("competencia", []) if canal_norm(e["canal"]) == canal_norm(c["canal"])), None)
        if anotado and anotado.get("programa"):
            c["programa"] = anotado["programa"]
    cfg = dict(cfg, competencia=comp_cfg)
    ids_amba = {f["id"] for f in FUENTES if f["escala"] == ESCALA_PRINCIPAL}
    print(f"Emisión del {fecha} ({DIAS_ES[fecha.weekday()].title()})")

    rating, comp_vals, fuentes = a.rating, {}, []
    for c in a.comp:
        k, _, v = c.partition("=")
        comp_vals[canal_norm(k)] = float(v.replace(",", "."))
    if rating is not None or a.comp:
        fuentes.append("manual")

    if rating is None:
        resultados = []
        for f in FUENTES:
            if a.fuente and f["id"] not in a.fuente:
                continue
            print(f"→ {f['id']}")
            r = consultar(f, fecha, cfg, a)
            if r:
                resultados.append((f, r))
        # si una fuente AMBA ya se había usado y hoy ninguna responde, lo cargado sigue mandando
        if existente and set(existente.get("fuentes", [])) & ids_amba and not any(f["escala"] == ESCALA_PRINCIPAL for f, _ in resultados):
            print("  (las fuentes AMBA no respondieron hoy; conservo lo ya cargado de ellas)")
            previo = (existente.get("rating"), {canal_norm(c["canal"]): c.get("rating") for c in existente.get("competencia", [])})
            resultados.insert(0, ({"id": existente["fuentes"][0], "escala": ESCALA_PRINCIPAL}, previo))
        rating, comp_auto, fuentes = combinar(resultados, cfg)
        for k, v in comp_auto.items():
            comp_vals.setdefault(k, v)
        if rating is None:
            if existente and existente.get("rating") is not None:
                print(f"\nNinguna fuente tiene '{cfg.get('programa')}' hoy; dejo la emisión como estaba.")
                return
            sys.exit(f"\nNinguna fuente tiene '{cfg.get('programa')}' para el {fecha}. Probá más tarde, "
                     "pasá --x-post con un link, o cargá a mano con --rating/--comp.")

    misma_escala_que_antes = bool(existente) and (set(existente.get("fuentes", [])) <= ids_amba | {"manual"}) and (set(fuentes) <= ids_amba | {"manual"})
    competencia = []
    for c in comp_cfg:
        k = canal_norm(c["canal"])
        v = comp_vals.get(k)
        if v is None and misma_escala_que_antes:  # conservar rivales ya cargados de la misma escala
            v = next((e.get("rating") for e in existente.get("competencia", []) if canal_norm(e["canal"]) == k), None)
        if v is None:
            print(f"  ! {c['canal']}: sin dato de '{c.get('programa') or '(sin programa configurado)'}'; queda pendiente")
        competencia.append({"canal": c["canal"], "programa": c.get("programa", ""), "rating": v})
    for k, v in comp_vals.items():  # canales cargados a mano que no estaban en config
        if not any(canal_norm(c["canal"]) == k for c in comp_cfg):
            competencia.append({"canal": k, "programa": "", "rating": v})
    provisorio = any(c["rating"] is None for c in competencia) or not (set(fuentes) & (ids_amba | {"manual"}))

    nueva = {
        "numero": existente.get("numero") if existente else len(emisiones) + 1,
        "fecha": fecha.isoformat(),
        "rating": rating,
        "youtube": a.youtube or (existente or {}).get("youtube", ""),
        "estado": "provisorio" if provisorio else "confirmado",
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
