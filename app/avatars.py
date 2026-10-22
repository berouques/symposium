"""Deterministic offline SVG avatar generation.

Implements the avatar specification (NG_11): local generation, byte-stable
for the same seed, SVG output, several styles, appearance derived from the
seed, immutable after member creation, fallback with initials.

Deviation from the spec: instead of DiceBear (a JavaScript/HTTP library with
no maintained official Python port), we ship a small in-house generator with
the same contract: offline, deterministic, SVG, style options stored with
the member. See docs/SPEC_COMPLIANCE.md.
"""
from __future__ import annotations

import colorsys
import hashlib
import html
import json
import random

STYLES = ("bust", "geometric", "botanical")


def make_seed(name: str, description: str, role: str) -> str:
    raw = f"{name.strip()}|{description.strip()}|{role.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def member_color(seed: str) -> str:
    """Deterministic accent color for transcript/border (hex)."""
    hue = int(seed[:6], 16) % 360
    sat = 0.42 + (int(seed[6:10], 16) % 200) / 1000.0  # 0.42..0.62
    light = 0.36 + (int(seed[10:14], 16) % 140) / 1000.0  # 0.36..0.50
    r, g, b = colorsys.hls_to_rgb(hue / 360.0, light, sat)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def _hex(h: float, l: float, s: float) -> str:
    r, g, b = colorsys.hls_to_rgb(h / 360.0, l, s)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def generate_svg(seed: str, style: str | None = None, name: str = "", params: dict | None = None) -> str:
    style = style if style in STYLES else "bust"
    if len(seed) < 16 or any(c not in "0123456789abcdef" for c in seed[:16]):
        seed = hashlib.sha256(seed.encode("utf-8")).hexdigest()      # произвольный seed (например, из редактора)
    rng = random.Random(int(seed[:16], 16))
    hue = int(seed[:6], 16) % 360
    accent = member_color(seed)

    if style == "bust":
        from . import portrait
        return portrait.render(seed, params, name)
    if style == "geometric":
        return _svg_geometric(rng, hue, accent)
    return _svg_botanical(rng, hue, accent)


def fallback_svg(seed: str, name: str) -> str:
    """Neutral placeholder: circle with initials, deterministic bg color."""
    bg = member_color(seed)
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
        f'<rect width="100" height="100" rx="50" fill="{bg}"/>'
        '<text x="50" y="58" font-family="Georgia, serif" font-size="34" '
        'fill="#f5f1e6" text-anchor="middle">'
        f"{html.escape(initials)}</text></svg>"
    )


# ----------------------------------------------------------------- styles

def _svg_bust(rng: random.Random, hue: float, accent: str, gender: str = "") -> str:
    bg = _hex((hue + 30) % 360, 0.90, 0.25)
    bg2 = _hex((hue + 30) % 360, 0.82, 0.28)
    skin = _hex((hue + 180) % 360, 0.72, 0.22)
    hair = _hex((hue + 210) % 360, 0.28, 0.30)
    toga = _hex((hue + 60) % 360, 0.60, 0.28)
    roll = rng.random()
    female = (gender or "").lower().startswith(("f", "ж"))
    male = (gender or "").lower().startswith(("m", "м"))
    beard = False if female else (roll > 0.45 if not male else roll > 0.55)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">',
        f'<defs><radialGradient id="g" cx="50%" cy="35%" r="75%">'
        f'<stop offset="0%" stop-color="{bg}"/><stop offset="100%" stop-color="{bg2}"/>'
        f"</radialGradient></defs>",
        f'<rect width="100" height="100" fill="url(#g)"/>',
        # shoulders / toga
        f'<path d="M22 100 C26 74 38 66 50 66 C62 66 74 74 78 100 Z" fill="{toga}"/>',
        f'<path d="M50 66 C56 66 60 70 58 74 C54 70 46 70 42 74 C40 70 44 66 50 66 Z" '
        f'fill="{_hex((hue + 60) % 360, 0.80, 0.25)}"/>',
        # head
        f'<ellipse cx="50" cy="46" rx="17" ry="20" fill="{skin}"/>',
        # hair
        f'<path d="M33 42 C33 26 42 22 50 22 C58 22 67 26 67 42 C67 36 62 32 58 33 '
        f'C55 28 45 28 42 33 C38 32 33 36 33 42 Z" fill="{hair}"/>',
    ]
    if female:   # длинные волосы по бокам лица
        parts.append(f'<path d="M33 42 C28 56 30 70 36 74 C34 62 35 52 37 44 Z" fill="{hair}"/>')
        parts.append(f'<path d="M67 42 C72 56 70 70 64 74 C66 62 65 52 63 44 Z" fill="{hair}"/>')
    # eyes
    ey = 46
    parts.append(f'<circle cx="43" cy="{ey}" r="2.1" fill="#2f2a24"/>')
    parts.append(f'<circle cx="57" cy="{ey}" r="2.1" fill="#2f2a24"/>')
    if female:   # ресницы
        parts.append(f'<path d="M40.5 43.6 L42 44.6 M45.5 43.6 L44 44.6 M54.5 43.6 L56 44.6 M59.5 43.6 L58 44.6" stroke="#2f2a24" stroke-width="0.9"/>')
    if beard:
        parts.append(
            f'<path d="M38 52 C40 66 46 70 50 70 C54 70 60 66 62 52 C58 58 54 60 50 60 '
            f'C46 60 42 58 38 52 Z" fill="{hair}" opacity="0.85"/>'
        )
    else:
        parts.append(f'<path d="M45 58 Q50 61 55 58" stroke="#2f2a24" stroke-width="1.4" fill="none"/>')
    # laurel dot
    parts.append(f'<circle cx="50" cy="14" r="3" fill="{accent}" opacity="0.85"/>')
    parts.append(f'<rect x="1" y="1" width="98" height="98" rx="12" fill="none" stroke="{accent}" stroke-width="2.5"/>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_geometric(rng: random.Random, hue: float, accent: str) -> str:
    bg = _hex((hue + 40) % 360, 0.92, 0.22)
    cols = [_hex((hue + k * 25) % 360, 0.55 + 0.05 * (k % 3), 0.35) for k in range(4)]
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">',
        f'<rect width="100" height="100" fill="{bg}"/>',
    ]
    cx, cy = 50, 50
    for i in range(3):
        r = 34 - i * 10
        rot = rng.randint(0, 90)
        col = cols[i % len(cols)]
        pts = []
        import math
        for k in range(6):
            a = math.radians(rot + k * 60)
            pts.append(f"{cx + r * math.cos(a):.1f},{cy + r * math.sin(a):.1f}")
        parts.append(f'<polygon points="{" ".join(pts)}" fill="none" stroke="{col}" stroke-width="2.4"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{rng.randint(6, 10)}" fill="{cols[2]}"/>')
    for _ in range(3):
        x, y = rng.randint(12, 88), rng.randint(12, 88)
        parts.append(f'<circle cx="{x}" cy="{y}" r="1.8" fill="{accent}" opacity="0.6"/>')
    parts.append(f'<rect x="1" y="1" width="98" height="98" rx="12" fill="none" stroke="{accent}" stroke-width="2.5"/>')
    parts.append("</svg>")
    return "".join(parts)


def _svg_botanical(rng: random.Random, hue: float, accent: str) -> str:
    bg = _hex((hue + 50) % 360, 0.90, 0.24)
    leaf = _hex(hue, 0.42, 0.35)
    leaf2 = _hex((hue + 20) % 360, 0.55, 0.35)
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">',
        f'<rect width="100" height="100" fill="{bg}"/>',
    ]
    # central stem
    parts.append(f'<path d="M50 88 C50 60 50 40 50 18" stroke="{leaf}" stroke-width="2.2" fill="none"/>')
    # symmetric leaves
    import math
    for i in range(5):
        y = 26 + i * 12
        ln = 16 - i * 1.6
        ang = 40 - i * 4
        for sign in (-1, 1):
            a = math.radians(ang if sign > 0 else 180 - ang)
            x2 = 50 + ln * math.cos(a)
            y2 = y - ln * 0.35 * math.sin(math.radians(90))
            col = leaf if (i % 2 == 0) else leaf2
            parts.append(
                f'<path d="M50 {y} Q{50 + sign * ln * 0.6} {y - 6} {x2:.1f} {y - ln * 0.5:.1f} '
                f'Q{50 + sign * ln * 0.5} {y + 4} 50 {y}" fill="{col}"/>'
            )
    parts.append(f'<circle cx="50" cy="14" r="4" fill="{accent}"/>')
    parts.append(f'<rect x="1" y="1" width="98" height="98" rx="12" fill="none" stroke="{accent}" stroke-width="2.5"/>')
    parts.append("</svg>")
    return "".join(parts)


def avatar_options_from_seed(seed: str) -> str:
    """Style options fixed at creation (stored, immutable)."""
    rng = random.Random(int(seed[:16], 16))
    return json.dumps({"variant": rng.randint(0, 3)})


def style_for_seed(seed: str, default_style: str | None = None) -> str:
    default_style = default_style if default_style in STYLES else "bust"
    return default_style
