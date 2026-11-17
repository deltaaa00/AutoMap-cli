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
use pyo3::types::{PyBytes, PyTuple};
use std::io::{Read, Write};
use std::process::{Command, Stdio};

/// Describes a single dynamic overlay layer passed from Python as a tuple:
/// `(rgba_bytes, x, y, width, height, start_sec, fade_in_sec)`.
#[derive(FromPyObject)]
struct DynamicLayer(Vec<u8>, i32, i32, usize, usize, f32, f32);

/// Optional camera zoom parameters:
/// `(target_cx, target_cy, z_scale, z_start, z_dur, z_out_enabled, z_out_dur)`.
type ZoomParams = (f32, f32, f32, f32, f32, bool, f32);

/// Optional country reveal animation & impact FX specification:
/// `(x, y, w, h, frame1_rgba, frame2_rgba, frame3_rgba, black_rgba, base_rgba, r_time, phase_end, fade_dur, impact_fx)`.
struct RevealSpec(
    i32,
    i32,
    usize,
    usize,
    Vec<u8>,
    Vec<u8>,
    Vec<u8>,
    Vec<u8>,
    Vec<u8>,
    f32,
    f32,
    f32,
    bool,
);

impl<'py> FromPyObject<'py> for RevealSpec {
    fn extract_bound(ob: &Bound<'py, PyAny>) -> PyResult<Self> {
        let t = ob.downcast::<PyTuple>()?;
        if t.len() != 13 {
            return Err(PyValueError::new_err(format!(
                "reveal_spec must be a 13-tuple, got length {}",
                t.len()
            )));
        }
        Ok(Self(
            t.get_item(0)?.extract()?,
            t.get_item(1)?.extract()?,
            t.get_item(2)?.extract()?,
            t.get_item(3)?.extract()?,
            t.get_item(4)?.extract()?,
            t.get_item(5)?.extract()?,
            t.get_item(6)?.extract()?,
            t.get_item(7)?.extract()?,
            t.get_item(8)?.extract()?,
            t.get_item(9)?.extract()?,
            t.get_item(10)?.extract()?,
            t.get_item(11)?.extract()?,
            t.get_item(12)?.extract()?,
        ))
    }
}

/// Composites the background video, static foreground, country reveal animation,
/// dynamic overlays, intro veil, camera zoom, and impact FX frame-by-frame in reusable
/// RGBA buffers, streaming directly to/from `ffmpeg`.
#[pyfunction]
#[pyo3(signature = (bg_video_path, static_rgba, dynamic_layers, veil_duration, audio_path, output_path, width, height, fps, duration, codec, preset=None, draft=false, zoom_params=None, reveal_spec=None))]
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
    zoom_params: Option<ZoomParams>,
    reveal_spec: Option<RevealSpec>,
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
    if let Some(RevealSpec(_, _, rw, rh, ref f1, ref f2, ref f3, ref black, ref base, _, _, _, _)) = reveal_spec {
        let rev_len = rw * rh * 4;
        for (name, buf) in [
            ("frame1_rgba", f1),
            ("frame2_rgba", f2),
            ("frame3_rgba", f3),
            ("black_rgba", black),
            ("base_rgba", base),
        ] {
            if buf.len() != rev_len {
                return Err(PyValueError::new_err(format!(
                    "reveal_spec {name} length {} does not match {}x{}x4 ({})",
                    buf.len(),
                    rw,
                    rh,
                    rev_len
                )));
            }
        }
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

        // 4. Reusable background, compositing, and zoom/FX frame buffers (~8.3 MB each for 1080x1920 RGBA).
        let impact_fx_enabled = reveal_spec.as_ref().is_some_and(|r| r.12);
        let need_zoom_buf = zoom_params.is_some() || impact_fx_enabled;
        let mut bg_buf = vec![0u8; expected_len];
        let mut frame_buf = vec![0u8; expected_len];
        let mut zoom_buf = if need_zoom_buf { vec![0u8; expected_len] } else { Vec::new() };
        let mut ca_row_buf = if impact_fx_enabled { vec![0u8; width * 4] } else { Vec::new() };
        let mut x_resample_lut = if need_zoom_buf { Vec::with_capacity(width) } else { Vec::new() };

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

            // Layer 1b: country reveal animation (dots -> fade from black to base).
            if let Some(RevealSpec(rx, ry, rw, rh, ref f1, ref f2, ref f3, ref black, ref base, r_time, phase_end, fade_dur, _)) =
                reveal_spec
            {
                if t < r_time {
                    let dot_frame = if t < phase_end * 0.33 {
                        f1
                    } else if t < phase_end * 0.66 {
                        f2
                    } else {
                        f3
                    };
                    blend_rect_in_place(&mut frame_buf, width, height, dot_frame, rx, ry, rw, rh, 255);
                } else if t >= r_time + fade_dur {
                    blend_rect_in_place(&mut frame_buf, width, height, base, rx, ry, rw, rh, 255);
                } else {
                    let fade_alpha = (1.0 - (t - r_time) / fade_dur).clamp(0.0, 1.0);
                    let fade_q8 = (fade_alpha * 256.0).round().clamp(0.0, 256.0) as i32;
                    blend_lerp_rect_in_place(&mut frame_buf, width, height, base, black, rx, ry, rw, rh, fade_q8);
                }
            }

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

            // Layer 4: camera zoom + reveal impact FX (punch, shake, CA, glitch bands, flash).
            let out_buf: &[u8] = if need_zoom_buf {
                let (mut cw, mut ch, mut x0, mut y0) = if let Some(zp) = zoom_params {
                    compute_zoom_crop(zp, width as f32, height as f32, t, duration)
                } else {
                    (width as f32, height as f32, 0.0, 0.0)
                };

                let impact_dt = if impact_fx_enabled {
                    reveal_spec.as_ref().map(|r| t - r.9).filter(|&dt| (0.0..0.60).contains(&dt))
                } else {
                    None
                };

                if let Some(dt) = impact_dt {
                    let env = (-dt * 5.8).exp() * (1.0 - dt / 0.60).max(0.0);
                    let punch = 1.0 + 0.042 * env;
                    let inv_p = 1.0 / punch;
                    let new_cw = cw * inv_p;
                    let new_ch = ch * inv_p;
                    let margin_x = (cw - new_cw) * 0.5;
                    let margin_y = (ch - new_ch) * 0.5;
                    let shake_x = env * 22.0 * (dt * 85.0).sin() + env * 8.0 * (dt * 145.0).cos();
                    let shake_y = env * 18.0 * (dt * 78.0).cos() - env * 6.0 * (dt * 130.0).sin();
                    cw = new_cw;
                    ch = new_ch;
                    x0 = (x0 + margin_x + shake_x).clamp(0.0, (width as f32 - cw).max(0.0));
                    y0 = (y0 + margin_y + shake_y).clamp(0.0, (height as f32 - ch).max(0.0));
                }

                let do_resample = cw < width as f32 - 0.5 || x0.abs() > 0.1 || y0.abs() > 0.1;
                let active_buf: &mut [u8] = if do_resample {
                    bilinear_resample_rgba(
                        &frame_buf,
                        &mut zoom_buf,
                        width,
                        height,
                        x0,
                        y0,
                        cw,
                        ch,
                        &mut x_resample_lut,
                    );
                    &mut zoom_buf
                } else {
                    &mut frame_buf
                };

                if let Some(dt) = impact_dt {
                    if dt < 0.38 {
                        apply_impact_fx_in_place(active_buf, width, height, dt, &mut ca_row_buf);
                    }
                }
                active_buf
            } else {
                &frame_buf
            };

            // Stream the composited frame into ffmpeg's stdin.
            enc_in
                .write_all(out_buf)
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

/// Computes the clipped destination/source bounds for a positioned overlay rectangle.
/// Returns `(start_x, start_y, end_y, row_bytes, ox_start)` or `None` if completely off-screen.
#[inline]
fn clip_rect(
    canvas_w: usize,
    canvas_h: usize,
    x: i32,
    y: i32,
    ov_w: usize,
    ov_h: usize,
) -> Option<(usize, usize, usize, usize, usize)> {
    if ov_w == 0 || ov_h == 0 {
        return None;
    }
    let start_y = y.max(0) as usize;
    let end_y = (y + ov_h as i32).min(canvas_h as i32).max(0) as usize;
    let start_x = x.max(0) as usize;
    let end_x = (x + ov_w as i32).min(canvas_w as i32).max(0) as usize;
    if start_x >= end_x || start_y >= end_y {
        return None;
    }
    let row_bytes = (end_x - start_x) * 4;
    let ox_start = (start_x as i32 - x) as usize;
    Some((start_x, start_y, end_y, row_bytes, ox_start))
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
    if op_u8 == 0 {
        return;
    }
    let Some((start_x, start_y, end_y, row_bytes, ox_start)) = clip_rect(canvas_w, canvas_h, x, y, ov_w, ov_h)
    else {
        return;
    };

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

/// Linearly interpolates `base_ov + (fade_q8 / 256) * (black_ov - base_ov)` and alpha-blends
/// the resulting RGBA pixels onto `canvas` at `(x, y)` in place.
#[allow(clippy::too_many_arguments)]
fn blend_lerp_rect_in_place(
    canvas: &mut [u8],
    canvas_w: usize,
    canvas_h: usize,
    base_ov: &[u8],
    black_ov: &[u8],
    x: i32,
    y: i32,
    ov_w: usize,
    ov_h: usize,
    fade_q8: i32,
) {
    let Some((start_x, start_y, end_y, row_bytes, ox_start)) = clip_rect(canvas_w, canvas_h, x, y, ov_w, ov_h)
    else {
        return;
    };

    for cy in start_y..end_y {
        let oy = (cy as i32 - y) as usize;
        let dst_start = (cy * canvas_w + start_x) * 4;
        let src_start = (oy * ov_w + ox_start) * 4;
        if src_start + row_bytes > base_ov.len()
            || src_start + row_bytes > black_ov.len()
            || dst_start + row_bytes > canvas.len()
        {
            continue;
        }
        let dst_row = &mut canvas[dst_start..dst_start + row_bytes];
        let base_row = &base_ov[src_start..src_start + row_bytes];
        let black_row = &black_ov[src_start..src_start + row_bytes];
        for ((dst, b), k) in dst_row
            .chunks_exact_mut(4)
            .zip(base_row.chunks_exact(4))
            .zip(black_row.chunks_exact(4))
        {
            let fg = [
                (b[0] as i32 + (((k[0] as i32 - b[0] as i32) * fade_q8 + 128) >> 8)).clamp(0, 255) as u8,
                (b[1] as i32 + (((k[1] as i32 - b[1] as i32) * fade_q8 + 128) >> 8)).clamp(0, 255) as u8,
                (b[2] as i32 + (((k[2] as i32 - b[2] as i32) * fade_q8 + 128) >> 8)).clamp(0, 255) as u8,
                (b[3] as i32 + (((k[3] as i32 - b[3] as i32) * fade_q8 + 128) >> 8)).clamp(0, 255) as u8,
            ];
            blend_pixel_u8(dst, &fg);
        }
    }
}

#[inline]
fn smootherstep(u: f32) -> f32 {
    u * u * u * (u * (u * 6.0 - 15.0) + 10.0)
}

/// Computes the camera zoom crop `(cw, ch, x0, y0)` at time `t` using quintic smootherstep.
fn compute_zoom_crop(zp: ZoomParams, canvas_w: f32, canvas_h: f32, t: f32, total_dur: f32) -> (f32, f32, f32, f32) {
    let (target_cx, target_cy, z_scale, z_start, z_dur, z_out_enabled, z_out_dur) = zp;
    if z_scale <= 1.0 || t <= z_start {
        return (canvas_w, canvas_h, 0.0, 0.0);
    }

    let final_cw = canvas_w / z_scale;
    let final_ch = canvas_h / z_scale;
    let final_x0 = (target_cx - final_cw * 0.5).clamp(0.0, (canvas_w - final_cw).max(0.0));
    let final_y0 = (target_cy - final_ch * 0.5).clamp(0.0, (canvas_h - final_ch).max(0.0));

    let ease = if z_dur > 0.0 && t < z_start + z_dur {
        smootherstep((t - z_start) / z_dur)
    } else if z_out_enabled && z_out_dur > 0.0 && total_dur > (z_start + z_dur + 0.05) {
        let out_start = (z_start + z_dur).max(total_dur - z_out_dur);
        let out_dur = (total_dur - out_start).max(0.05);
        if t >= total_dur {
            0.0
        } else if t > out_start {
            1.0 - smootherstep((t - out_start) / out_dur)
        } else {
            1.0
        }
    } else {
        1.0
    };

    if ease <= 0.0 {
        return (canvas_w, canvas_h, 0.0, 0.0);
    }

    let cw = canvas_w + (final_cw - canvas_w) * ease;
    let ch = canvas_h + (final_ch - canvas_h) * ease;
    let x0 = final_x0 * ease;
    let y0 = final_y0 * ease;
    (cw, ch, x0, y0)
}

/// Fast 8-bit fixed-point bilinear resample of the crop `[x0, x0+cw] x [y0, y0+ch]` from `src` into `dst`.
#[allow(clippy::too_many_arguments)]
fn bilinear_resample_rgba(
    src: &[u8],
    dst: &mut [u8],
    width: usize,
    height: usize,
    x0: f32,
    y0: f32,
    cw: f32,
    ch: f32,
    x_lut: &mut Vec<(usize, usize, u32, u32)>,
) {
    if width == 0 || height == 0 {
        return;
    }
    let scale_x = cw / width as f32;
    let scale_y = ch / height as f32;
    let max_x = (width - 1) as f32;
    let max_y = (height - 1) as f32;

    x_lut.clear();
    for dx in 0..width {
        let sx = (x0 + (dx as f32 + 0.5) * scale_x - 0.5).clamp(0.0, max_x);
        let ix0 = (sx as usize).min(width - 1);
        let ix1 = (ix0 + 1).min(width - 1);
        let wx1 = ((sx - ix0 as f32) * 256.0).round().clamp(0.0, 256.0) as u32;
        let wx0 = 256 - wx1;
        x_lut.push((ix0 * 4, ix1 * 4, wx0, wx1));
    }

    let row_stride = width * 4;
    for dy in 0..height {
        let sy = (y0 + (dy as f32 + 0.5) * scale_y - 0.5).clamp(0.0, max_y);
        let iy0 = (sy as usize).min(height - 1);
        let iy1 = (iy0 + 1).min(height - 1);
        let wy1 = ((sy - iy0 as f32) * 256.0).round().clamp(0.0, 256.0) as u32;
        let wy0 = 256 - wy1;

        let row0 = &src[iy0 * row_stride..(iy0 + 1) * row_stride];
        let row1 = &src[iy1 * row_stride..(iy1 + 1) * row_stride];
        let dst_row = &mut dst[dy * row_stride..(dy + 1) * row_stride];

        for (dst_px, &(ox0, ox1, wx0, wx1)) in dst_row.chunks_exact_mut(4).zip(x_lut.iter()) {
            let p00 = &row0[ox0..ox0 + 4];
            let p10 = &row0[ox1..ox1 + 4];
            let p01 = &row1[ox0..ox0 + 4];
            let p11 = &row1[ox1..ox1 + 4];

            for c in 0..3 {
                let top = p00[c] as u32 * wx0 + p10[c] as u32 * wx1;
                let bot = p01[c] as u32 * wx0 + p11[c] as u32 * wx1;
                dst_px[c] = ((top * wy0 + bot * wy1 + 32768) >> 16) as u8;
            }
            dst_px[3] = 255;
        }
    }
}

/// Applies RGB chromatic aberration, horizontal glitch band rolls, and flash brightness boost in place.
fn apply_impact_fx_in_place(buf: &mut [u8], width: usize, height: usize, dt: f32, row_buf: &mut [u8]) {
    if width == 0 || height == 0 {
        return;
    }
    let glitch_env = (-dt * 7.5).exp();
    let sign = if (dt * 105.0).sin() >= 0.0 { 1.0 } else { -1.0 };
    let ca_shift = (10.0 * glitch_env * sign).round() as i32;
    let row_stride = width * 4;

    if ca_shift > 0 && (ca_shift as usize) < width {
        let s = ca_shift as usize;
        for row in buf.chunks_exact_mut(row_stride) {
            row_buf.copy_from_slice(row);
            for x in s..width {
                row[x * 4] = row_buf[(x - s) * 4];
            }
            for x in 0..(width - s) {
                row[x * 4 + 2] = row_buf[(x + s) * 4 + 2];
            }
        }
    } else if ca_shift < 0 && ((-ca_shift) as usize) < width {
        let s = (-ca_shift) as usize;
        for row in buf.chunks_exact_mut(row_stride) {
            row_buf.copy_from_slice(row);
            for x in 0..(width - s) {
                row[x * 4] = row_buf[(x + s) * 4];
            }
            for x in s..width {
                row[x * 4 + 2] = row_buf[(x - s) * 4 + 2];
            }
        }
    }

    if glitch_env > 0.20 {
        let step = (dt * 50.0) as usize;
        let band_h = (height / 65).max(8);
        let dx = ((18.0 * glitch_env).round() as usize).max(4) % width;
        if dx > 0 {
            let byte_shift = dx * 4;
            for (b_idx, frac) in [0.38_f32, 0.56_f32].iter().enumerate() {
                let offset = ((step * 53 + b_idx * 97) % 140) as i32 - 70;
                let by0 = (((height as f32 * frac) as i32 + offset).max(0) as usize)
                    .min(height.saturating_sub(band_h));
                let by1 = (by0 + band_h).min(height);
                let roll_right = (step + b_idx) % 2 == 0;
                for y in by0..by1 {
                    let row = &mut buf[y * row_stride..(y + 1) * row_stride];
                    if roll_right {
                        row.rotate_right(byte_shift);
                    } else {
                        row.rotate_left(byte_shift);
                    }
                }
            }
        }
    }

    if dt < 0.14 {
        let boost = (18.0 * (1.0 - dt / 0.14)).round() as i32;
        if boost > 0 {
            let b = boost.min(255) as u8;
            for px in buf.chunks_exact_mut(4) {
                px[0] = px[0].saturating_add(b);
                px[1] = px[1].saturating_add(b);
                px[2] = px[2].saturating_add(b);
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
