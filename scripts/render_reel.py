#!/usr/bin/env python3
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

reel_idx = int(sys.argv[1])
spec_path = sys.argv[2]
video_in = sys.argv[3]
audio_in = sys.argv[4]
out_dir = Path(sys.argv[5])
out_dir.mkdir(parents=True, exist_ok=True)

spec_all = json.load(open(spec_path, encoding="utf-8"))
reels = spec_all["reels"]
if reel_idx >= len(reels):
    print(f"Reel index {reel_idx} not in spec (count={len(reels)}), skipping.")
    sys.exit(0)

reel = reels[reel_idx]
slug = reel["slug"]
cuts = reel["cuts"]  # list of [start_sec, end_sec] in original video timestamps
words_in = reel["words"]  # list of {"start": sec_in_original, "end": sec_in_original, "text": "..."}

min_t = max(0.0, min(c[0] for c in cuts) - 2.0)
max_t = max(c[1] for c in cuts) + 2.0
win_dur = max_t - min_t

# 1. Extract clean window MKV with H.264 + AAC so local timestamps start at 0 = min_t
clip_mkv = f"/tmp/src_{reel_idx}.mkv"
subprocess.check_call([
    "ffmpeg", "-y",
    "-ss", f"{min_t:.3f}", "-t", f"{win_dur:.3f}", "-i", video_in,
    "-ss", f"{min_t:.3f}", "-t", f"{win_dur:.3f}", "-i", audio_in,
    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
    clip_mkv, "-loglevel", "error"
])

probe_dur = float(subprocess.check_output([
    "ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", clip_mkv
]).decode().strip())

# 2. Build Voyager .vvideo project with exact subclip cuts relative to min_t
proj = f"/tmp/proj_{reel_idx}.vvideo"
if os.path.exists(proj):
    shutil.rmtree(proj)

cut_args = [f"{clip_mkv}@{round(c[0]-min_t, 3)}..{round(c[1]-min_t, 3)}" for c in cuts]
subprocess.check_call(["voyager-video", "new", proj, *cut_args])

# 3. Run 1-second fast probe transcription to get exact video-transcript/1 identity/stream header
media_id = f"src_{reel_idx}"
subprocess.check_call([
    "voyager-video", "-C", proj, "media", "transcribe",
    "--range", "0..1.2", "--lang", "es", media_id
])
skel_path = f"/tmp/skel_{reel_idx}.json"
subprocess.check_call([
    "voyager-video", "-C", proj, "export", "transcript",
    "--format", "json", "--media", media_id, "-o", skel_path
])
skel = json.load(open(skel_path, encoding="utf-8"))

# 4. Build clean Moroccan Darija word-timed transcript shifted by min_t
words_json = []
for idx, w in enumerate(words_in):
    ts = round(max(0.0, float(w["start"]) - min_t), 3)
    te = round(min(probe_dur - 0.02, float(w["end"]) - min_t), 3)
    if te <= ts:
        te = round(min(probe_dur - 0.01, ts + 0.14), 3)
    txt = str(w["text"]).strip()
    if not txt:
        continue
    wid = f"w{idx:04d}"
    h = hashlib.sha256(f"{wid}:{ts}:{txt}".encode("utf-8")).hexdigest()[:12]
    words_json.append({
        "conf": 0.99,
        "end": {"timescale": 1000, "value": str(int(round(te * 1000)))},
        "end_seconds": te,
        "hash": h,
        "id": wid,
        "kind": "word",
        "start": {"timescale": 1000, "value": str(int(round(ts * 1000)))},
        "start_seconds": ts,
        "text": txt,
    })

segments = []
cur = []
for w in words_json:
    if cur and ((w["start_seconds"] - cur[-1]["end_seconds"] > 0.55) or len(cur) >= 10 or cur[-1]["text"][-1:] in ".؟?!"):
        s0, s1 = cur[0], cur[-1]
        segments.append({
            "id": f"s{len(segments):04d}",
            "paragraph": 0,
            "first": s0["id"],
            "last": s1["id"],
            "start": s0["start"],
            "start_seconds": s0["start_seconds"],
            "end": s1["end"],
            "end_seconds": s1["end_seconds"],
            "text": " ".join(x["text"] for x in cur),
        })
        cur = []
    cur.append(w)
if cur:
    s0, s1 = cur[0], cur[-1]
    segments.append({
        "id": f"s{len(segments):04d}",
        "paragraph": 0,
        "first": s0["id"],
        "last": s1["id"],
        "start": s0["start"],
        "start_seconds": s0["start_seconds"],
        "end": s1["end"],
        "end_seconds": s1["end_seconds"],
        "text": " ".join(x["text"] for x in cur),
    })

merged_tx = dict(skel)
merged_tx["language"] = reel.get("lang", "ar")
merged_tx["corrections"] = []
merged_tx["segments"] = segments
merged_tx["words"] = words_json
if "speakers" in merged_tx:
    merged_tx["speakers"] = []

# Extend coverage to full clip duration
if merged_tx.get("coverage"):
    merged_tx["coverage"][0]["end"] = merged_tx["duration"]

clean_tx_path = f"/tmp/clean_tx_{reel_idx}.json"
json.dump(merged_tx, open(clean_tx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

subprocess.check_call([
    "voyager-video", "-C", proj, "import", "transcript",
    clean_tx_path, "--format", "json", "--media", media_id
])

# 5. Smart AI YuNet face-tracking 9:16 reframe
subprocess.check_call([
    "voyager-video", "-C", proj, "sequence", "reframe",
    "--aspect", "9:16", "--size", "1080x1920", "--in-place",
    "--subject", "face", "--smooth", "0.8s"
])

# 6. Social captions + bold active-word highlight
lang_code = reel.get("lang", "ar")
subprocess.check_call([
    "voyager-video", "-C", proj, "caption", "add",
    "--lang", lang_code, "--rules", "social", "--replace", "--fresh"
])

style_cmd = [
    "voyager-video", "-C", proj, "caption", "set",
    "--style", "captions", "--style-format", "9:16", "--burn",
    "--highlight", "active-word",
    "--accent-color", reel.get("accent_color", "#FFD400"),
    "--style-param", f"font-size={reel.get('font_size', '66px')}",
    "--style-param", "font-weight=900",
    "--style-param", "placement=bottom-center",
    "--style-param", f"offset={reel.get('offset', '-30px,-335px')}",
    "--style-param", "text-shadow=0 4px 18px rgba(0,0,0,0.95)",
]
if reel.get("font_family"):
    style_cmd.extend(["--style-param", f"font-family={reel['font_family']}"])
subprocess.check_call(style_cmd)

# Export SRT & FCPXML
srt_out = out_dir / f"{slug}.srt"
fcpxml_out = out_dir / f"{slug}.fcpxml"
subprocess.check_call(["voyager-video", "-C", proj, "export", "captions", "--format", "srt", "-o", str(srt_out)])
subprocess.check_call(["voyager-video", "-C", proj, "export", "fcpxml", "-o", str(fcpxml_out)])

# Render 1 full-res PNG still to verify Arabic RTL shaping & framing
png_out = out_dir / f"{slug}_preview.png"
preview_t = reel.get("preview_sec", 3.0)
subprocess.check_call([
    "voyager-video", "-C", proj, "render",
    "--format", "png", "--range", f"{preview_t}..+1f", "--burn-captions", "-o", str(png_out)
])

# Render full MP4 via Voyager social-9x16 preset, then optimize H.264 High Profile CRF 21 + +faststart
raw_mp4 = f"/tmp/{slug}_raw.mp4"
final_mp4 = out_dir / f"{slug}.mp4"
subprocess.check_call([
    "voyager-video", "-C", proj, "render",
    "--preset", "social-9x16", "--burn-captions", "--loudness", "-15LUFS",
    "-o", raw_mp4
])
subprocess.check_call([
    "ffmpeg", "-y", "-i", raw_mp4,
    "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p",
    "-c:a", "copy", "-movflags", "+faststart", str(final_mp4), "-loglevel", "error"
])

# Generate 8-frame contact sheet
sheet_out = out_dir / f"{slug}_sheet.jpg"
subprocess.check_call([
    "ffmpeg", "-y", "-i", str(final_mp4),
    "-vf", "fps=1/6,scale=270:480,tile=4x2", "-frames:v", "1",
    str(sheet_out), "-loglevel", "error"
])
print(f"Completed Reel {reel_idx}: {final_mp4} ({final_mp4.stat().st_size // 1024} KB)")
