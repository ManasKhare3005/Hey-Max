"""Build the "Max edition" avatar from VRoid's CC0 sample HairSample_Male.

Recolours the hoodie to Max's navy with cyan trim, puts the Max orb on the chest, turns the eyes
cyan, and makes the trim and logo glow softly (MToon emission). The base model is CC0
(https://vroid.pixiv.help/hc/en-us/articles/4402614652569), so the result can live anywhere;
it is still kept out of git because it's 18 MB. Rebuild any time:

    .venv\\Scripts\\python -m max_assistant.avatar.edition
"""
from __future__ import annotations

import colorsys
import io
import json
import struct
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter

from ..config import ROOT

BASE_URL = "https://raw.githubusercontent.com/madjin/vrm-samples/master/vroid/beta/HairSample_Male.vrm"
BASE = ROOT / "data" / "avatar-candidates" / "HairSample_Male.vrm"
OUT = ROOT / "dashboard" / "public" / "avatar" / "model.vrm"

NAVY_DARK, NAVY_LIGHT = (7, 11, 20), (40, 58, 92)          # hoodie fabric, from shadow to highlight
CYAN_DARK, CYAN = (6, 70, 84), (34, 211, 238)              # Max's accent (#22D3EE)

# Hoodie texture regions (fractions of the 2048² "Tops" texture), found by looking at the layout
TRIM = [(0.00, 0.390, 0.307, 0.488),     # left cuff
        (0.703, 0.390, 1.00, 0.488),     # right cuff
        (0.098, 0.898, 0.900, 1.000),    # hem band
        (0.336, 0.417, 0.362, 0.630),    # drawstrings
        (0.638, 0.417, 0.664, 0.630)]
LOGO = (0.568, 0.572, 0.030)             # chest: centre x, centre y, radius
LOGO_SQUASH = 0.9                        # the chest texture is stretched sideways on the body


# ----- GLB (binary glTF) in and out -----

def read_glb(data: bytes) -> tuple[dict, bytes]:
    jlen = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + jlen])
    blen = struct.unpack_from("<I", data, 20 + jlen)[0]
    return gltf, data[28 + jlen:28 + jlen + blen]


def write_glb(gltf: dict, views: list[bytes]) -> bytes:
    """Lay the buffer views out again (4-byte aligned) and pack the file."""
    blob = bytearray()
    for view, chunk in zip(gltf["bufferViews"], views):
        blob += b"\0" * (-len(blob) % 4)
        view["byteOffset"], view["byteLength"], view["buffer"] = len(blob), len(chunk), 0
        blob += chunk
    blob += b"\0" * (-len(blob) % 4)
    gltf["buffers"] = [{"byteLength": len(blob)}]
    js = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(blob)
    return (struct.pack("<III", 0x46546C67, 2, total) + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(blob), 0x004E4942) + bytes(blob))


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ----- the edits -----

def boxes(size: int, regions) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    for x0, y0, x1, y1 in regions:
        d.rectangle([x0 * size, y0 * size, x1 * size, y1 * size], fill=255)
    return mask.filter(ImageFilter.GaussianBlur(size / 700))


def ramp(lum: np.ndarray, dark, light) -> np.ndarray:
    t = lum[..., None]
    return np.asarray(dark, np.float32) * (1 - t) + np.asarray(light, np.float32) * t


def draw_logo(size: int) -> tuple[Image.Image, Image.Image]:
    """The Max orb (same design as the app icon): a glowing cyan disc with a highlight, plus its glow mask."""
    cx, cy, r = LOGO[0] * size, LOGO[1] * size, LOGO[2] * size
    k = LOGO_SQUASH
    art = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(art)
    oval = lambda f, dx=0.0, dy=0.0: [cx + (dx - f) * r * k, cy + (dy - f) * r, cx + (dx + f) * r * k, cy + (dy + f) * r]
    d.ellipse(oval(1.0), outline=CYAN + (255,), width=max(2, int(r * 0.16)))
    d.ellipse(oval(0.55), fill=CYAN + (255,))
    d.ellipse(oval(0.17, -0.2, -0.25), fill=(230, 252, 255, 230))
    glow = art.split()[3]
    return art, glow


def recolor_tops(img: Image.Image) -> tuple[Image.Image, Image.Image]:
    rgba = np.asarray(img.convert("RGBA")).astype(np.float32)
    lum = (rgba[..., :3] @ np.array([0.299, 0.587, 0.114], np.float32)) / 255
    lum = np.clip((lum - 0.35) / 0.65, 0, 1) ** 0.9            # the white fabric spans ~0.35-1.0
    trim = np.asarray(boxes(img.width, TRIM), np.float32)[..., None] / 255
    rgb = ramp(lum, NAVY_DARK, NAVY_LIGHT) * (1 - trim) + ramp(lum, CYAN_DARK, CYAN) * trim
    out = Image.fromarray(np.dstack([rgb, rgba[..., 3:]]).astype(np.uint8), "RGBA")
    logo, logo_glow = draw_logo(img.width)
    out.alpha_composite(logo)
    glow = Image.fromarray((np.asarray(boxes(img.width, TRIM), np.float32) * 0.55).astype(np.uint8), "L")
    glow = Image.fromarray(np.maximum(np.asarray(glow), np.asarray(logo_glow)), "L")
    emission = Image.merge("RGB", [glow, glow, glow])          # white mask; the colour comes from _EmissionColor
    return out, emission


def recolor_iris(img: Image.Image) -> Image.Image:
    rgba = np.asarray(img.convert("RGBA")).astype(np.float32) / 255
    out = rgba.copy()
    hue = 0.525                                                   # cyan
    flat = rgba[..., :3].reshape(-1, 3)
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in flat]).reshape(rgba.shape[0], rgba.shape[1], 3)
    sat = hsv[..., 1] > 0.15
    hsv[..., 0] = np.where(sat, hue, hsv[..., 0])
    rgb = np.array([colorsys.hsv_to_rgb(*p) for p in hsv.reshape(-1, 3)]).reshape(rgba.shape[0], rgba.shape[1], 3)
    out[..., :3] = rgb
    return Image.fromarray((out * 255).astype(np.uint8), "RGBA")


def build(base: Path = BASE, out: Path = OUT) -> Path:
    if not base.exists():
        base.parent.mkdir(parents=True, exist_ok=True)
        base.write_bytes(requests.get(BASE_URL, timeout=120).content)
    gltf, blob = read_glb(base.read_bytes())
    views = [blob[v.get("byteOffset", 0):v.get("byteOffset", 0) + v["byteLength"]] for v in gltf["bufferViews"]]
    images = gltf["images"]
    by_name = {img["name"]: i for i, img in enumerate(images)}

    def load(i):
        return Image.open(io.BytesIO(views[images[i]["bufferView"]]))

    def replace(i, img):
        views[images[i]["bufferView"]] = png(img)

    tops_i, iris_i = by_name["M00_006_01_Tops_01"], by_name["M00_000_00_EyeIris_00"]
    tops, emission = recolor_tops(load(tops_i))
    replace(tops_i, tops)
    replace(iris_i, recolor_iris(load(iris_i)))

    # The glow mask is a new image + texture
    gltf["bufferViews"].append({"buffer": 0, "byteOffset": 0, "byteLength": 0})
    views.append(png(emission.resize((1024, 1024))))
    images.append({"name": "Max_Tops_Emission", "mimeType": "image/png", "bufferView": len(views) - 1})
    sampler = gltf["textures"][tops_i].get("sampler", 0)
    gltf["textures"].append({"source": len(images) - 1, "sampler": sampler})
    glow_tex = len(gltf["textures"]) - 1

    vrm = gltf["extensions"]["VRM"]
    for mp in vrm["materialProperties"]:
        if "Tops" in mp["name"]:
            mp["vectorProperties"]["_ShadeColor"] = [0.62, 0.68, 0.82, 1]
            mp["vectorProperties"]["_OutlineColor"] = [0.02, 0.04, 0.08, 1]
            mp["vectorProperties"]["_EmissionColor"] = [CYAN[0] / 255 * 0.8, CYAN[1] / 255 * 0.8, CYAN[2] / 255 * 0.8, 1]
            mp["textureProperties"]["_EmissionMap"] = glow_tex
    for m in gltf["materials"]:                                  # the plain glTF fallback, for other viewers
        if "Tops" in m["name"]:
            m["emissiveTexture"] = {"index": glow_tex}
            m["emissiveFactor"] = [c / 255 * 0.8 for c in CYAN]
    vrm["meta"].update({"title": "Max", "author": "Max assistant (from VRoid HairSample_Male, CC0)",
                        "version": "1.0", "licenseName": "CC0"})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(write_glb(gltf, views))
    return out


if __name__ == "__main__":
    print(f"Max edition written to {build()}")
