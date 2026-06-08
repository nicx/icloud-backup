"""Erzeugt ``build/icon.icns`` (App-Icon) – ohne externe Assets.

Rendert per AppKit (pyobjc, ohnehin als rumps-Abhängigkeit vorhanden) ein 1024×1024-PNG:
abgerundeter Blau-Verlauf + weiße Wolke (Bezier) + kleiner Down-Pfeil (Backup/Download).
Anschließend baut ``iconutil`` aus dem PNG ein ``.icns``.

Ausführen vom Repo-Root::

    .venv/bin/python build/make_icon.py
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import AppKit
from Foundation import NSMakeRect, NSMakePoint

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = 1024


def _color(r, g, b, a=1.0):
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, a)


def _rounded_rect(x, y, w, h, radius):
    return AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), radius, radius
    )


def _cloud_path(cx, cy, scale):
    """Wolke aus mehreren überlappenden Kreisen + Basisrechteck."""
    path = AppKit.NSBezierPath.bezierPath()

    def circle(dx, dy, r):
        path.appendBezierPathWithOvalInRect_(
            NSMakeRect(cx + dx * scale - r * scale, cy + dy * scale - r * scale,
                       2 * r * scale, 2 * r * scale)
        )

    # Basis (verbindet die Kreise zu einer Wolkenform)
    path.appendBezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx - 200 * scale, cy - 70 * scale, 400 * scale, 120 * scale),
        60 * scale, 60 * scale,
    )
    circle(-130, 0, 95)
    circle(-10, 55, 130)
    circle(140, 5, 100)
    circle(40, -20, 110)
    return path


def render_png(path_png: str) -> None:
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bitmapFormat_bytesPerRow_bitsPerPixel_(
        None, SIZE, SIZE, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0, 0
    )
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)

    # Hintergrund: abgerundetes Quadrat mit Blau-Verlauf
    bg = _rounded_rect(0, 0, SIZE, SIZE, 230)
    bg.addClip()
    gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
        _color(0.16, 0.49, 0.96), _color(0.10, 0.30, 0.78)
    )
    gradient.drawInBezierPath_angle_(bg, -90.0)

    # Wolke (weiß, leichter Schatten durch zweite, dunklere Wolke darunter)
    shadow = _cloud_path(SIZE / 2, SIZE / 2 - 30, 1.0)
    _color(0.0, 0.0, 0.0, 0.12).setFill()
    shadow.fill()
    cloud = _cloud_path(SIZE / 2, SIZE / 2, 1.0)
    _color(1.0, 1.0, 1.0, 1.0).setFill()
    cloud.fill()

    # Down-Pfeil (Backup/Download) in Blau auf der Wolke
    arrow = AppKit.NSBezierPath.bezierPath()
    cx, cy = SIZE / 2 + 40, SIZE / 2 + 20
    arrow.moveToPoint_(NSMakePoint(cx - 55, cy + 30))
    arrow.lineToPoint_(NSMakePoint(cx - 20, cy + 30))
    arrow.lineToPoint_(NSMakePoint(cx - 20, cy + 90))
    arrow.lineToPoint_(NSMakePoint(cx + 20, cy + 90))
    arrow.lineToPoint_(NSMakePoint(cx + 20, cy + 30))
    arrow.lineToPoint_(NSMakePoint(cx + 55, cy + 30))
    arrow.lineToPoint_(NSMakePoint(cx, cy - 35))
    arrow.closePath()
    _color(0.12, 0.34, 0.82).setFill()
    arrow.fill()

    AppKit.NSGraphicsContext.restoreGraphicsState()

    png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    png.writeToFile_atomically_(path_png, True)


def build_icns() -> str:
    out_icns = os.path.join(HERE, "icon.icns")
    with tempfile.TemporaryDirectory() as tmp:
        master = os.path.join(tmp, "icon_1024.png")
        render_png(master)
        iconset = os.path.join(tmp, "icon.iconset")
        os.makedirs(iconset, exist_ok=True)
        sizes = [16, 32, 128, 256, 512]
        for s in sizes:
            for scale, suffix in ((1, ""), (2, "@2x")):
                px = s * scale
                name = f"icon_{s}x{s}{suffix}.png"
                subprocess.run(
                    ["sips", "-z", str(px), str(px), master, "--out",
                     os.path.join(iconset, name)],
                    check=True, capture_output=True,
                )
        # 512@2x = 1024 (Master direkt)
        subprocess.run(["cp", master, os.path.join(iconset, "icon_512x512@2x.png")], check=True)
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out_icns], check=True)
    return out_icns


if __name__ == "__main__":
    path = build_icns()
    print("Icon erzeugt:", path)
