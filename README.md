# Album Art Metadata Enricher

Apply ordered metadata (including per-track cover art) to a batch of audio or video files using `ffmpeg`. The scripts in this repository pair JSON entries to media files in a predictable order, copy streams without re-encoding, and attach artwork where available.

## Requirements

- Python 3.10 or newer (uses standard library only)
- `ffmpeg`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`
  - Windows: install the prebuilt binaries and add `ffmpeg` to `PATH`

## Repository Layout

- `apply_meta.py` — core driver that reads JSON and invokes `ffmpeg`
- `enrich_metadata.sh` — convenience wrapper for a common workflow
- `album.example.json` — sample metadata array
- `songs/` — place source media files here (not tracked)
- `art/` — place cover images referenced by metadata
- `output/` — destination folder for tagged copies

## Preparing Metadata

1. Adjust `album.json` (or your chosen filename) to include desired metadata.
2. Ensure the JSON root is an array; each element corresponds to one media file.
3. Supported keys are passed directly to `ffmpeg` as `-metadata` values (for example: `title`, `artist`, `album`, `track`).
4. Optional `image` entries point to artwork files. Use paths relative to the JSON file or absolute paths.

```json
[
  {
    "title": "Sample Track",
    "artist": "Example Artist",
    "album": "Demo EP",
    "track": "1",
    "date": "2025",
    "image": "./art/01.jpeg"
  }
]
```

Tips:
- Objects are matched to media files in order. Ensure the JSON array and file ordering align.
- Leave out `image` or set it to `null` when you want a track to inherit the default cover or have none.

## Running the Workflow

1. Populate `songs/` with the source files. Files are sorted alphabetically (case-insensitive).
2. Populate `art/` with images referenced by the metadata.
3. Adjust `album.json` (or your chosen metadata file) to match the track order and artwork.
4. Run either the Python entry point or the helper script:

   ```bash
   python3 apply_meta.py album.json --dir ./songs --outdir ./output --suffix ""
   ```

   or

   ```bash
   ./enrich_metadata.sh
   ```

   The wrapper script defaults to `album.json`, the `songs/` directory, and writes results into `output/`.

5. Tagging output appears in `output/`. Original files remain untouched.

## Useful Options

`apply_meta.py` exposes additional arguments:

- `--files <paths...>` — specify an explicit ordered list instead of scanning a directory.
- `--suffix _tagged` — append a suffix before the extension when writing output files.
- `--cover path/to/default.jpg` — fallback artwork when a JSON entry omits `image`.
- `--dry-run` — print the generated `ffmpeg` commands without executing them.
- `-y/--yes` — overwrite outputs if they already exist.

## Validation Checklist

- Every path referenced in the JSON (media, artwork) exists before running the script.
- `ffmpeg` is callable from your shell (`ffmpeg -version`).
- Output files open with updated metadata and artwork in your media player of choice.

## Troubleshooting

- **Mismatched counts** — The script warns when the number of files differs from the number of metadata entries and only processes the shortest pairing.
- **Missing artwork** — Verify the `image` path resolves relative to the JSON file or provide an absolute path. Use `--cover` for a shared fallback image.
- **Permission denied** — Ensure the script files are executable (`chmod +x enrich_metadata.sh`) and that you have write access to the `output/` directory.
