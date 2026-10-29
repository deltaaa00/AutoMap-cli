#This file makes blured borders


from PIL import Image, ImageFilter
import numpy as np

def borders_procesing(image: Image.Image): #Function prepares borders to composing after ImageFilter.GaussianBlur()

    pixels = np.array(image)

    r, g, b, a = pixels[..., 0], pixels[..., 1], pixels[..., 2], pixels[..., 3]

    mask = (r != 0) & (g != 0) & (b != 0)

    pixels[mask, 0:3] = 0

    alpha_mask = mask & (a != 0)
    pixels[alpha_mask, 3] = np.clip(a[alpha_mask].astype(np.int16) + 30, 0, 255)

    return Image.fromarray(pixels)


borders = Image.open("default_map.png").convert("RGBA")
borders = borders.filter(ImageFilter.GaussianBlur(radius=15))

borders = borders_procesing(borders)

borders.save("default_map_blured.png")