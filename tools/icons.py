"""Genera todos los iconos de Quincena VE desde un solo diseño: la "Q" con los colores de la bandera de Venezuela
(amarillo, azul y rojo en tercios iguales), las 8 estrellas en arco y la cola en el verde de Quincena.

Ejecutar desde la raíz del proyecto:  python3 tools/icons.py
Necesita Google Chrome (para dibujar los SVG) y sips (viene con macOS). No necesita librerías de Python.

Crea:
  static/icons/icon.svg              icono vectorial (favicon moderno)
  static/icons/favicon.ico           16, 32 y 48 px para navegadores viejos y Windows
  static/icons/icon-192.png          icono "any" con esquinas redondeadas (PWA, aviso de APK)
  static/icons/icon-512.png
  static/icons/maskable-512.png      icono a sangre para Android al instalar la web (el sistema le pone la forma)
  static/icons/apple-touch-icon.png  180 px a sangre (iOS redondea solo)
  android/.../mipmap-*/ic_launcher.png         respaldo en PNG del icono de la app
  android/.../drawable/ic_launcher_*.xml       icono adaptativo vectorial (fondo, figura y capa monocromática)
"""
import math
import pathlib
import shutil
import struct
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ICONS = ROOT / "static" / "icons"
RES = ROOT / "android" / "app" / "src" / "main" / "res"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

YELLOW, BLUE, RED, GREEN, BACKGROUND = "#FFCC00", "#00247D", "#CF142B", "#0B6B4D", "#FFFFFF"

# Geometría en un lienzo de 512. El anillo va de y=85 a y=387: cada franja ocupa un tercio exacto.
RING_C, RING_R, STROKE = (246, 236), 122, 58
BANDS = (85, 186, 286, 387)
TAIL = ((262, 330), (330, 398), (410, 398))
STARS_C, STARS_RAD, STAR_R, STARS_FROM, STARS_TO = (246, 270), 62, 14, 200, 340
CENTER = (267, 256)  # centro de la figura completa (anillo + cola)


def star_points(cx: float, cy: float, r: float) -> list:
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.42
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def stars() -> list:
    out = []
    for i in range(8):
        a = math.radians(STARS_FROM + (STARS_TO - STARS_FROM) * i / 7)
        out.append(star_points(STARS_C[0] + STARS_RAD * math.cos(a), STARS_C[1] + STARS_RAD * math.sin(a), STAR_R))
    return out


def mark(scale: float = 1.0) -> str:
    """La Q en SVG. scale < 1 la achica hacia el centro para respetar la zona segura."""
    cx, cy = RING_C
    t = f'translate(256 256) scale({scale}) translate({-CENTER[0]} {-CENTER[1]})'
    clips = "".join(f'<clipPath id="b{i}"><rect y="{y0 if i else 0}" width="512" height="{(y1 if i < 2 else 512) - (y0 if i else 0)}"/></clipPath>'
                    for i, (y0, y1) in enumerate(zip(BANDS[:-1], BANDS[1:])))
    ring = "".join(f'<circle cx="{cx}" cy="{cy}" r="{RING_R}" fill="none" stroke="{c}" stroke-width="{STROKE}" clip-path="url(#b{i})"/>'
                   for i, c in enumerate((YELLOW, BLUE, RED)))
    star_svg = "".join(f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in s)}" fill="{BLUE}"/>' for s in stars())
    tail = " L".join(f"{x} {y}" for x, y in TAIL)
    return (f'<g transform="{t}"><defs>{clips}</defs>{ring}{star_svg}'
            f'<path d="M{tail}" fill="none" stroke="{GREEN}" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round"/></g>')


def svg(rounded: bool, scale: float) -> str:
    shape = f'<rect width="512" height="512" rx="112" fill="{BACKGROUND}"/>' if rounded else f'<rect width="512" height="512" fill="{BACKGROUND}"/>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{shape}{mark(scale)}</svg>'


def render(svg_text: str, out: pathlib.Path, size: int, tmp: pathlib.Path) -> None:
    """Dibuja el SVG a 512 px con Chrome (fondo transparente) y lo reduce con sips al tamaño pedido."""
    page = tmp / "i.html"
    page.write_text(f'<html><body style="margin:0;background:transparent">{svg_text.replace("<svg ", "<svg width=512 height=512 ", 1)}</body></html>')
    big = tmp / "big.png"
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars", "--default-background-color=00000000",
                    "--window-size=512,512", f"--screenshot={big}", page.as_uri()], check=True, capture_output=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(big, out)
    if size != 512:
        subprocess.run(["sips", "-z", str(size), str(size), str(out)], check=True, capture_output=True)


def write_ico(pngs: list, out: pathlib.Path) -> None:
    """Un .ico puede guardar PNG tal cual: cabecera + una entrada por tamaño + los PNG."""
    data = [p.read_bytes() for p in pngs]
    sizes = [struct.unpack(">II", d[16:24])[0] for d in data]
    offset = 6 + 16 * len(data)
    entries = b""
    for size, blob in zip(sizes, data):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    out.write_bytes(struct.pack("<HHH", 0, 1, len(data)) + entries + b"".join(data))


# ---- Android: el mismo dibujo como vector de 108 dp; la figura cabe en el círculo seguro de 66 dp ----
ANDROID_SCALE = 0.14


def dp(x: float, y: float) -> tuple:
    return 54 + (x - CENTER[0]) * ANDROID_SCALE, 54 + (y - CENTER[1]) * ANDROID_SCALE


def android_vectors(mono: bool) -> str:
    s = ANDROID_SCALE
    (cx, cy), r, w = dp(*RING_C), RING_R * s, STROKE * s
    circle = f"M{cx - r:.2f},{cy:.2f} a{r:.2f},{r:.2f} 0 1,0 {2 * r:.2f},0 a{r:.2f},{r:.2f} 0 1,0 {-2 * r:.2f},0"
    color = lambda c: "#FFFFFFFF" if mono else "#FF" + c.lstrip("#")
    parts = []
    bands = [0, *(dp(0, y)[1] for y in BANDS[1:-1]), 108]
    for i, c in enumerate((YELLOW, BLUE, RED)):
        y0, y1 = bands[i], bands[i + 1]
        parts.append(f'    <group>\n        <clip-path android:pathData="M0,{y0:.2f}h108v{y1 - y0:.2f}h-108z" />\n'
                     f'        <path android:strokeColor="{color(c)}" android:strokeWidth="{w:.2f}" android:fillColor="#00000000"\n'
                     f'            android:pathData="{circle}" />\n    </group>')
    star_paths = " ".join("M" + " L".join(f"{dp(x, y)[0]:.2f},{dp(x, y)[1]:.2f}" for x, y in st) + " Z" for st in stars())
    parts.append(f'    <path android:fillColor="{color(BLUE)}" android:pathData="{star_paths}" />')
    tail = " L".join(f"{dp(x, y)[0]:.2f},{dp(x, y)[1]:.2f}" for x, y in TAIL)
    parts.append(f'    <path android:strokeColor="{color(GREEN)}" android:strokeWidth="{w:.2f}" android:fillColor="#00000000"\n'
                 f'        android:strokeLineCap="round" android:strokeLineJoin="round" android:pathData="M{tail}" />')
    title = ("Capa para los íconos temáticos de Android 13+: la misma Q en un solo color (el sistema la tiñe)." if mono else
             "Ícono de Quincena VE: la Q con la bandera de Venezuela (tercios iguales y 8 estrellas) y la cola verde. Generado por tools/icons.py.")
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            f'<!-- {title} -->\n'
            '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
            '    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">\n'
            + "\n".join(parts) + "\n</vector>\n")


ANDROID_BACKGROUND = ('<?xml version="1.0" encoding="utf-8"?>\n<!-- Fondo blanco del ícono de Quincena VE. Generado por tools/icons.py. -->\n'
                      '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
                      '    android:width="108dp" android:height="108dp" android:viewportWidth="108" android:viewportHeight="108">\n'
                      '    <path android:fillColor="#FFFFFFFF" android:pathData="M0,0h108v108h-108z" />\n</vector>\n')


def main() -> None:
    # maskable: la punta de la cola queda dentro del círculo seguro del 80 % que exige Android.
    rounded, bleed, maskable = svg(True, 0.92), svg(False, 0.86), svg(False, 0.82)
    (ICONS / "icon.svg").write_text(rounded + "\n", encoding="utf-8")
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        for size in (192, 512):
            render(rounded, ICONS / f"icon-{size}.png", size, tmp)
        render(maskable, ICONS / "maskable-512.png", 512, tmp)
        render(bleed, ICONS / "apple-touch-icon.png", 180, tmp)
        small = []
        for size in (16, 32, 48):
            p = tmp / f"fav-{size}.png"
            render(rounded, p, size, tmp)
            small.append(p)
        write_ico(small, ICONS / "favicon.ico")
        for folder, size in (("mdpi", 48), ("hdpi", 72), ("xhdpi", 96), ("xxhdpi", 144), ("xxxhdpi", 192)):
            render(rounded, RES / f"mipmap-{folder}" / "ic_launcher.png", size, tmp)
    drawable = RES / "drawable"
    (drawable / "ic_launcher_foreground.xml").write_text(android_vectors(mono=False), encoding="utf-8")
    (drawable / "ic_launcher_monochrome.xml").write_text(android_vectors(mono=True), encoding="utf-8")
    (drawable / "ic_launcher_background.xml").write_text(ANDROID_BACKGROUND, encoding="utf-8")
    print("Iconos generados en", ICONS, "y", RES)


if __name__ == "__main__":
    main()
