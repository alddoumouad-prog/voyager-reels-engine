#!/usr/bin/env python3
import hashlib, json, os, subprocess, sys

reel_idx = int(sys.argv[1])
spec_json = sys.argv[2]
video_mp4 = sys.argv[3]
audio_m4a = sys.argv[4]
out_dir = sys.argv[5]
os.makedirs(out_dir, exist_ok=True)

spec = json.load(open(spec_json, encoding="utf-8"))
reel = spec["reels"][reel_idx]
slug = reel["slug"]
cuts = reel["cuts"]

# 1. Extract each cut accurately to 30fps CFR + 48kHz PCM MKV and concat if multiple cuts
part_files = []
cut_offsets = []
running_offset = 0.0
for k, (a, b) in enumerate(cuts):
    dur = b - a
    cut_offsets.append((a, b, running_offset))
    running_offset += dur
    part_path = f"/tmp/part_{reel_idx}_{k}.mkv"
    subprocess.check_call([
        "ffmpeg", "-y",
        "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", video_mp4,
        "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", audio_m4a,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        part_path, "-loglevel", "error"
    ])
    part_files.append(part_path)

src_mkv = f"/tmp/src_{reel_idx}.mkv"
if len(part_files) == 1:
    os.replace(part_files[0], src_mkv)
else:
    concat_txt = f"/tmp/concat_{reel_idx}.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in part_files:
            f.write(f"file '{p}'\n")
    subprocess.check_call([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_txt,
        "-c", "copy", src_mkv, "-loglevel", "error"
    ])

total_dur = running_offset

# 2. Create Voyager .vvideo project across the assembled reel source
proj = f"/tmp/proj_{reel_idx}.vvideo"
if os.path.exists(proj):
    subprocess.check_call(["rm", "-rf", proj])

subprocess.check_call(["voyager-video", "new", proj, src_mkv])

# 3. Seed 2s transcript envelope & import exact word-timed Moroccan Darija / target captions
subprocess.check_call([
    "voyager-video", "-C", proj, "media", "transcribe",
    "--range", "0..2", "--model", "whisper-tiny.en"
])
skel_path = f"/tmp/skel_{reel_idx}.json"
subprocess.check_call([
    "voyager-video", "-C", proj, "export", "transcript",
    "--format", "json", "-o", skel_path
])
skel = json.load(open(skel_path, encoding="utf-8"))
media_id = skel["media"]

w_objs = []
for w in reel["words"]:
    ws, we = float(w["start"]), float(w["end"])
    for a, b, off in cut_offsets:
        if (a - 0.15) <= ws <= (b + 0.15):
            ns = max(0.0, round(off + (ws - a), 3))
            ne = min(round(total_dur - 0.03, 3), round(off + (we - a), 3))
            if ne > ns:
                sm, em = int(round(ns * 1000)), int(round(ne * 1000))
                if em <= sm:
                    em = sm + 80
                wid = f"w{len(w_objs):04d}"
                txt = w["text"].strip()
                h = hashlib.sha256(f"{wid}:{sm}:{txt}".encode("utf-8")).hexdigest()[:12]
                w_objs.append({
                    "conf": 0.99,
                    "id": wid,
                    "hash": h,
                    "kind": "word",
                    "start": {"timescale": 1000, "value": str(sm)},
                    "start_seconds": round(sm / 1000.0, 3),
                    "end": {"timescale": 1000, "value": str(em)},
                    "end_seconds": round(em / 1000.0, 3),
                    "text": txt,
                })
            break

# Build clause/sentence segments for natural Arabic/Darija line grouping
segments = []
cur_chunk = []
for w in w_objs:
    cur_chunk.append(w)
    if w["text"].endswith(("!", "؟", ".", "،", ":")):
        sid = f"s{len(segments):04d}"
        segments.append({
            "id": sid,
            "first": cur_chunk[0]["id"],
            "last": cur_chunk[-1]["id"],
            "paragraph": 0,
            "start": cur_chunk[0]["start"],
            "start_seconds": cur_chunk[0]["start_seconds"],
            "end": cur_chunk[-1]["end"],
            "end_seconds": cur_chunk[-1]["end_seconds"],
            "text": " ".join(x["text"] for x in cur_chunk),
        })
        cur_chunk = []
if cur_chunk:
    sid = f"s{len(segments):04d}"
    segments.append({
        "id": sid,
        "first": cur_chunk[0]["id"],
        "last": cur_chunk[-1]["id"],
        "paragraph": 0,
        "start": cur_chunk[0]["start"],
        "start_seconds": cur_chunk[0]["start_seconds"],
        "end": cur_chunk[-1]["end"],
        "end_seconds": cur_chunk[-1]["end_seconds"],
        "text": " ".join(x["text"] for x in cur_chunk),
    })

skel["language"] = reel.get("lang", "ar")
skel["words"] = w_objs
skel["segments"] = segments
skel["coverage"] = skel["coverage"][:1]
skel["coverage"][0]["start"] = {"timescale": 1, "value": "0"}
skel["coverage"][0]["start_seconds"] = 0.0
skel["coverage"][0]["end"] = dict(skel["duration"])
if "end_seconds" in skel["coverage"][0]:
    skel["coverage"][0]["end_seconds"] = round(total_dur, 3)
if w_objs:
    skel["speech"] = [{
        "start": w_objs[0]["start"],
        "start_seconds": w_objs[0]["start_seconds"],
        "end": w_objs[-1]["end"],
        "end_seconds": w_objs[-1]["end_seconds"],
    }]

clean_tx = f"/tmp/clean_tx_{reel_idx}.json"
json.dump(skel, open(clean_tx, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
subprocess.check_call([
    "voyager-video", "-C", proj, "import", "transcript", "--media", media_id, clean_tx
])

# 4. Voyager AI Face-Tracked 1080x1920 9:16 Reframing + DAW Dialogue Polish + Active-Word Captions
subprocess.check_call([
    "voyager-video", "-C", proj, "sequence", "reframe",
    "--aspect", "9:16", "--size", "1080x1920", "--in-place", "--subject", "face"
])
subprocess.check_call([
    "voyager-video", "-C", proj, "track", "clean", "a1",
    "--denoise", "dialogue-natural", "--eq", "--level"
])
subprocess.check_call([
    "voyager-video", "-C", proj, "caption", "add",
    "--lang", reel.get("lang", "ar"), "--rules", "social",
    "--max-chars", "28", "--max-lines", "2"
])
subprocess.check_call([
    "voyager-video", "-C", proj, "caption", "set",
    "--style", "captions", "--style-format", "9:16", "--burn",
    "--highlight", "active-word",
    "--accent-color", reel.get("accent_color", "#FFD400"),
    "--style-param", "font-family=Noto Sans Arabic",
    "--style-param", f"font-size={reel.get('font_size', '64px')}",
    "--style-param", "font-weight=900",
    "--style-param", "placement=bottom-center",
    "--style-param", f"offset={reel.get('offset', '0,-310px')}",
    "--style-param", "text-shadow=0 4px 18px rgba(0,0,0,0.95)",
])

# 5. Export SRT + FCPXML + Render 1080x1920 H.264 MP4 (-14 LUFS) + Contact sheet & Preview frame
out_mp4 = os.path.join(out_dir, f"{slug}.mp4")
out_srt = os.path.join(out_dir, f"{slug}.srt")
out_fcpxml = os.path.join(out_dir, f"{slug}.fcpxml")
out_preview = os.path.join(out_dir, f"{slug}_preview.png")
out_sheet = os.path.join(out_dir, f"{slug}_sheet.jpg")

subprocess.check_call(["voyager-video", "-C", proj, "export", "captions", "--format", "srt", "-o", out_srt])
subprocess.check_call(["voyager-video", "-C", proj, "export", "fcpxml", "-o", out_fcpxml])
subprocess.check_call([
    "voyager-video", "-C", proj, "render",
    "--preset", "social-9x16",
    "--loudness", "-14LUFS",
    "--true-peak", "-1dBTP",
    "--burn-captions",
    "-o", out_mp4
])

preview_t = min(max(1.5, float(reel.get("preview_sec", 4.0))), max(1.5, total_dur - 1.5))
subprocess.check_call([
    "ffmpeg", "-y", "-ss", f"{preview_t:.2f}", "-i", out_mp4,
    "-frames:v", "1", out_preview, "-loglevel", "error"
])
subprocess.check_call([
    "ffmpeg", "-y", "-i", out_mp4,
    "-vf", f"fps=4/{max(8.0, total_dur):.2f},scale=360:640,tile=4x1",
    "-frames:v", "1", out_sheet, "-loglevel", "error"
])
print("Successfully rendered:", out_mp4)
