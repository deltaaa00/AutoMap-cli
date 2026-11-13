# Changelog

## Unreleased

### Added

- Title font selection and a 48–144 px size control in Video Studio, with DIN
  Condensed at 100 px as the default and a reset button.
- A title preview endpoint that uses the same font rendering, wrapping, fitting,
  and shadows as the final video.
- Tests for AI parsing, filling configuration, map generation, CLI commands,
  Flask APIs, service failures, audio handling, file cleanup, and download paths.
- Real FFmpeg rendering tests, offline browser interaction tests, and dependency
  consistency checks.
- Test setup documentation and GitHub Actions coverage reporting, with a 90%
  minimum including branch coverage.

### Changed

- Use a responsive 9:16 preview and render videos at 1080×1920.
- Center the title and map as a group, preserve background proportions with a
  centered crop, and improve readability with a dark overlay and text shadows.
- Scale preview composition to match the output layout, debounce title updates,
  cancel stale requests, release replaced blob URLs, and pause background playback
  when the preview or browser tab is hidden.
- Include bundled fonts in the video worker Docker image.

### Fixed

- Position the finished video player over the full preview frame.
- Apply the correct horizontal offset to map labels with four or more characters.
- Reject AI responses whose JSON root is not an object, including fenced responses.
- Align runtime dependencies between `pyproject.toml` and `requirements.txt`,
  resolve the Pillow version conflict, and declare MoviePy in package dependencies.
