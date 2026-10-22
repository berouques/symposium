"""Portrait avatars with an editable appearance (offline SVG, deterministic).

Appearance = four independent controls (the avatar editor in the UI):
  1. hair        — style + color (from white/grey to black and ginger)
  2. face        — one feature: mustache, beard, stubble, goatee, makeup …
                   (facial hair always takes the hair colour)
  3. background  — colour
  4. outfit      — colour of the clothes AND of the frame
Anything not set comes from the seed, so a character always has a stable
face; changing one control never disturbs the others.
"""
from __future__ import annotations

import colorsys
import html
import random
import re

HAIR_STYLES = ["bald", "buzz", "short", "side", "long", "curly", "bun"]
HAIR_COLORS = ["#ececec", "#9aa0a6", "#d9b96c", "#b5532a", "#8b5e3c", "#5a3a22", "#34231a", "#16120f"]
FACE_FEATURES = ["none", "mustache", "stubble", "beard_short", "beard_full", "goatee", "lipstick", "makeup_light", "makeup_bold"]
BACKGROUNDS = ["#cfe3e0", "#d9d6ee", "#f1dfc6", "#e8cfd4", "#cfd9ea", "#d8e6c9", "#2b3a4a", "#3b2f3f"]
OUTFITS = ["#2b7a74", "#7a4fa3", "#b0653a", "#a63d52", "#3d5fa6", "#5d8a3a", "#555b66", "#c9a227"]
SKINS = ["#f2c9a5", "#e5b48d", "#d19a72", "#b57e56", "#8d5a3b", "#6b4229"]
OPTIONS = {"hair": HAIR_STYLES, "hair_color": HAIR_COLORS, "face": FACE_FEATURES, "bg": BACKGROUNDS, "outfit": OUTFITS}
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def defaults(seed: str) -> dict:
    rng = random.Random(seed)
    return {
        "hair": rng.choice(HAIR_STYLES), "hair_color": rng.choice(HAIR_COLORS),
        "face": rng.choice(["none", "none", "mustache", "stubble", "beard_short", "beard_full", "goatee", "lipstick", "makeup_light"]),
        "bg": rng.choice(BACKGROUNDS[:6]), "outfit": rng.choice(OUTFITS),
    }


def sanitize(params: dict | None) -> dict:
    """Keep only valid keys/values (the SVG is built from them: no injection)."""
    out: dict = {}
    for k, v in (params or {}).items():
        if k == "hair" and v in HAIR_STYLES:
            out[k] = v
        elif k == "face" and v in FACE_FEATURES:
            out[k] = v
        elif k in ("hair_color", "bg", "outfit") and isinstance(v, str) and _HEX.match(v):
            out[k] = v.lower()
    return out


def resolve(seed: str, params: dict | None) -> dict:
    return {**defaults(seed), **sanitize(params)}


def _shade(hex_color: str, f: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb(h, max(0, min(1, l * f)), s)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def render(seed: str, params: dict | None = None, name: str = "") -> str:
    p = resolve(seed, params)
    rng = random.Random(seed + "skin")
    skin = rng.choice(SKINS)
    skin_d = _shade(skin, 0.86)
    hair, hair_d = p["hair_color"], _shade(p["hair_color"], 0.75)
    bg, bg2, outfit, collar = p["bg"], _shade(p["bg"], 0.88), p["outfit"], _shade(p["outfit"], 0.8)
    style, face = p["hair"], p["face"]
    ink = "#2b2420"
    s = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">',
        f'<defs><radialGradient id="g" cx="50%" cy="35%" r="80%"><stop offset="0%" stop-color="{bg}"/><stop offset="100%" stop-color="{bg2}"/></radialGradient></defs>',
        '<rect width="100" height="100" fill="url(#g)"/>',
    ]
    # hair behind the head / shoulders
    if style == "long":
        s.append(f'<path d="M30 44 C24 62 26 82 38 88 L62 88 C74 82 76 62 70 44 C70 28 60 20 50 20 C40 20 30 28 30 44 Z" fill="{hair}"/>')
    if style == "bun":
        s.append(f'<circle cx="50" cy="17" r="8" fill="{hair}"/><circle cx="50" cy="17" r="8" fill="none" stroke="{hair_d}" stroke-width="1"/>')
    # shoulders
    s.append(f'<path d="M20 100 C24 76 37 68 50 68 C63 68 76 76 80 100 Z" fill="{outfit}"/>')
    s.append(f'<path d="M42 70 L50 80 L58 70 C55 68 45 68 42 70 Z" fill="{collar}"/>')
    s.append(f'<rect x="44" y="58" width="12" height="13" rx="4" fill="{skin_d}"/>')
    # head
    s.append(f'<ellipse cx="50" cy="46" rx="17" ry="20" fill="{skin}"/>')
    s.append(f'<ellipse cx="33.2" cy="48" rx="2.4" ry="4" fill="{skin_d}"/><ellipse cx="66.8" cy="48" rx="2.4" ry="4" fill="{skin_d}"/>')
    # hair front
    cap = f'<path d="M32.5 44 C31 26 41 21 50 21 C59 21 69 26 67.5 44 C66 37 62 33 57 34 C54 30 46 30 43 34 C38 33 34 37 32.5 44 Z" fill="{hair}"/>'
    if style == "buzz":
        s.append(f'<path d="M33 42 C32 27 41 22 50 22 C59 22 68 27 67 42 C64 34 58 31 50 31 C42 31 36 34 33 42 Z" fill="{hair}" opacity="0.55"/>')
    elif style in ("short", "long", "bun"):
        s.append(cap)
    elif style == "side":
        s.append(f'<path d="M32.5 44 C30 26 42 20 52 21 C62 22 70 28 67.5 44 C66 38 60 33 50 34 C42 35 36 38 32.5 44 Z" fill="{hair}"/>'
                 f'<path d="M32 42 C36 30 50 28 62 30 C54 30 42 33 36 46 Z" fill="{hair_d}"/>')
    elif style == "curly":
        s.append(cap)
        for cx, cy, r in ((36, 30, 6), (44, 24, 6.5), (54, 23, 6.5), (63, 29, 6), (32, 40, 5), (68, 40, 5)):
            s.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{hair}"/>')
    # brows, eyes, nose, mouth
    s.append(f'<path d="M38 41 Q43 38.5 47 41 M53 41 Q57 38.5 62 41" stroke="{hair_d if style != "bald" else skin_d}" stroke-width="1.6" fill="none" stroke-linecap="round"/>')
    if face in ("makeup_light", "makeup_bold"):
        shade = "#7a5a8a" if face == "makeup_light" else "#2d2433"
        s.append(f'<ellipse cx="43" cy="45.2" rx="4.6" ry="2.4" fill="{shade}" opacity="{0.35 if face == "makeup_light" else 0.7}"/>'
                 f'<ellipse cx="57" cy="45.2" rx="4.6" ry="2.4" fill="{shade}" opacity="{0.35 if face == "makeup_light" else 0.7}"/>')
        s.append('<circle cx="38.5" cy="54" r="3.6" fill="#e8737f" opacity="0.28"/><circle cx="61.5" cy="54" r="3.6" fill="#e8737f" opacity="0.28"/>')
    s.append(f'<circle cx="43" cy="46" r="2" fill="{ink}"/><circle cx="57" cy="46" r="2" fill="{ink}"/>')
    if face in ("makeup_light", "makeup_bold", "lipstick"):
        s.append(f'<path d="M40.4 43.8 L41.8 44.9 M45.6 43.8 L44.2 44.9 M54.4 43.8 L55.8 44.9 M59.6 43.8 L58.2 44.9" stroke="{ink}" stroke-width="0.9"/>')
    s.append(f'<path d="M50 47 L48.4 54 Q50 55.2 51.6 54" stroke="{skin_d}" stroke-width="1.2" fill="none" stroke-linecap="round"/>')
    lip = "#c2455a" if face in ("lipstick", "makeup_bold", "makeup_light") else "#8a4a3f"
    mouth_w = 1.8 if face in ("lipstick", "makeup_bold") else 1.3
    # facial hair (always in hair colour)
    if face == "beard_full":
        s.append(f'<path d="M33.5 50 C34 66 42 74 50 74 C58 74 66 66 66.5 50 C62 60 56 62 50 62 C44 62 38 60 33.5 50 Z" fill="{hair}"/>')
    elif face == "beard_short":
        s.append(f'<path d="M34.5 52 C36 63 43 68 50 68 C57 68 64 63 65.5 52 C61 58 56 60 50 60 C44 60 39 58 34.5 52 Z" fill="{hair}" opacity="0.92"/>')
    elif face == "stubble":
        s.append(f'<path d="M34.5 52 C36 63 43 68 50 68 C57 68 64 63 65.5 52 C61 58 56 60 50 60 C44 60 39 58 34.5 52 Z" fill="{hair}" opacity="0.28"/>')
    elif face == "goatee":
        s.append(f'<path d="M45 60 C45 67 47 70 50 70 C53 70 55 67 55 60 C53 63 47 63 45 60 Z" fill="{hair}"/>')
    if face in ("mustache", "beard_full", "beard_short", "goatee"):
        s.append(f'<path d="M42 57 C44 53.5 48 54.5 50 56 C52 54.5 56 53.5 58 57 C55 56 52 57.4 50 57.4 C48 57.4 45 56 42 57 Z" fill="{hair}"/>')
    s.append(f'<path d="M45 {60.5 if face not in ("beard_full", "beard_short") else 61.5} Q50 {63 if face not in ("beard_full", "beard_short") else 63.8} 55 {60.5 if face not in ("beard_full", "beard_short") else 61.5}" stroke="{lip}" stroke-width="{mouth_w}" fill="none" stroke-linecap="round"/>')
    s.append(f'<rect x="1.5" y="1.5" width="97" height="97" rx="12" fill="none" stroke="{outfit}" stroke-width="3"/>')
    s.append("</svg>")
    return "".join(s)
