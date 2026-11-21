# Copyright 2025-2026 deltaaa00
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Offline vector artwork for the extended sticker collection.

``media.stickers`` delegates icons outside its original set to
``draw_extra_sticker``. Artwork uses normalized coordinates scaled to a square
Pillow canvas; this module draws into the caller's canvas and does not save files,
apply shadows, fetch assets, or choose themes. Returning False lets the caller
continue its own fallback drawing for an unsupported identifier.
"""


def draw_extra_sticker(kind, draw, s, outline, ow, star_polygon_fn=None):
    """Draw an extended icon into the caller's Pillow ImageDraw canvas.

    ``s`` is the square canvas size in pixels and ``ow`` the outline width.
    Normalized coordinates are scaled internally; ``star_polygon_fn`` is the
    caller's shared star geometry helper. Return True when the icon is drawn,
    or False without drawing when this collection does not handle ``kind``.
    """
    extras = {
        'mountain', 'volcano', 'island', 'rainbow', 'moon', 'rain', 'robot',
        'satellite', 'laptop', 'graduation', 'medal', 'cat', 'dog', 'fish', 'burger', 'train',
        'bank', 'coin', 'hospital', 'pill', 'family', 'city', 'castle', 'museum',
        'gamepad', 'camera', 'oil', 'leaf', 'scales', 'ship',
    }
    if kind not in extras:
        return False

    def coords(values):
        return tuple(value * s for value in values)

    def ellipse(box, color):
        draw.ellipse(coords(box), fill=color, outline=outline, width=ow)

    def rect(box, color):
        draw.rounded_rectangle(coords(box), radius=ow * 2, fill=color, outline=outline, width=ow)

    def poly(points, color):
        draw.polygon([coords(point) for point in points], fill=color, outline=outline, width=ow)

    def line(points, color, width=None):
        draw.line([coords(point) for point in points], fill=color, width=width or ow)

    blue, gold, white = '#38bdf8', '#fbbf24', '#f8fafc'
    if kind in ('mountain', 'volcano'):
        if kind == 'volcano':
            ellipse((.35, .06, .61, .3), '#94a3b8')
        poly([(.08, .9), (.48, .2), (.9, .9)], '#78716c' if kind == 'volcano' else '#64748b')
        poly([(.32, .49), (.48, .2), (.66, .49), (.53, .42), (.43, .51)], '#fb923c' if kind == 'volcano' else white)
        if kind == 'volcano':
            line([(.49, .42), (.6, .65), (.55, .86)], '#ef4444', ow * 2)
    elif kind == 'island':
        ellipse((.07, .65, .94, .94), blue)
        ellipse((.19, .66, .84, .87), gold)
        line([(.48, .74), (.6, .3)], '#92400e', ow * 2)
        for tip in [(.23, .3), (.38, .1), (.82, .16), (.91, .39), (.7, .5)]:
            poly([(.58, .31), tip, (.55, .4)], '#22c55e')
    elif kind == 'rainbow':
        for index, color in enumerate(['#ef4444', '#fb923c', gold, '#22c55e', '#38bdf8', '#a78bfa']):
            inset = index * .055
            draw.arc(coords((.08 + inset, .13 + inset, .92 - inset, .97 - inset)), 180, 360, fill=color, width=ow * 2)
    elif kind == 'moon':
        ellipse((.15, .08, .85, .89), gold)
        draw.ellipse(coords((.37, .01, .99, .7)), fill=(0, 0, 0, 0))
    elif kind == 'rain':
        for box in [(.12, .25, .57, .61), (.31, .12, .75, .61), (.56, .29, .9, .63)]:
            ellipse(box, '#cbd5e1')
        for x in (.27, .5, .73):
            line([(x, .7), (x - .06, .89)], blue, ow * 2)
    elif kind == 'robot':
        line([(.5, .1), (.5, .25)], '#94a3b8')
        ellipse((.44, .05, .56, .18), '#ef4444')
        rect((.14, .25, .86, .86), '#94a3b8')
        for x in (.27, .6):
            ellipse((x, .39, x + .13, .55), blue)
        rect((.29, .65, .71, .77), white)
        line([(.43, .66), (.43, .76)], outline)
        line([(.57, .66), (.57, .76)], outline)
    elif kind == 'satellite':
        rect((.05, .3, .31, .73), '#2563eb')
        rect((.69, .3, .95, .73), '#2563eb')
        line([(.29, .52), (.72, .52)], gold, ow * 2)
        rect((.35, .25, .65, .78), '#cbd5e1')
        poly([(.41, .24), (.64, .07), (.76, .28)], white)
    elif kind == 'laptop':
        rect((.18, .16, .82, .69), '#475569')
        rect((.25, .24, .75, .6), blue)
        poly([(.18, .69), (.82, .69), (.96, .86), (.04, .86)], '#cbd5e1')
    elif kind == 'graduation':
        rect((.25, .4, .74, .75), '#1e293b')
        poly([(.07, .34), (.5, .13), (.94, .34), (.5, .55)], '#334155')
        line([(.51, .34), (.83, .42), (.83, .78)], gold, ow * 2)
    elif kind == 'medal':
        poly([(.25, .09), (.47, .09), (.61, .51), (.36, .55)], '#ef4444')
        poly([(.54, .09), (.76, .09), (.64, .55), (.4, .51)], '#2563eb')
        ellipse((.2, .4, .81, .96), gold)
        if star_polygon_fn is not None:
            draw.polygon(star_polygon_fn(s * .5, s * .68, s * .18, s * .08), fill=white)
    elif kind in ('cat', 'dog'):
        color = '#fb923c' if kind == 'cat' else '#b45309'
        if kind == 'cat':
            poly([(.18, .43), (.18, .12), (.47, .35)], color)
            poly([(.53, .35), (.83, .12), (.83, .43)], color)
        else:
            ellipse((.04, .19, .34, .72), color)
            ellipse((.67, .19, .96, .72), color)
        ellipse((.18, .27, .83, .91), color)
        for x in (.33, .62):
            ellipse((x, .47, x + .06, .55), outline)
        ellipse((.36, .61, .66, .81), '#ffedd5')
        poly([(.44, .64), (.59, .64), (.51, .73)], outline)
    elif kind == 'fish':
        poly([(.59, .51), (.92, .27), (.92, .79)], '#fb923c')
        ellipse((.08, .26, .74, .8), blue)
        ellipse((.22, .41, .29, .48), outline)
        line([(.47, .39), (.41, .54), (.49, .65)], '#2563eb')
    elif kind == 'burger':
        ellipse((.13, .13, .88, .62), gold)
        for y, color in [(.44, '#22c55e'), (.56, '#ef4444'), (.68, '#78350f'), (.78, gold)]:
            rect((.12, y, .89, y + .12), color)
    elif kind == 'train':
        rect((.21, .09, .8, .8), '#ef4444')
        rect((.28, .22, .72, .48), blue)
        for x in (.3, .61):
            ellipse((x, .59, x + .1, .69), gold)
        line([(.27, .83), (.14, .95)], outline, ow * 2)
        line([(.74, .83), (.87, .95)], outline, ow * 2)
    elif kind in ('bank', 'museum'):
        poly([(.08, .35), (.5, .1), (.92, .35)], '#cbd5e1')
        rect((.12, .35, .88, .44), '#94a3b8')
        for x in (.2, .43, .67):
            rect((x, .44, x + .13, .82), '#e2e8f0')
        rect((.08, .82, .92, .93), '#64748b')
    elif kind == 'coin':
        ellipse((.12, .12, .88, .88), gold)
        ellipse((.23, .23, .77, .77), '#f59e0b')
        line([(.5, .32), (.5, .68)], white, ow * 2)
    elif kind == 'hospital':
        rect((.14, .14, .86, .88), white)
        rect((.43, .27, .57, .73), '#ef4444')
        rect((.27, .43, .73, .57), '#ef4444')
    elif kind == 'pill':
        rect((.16, .33, .52, .67), '#ef4444')
        rect((.48, .33, .84, .67), white)
    elif kind == 'family':
        ellipse((.16, .2, .4, .44), blue)
        rect((.12, .46, .44, .86), blue)
        ellipse((.6, .2, .84, .44), '#f472b6')
        rect((.56, .46, .88, .86), '#f472b6')
        ellipse((.4, .4, .6, .58), gold)
        rect((.36, .6, .64, .88), gold)
    elif kind == 'city':
        rect((.1, .34, .36, .9), '#475569')
        rect((.34, .12, .66, .9), '#334155')
        rect((.64, .28, .9, .9), '#64748b')
        for y in (.24, .4, .56):
            rect((.42, y, .58, y + .08), gold)
    elif kind == 'castle':
        rect((.12, .28, .32, .88), '#94a3b8')
        rect((.68, .28, .88, .88), '#94a3b8')
        rect((.28, .44, .72, .88), '#cbd5e1')
        poly([(.1, .28), (.22, .1), (.34, .28)], '#ef4444')
        poly([(.66, .28), (.78, .1), (.9, .28)], '#ef4444')
        rect((.42, .62, .58, .88), '#78350f')
    elif kind == 'gamepad':
        rect((.1, .28, .9, .76), '#334155')
        line([(.28, .44), (.28, .6)], white, ow * 2)
        line([(.2, .52), (.36, .52)], white, ow * 2)
        ellipse((.64, .42, .74, .52), '#22c55e')
        ellipse((.74, .54, .84, .64), '#ef4444')
    elif kind == 'camera':
        rect((.34, .18, .66, .3), '#64748b')
        rect((.1, .28, .9, .82), '#334155')
        ellipse((.33, .38, .67, .72), blue)
        ellipse((.42, .47, .58, .63), '#1e293b')
    elif kind == 'oil':
        rect((.24, .16, .76, .88), '#1e293b')
        line([(.24, .38), (.76, .38)], gold, ow * 2)
        line([(.24, .66), (.76, .66)], gold, ow * 2)
    elif kind == 'leaf':
        poly([(.18, .78), (.3, .3), (.82, .18), (.7, .7)], '#22c55e')
        line([(.14, .86), (.72, .28)], '#15803d', ow * 2)
    elif kind == 'scales':
        line([(.5, .14), (.5, .86)], gold, ow * 2)
        line([(.28, .86), (.72, .86)], gold, ow * 2)
        line([(.18, .3), (.82, .3)], gold, ow * 2)
        poly([(.12, .62), (.24, .62), (.18, .32)], '#cbd5e1')
        poly([(.76, .62), (.88, .62), (.82, .32)], '#cbd5e1')
    elif kind == 'ship':
        rect((.34, .22, .66, .56), white)
        rect((.44, .1, .56, .24), '#ef4444')
        poly([(.08, .56), (.92, .56), (.78, .82), (.22, .82)], '#334155')
        line([(.08, .88), (.92, .88)], blue, ow * 2)
    return True
