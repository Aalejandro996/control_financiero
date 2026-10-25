"""Genera todos los iconos de Quincena desde un solo diseño (la "Q" blanca con cola menta sobre verde).

Ejecutar desde la raíz del proyecto:  python3 tools/icons.py
Necesita Google Chrome (para dibujar los SVG) y sips (viene con macOS). No necesita librerías de Python.

Crea:
  static/icons/icon.svg            icono vectorial (favicon moderno)
  static/icons/favicon.ico         16, 32 y 48 px para navegadores viejos y Windows
  static/icons/icon-192.png        icono "any" con esquinas redondeadas (PWA, aviso de APK)
  static/icons/icon-512.png
  static/icons/maskable-512.png    icono a sangre para Android al instalar la web (el sistema le pone la forma)
  static/icons/apple-touch-icon.png  180 px a sangre (iOS redondea solo)
  android/.../mipmap-*/ic_launcher.png  respaldo en PNG del icono de la app
El icono adaptativo de Android es vectorial y vive en drawable/ic_launcher_foreground.xml (mismas medidas que aquí).
"""
import pathlib
import shutil
import struct
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ICONS = ROOT / "static" / "icons"
RES = ROOT / "android" / "app" / "src" / "main" / "res"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

GREEN_TOP, GREEN_BOTTOM, MINT = "#12936A", "#0B6B4D", "#BFF0D8"


def mark(scale: float = 1.0) -> str:
    """La Q en un lienzo de 512: anillo blanco y cola menta que sale a la derecha (así no parece una lupa).
    (267, 256) es el centro de la figura; scale < 1 la achica hacia el centro para respetar la zona segura."""
    t = f'translate(256 256) scale({scale}) translate(-267 -256)'
    return (f'<g transform="{t}">'
            f'<circle cx="246" cy="236" r="122" fill="none" stroke="#fff" stroke-width="58"/>'
            f'<path d="M262 330 L330 398 L410 398" fill="none" stroke="{MINT}" stroke-width="58" stroke-linecap="round" stroke-linejoin="round"/>'
            f'</g>')


def svg(rounded: bool, scale: float) -> str:
    shape = '<rect width="512" height="512" rx="112" fill="url(#g)"/>' if rounded else '<rect width="512" height="512" fill="url(#g)"/>'
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{GREEN_TOP}"/>'
            f'<stop offset="1" stop-color="{GREEN_BOTTOM}"/></linearGradient></defs>{shape}{mark(scale)}</svg>')


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


def write_ico(pngs: list[pathlib.Path], out: pathlib.Path) -> None:
    """Un .ico puede guardar PNG tal cual: cabecera + una entrada por tamaño + los PNG."""
    data = [p.read_bytes() for p in pngs]
    sizes = [struct.unpack(">II", d[16:24])[0] for d in data]
    offset = 6 + 16 * len(data)
    header = struct.pack("<HHH", 0, 1, len(data))
    entries = b""
    for size, blob in zip(sizes, data):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    out.write_bytes(header + entries + b"".join(data))


def main() -> None:
    # maskable: el punto más lejano (la punta de la cola) queda dentro del círculo seguro del 80 % que exige Android.
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
    print("Iconos generados en", ICONS, "y", RES)


if __name__ == "__main__":
    main()
