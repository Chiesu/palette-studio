# SPDX-License-Identifier: GPL-3.0-or-later
"""Color conversion and palette variants. Pure Python; no Blender dependency."""
import colorsys
import math
import re


def clamp(value, low=0.0, high=1.0):
    return max(low, min(high, float(value)))


def srgb_to_linear(channel):
    value = clamp(channel)
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(channel):
    value = max(0.0, float(channel))
    return 12.92 * value if value <= 0.0031308 else 1.055 * value ** (1.0 / 2.4) - 0.055


def hex_to_rgba(value):
    """Parse sRGB #RGB, #RGBA, #RRGGBB, #RRGGBBAA into linear RGBA."""
    text = value.strip().lstrip('#')
    if not re.fullmatch(r'(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})', text):
        raise ValueError('Use #RGB, #RRGGBB or #RRGGBBAA hexadecimal color.')
    if len(text) in (3, 4):
        text = ''.join(ch * 2 for ch in text)
    if len(text) == 6:
        text += 'FF'
    channels = [int(text[index:index + 2], 16) / 255 for index in (0, 2, 4, 6)]
    return tuple(srgb_to_linear(v) for v in channels[:3]) + (channels[3],)


def rgba_to_hex(rgba, include_alpha=False):
    rgb = tuple(int(round(clamp(linear_to_srgb(x)) * 255)) for x in rgba[:3])
    alpha = int(round(clamp(rgba[3] if len(rgba) > 3 else 1.0) * 255))
    result = '#' + ''.join(f'{x:02X}' for x in rgb)
    return result + f'{alpha:02X}' if include_alpha or alpha < 255 else result


def variant(rgba, style):
    """Create a variation in display sRGB, then convert back to linear."""
    r, g, b = [clamp(linear_to_srgb(x)) for x in rgba[:3]]
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if style == 'WARM':
        # Warmth primarily affects colored regions and gently tints neutrals.
        r, g, b = (r * 0.88 + 0.12, g * 0.92 + 0.056, b * 0.90 + 0.018)
    elif style == 'COOL':
        r, g, b = (r * 0.90 + 0.018, g * 0.94 + 0.052, b * 0.86 + 0.14)
    elif style == 'MUTED':
        r, g, b = colorsys.hsv_to_rgb(h, s * 0.62, v * 0.96)
    elif style == 'DARK':
        r, g, b = colorsys.hsv_to_rgb(h, min(1, s * 1.08), v * 0.62)
    elif style == 'LIGHT':
        r, g, b = colorsys.hsv_to_rgb(h, s * 0.80, v * 0.65 + 0.35)
    else:
        raise ValueError(f'Unknown variation: {style}')
    return tuple(srgb_to_linear(clamp(c)) for c in (r, g, b)) + (rgba[3],)


def nearly_equal(a, b, tolerance=1e-6):
    return len(a) == len(b) and all(math.isclose(x, y, abs_tol=tolerance) for x, y in zip(a, b))


STARTER_ROLES = ('Primary', 'Secondary', 'Accent', 'Surface', 'Background', 'Foliage')
STARTER_PALETTES = (
    ('Studio / Neutral', ('#DAD8D3', '#5D676C', '#F2A65A', '#A1A7A6', '#21282B', '#778C77')),
    ('Sunset / Warm', ('#E4BB94', '#B86C49', '#F59C54', '#8F7668', '#423840', '#647555')),
    ('Blue Hour', ('#94A8C7', '#4E6585', '#D4A7B5', '#4B566D', '#161E33', '#3D686E')),
    ('Night / Cool', ('#7B86AD', '#3B4A75', '#A783C8', '#2D3A51', '#101725', '#285B57')),
)


# Starting looks deliberately have different, palette-local color names.
STARTER_LOCAL_NAMES = {
    'Studio / Neutral': ('Paper', 'Graphite', 'Signal', 'Concrete', 'Deep Grey', 'Sage'),
    'Sunset / Warm': ('Golden Light', 'Terracotta', 'Sun Flare', 'Warm Stone', 'Dusk Sky', 'Olive'),
    'Blue Hour': ('Blue Key', 'Dusk Blue', 'Rose Glow', 'Slate', 'Indigo Sky', 'Teal'),
    'Night / Cool': ('Moonlight', 'Midnight', 'Violet Glow', 'Cool Stone', 'Night Sky', 'Dark Teal'),
}
