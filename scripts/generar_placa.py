#!/usr/bin/env python3
"""
Genera la placa 1200x630 del último round con dato (assets/placas/round-N.jpg)
y apunta las metas og:image / twitter:image de index.html a esa placa, para que
al compartir el link en X o WhatsApp la tarjeta muestre el rating de ese round.

Usa ImageMagick (`magick` o `convert`) con las fuentes de assets/fonts/.
Idempotente: si la placa y el HTML ya están al día, no escribe nada.

    python3 scripts/generar_placa.py            # genera y actualiza index.html
    python3 scripts/generar_placa.py --dry-run  # solo informa
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from actualizar_rating import leer_datos  # noqa: E402

DOMINIO = "https://rebordcontraibope.com"
INDEX = ROOT / "index.html"
PLACAS = ROOT / "assets" / "placas"
POSTER = ROOT / "assets" / "poster.webp"
FONT_DISPLAY = ROOT / "assets" / "fonts" / "Anton-Regular.ttf"
FONT_BODY = ROOT / "assets" / "fonts" / "Barlow-SemiBold.ttf"

BG, CREMA, GRIS, DORADO, BORDO, AZUL = "#070c1c", "#f5efe0", "#b9b4a6", "#f2b544", "#b3202f", "#172448"
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def magick() -> list[str]:
    for c in ("magick", "convert"):
        if shutil.which(c):
            return [c]
    sys.exit("Falta ImageMagick (magick/convert).")


def fmt1(n) -> str:
    return "–" if n is None else f"{n:.1f}".replace(".", ",")


def fecha_larga(iso: str) -> str:
    from datetime import date
    d = date.fromisoformat(iso)
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def veredicto(em: dict) -> dict:
    comp = [c for c in em.get("competencia", []) if c.get("rating") is not None]
    wins = sum(1 for c in comp if em["rating"] > c["rating"])
    total = len(comp)
    if not total:
        return {"cls": "mixed", "lineas": ["RATING SEGÚN IBOPE"], "wins": wins, "total": total}
    if wins == total:
        return {"cls": "win", "lineas": [f"LE GANÓ A LOS {total}", "CANALES DE AIRE"], "wins": wins, "total": total}
    if wins == 0:
        return {"cls": "lose", "lineas": [f"PERDIÓ CON LOS {total}", "CANALES DE AIRE"], "wins": wins, "total": total}
    return {"cls": "mixed", "lineas": [f"LE GANÓ A {wins} DE {total}", "CANALES DE AIRE"], "wins": wins, "total": total}


def rival_que_gano(em: dict):
    rivales = [c for c in em.get("competencia", []) if c.get("rating") is not None and c["rating"] >= em["rating"]]
    return max(rivales, key=lambda c: c["rating"]) if rivales else None


def sello(em: dict, v: dict):
    """Texto del sello y color de fondo, o None si no hay competencia cargada."""
    if v["cls"] == "win":
        return "IBOPE ESTÁ COGIDO", BORDO
    if v["total"] > 0:
        r = rival_que_gano(em)
        nombre = (r.get("programa") or r["canal"]).upper()
        return f"{nombre} NOS COGIÓ", AZUL
    return None


def componer(em: dict, destino: Path) -> None:
    v = veredicto(em)
    n = em.get("numero") or "?"
    kicker = f"ROUND {n} · {fecha_larga(em['fecha']).upper()}"
    rivales = " · ".join(f"{c['canal']} {fmt1(c.get('rating'))}" for c in em.get("competencia", []))
    provisorio = em.get("estado") == "provisorio"

    cmd = magick() + ["-size", "1200x630", f"xc:{BG}"]
    # fondo: afiche desenfocado y oscurecido, como el hero de la página
    cmd += ["(", str(POSTER), "-resize", "1200x630^", "-gravity", "center", "-extent", "1200x630",
            "-blur", "0x18", "-fill", BG, "-colorize", "72", ")", "-compose", "over", "-composite"]
    # afiche entero como tarjeta a la izquierda
    cmd += ["(", str(POSTER), "-resize", "x540", "-bordercolor", DORADO, "-border", "4", ")",
            "-gravity", "west", "-geometry", "+70+0", "-composite"]
    cmd += ["-gravity", "northwest"]
    # kicker
    cmd += ["-font", str(FONT_BODY), "-fill", DORADO, "-pointsize", "26", "-annotate", "+562+70", kicker]
    # rating grande + "puntos" al lado
    cmd += ["-font", str(FONT_DISPLAY), "-fill", DORADO, "-pointsize", "190", "-annotate", "+556+95", fmt1(em["rating"])]
    ancho_rating = 68 * len(fmt1(em["rating"]))  # Anton es angosta; alcanza para ubicar el texto de al lado
    cmd += ["-font", str(FONT_BODY), "-fill", GRIS, "-pointsize", "24",
            "-annotate", f"+{566 + ancho_rating}+212", "puntos de rating",
            "-annotate", f"+{566 + ancho_rating}+242", "según IBOPE" + (" (provisorio)" if provisorio else "")]
    # veredicto
    y = 310
    for linea in v["lineas"]:
        cmd += ["-font", str(FONT_DISPLAY), "-fill", CREMA, "-pointsize", "46", "-annotate", f"+560+{y}", linea]
        y += 52
    # sello como etiqueta con fondo (label se autoajusta al texto)
    s = sello(em, v)
    if s:
        texto, color = s
        pt = "44" if len(texto) <= 20 else "34" if len(texto) <= 30 else "26"
        # +size: que el label no herede el tamaño del lienzo; -background none antes de rotar para esquinas limpias
        cmd += ["(", "+size", "-background", color, "-fill", DORADO if color == BORDO else CREMA,
                "-font", str(FONT_DISPLAY), "-pointsize", pt, f"label:  {texto}  ",
                "-background", "none", "-rotate", "-2", ")",
                "-geometry", f"+556+{y + 10}", "-composite"]
        y += 90
    # rivales
    if rivales:
        cmd += ["-font", str(FONT_BODY), "-fill", GRIS, "-pointsize", "20", "-annotate", f"+562+{max(y, 500)}", rivales]
    # url
    cmd += ["-font", str(FONT_BODY), "-fill", DORADO, "-pointsize", "22", "-annotate", "+562+578", "REBORDCONTRAIBOPE.COM"]
    cmd += ["-quality", "85", str(destino)]
    subprocess.run(cmd, check=True)


def actualizar_index(url_placa: str, alt: str) -> bool:
    html = INDEX.read_text(encoding="utf-8")
    nuevo = html
    reemplazos = [
        (r'(<meta property="og:image" content=")[^"]*(")', url_placa),
        (r'(<meta name="twitter:image" content=")[^"]*(")', url_placa),
        (r'(<meta property="og:image:alt" content=")[^"]*(")', alt),
        (r'("primaryImageOfPage": ")[^"]*(")', url_placa),
    ]
    for patron, valor in reemplazos:
        nuevo, n = re.subn(patron, lambda m: m.group(1) + valor + m.group(2), nuevo, count=1)
        if n != 1:
            sys.exit(f"No encontré en index.html el patrón {patron!r}; revisá las metas.")
    if nuevo == html:
        return False
    INDEX.write_text(nuevo, encoding="utf-8")
    return True


def main() -> None:
    dry = "--dry-run" in sys.argv
    con_dato = [e for e in leer_datos().get("emisiones", []) if e.get("rating") is not None]
    if not con_dato:
        print("Todavía no hay ningún round con rating; la tarjeta sigue con la placa genérica.")
        if dry:  # igual renderizamos una de prueba: valida ImageMagick, webp y fuentes en el runner
            muestra = {"numero": 1, "fecha": "2026-11-15", "rating": 4.2, "estado": "confirmado",
                       "competencia": [{"canal": "Telefe", "programa": "MasterChef Celebrity", "rating": 7.9},
                                       {"canal": "eltrece", "programa": "Cine", "rating": 3.1}]}
            with tempfile.TemporaryDirectory() as tmp:
                destino = Path(tmp) / "prueba.jpg"
                componer(muestra, destino)
                print(f"[dry-run] render de prueba OK ({destino.stat().st_size} bytes).")
        return
    em = max(con_dato, key=lambda e: e["fecha"])
    nombre = f"round-{em.get('numero') or em['fecha']}.jpg"
    destino = PLACAS / nombre
    v = veredicto(em)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_jpg = Path(tmp) / nombre
        componer(em, tmp_jpg)
        placa_cambio = not destino.exists() or destino.read_bytes() != tmp_jpg.read_bytes()
        if placa_cambio and not dry:
            PLACAS.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(tmp_jpg, destino)

    url = f"{DOMINIO}/assets/placas/{nombre}"
    alt = f"Round {em.get('numero')}: Dominbord hizo {fmt1(em['rating'])} puntos de rating según IBOPE. " + " ".join(v["lineas"]).capitalize() + "."
    if dry:
        print(f"[dry-run] placa {'nueva/cambiada' if placa_cambio else 'sin cambios'}: {destino.relative_to(ROOT)}")
        print(f"[dry-run] og:image -> {url}")
        return
    html_cambio = actualizar_index(url, alt)
    print(f"Placa: {destino.relative_to(ROOT)} ({'escrita' if placa_cambio else 'sin cambios'}). "
          f"index.html {'actualizado' if html_cambio else 'sin cambios'}.")


if __name__ == "__main__":
    main()
