#!/usr/bin/env python3
import argparse
import json
import re
import shlex
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

def is_media_file(p: Path) -> bool:
    exts = {".mp3", ".m4a", ".mp4", ".mov", ".mkv", ".flac", ".wav", ".ogg", ".opus", ".aac", ".webm"}
    return p.suffix.lower() in exts

def natural_sort_key(p: Path):
    return [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', p.name)]

def sorted_media_files_from_dir(d: Path):
    files = [p for p in d.iterdir() if p.is_file() and is_media_file(p)]
    return sorted(files, key=natural_sort_key)

def extract_cover_from_id3(inp: Path) -> Path | None:
    """Extract cover art directly from MP3 ID3v2 APIC frame, bypassing ffmpeg codec detection."""
    try:
        with open(inp, 'rb') as f:
            header = f.read(10)
            if len(header) < 10 or header[:3] != b'ID3':
                return None
            major = header[3]
            size = ((header[6] & 0x7f) << 21) | ((header[7] & 0x7f) << 14) | \
                   ((header[8] & 0x7f) << 7) | (header[9] & 0x7f)
            tag_data = f.read(size)
    except OSError:
        return None

    pos = 0
    while pos < len(tag_data) - 10:
        fid = tag_data[pos:pos+4]
        if fid[0] == 0:
            break
        if major == 4:  # ID3v2.4 syncsafe frame size
            fs = ((tag_data[pos+4] & 0x7f) << 21) | ((tag_data[pos+5] & 0x7f) << 14) | \
                 ((tag_data[pos+6] & 0x7f) << 7) | (tag_data[pos+7] & 0x7f)
        else:  # ID3v2.3 big-endian frame size
            fs = int.from_bytes(tag_data[pos+4:pos+8], 'big')
        frame_data = tag_data[pos+10:pos+10+fs]

        if fid == b'APIC' and len(frame_data) > 4:
            enc = frame_data[0]
            try:
                null1 = frame_data.index(0, 1)
            except ValueError:
                pos += 10 + fs
                continue
            # Skip: mime type, picture type byte, then null-terminated description
            desc_start = null1 + 2
            if enc in (0, 3):  # Latin-1 or UTF-8
                try:
                    null2 = frame_data.index(0, desc_start)
                except ValueError:
                    null2 = desc_start
                img_data = frame_data[null2+1:]
            elif enc in (1, 2):  # UTF-16
                j = desc_start
                while j < len(frame_data) - 1:
                    if frame_data[j] == 0 and frame_data[j+1] == 0:
                        break
                    j += 1
                img_data = frame_data[j+2:]
            else:
                img_data = frame_data[desc_start:]

            if len(img_data) < 4:
                pos += 10 + fs
                continue

            # Detect actual format from magic bytes, not the (possibly wrong) mime type
            ext = ".png" if img_data[:4] == b'\x89PNG' else ".jpg"
            tmp = Path(tempfile.mktemp(suffix=ext))
            tmp.write_bytes(img_data)
            return tmp

        pos += 10 + fs
    return None


def build_ffmpeg_cmd(inp: Path, outp: Path, meta: dict, cover: Path | None, yes: bool):
    cmd = ["ffmpeg", "-hide_banner"]
    cmd += ["-y"] if yes else ["-n"]

    cmd += ["-i", str(inp)]

    have_cover = cover is not None
    if have_cover:
        cmd += ["-i", str(cover)]
        # Take only audio from input, artwork from cover file
        cmd += ["-map", "0:a", "-map", "1"]
        cmd += ["-c", "copy"]
        # Mark the artwork stream as attached picture
        cmd += ["-disposition:v:0", "attached_pic"]
    else:
        cmd += ["-map", "0:a"]
        cmd += ["-c", "copy"]

    # Keep existing metadata, only override fields specified below
    cmd += ["-map_metadata", "0"]

    # Apply metadata keys; skip image key and empty values
    for k, v in meta.items():
        if k == "image":
            continue
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        s = str(v).strip()
        if not s:
            continue
        cmd += ["-metadata", f"{k}={s}"]

    # MP4/M4A helpful flag (ignored by other muxers)
    if outp.suffix.lower() in {".m4a", ".mp4", ".mov"}:
        cmd += ["-movflags", "use_metadata_tags"]

    cmd += [str(outp)]
    return cmd

def resolve_cover_for_entry(meta: dict, json_base: Path, global_cover: Path | None) -> Path | None:
    """
    If meta has 'image', use it (resolved relative to the JSON file directory).
    Otherwise use global_cover if provided.
    """
    img = meta.get("image")
    if img is None or str(img).strip() == "":
        return global_cover

    p = Path(str(img))
    if not p.is_absolute():
        p = (json_base / p).resolve()

    if not p.exists():
        raise FileNotFoundError(f"Artwork image not found: {p}")
    return p

def main():
    ap = argparse.ArgumentParser(
        description="Apply ordered JSON-array metadata entries to ordered media files using ffmpeg (supports per-item artwork via 'image')."
    )
    ap.add_argument("json_file", help="Path to JSON file containing an array of metadata objects.")
    ap.add_argument("--dir", help="Directory containing media files to process (sorted by filename).")
    ap.add_argument("--files", nargs="*", help="Explicit list of input files (keeps given order).")
    ap.add_argument("--outdir", required=True, help="Output directory.")
    ap.add_argument("--suffix", default="", help="Optional suffix before extension, e.g. '_tagged'.")
    ap.add_argument("--cover", help="Optional default cover image used when an entry has no 'image'.")
    ap.add_argument("--dry-run", action="store_true", help="Print ffmpeg commands but do not run them.")
    ap.add_argument("-y", "--yes", action="store_true", help="Overwrite outputs if they exist.")
    args = ap.parse_args()

    json_path = Path(args.json_file).resolve()
    json_base = json_path.parent
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    global_cover = Path(args.cover).resolve() if args.cover else None
    if global_cover and not global_cover.exists():
        raise SystemExit(f"Global cover not found: {global_cover}")

    # Load JSON array
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("JSON root must be an array (list) of metadata objects.")

    # Inputs in order
    if args.files:
        inputs = [Path(x).resolve() for x in args.files]
    elif args.dir:
        inputs = sorted_media_files_from_dir(Path(args.dir).resolve())
    else:
        raise SystemExit("Provide either --dir or --files.")

    inputs = [p for p in inputs if p.exists() and p.is_file()]
    if not inputs:
        raise SystemExit("No input files found.")

    n = min(len(inputs), len(data))
    if len(inputs) != len(data):
        print(f"WARNING: files={len(inputs)} metadata_entries={len(data)}; applying first {n} pairs in order.")

    for i in range(n):
        inp = inputs[i]
        meta = data[i]
        if not isinstance(meta, dict):
            raise SystemExit(f"Metadata entry at index {i} is not an object/dict.")

        # Determine cover for this entry
        try:
            cover = resolve_cover_for_entry(meta, json_base, global_cover)
        except FileNotFoundError as e:
            raise SystemExit(str(e))

        # If no explicit cover, try to preserve existing embedded art
        temp_cover = None
        if cover is None and inp.suffix.lower() == ".mp3":
            temp_cover = extract_cover_from_id3(inp)
            cover = temp_cover

        out_name = inp.stem + args.suffix + inp.suffix
        outp = outdir / out_name

        cmd = build_ffmpeg_cmd(inp, outp, meta, cover, args.yes)

        if args.dry_run:
            print(" ".join(shlex.quote(x) for x in cmd))
            if temp_cover:
                temp_cover.unlink(missing_ok=True)
            continue

        art_label = f" (art: {cover.name})" if cover else ""
        if temp_cover:
            art_label = " (art: existing)"
        print(f"[{i+1}/{n}] {inp.name} -> {outp.name}{art_label}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if temp_cover:
            temp_cover.unlink(missing_ok=True)
        if res.returncode != 0:
            print(res.stderr)
            raise SystemExit(f"ffmpeg failed on: {inp}")

    print("Done.")

if __name__ == "__main__":
    main()
