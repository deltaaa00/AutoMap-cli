// Copyright 2025-2026 deltaaa00
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::io::{Read, Write};
use std::process::{Command, Stdio};

/// Describes a single dynamic overlay layer passed from Python as a tuple:
/// `(rgba_bytes, x, y, width, height, start_sec, fade_in_sec)`.
#[derive(FromPyObject)]
struct DynamicLayer(Vec<u8>, i32, i32, usize, usize, f32, f32);

/// Composites the background video, static foreground, dynamic overlays, and intro veil
/// frame-by-frame in a single reusable RGBA buffer, streaming directly to/from `ffmpeg`.
#[pyfunction]
#[pyo3(signature = (bg_video_path, static_rgba, dynamic_layers, veil_duration, audio_path, output_path, width, height, fps, duration, codec, preset=None, draft=false))]
#[allow(clippy::too_many_arguments)]
fn render_video_rs(
    py: Python<'_>,
    bg_video_path: &str,
    static_rgba: &[u8],
    dynamic_layers: Vec<DynamicLayer>,
    veil_duration: f32,
    audio_path: Option<&str>,
    output_path: &str,
    width: usize,
    height: usize,
    fps: u32,
    duration: f32,
    codec: &str,
    preset: Option<&str>,
    draft: bool,
) -> PyResult<()> {
    let expected_len = width * height * 4;
    if static_rgba.len() != expected_len {
        return Err(pyo3::exceptions::PyValueError::new_err(format!(
            "static_rgba length {} does not match {}x{}x4 ({})",
            static_rgba.len(),
            width,
            height,
            expected_len
        )));
    }

    // Release Python's GIL so other Flask/worker threads are not blocked during rendering.
    py.allow_threads(|| {
        let ffmpeg_bin = std::env::var("FFMPEG_BINARY").unwrap_or_else(|_| "ffmpeg".to_string());
        let total_frames = (duration * fps as f32).round().max(1.0) as u32;
        let dur_str = format!("{duration:.3}");
        let fps_str = fps.to_string();

        // 1. Spawn ffmpeg decoder: reads the prepared background MP4 and outputs raw RGBA frames at `fps`.
        let mut decoder = Command::new(&ffmpeg_bin)
            .args([
                "-y",
                "-i",
                bg_video_path,
                "-r",
                &fps_str,
                "-t",
                &dur_str,
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgba",
                "-",
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("ffmpeg decoder spawn failed: {e}")))?;

        let mut dec_out = decoder
            .stdout
            .take()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("missing decoder stdout"))?;

        // 2. Build ffmpeg encoder args: reads raw RGBA frames from stdin and encodes with the chosen codec.
        let size_str = format!("{width}x{height}");
        let mut enc_args: Vec<String> = vec![
            "-y".into(),
            "-f".into(),
            "rawvideo".into(),
            "-pix_fmt".into(),
            "rgba".into(),
            "-s".into(),
            size_str,
            "-r".into(),
            fps_str,
            "-i".into(),
            "-".into(),
        ];

        if let Some(aud) = audio_path {
            enc_args.extend(["-i".into(), aud.into(), "-t".into(), dur_str.clone(), "-c:a".into(), "aac".into()]);
        }

        enc_args.extend(["-c:v".into(), codec.into()]);
        if let Some(p) = preset {
            enc_args.extend(["-preset".into(), p.into()]);
        }
        if draft {
            enc_args.extend(["-vf".into(), "scale=720:1280:flags=bicubic".into()]);
        }
        enc_args.extend(["-pix_fmt".into(), "yuv420p".into(), output_path.into()]);

        let mut encoder = Command::new(&ffmpeg_bin)
            .args(&enc_args)
            .stdin(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("ffmpeg encoder spawn failed: {e}")))?;

        let mut enc_in = encoder
            .stdin
            .take()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("missing encoder stdin"))?;

        // 3. Precompute non-transparent byte spans in static_rgba so alpha==0 regions are skipped per frame.
        let static_spans = build_non_transparent_spans(static_rgba);

        // 4. Reusable background and compositing frame buffers (~8.3 MB each for 1080x1920 RGBA).
        let mut bg_buf = vec![0u8; expected_len];
        let mut frame_buf = vec![0u8; expected_len];

        for frame_idx in 0..total_frames {
            if dec_out.read_exact(&mut bg_buf).is_err() {
                if frame_idx == 0 {
                    break;
                }
                // Hold the last decoded background frame if the source ended slightly early.
            }
            frame_buf.copy_from_slice(&bg_buf);

            let t = frame_idx as f32 / fps as f32;

            // Layer 1: flattened static foreground (map, title, watermark).
            blend_spans_in_place(&mut frame_buf, static_rgba, &static_spans);

            // Layer 2: dynamic overlays (subscribe badge, stars) with optional fade-in.
            for layer in &dynamic_layers {
                let start_sec = layer.5;
                let fade_in_sec = layer.6;
                if t < start_sec {
                    continue;
                }
                let opacity = if fade_in_sec > 0.0 {
                    ((t - start_sec) / fade_in_sec).clamp(0.0, 1.0)
                } else {
                    1.0
                };
                let op_u8 = (opacity * 255.0).round().clamp(0.0, 255.0) as u32;
                if op_u8 == 0 {
                    continue;
                }
                blend_rect_in_place(
                    &mut frame_buf,
                    width,
                    height,
                    &layer.0,
                    layer.1,
                    layer.2,
                    layer.3,
                    layer.4,
                    op_u8,
                );
            }

            // Layer 3: intro fade-in veil (darkens RGB channels linearly at the start).
            if veil_duration > 0.0 && t < veil_duration {
                let veil_alpha = (0.88 * (1.0 - (t / veil_duration))).clamp(0.0, 1.0);
                let brightness_q8 = ((1.0 - veil_alpha) * 256.0).round().clamp(0.0, 256.0) as u32;
                if brightness_q8 < 256 {
                    for px in frame_buf.chunks_exact_mut(4) {
                        px[0] = ((px[0] as u32 * brightness_q8) >> 8) as u8;
                        px[1] = ((px[1] as u32 * brightness_q8) >> 8) as u8;
                        px[2] = ((px[2] as u32 * brightness_q8) >> 8) as u8;
                    }
                }
            }

            // Stream the composited frame into ffmpeg's stdin.
            enc_in
                .write_all(&frame_buf)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("encoder write failed: {e}")))?;
        }

        // Close stdin so ffmpeg flushes and finalizes the MP4 container.
        drop(enc_in);
        let status = encoder
            .wait()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("encoder wait failed: {e}")))?;
        let _ = decoder.wait();

        if !status.success() {
            return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                "ffmpeg encoder exited with status {status}"
            )));
        }
        Ok(())
    })
}

/// Collects contiguous byte ranges `[start, end)` in `rgba` where `alpha > 0`.
fn build_non_transparent_spans(rgba: &[u8]) -> Vec<(usize, usize)> {
    let mut spans = Vec::new();
    let mut in_span = false;
    let mut start = 0usize;
    for (idx, px) in rgba.chunks_exact(4).enumerate() {
        let non_zero = px[3] > 0;
        if non_zero && !in_span {
            start = idx * 4;
            in_span = true;
        } else if !non_zero && in_span {
            spans.push((start, idx * 4));
            in_span = false;
        }
    }
    if in_span {
        spans.push((start, rgba.len() - (rgba.len() % 4)));
    }
    spans
}

/// Alpha-blends precomputed non-transparent byte spans of `overlay` onto `base` in place.
fn blend_spans_in_place(base: &mut [u8], overlay: &[u8], spans: &[(usize, usize)]) {
    for &(start, end) in spans {
        for (bg, fg) in base[start..end].chunks_exact_mut(4).zip(overlay[start..end].chunks_exact(4)) {
            blend_pixel_u8(bg, fg);
        }
    }
}

/// Alpha-blends a positioned rectangular RGBA overlay onto `base` in place, clipped to canvas bounds.
#[allow(clippy::too_many_arguments)]
fn blend_rect_in_place(
    base: &mut [u8],
    canvas_w: usize,
    canvas_h: usize,
    overlay: &[u8],
    x: i32,
    y: i32,
    ov_w: usize,
    ov_h: usize,
    op_u8: u32,
) {
    if op_u8 == 0 || ov_w == 0 || ov_h == 0 {
        return;
    }
    let start_y = y.max(0) as usize;
    let end_y = (y + ov_h as i32).min(canvas_h as i32).max(0) as usize;
    let start_x = x.max(0) as usize;
    let end_x = (x + ov_w as i32).min(canvas_w as i32).max(0) as usize;
    if start_x >= end_x || start_y >= end_y {
        return;
    }
    let row_bytes = (end_x - start_x) * 4;
    let ox_start = (start_x as i32 - x) as usize;

    for cy in start_y..end_y {
        let oy = (cy as i32 - y) as usize;
        let bg_start = (cy * canvas_w + start_x) * 4;
        let fg_start = (oy * ov_w + ox_start) * 4;
        if fg_start + row_bytes > overlay.len() || bg_start + row_bytes > base.len() {
            continue;
        }
        let bg_row = &mut base[bg_start..bg_start + row_bytes];
        let fg_row = &overlay[fg_start..fg_start + row_bytes];
        if op_u8 == 255 {
            for (bg, fg) in bg_row.chunks_exact_mut(4).zip(fg_row.chunks_exact(4)) {
                blend_pixel_u8(bg, fg);
            }
        } else {
            for (bg, fg) in bg_row.chunks_exact_mut(4).zip(fg_row.chunks_exact(4)) {
                blend_pixel_scaled(bg, fg, op_u8);
            }
        }
    }
}

#[inline(always)]
fn blend_pixel_u8(bg: &mut [u8], fg: &[u8]) {
    let a = fg[3] as u32;
    if a == 0 {
        return;
    }
    if a == 255 {
        bg.copy_from_slice(fg);
        return;
    }
    let inv_a = 255 - a;
    bg[0] = (((fg[0] as u32 * a + bg[0] as u32 * inv_a + 128) * 257) >> 16) as u8;
    bg[1] = (((fg[1] as u32 * a + bg[1] as u32 * inv_a + 128) * 257) >> 16) as u8;
    bg[2] = (((fg[2] as u32 * a + bg[2] as u32 * inv_a + 128) * 257) >> 16) as u8;
}

#[inline(always)]
fn blend_pixel_scaled(bg: &mut [u8], fg: &[u8], op_u8: u32) {
    let raw_a = fg[3] as u32;
    if raw_a == 0 {
        return;
    }
    let a = ((raw_a * op_u8 + 128) * 257) >> 16;
    if a == 0 {
        return;
    }
    let inv_a = 255 - a;
    bg[0] = (((fg[0] as u32 * a + bg[0] as u32 * inv_a + 128) * 257) >> 16) as u8;
    bg[1] = (((fg[1] as u32 * a + bg[1] as u32 * inv_a + 128) * 257) >> 16) as u8;
    bg[2] = (((fg[2] as u32 * a + bg[2] as u32 * inv_a + 128) * 257) >> 16) as u8;
}

/// Copies `base_rgba` and paints pixels at `flat_indices` (native-endian `u32` bytes)
/// using `region_ids` (native-endian `u16` bytes) into `lut_rgba` where `use_mask[rid] != 0`.
#[pyfunction]
fn paint_regions_rs(
    py: Python<'_>,
    base_rgba: &[u8],
    flat_indices: &[u8],
    region_ids: &[u8],
    lut_rgba: &[u8],
    use_mask: &[u8],
) -> PyResult<Py<PyBytes>> {
    if base_rgba.len() % 4 != 0 {
        return Err(PyValueError::new_err("base_rgba length must be a multiple of 4"));
    }
    if flat_indices.len() % 4 != 0 {
        return Err(PyValueError::new_err("flat_indices byte length must be a multiple of 4 (u32)"));
    }
    if region_ids.len() % 2 != 0 {
        return Err(PyValueError::new_err("region_ids byte length must be a multiple of 2 (u16)"));
    }
    let count = flat_indices.len() / 4;
    if region_ids.len() / 2 != count {
        return Err(PyValueError::new_err(format!(
            "flat_indices count ({count}) != region_ids count ({})",
            region_ids.len() / 2
        )));
    }
    let num_regions = use_mask.len();
    if lut_rgba.len() < num_regions * 4 {
        return Err(PyValueError::new_err(format!(
            "lut_rgba length ({}) is smaller than use_mask.len() * 4 ({})",
            lut_rgba.len(),
            num_regions * 4
        )));
    }
    let num_pixels = base_rgba.len() / 4;

    let bytes = PyBytes::new_with(py, base_rgba.len(), |out| {
        out.copy_from_slice(base_rgba);
        for (idx_b, id_b) in flat_indices.chunks_exact(4).zip(region_ids.chunks_exact(2)) {
            let rid = u16::from_ne_bytes([id_b[0], id_b[1]]) as usize;
            if rid < num_regions && use_mask[rid] != 0 {
                let pix_idx = u32::from_ne_bytes([idx_b[0], idx_b[1], idx_b[2], idx_b[3]]) as usize;
                if pix_idx < num_pixels {
                    let dst = pix_idx * 4;
                    let src = rid * 4;
                    out[dst..dst + 4].copy_from_slice(&lut_rgba[src..src + 4]);
                }
            }
        }
        Ok(())
    })?;
    Ok(bytes.unbind())
}

/// Fast integer Porter-Duff "over" compositing for straight RGBA buffers of equal length.
#[pyfunction]
fn alpha_composite_rs(
    py: Python<'_>,
    base_rgba: &[u8],
    overlay_rgba: &[u8],
) -> PyResult<Py<PyBytes>> {
    if base_rgba.len() != overlay_rgba.len() || base_rgba.len() % 4 != 0 {
        return Err(PyValueError::new_err(format!(
            "base_rgba ({}) and overlay_rgba ({}) must have equal length divisible by 4",
            base_rgba.len(),
            overlay_rgba.len()
        )));
    }

    let bytes = PyBytes::new_with(py, base_rgba.len(), |out| {
        out.copy_from_slice(base_rgba);
        for (dst, src) in out.chunks_exact_mut(4).zip(overlay_rgba.chunks_exact(4)) {
            let sa = src[3] as u32;
            if sa == 0 {
                continue;
            }
            if sa == 255 || dst[3] == 0 {
                dst.copy_from_slice(src);
                continue;
            }
            let da = dst[3] as u32;
            if da == 255 {
                let inv_sa = 255 - sa;
                dst[0] = (((src[0] as u32 * sa + dst[0] as u32 * inv_sa + 128) * 257) >> 16) as u8;
                dst[1] = (((src[1] as u32 * sa + dst[1] as u32 * inv_sa + 128) * 257) >> 16) as u8;
                dst[2] = (((src[2] as u32 * sa + dst[2] as u32 * inv_sa + 128) * 257) >> 16) as u8;
                // dst[3] remains 255
            } else {
                let sa_255 = sa * 255;
                let da_inv = da * (255 - sa);
                let out_a_255 = sa_255 + da_inv;
                let half = out_a_255 >> 1;
                dst[0] = ((src[0] as u32 * sa_255 + dst[0] as u32 * da_inv + half) / out_a_255) as u8;
                dst[1] = ((src[1] as u32 * sa_255 + dst[1] as u32 * da_inv + half) / out_a_255) as u8;
                dst[2] = ((src[2] as u32 * sa_255 + dst[2] as u32 * da_inv + half) / out_a_255) as u8;
                dst[3] = (((out_a_255 + 128) * 257) >> 16) as u8;
            }
        }
        Ok(())
    })?;
    Ok(bytes.unbind())
}

#[pymodule]
fn automap_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_video_rs, m)?)?;
    m.add_function(wrap_pyfunction!(paint_regions_rs, m)?)?;
    m.add_function(wrap_pyfunction!(alpha_composite_rs, m)?)?;
    Ok(())
}
