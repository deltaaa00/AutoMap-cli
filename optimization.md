# Performance optimization review

This review covers the map generation engine (`map/auto_map.py` and
`map/functions.py`) and the video rendering engine (`yt-dlp_serv.py`). The
bottlenecks below are inferred from the code, not measured with a profiler.
Actual timings depend on the map type, AI service latency, input media,
video duration, and available CPU resources.

## Map generation

### Repeated flood-filling

The default map is 3900 × 4104 pixels, approximately 16 megapixels. The engine
calls `ImageDraw.floodfill` for each region's seed points, including additional
points for islands and disconnected land areas. Each generation discovers the
region boundaries again even though the underlying map artwork is unchanged.
This is the strongest candidate for the map engine's largest local CPU cost.
Flag maps skip this step.

**Proposed improvement:** precompute a region mask or region-ID image for each
base map. During generation, assign colors using those masks or a NumPy color
lookup table instead of running flood fill repeatedly. A region-ID image stores
one region identifier per pixel and avoids keeping a full-size mask for every
region in memory.

**Tradeoffs and validation:** regenerate the masks whenever the base artwork or
seed coordinates change. Compare filled boundaries, islands, transparent areas,
and overlapping seeds with the current output before replacing the algorithm.
Existing fill tolerances must be reflected in the preprocessing step.

### AI request latency

Every map generation requests data from Gemini before painting the regions.
Network and model response time can exceed local image processing time. Changing
only the font, font size, or rendering style currently still requires a new
request.

**Proposed improvement:** separate data retrieval from rendering and reuse an
existing response for visual changes. An optional response cache should account
for the prompt, map type, model, and options that modify the AI request.

**Tradeoffs:** time-sensitive data needs expiration and a refresh option. Cached
responses should remain isolated between users where appropriate, and API keys
must not be stored as cache contents.

### Loading and compositing large images

The engine loads the base artwork, relief, and borders for each generation. It
also resizes the relief, adjusts its opacity, and composites several large RGBA
images. PNG encoding adds another cost at the end.

**Proposed improvements:**

- Cache decoded artwork and prepared overlays within each worker process.
- Copy cached base images before modifying them so requests cannot affect one another.
- Offer a smaller output size for maps intended only for video, scaling seed
  coordinates, labels, and outlines consistently.
- Measure PNG encoding separately before changing compression settings.

**Tradeoffs:** image caches consume memory in every worker process. Smaller
outputs reduce detail and may affect small regions and labels. Keep the original
resolution available for image exports.

## Video rendering

### Processing frames through Python and MoviePy

The engine outputs 1080 × 1920 video at 30 FPS. An eight-second video requires
240 output frames; a thirty-second video requires 900. MoviePy scales and crops
the background, applies a Python/Pillow blur when effects are enabled, composites
the layers, and passes frames to FFmpeg for encoding. Repeated array conversion,
resizing, and frame transfer can be expensive.

**Proposed improvement:** use one native FFmpeg pipeline for background scaling,
cropping, blur, overlays, audio handling, and encoding. Continue preparing title
and watermark images with Pillow to preserve their existing appearance.

**Tradeoffs:** this is the largest implementation change. Verify transparency,
layer order, placement, colors, source duration limits, audio synchronization,
and preview consistency. Moving processing into FFmpeg does not automatically
provide GPU acceleration.

### Compositing static layers repeatedly

The map, title, logo, watermark text, and shading do not change during a video.
They are currently separate clips inside `CompositeVideoClip`, so their layers
are composed into each output frame.

**Proposed improvement:** compose these static elements into one transparent
1080 × 1920 foreground image before encoding. Each frame then combines the
moving background with a single foreground layer.

**Tradeoffs:** preserve the current layer order and alpha handling. This fits
the current static design; future animations would need separate dynamic layers.
The improvement still needs measurement because video encoding may dominate.

### CPU video encoding

`write_videofile` receives the codec and frame rate but no explicit speed preset.
The currently deployed Docker worker reports only `libx264` as usable, so video
encoding runs on the CPU. The presence of an NVIDIA or Apple encoder in a menu
does not make that hardware available inside the worker.

**Proposed improvements:**

- Benchmark `fast` and `veryfast` x264 presets against the current configuration.
- Offer a 720 × 1280 draft mode for quick layout checks.
- Use hardware encoding only when the worker's capability probe succeeds and
  representative renders confirm compatibility.

**Tradeoffs:** faster presets trade compression efficiency for speed and can
increase file size at comparable quality settings. Draft resolution reduces
detail. Hardware encoding requires a compatible runtime and device access.

### Upload and proxy overhead

The browser converts materials into base64 strings, sends them as JSON to the
web service, and the web service forwards them to the worker. Base64 adds roughly
one third to the binary payload size before JSON overhead and requires encoding,
decoding, and additional memory copies.

**Proposed improvement:** consider binary multipart uploads or storing uploaded
materials once and referring to them by an identifier for subsequent renders.

**Tradeoffs:** this requires upload lifecycle management and cleanup. It is a
secondary priority unless measurements show upload time or memory pressure is
significant.

## What is probably not worth optimizing first

Title rasterization, logo shadows, and watermark lettering are prepared once
per render. Font objects are already reused within a map generation. These are
unlikely to offer the same gains as eliminating repeated flood fills or reducing
per-frame video processing. Measure before spending time on them.

## Suggested implementation order

| Priority | Improvement | Reason |
| --- | --- | --- |
| 1 | Precomputed region masks or a region-ID image | Removes repeated boundary discovery from map generation. |
| 2 | One static video foreground | Reduces repeated compositing with a relatively contained change. |
| 3 | Faster encoding preset and optional draft resolution | Provides adjustable speed and quality tradeoffs. |
| 4 | Native FFmpeg video pipeline | Addresses Python frame processing and transfer, with more implementation work. |
| 5 | Reusable AI responses and prepared map overlays | Helps repeated edits; prioritize AI response reuse sooner if network latency dominates. |
| 6 | Binary or reusable uploads | Addresses transfer and memory overhead when those are measurable bottlenecks. |

## How to measure the improvements

Before implementing changes, record separate timings for AI retrieval, artwork
loading, flood filling, label drawing, image compositing, PNG encoding, uploads,
video setup, and frame processing plus encoding. The `write_videofile` call
includes frame generation as well as encoding, so its elapsed time alone cannot
identify which of those is slower.

Use fixed AI responses for local map benchmarks so network variability does not
hide CPU improvements. Compare identical inputs and output settings across
several runs, distinguishing the first run from warm-cache runs. For video,
measure effects enabled and disabled, short and long clips, and representative
background resolutions. Record wall-clock time, peak memory, output size, and
visual quality. Do not promise a particular speedup until these comparisons are
available.

This document proposes optimizations; creating it does not change either engine.
