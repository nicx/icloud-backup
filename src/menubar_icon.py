"""Erzeugt das Menüleisten-Icon als **Template-Image** (richtige Größe, system-getönt).

Anders als ein Text-Glyph (das auf Schriftgröße gerendert wird) füllt ein Template-Image
die Menüleistenhöhe wie echte System-Icons und passt sich Hell/Dunkel automatisch an.

Das Bild wird einmalig zur Laufzeit nach App Support gerendert (kein Bundling nötig; gleich
in Entwicklung und im .app-Bundle) und der Pfad zurückgegeben.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .config.paths import app_support_dir

LOGGER = logging.getLogger(__name__)

# Punktgröße in der Menüleiste (~22 pt nutzbare Höhe) bei 2× Pixeldichte.
_POINTS = 22
_SCALE = 2
_PX = _POINTS * _SCALE  # 44 px


def ensure_menubar_icon() -> Optional[str]:
    """Abwärtskompatibel: Pfad des gefüllten (aktiven) Icons, sonst ``None``."""
    return ensure_menubar_icons().get("active")


def ensure_menubar_icons() -> dict:
    """Rendert beide Template-PNGs und gibt ``{"active": path|None, "idle": path|None}`` zurück.

    ``active`` = gefüllte Wolke (`icloud.fill`, Auto-Sync läuft), ``idle`` = umrandete Wolke
    (`icloud`, Auto-Sync pausiert). Fehlschlag je Variante ⇒ ``None`` (App fällt auf Textglyph
    zurück).
    """
    out: dict = {"active": None, "idle": None}
    for key, symbol, filled, fname in (
        ("active", "icloud.fill", True, "menubar_template_active.png"),
        ("idle", "icloud", False, "menubar_template_idle.png"),
    ):
        dest = app_support_dir() / fname
        try:
            _render(dest, symbol, filled)
            out[key] = str(dest)
        except Exception as exc:  # noqa: BLE001 - ohne Icon fällt die App auf Textglyph zurück
            LOGGER.warning("Menüleisten-Icon (%s) konnte nicht erzeugt werden: %s", key, exc)
    return out


def _render(dest: Path, symbol_name: str, filled: bool) -> None:
    """Bevorzugt das passende SF-Symbol; sonst gezeichnete Wolke (gefüllt oder umrandet)."""
    symbol = _sf_symbol_image(symbol_name)
    if symbol is not None:
        _write_png(_bitmap_from_image(symbol), dest)
        return
    _write_png(_draw_cloud_rep(filled), dest)


def _sf_symbol_image(name: str):
    """SF-Symbol als template NSImage in Menüleistengröße (oder None, falls nicht verfügbar)."""
    import AppKit

    fn = getattr(AppKit.NSImage, "imageWithSystemSymbolName_accessibilityDescription_", None)
    if fn is None:  # macOS < 11
        return None
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    cfg_cls = getattr(AppKit, "NSImageSymbolConfiguration", None)
    if cfg_cls is not None:
        cfg = cfg_cls.configurationWithPointSize_weight_(float(_POINTS), 0.0)
        configured = img.imageWithSymbolConfiguration_(cfg)
        if configured is not None:
            img = configured
    img.setTemplate_(True)
    return img


def _bitmap_from_image(img):
    """Rendert ein NSImage zentriert in einen **quadratischen** Bitmap-Rep (schwarz/alpha, retina).

    Quadratisch ist wichtig: rumps zwingt das Menüleisten-Icon auf 20×20. Ein nicht-quadratisches
    Bild (die Wolke ist ~32×22) würde dadurch gestaucht. Im Quadrat mit erhaltenem Seitenverhältnis
    bleibt die Form korrekt (mit etwas vertikalem Rand).
    """
    import AppKit
    from Foundation import NSMakeRect, NSSize

    size = img.size()
    sw, sh = (size.width or _POINTS), (size.height or _POINTS)
    side = max(sw, sh)
    px = int(round(side * _SCALE))
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bitmapFormat_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0, 0
    )
    rep.setSize_(NSSize(side, side))
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)
    AppKit.NSColor.blackColor().set()
    img.drawInRect_(NSMakeRect((side - sw) / 2, (side - sh) / 2, sw, sh))  # zentriert
    AppKit.NSGraphicsContext.restoreGraphicsState()
    return rep


def _draw_cloud_rep(filled: bool = True):
    """Fallback: gezeichnete Wolken-Silhouette (flache Basis + drei Bögen).

    ``filled=False`` zeichnet eine Kontur (Näherung; eine saubere Outline liefert v. a. das
    SF-Symbol auf macOS 11+, das im Normalfall greift).
    """
    import AppKit
    from Foundation import NSMakeRect, NSSize

    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bitmapFormat_bytesPerRow_bitsPerPixel_(
        None, _PX, _PX, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0, 0
    )
    rep.setSize_(NSSize(_POINTS, _POINTS))
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)
    AppKit.NSColor.blackColor().set()
    u = _PX / 44.0
    cloud = AppKit.NSBezierPath.bezierPath()

    def add_oval(x, y, r):
        cloud.appendBezierPathWithOvalInRect_(NSMakeRect((x - r) * u, (y - r) * u, 2 * r * u, 2 * r * u))

    cloud.appendBezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(7 * u, 12 * u, 30 * u, 13 * u), 6 * u, 6 * u
    )
    add_oval(15, 23, 7.5)
    add_oval(23, 28, 10.0)
    add_oval(31, 24, 8.0)
    cloud.setWindingRule_(AppKit.NSWindingRuleNonZero)
    if filled:
        cloud.fill()
    else:
        cloud.setLineWidth_(2.0 * u)
        cloud.stroke()
    AppKit.NSGraphicsContext.restoreGraphicsState()
    return rep


def _write_png(rep, dest: Path) -> None:
    import AppKit

    png = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    png.writeToFile_atomically_(str(dest), True)
