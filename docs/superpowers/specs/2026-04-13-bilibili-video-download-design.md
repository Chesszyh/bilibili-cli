# Bilibili Video Download Design

Date: 2026-04-13
Status: Approved for planning

## Context

The project currently supports video metadata queries via `bili video` and audio extraction via `bili audio`, but it does not support downloading full video files. The existing audio path is optimized for ASR workflows rather than archival-quality media output:

- `bili video` only exposes metadata, subtitles, AI summary, comments, and related videos.
- `bili audio` downloads only the audio stream and optionally splits it into 16 kHz mono WAV segments.
- The current audio stream selection explicitly caps quality at 64 Kbps and disables Hi-Res and Dolby audio.

Users now want a video download capability with "lossless" behavior. In this design, "lossless" means downloading the highest-quality streams returned by Bilibili and remuxing them without transcoding. It does not mean recovering the uploader's original master file.

## Goals

- Add a first-class CLI command for downloading Bilibili videos.
- Preserve source stream quality by avoiding any audio or video transcoding.
- Support both progressive streams and DASH-separated video/audio streams.
- Keep the UX consistent with the existing `bili audio` command.
- Produce predictable output paths and actionable error messages.

## Non-Goals

- No transcoding, scaling, re-encoding, watermark removal, or format conversion beyond container remux.
- No batch download, playlist download, danmaku burn-in, subtitle muxing, or automatic metadata tagging in the first version.
- No promise to bypass Bilibili membership, region, or DRM restrictions.
- No guarantee that the output matches the uploader's original upload bit-for-bit.

## User Experience

### Command shape

Add a new command:

```bash
bili download <BV|URL> [options]
```

Initial options:

- `--output`, `-o`: output file path or output directory
- `--page`: page index for multi-part videos, default `1`
- `--container`: `mkv|mp4`, default `mkv`
- `--keep-raw`: keep downloaded raw stream files after merge

The MVP implementation should support exactly `--output`, `--page`, `--container`, and `--keep-raw`.

### Output behavior

- If Bilibili returns a progressive FLV or MP4 stream, download that single stream directly and preserve the native extension unless the user explicitly provides a filename.
- If Bilibili returns separate DASH video and audio streams, download both raw streams, then remux them into the selected container with `ffmpeg -c copy`.
- Default container for DASH output is `mkv` because it is the safest no-transcode target across codec combinations.
- If `--output` points to a directory, generate a sanitized filename from the video title.
- If `--output` points to a file, respect it exactly.

### Definition of success

The command succeeds when:

- the best available stream set for the requested page is resolved,
- all required media assets are downloaded successfully,
- remux completes without transcoding when a merge is required,
- the final output file exists and is non-empty,
- temporary files are removed unless `--keep-raw` is set.

## Technical Design

### Command layer

Create a new command module, likely `bili_cli/commands/download.py`, and register it in `bili_cli/cli.py`.

The command flow:

1. Parse BV ID or URL.
2. Load optional credential, since higher-quality or member-only streams may require authentication.
3. Fetch video info for title, duration, and validation.
4. Resolve download streams for the selected page.
5. Decide whether this is a progressive download or DASH download.
6. Download required assets.
7. If DASH, call `ffmpeg` to remux with stream copy.
8. Print final output path and size summary.

This command should use rich terminal output only. Structured `--json` / `--yaml` output is not part of the MVP because it would require a new schema for download job results and is not essential to user value.

### Client layer

Extend `bili_cli/client.py` with reusable download primitives:

- `get_video_download_streams(...)`
  - Calls `video.Video(...).get_download_url(page_index=page - 1)`
  - Uses `VideoDownloadURLDataDetecter`
  - Returns a normalized object describing either:
    - a progressive single-file stream, or
    - separate best video stream and best audio stream
- `download_stream(url, output_path)`
  - Reuses the current retry and chunked-write behavior from `download_audio`
  - Generalizes naming and errors so it works for both video and audio assets
- `merge_streams_ffmpeg(video_path, audio_path, output_path, container)`
  - Invokes `ffmpeg` with stream copy only
  - Fails hard if `ffmpeg` is missing or the merge process exits non-zero

The current `download_audio()` implementation should be refactored into the generic `download_stream()` helper rather than duplicated.

### Stream selection policy

Use Bilibili's best available streams from `VideoDownloadURLDataDetecter.detect_best_streams()` with these rules:

- For video, prefer the detector's highest-ranked stream using its built-in ordering.
- For audio, prefer the detector's highest-ranked stream using its built-in ordering.
- Do not artificially cap audio quality to 64 Kbps.
- Do not disable Hi-Res or Dolby unless the user adds future opt-out flags.

This is the core change needed to align implementation with the "lossless download" expectation.

### Merge strategy

Use external `ffmpeg` rather than PyAV for DASH remux.

Reasons:

- `ffmpeg -c copy` is the most reliable way to remux heterogeneous Bilibili streams without re-encoding.
- The environment often already has `ffmpeg` installed, while PyAV is currently only an optional dependency for audio segmentation.
- The project does not need frame-level media processing for this feature.

Expected command shape:

```bash
ffmpeg -y -i <video> -i <audio> -c copy <output>
```

If container-specific flags become necessary during implementation, they should still preserve stream copy and avoid any encoder selection.

### File layout and cleanup

For DASH downloads:

- Create a temporary working directory under the target output directory.
- Save raw files with stable internal names such as `_video.m4s` and `_audio.m4s`.
- Write the merged artifact to the final path only after a successful remux.
- Delete raw files and temp directories on success unless `--keep-raw` is set.

For progressive downloads:

- Download directly to the final output path through a temporary `.part` file and rename on success.

Atomic rename behavior is preferred where practical to avoid leaving partial final files on interruption.

## Error Handling

The command should fail with explicit user-facing messages for:

- invalid BV or URL input
- page index outside available range
- no downloadable stream returned
- member-only or restricted streams
- HTTP/network failures during media download
- missing `ffmpeg`
- `ffmpeg` remux failure
- final output path conflicts that cannot be resolved safely

Errors should remain consistent with the existing command style: concise Chinese messages for terminal UX and internal exception types for tests.

## Testing Strategy

Add unit tests covering:

- progressive stream resolution
- DASH stream resolution
- no-stream error path
- generic stream download writes bytes correctly
- command success for progressive downloads
- command success for DASH download + merge
- cleanup behavior with and without `--keep-raw`
- missing `ffmpeg` error path
- merge failure error path
- invalid page / invalid BV handling

Tests should continue to mock network, SDK detector behavior, and subprocess execution. No real Bilibili download or real `ffmpeg` invocation is required in unit tests.

## Documentation Changes

Update:

- `README.md` feature list
- install section to mention `ffmpeg` requirement for video download
- usage examples for `bili download`
- troubleshooting section for missing `ffmpeg` and restricted streams
- `SKILL.md` so agents know when to use `bili download` instead of `bili audio`

## Rollout Order

1. Add client-side stream resolution and generic download helpers.
2. Add `ffmpeg` merge helper.
3. Add `bili download` command and wire it into the CLI.
4. Add tests for client helpers and command behavior.
5. Update README and `SKILL.md`.

## Open Decisions Resolved

- "Lossless" is defined as no-transcode download and remux of Bilibili-provided streams.
- Default DASH container is `mkv`, not `mp4`, to avoid codec/container incompatibilities.
- `ffmpeg` is the merge backend for MVP.
- Structured JSON/YAML output is out of scope for the first version.
