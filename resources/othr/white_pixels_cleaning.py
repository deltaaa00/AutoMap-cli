from PIL import Image

# Run from the asset directory: this utility overwrites map.png in place.
img = Image.open("map.png").convert("RGBA")

pixels = img.load()
for x in range(img.width):
    for y in range(img.height):
        r, g, b, a = pixels[x, y]

        # Remove partially transparent edge pixels while preserving opaque artwork.
        if a < 255:
            pixels[x, y] = (r, g, b, 0)

img.save("map.png")
