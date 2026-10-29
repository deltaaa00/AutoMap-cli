from PIL import Image

img = Image.open("map.png").convert("RGBA")

pixels = img.load()
for x in range(img.width):
    for y in range(img.height):
        r, g, b, a = pixels[x, y]

        if a < 255:
            pixels[x, y] = (r, g, b, 0)

img.save("map.png")
