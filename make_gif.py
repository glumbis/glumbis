"""One-off: turn the source ASCII-art GIF into theme-matched dark/light GIFs.

usage: python make_gif.py path/to/source.gif

- Trims to one exact animation cycle so the loop has no jump.
- Uses each pixel's brightness as glyph intensity and repaints it on the panel
  background with the panel's own colours (same theme values as today.py).
- Rounds the corners to match the panel's rx=15 at the README's display widths.
"""

import sys

import numpy as np
from PIL import Image, ImageDraw, ImageSequence

WIDTH = 760          # ~2x the displayed width on a profile page
PADDING = 0.04       # inset so glyphs don't touch the rounded corners
GAMMA = 0.55         # lifts the dim glyphs so the art reads at README size
# The panel (624px wide, rx=15) is shown at 53% and the GIF at 45% of the row.
RADIUS = round(15 * (0.53 / 624) / (0.45 / WIDTH))

THEMES = {
    # bg, then gradient stops (intensity, colour)
    "ascii_dark.gif": ("#161b22", [(0.0, "#161b22"), (0.75, "#ffa657"), (1.0, "#ffe3c8")]),
    "ascii_light.gif": ("#f6f8fa", [(0.0, "#f6f8fa"), (0.75, "#953800"), (1.0, "#4a1800")]),
}


def rgb(h):
    return np.array([int(h[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)


def cycle_length(frames):
    """Smallest period p where frame p matches frame 0."""
    first = frames[0]
    for p in range(2, len(frames)):
        if np.abs(frames[p] - first).mean() < 0.05:
            return p
    return len(frames)


def palette(stops):
    """255 gradient colours for intensities 0..1 (index 255 is reserved for transparency)."""
    t = np.linspace(0, 1, 255)
    xs = [s for s, _ in stops]
    cols = np.stack([rgb(c) for _, c in stops])
    pal = np.stack([np.interp(t, xs, cols[:, k]) for k in range(3)], axis=1)
    return np.round(pal).astype(np.uint8)


def main(src):
    im = Image.open(src)
    durations = [f.info.get("duration", 70) for f in ImageSequence.Iterator(im)]
    lum = [np.asarray(f.convert("L"), dtype=float) for f in ImageSequence.Iterator(Image.open(src))]
    period = cycle_length(lum)
    print(f"{len(lum)} frames, loop period {period}")
    lum, durations = lum[:period], durations[:period]

    peak = np.percentile(np.concatenate([f[f > 8] for f in lum]), 99.5)
    w0, h0 = Image.fromarray(lum[0]).size
    height = round(h0 * WIDTH / w0)
    inner = (round(WIDTH * (1 - 2 * PADDING)), round(height * (1 - 2 * PADDING)))
    offset = ((WIDTH - inner[0]) // 2, (height - inner[1]) // 2)

    mask = Image.new("L", (WIDTH, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, WIDTH - 1, height - 1), RADIUS, fill=255)
    corners = np.asarray(mask) < 128

    # Intensity frames as palette indices 0..254, shared by both themes.
    indexed = []
    for f in lum:
        small = Image.fromarray(np.clip(f / peak, 0, 1)).resize(inner, Image.LANCZOS)
        level = np.clip(np.asarray(small), 0, 1) ** GAMMA
        idx = np.zeros((height, WIDTH), dtype=np.uint8)
        idx[offset[1]:offset[1] + inner[1], offset[0]:offset[0] + inner[0]] = \
            np.round(level * 254).astype(np.uint8)
        idx[corners] = 255
        indexed.append(idx)

    for name, (_, stops) in THEMES.items():
        pal = np.vstack([palette(stops), [[0, 0, 0]]]).astype(np.uint8)
        frames = []
        for idx in indexed:
            fr = Image.fromarray(idx, mode="P")
            fr.putpalette(pal.flatten().tolist())
            frames.append(fr)
        frames[0].save(name, save_all=True, append_images=frames[1:], duration=durations,
                       loop=0, transparency=255, disposal=2, optimize=False)
        print(f"wrote {name} ({WIDTH}x{height}, {period} frames)")


if __name__ == "__main__":
    main(sys.argv[1])
