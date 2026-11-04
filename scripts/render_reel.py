#!/usr/bin/env python3
import hashlib, json, os, subprocess, sys

reel_idx = int(sys.argv[1])
spec_json = sys.argv[2]
video_file = sys.argv[3]
audio_file = sys.argv[4]
out_dir = sys.argv[5]
os.makedirs(out_dir, exist_ok=True)

spec = json.load(open(spec_json, encoding="utf-8"))
if reel_idx >= len(spec["reels"]):
    print(f"Reel index {reel_idx} not in spec ({len(spec['reels'])} reels); skipping cleanly.")
    sys.exit(0)
reel = spec["reels"][reel_idx]
slug = reel["slug"]
cuts = reel["cuts"]
burn_captions = bool(reel.get("burn_captions", True))
fps = int(reel.get("fps", 30))

# Determine whether any cut uses pre-stacked 1080x1920 layout (e.g. split_rl)
has_split = any((len(c) >= 3 and c[2] == "split_rl") for c in cuts)

part_files = []
cut_offsets = []
running_offset = 0.0

for k, c in enumerate(cuts):
    a, b = float(c[0]), float(c[1])
    mode = c[2] if len(c) >= 3 else "face"
    dur = b - a
    cut_offsets.append((a, b, running_offset))
    running_offset += dur
    part_path = f"/tmp/part_{reel_idx}_{k}.mkv"

    if mode == "split_rl":
        # Top (1080x1000): Right-half 960x1040 card preserved 100% uncropped over seamless blurred slate backdrop
        # Bottom (1080x920): Left-half 960x818 studio facecam scaled to 1080x920
        vf = (
            "[0:v]split=3[r_bg][r_fg][l_cam];"
            "[r_bg]crop=w=iw/2:h=ih*1040/1080:x=iw/2:y=0,scale=1080:1000,setsar=1,gblur=sigma=28[bg];"
            "[r_fg]crop=w=iw/2:h=ih*1040/1080:x=iw/2:y=0,scale=924:1000,setsar=1[fg];"
            "[bg][fg]overlay=(W-w)/2:0,drawbox=x=0:y=996:w=1080:h=4:color=0x0f172a@0.95:t=fill[top];"
            "[l_cam]crop=w=iw/2:h=ih*818/1080:x=0:y=ih*24/1080,scale=1080:920,setsar=1[bot];"
            "[top][bot]vstack=inputs=2,setsar=1[vout]"
        )
        subprocess.check_call([
            "ffmpeg", "-y",
            "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", video_file,
            "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", audio_file,
            "-filter_complex", vf,
            "-map", "[vout]", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "15", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-af", f"afade=t=in:st=0:d=0.018,afade=t=out:st={max(0.02, dur-0.018):.3f}:d=0.018", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            part_path, "-loglevel", "error"
        ])
    elif has_split and mode == "face":
        # Subclip inside a mixed split+face reel: reframe this subclip to 1080x1920 via Voyager YuNet face tracking!
        raw_sub = f"/tmp/raw_sub_{reel_idx}_{k}.mkv"
        sub_proj = f"/tmp/sub_proj_{reel_idx}_{k}.vvideo"
        subprocess.check_call([
            "ffmpeg", "-y",
            "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", video_file,
            "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", audio_file,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "15", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-af", f"afade=t=in:st=0:d=0.018,afade=t=out:st={max(0.02, dur-0.018):.3f}:d=0.018", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            raw_sub, "-loglevel", "error"
        ])
        subprocess.check_call(["rm", "-rf", sub_proj])
        subprocess.check_call(["voyager-video", "new", sub_proj, raw_sub])
        subprocess.check_call([
            "voyager-video", "-C", sub_proj, "sequence", "reframe",
            "--aspect", "9:16", "--size", "1080x1920", "--in-place", "--subject", "face"
        ])
        reframed_mp4 = f"/tmp/reframed_{reel_idx}_{k}.mp4"
        subprocess.check_call([
            "voyager-video", "-C", sub_proj, "render",
            "--preset", "social-9x16", "--quality", "92", "-o", reframed_mp4
        ])
        subprocess.check_call([
            "ffmpeg", "-y", "-i", reframed_mp4, "-i", raw_sub,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "15", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-af", f"afade=t=in:st=0:d=0.018,afade=t=out:st={max(0.02, dur-0.018):.3f}:d=0.018", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            part_path, "-loglevel", "error"
        ])
    else:
        subprocess.check_call([
            "ffmpeg", "-y",
            "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", video_file,
            "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", audio_file,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(fps),
            "-af", f"afade=t=in:st=0:d=0.018,afade=t=out:st={max(0.02, dur-0.018):.3f}:d=0.018", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
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

# Create main Voyager .vvideo project
proj = f"/tmp/proj_{reel_idx}.vvideo"
subprocess.check_call(["rm", "-rf", proj])
subprocess.check_call(["voyager-video", "new", proj, src_mkv])

if not has_split:
    subprocess.check_call([
        "voyager-video", "-C", proj, "sequence", "reframe",
        "--aspect", "9:16", "--size", "1080x1920", "--in-place", "--subject", "face"
    ])

# Voyager DAW Dialogue Polish
subprocess.check_call([
    "voyager-video", "-C", proj, "track", "clean", "a1",
    "--denoise", "dialogue-natural", "--eq", "--level"
])

out_mp4 = os.path.join(out_dir, f"{slug}.mp4")
out_fcpxml = os.path.join(out_dir, f"{slug}.fcpxml")
out_preview = os.path.join(out_dir, f"{slug}_preview.png")
out_sheet = os.path.join(out_dir, f"{slug}_sheet.jpg")

if burn_captions and reel.get("words"):
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
    out_srt = os.path.join(out_dir, f"{slug}.srt")
    subprocess.check_call(["voyager-video", "-C", proj, "export", "captions", "--format", "srt", "-o", out_srt])

subprocess.check_call(["voyager-video", "-C", proj, "export", "fcpxml", "-o", out_fcpxml])

render_cmd = [
    "voyager-video", "-C", proj, "render",
    "--preset", "social-9x16",
    "--loudness", "-14LUFS",
    "--true-peak", "-1dBTP",
]
if burn_captions and reel.get("words"):
    render_cmd.append("--burn-captions")
render_cmd.extend(["-o", out_mp4])
subprocess.check_call(render_cmd)

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
