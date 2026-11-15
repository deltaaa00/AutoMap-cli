use pyo3::prelude::*;
use std::io::{Read, Write};
use std::process::{Command, Stdio};

/// Describes a single dynamic overlay layer passed from Python as a tuple:
/// `(rgba_bytes, x, y, width, height, start_sec, fade_in_sec)`.
#[derive(FromPyObject)]
struct DynamicLayer {
    rgba: Vec<u8>,
    x: i32,
    y: i32,
    width: usize,
    height: usize,
    start_sec: f32,
    fade_in_sec: f32,
}

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
        let total_frames = (duration * fps as f32).round().max(1.0) as u32;
        let dur_str = format!("{duration:.3}");

        // 1. Spawn ffmpeg decoder: reads the prepared background MP4 and outputs raw RGBA frames.
        let mut decoder = Command::new("ffmpeg")
            .args([
                "-y",
                "-i",
                bg_video_path,
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
        let fps_str = fps.to_string();
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

        let mut encoder = Command::new("ffmpeg")
            .args(&enc_args)
            .stdin(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("ffmpeg encoder spawn failed: {e}")))?;

        let mut enc_in = encoder
            .stdin
            .take()
            .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("missing encoder stdin"))?;

        // 3. Single reusable frame buffer (~8.3 MB for 1080x1920 RGBA) to avoid per-frame allocations.
        let mut frame_buf = vec![0u8; expected_len];

        for frame_idx in 0..total_frames {
            if dec_out.read_exact(&mut frame_buf).is_err() {
                break;
            }

            let t = frame_idx as f32 / fps as f32;

            // Layer 1: flattened static foreground (map, title, watermark).
            blend_full_in_place(&mut frame_buf, static_rgba, 1.0);

            // Layer 2: dynamic overlays (subscribe badge, stars) with optional fade-in.
            for layer in &dynamic_layers {
                if t < layer.start_sec {
                    continue;
                }
                let opacity = if layer.fade_in_sec > 0.0 {
                    ((t - layer.start_sec) / layer.fade_in_sec).clamp(0.0, 1.0)
                } else {
                    1.0
                };
                blend_rect_in_place(
                    &mut frame_buf,
                    width,
                    height,
                    &layer.rgba,
                    layer.x,
                    layer.y,
                    layer.width,
                    layer.height,
                    opacity,
                );
            }

            // Layer 3: intro fade-in veil (darkens RGB channels linearly at the start).
            if veil_duration > 0.0 && t < veil_duration {
                let veil_alpha = (0.88 * (1.0 - (t / veil_duration))).clamp(0.0, 1.0);
                let brightness = 1.0 - veil_alpha;
                for px in frame_buf.chunks_exact_mut(4) {
                    px[0] = (px[0] as f32 * brightness) as u8;
                    px[1] = (px[1] as f32 * brightness) as u8;
                    px[2] = (px[2] as f32 * brightness) as u8;
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

/// Alpha-blends a full-canvas RGBA overlay onto `base` in place.
fn blend_full_in_place(base: &mut [u8], overlay: &[u8], opacity: f32) {
    if opacity <= 0.0 {
        return;
    }
    for (bg, fg) in base.chunks_exact_mut(4).zip(overlay.chunks_exact(4)) {
        blend_pixel(bg, fg, opacity);
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
    opacity: f32,
) {
    if opacity <= 0.0 || ov_w == 0 || ov_h == 0 {
        return;
    }
    let start_y = y.max(0) as usize;
    let end_y = (y + ov_h as i32).min(canvas_h as i32).max(0) as usize;
    let start_x = x.max(0) as usize;
    let end_x = (x + ov_w as i32).min(canvas_w as i32).max(0) as usize;

    for cy in start_y..end_y {
        let oy = (cy as i32 - y) as usize;
        for cx in start_x..end_x {
            let ox = (cx as i32 - x) as usize;
            let bg_idx = (cy * canvas_w + cx) * 4;
            let fg_idx = (oy * ov_w + ox) * 4;
            if fg_idx + 4 <= overlay.len() && bg_idx + 4 <= base.len() {
                blend_pixel(&mut base[bg_idx..bg_idx + 4], &overlay[fg_idx..fg_idx + 4], opacity);
            }
        }
    }
}

#[inline(always)]
fn blend_pixel(bg: &mut [u8], fg: &[u8], opacity: f32) {
    let fg_a = (fg[3] as f32 / 255.0) * opacity;
    if fg_a <= 0.0 {
        return;
    }
    if fg_a >= 1.0 {
        bg.copy_from_slice(fg);
        return;
    }
    let inv_a = 1.0 - fg_a;
    bg[0] = (fg[0] as f32 * fg_a + bg[0] as f32 * inv_a) as u8;
    bg[1] = (fg[1] as f32 * fg_a + bg[1] as f32 * inv_a) as u8;
    bg[2] = (fg[2] as f32 * fg_a + bg[2] as f32 * inv_a) as u8;
}

#[pymodule]
fn automap_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_video_rs, m)?)?;
    Ok(())
}
