from PIL import Image

_OPAQUE_ALPHA_LUT: list[int] = [255 if a == 255 else 0 for a in range(256)]

# Run from the asset directory: this utility overwrites map.png in place.
with Image.open("map.png") as opened:
    img = opened.convert("RGBA")

# Remove partially transparent edge pixels in C via LUT while preserving opaque artwork.
r, g, b, a = img.split()
img = Image.merge("RGBA", (r, g, b, a.point(_OPAQUE_ALPHA_LUT)))
img.save("map.png")
