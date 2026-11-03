#!/usr/bin/env python3
import json, os, subprocess, sys

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

# 2. Create Voyager .vvideo project across the full assembled reel source
proj = f"/tmp/proj_{reel_idx}.vvideo"
if os.path.exists(proj):
    os.remove(proj)

subprocess.check_call(["voyager-video", "new", proj, src_mkv])

# 3. Seed transcript schema & inject exact word-timed Moroccan Darija / target captions
subprocess.check_call([
    "voyager-video", "-C", proj, "media", "transcribe",
    "--lang", reel.get("lang", "ar"), "--model", "tiny", "--no-verbatim"
])
skel_path = f"/tmp/skel_{reel_idx}.json"
subprocess.check_call([
    "voyager-video", "-C", proj, "export", "transcript",
    "--format", "json", "-o", skel_path
])
skel = json.load(open(skel_path, encoding="utf-8"))
media_id = skel["media"]

mapped_words = []
for w in reel["words"]:
    ws, we = float(w["start"]), float(w["end"])
    for a, b, off in cut_offsets:
        if (a - 0.15) <= ws <= (b + 0.15):
            ns = max(0.0, round(off + (ws - a), 3))
            ne = min(round(total_dur - 0.02, 3), round(off + (we - a), 3))
            if ne > ns:
                mapped_words.append({
                    "start": ns,
                    "end": ne,
                    "text": w["text"],
                    "speaker": "speaker_0",
                    "confidence": 0.99,
                })
            break

skel["language"] = reel.get("lang", "ar")
skel["words"] = mapped_words
if skel.get("coverage"):
    skel["coverage"][0]["start"] = {"timescale": skel["duration"]["timescale"], "value": "0"}
    skel["coverage"][0]["start_seconds"] = 0.0
    skel["coverage"][0]["end"] = dict(skel["duration"])
    if "end_seconds" in skel["coverage"][0]:
        skel["coverage"][0]["end_seconds"] = round(total_dur, 3)

clean_tx = f"/tmp/clean_tx_{reel_idx}.json"
json.dump(skel, open(clean_tx, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
subprocess.check_call([
    "voyager-video", "-C", proj, "media", "import-transcript", media_id, clean_tx
])

# 4. Voyager AI Face-Tracked 9:16 Reframing + Broadcast Audio Polish (-14 LUFS) + Active-Word Captions
subprocess.check_call([
    "voyager-video", "-C", proj, "reframe", "--to", "9:16", "--detect", "faces"
])
subprocess.check_call([
    "voyager-video", "-C", proj, "audio", "polish", "--preset", "podcast", "--target", "-14"
])
subprocess.check_call([
    "voyager-video", "-C", proj, "caption", "set",
    "--style", "captions", "--style-format", "9:16", "--burn",
    "--highlight", "active-word",
    "--accent-color", reel.get("accent_color", "#FFD400"),
    "--style-param", f"font-size={reel.get('font_size', '64px')}",
    "--style-param", "font-weight=900",
    "--style-param", "placement=bottom-center",
    "--style-param", f"offset={reel.get('offset', '-30px,-335px')}",
    "--style-param", "text-shadow=0 4px 18px rgba(0,0,0,0.95)",
])

# 5. Export SRT + FCPXML + Render 1080x1920 H.264 MP4 + Contact sheet & Preview frame
out_mp4 = os.path.join(out_dir, f"{slug}.mp4")
out_srt = os.path.join(out_dir, f"{slug}.srt")
out_fcpxml = os.path.join(out_dir, f"{slug}.fcpxml")
out_preview = os.path.join(out_dir, f"{slug}_preview.png")
out_sheet = os.path.join(out_dir, f"{slug}_sheet.jpg")

subprocess.check_call(["voyager-video", "-C", proj, "export", "srt", "-o", out_srt])
subprocess.check_call(["voyager-video", "-C", proj, "export", "fcpxml", "-o", out_fcpxml])
subprocess.check_call(["voyager-video", "-C", proj, "render", "-o", out_mp4, "--quality", "high"])

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
