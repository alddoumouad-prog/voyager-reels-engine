#!/usr/bin/env python3
import json, subprocess, sys
import numpy as np
from faster_whisper import WhisperModel

chunk_idx = int(sys.argv[1])
total_chunks = int(sys.argv[2])
audio_path = sys.argv[3]
out_json = sys.argv[4]

probe = subprocess.check_output([
    "ffprobe", "-v", "error", "-show_entries", "format=duration",
    "-of", "default=noprint_wrappers=1:nokey=1", audio_path
]).decode().strip()
total_dur = float(probe)

step = total_dur / total_chunks
start_sec = max(0.0, chunk_idx * step - (3.0 if chunk_idx > 0 else 0.0))
end_sec = min(total_dur, (chunk_idx + 1) * step + (3.0 if chunk_idx + 1 < total_chunks else 0.0))
dur_sec = end_sec - start_sec

pcm_path = f"/tmp/chunk_{chunk_idx}.pcm"
subprocess.check_call([
    "ffmpeg", "-y", "-ss", f"{start_sec:.3f}", "-t", f"{dur_sec:.3f}",
    "-i", audio_path, "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
    pcm_path, "-loglevel", "error"
])
audio_np = np.fromfile(pcm_path, dtype=np.int16).astype(np.float32) / 32768.0

model = WhisperModel("deepdml/faster-whisper-large-v3-turbo-ct2", device="cpu", compute_type="int8", cpu_threads=4)
segments_gen, info = model.transcribe(
    audio_np,
    language="ar",
    task="transcribe",
    beam_size=5,
    word_timestamps=True,
    vad_filter=True,
    condition_on_previous_text=True,
    initial_prompt="السلام عليكم الخوت، هادي تجربتي مع الصوم بالماء لمدة ثلاثين يوم لغرض العلاج، كيفاش صبرت وكيفاش دوزت هاد المدة بالدارجة المغربية."
)

keep_from = chunk_idx * step
keep_until = (chunk_idx + 1) * step if (chunk_idx + 1 < total_chunks) else (total_dur + 10.0)

words = []
segs = []
for s in segments_gen:
    s_words = []
    for w in (s.words or []):
        ws = round(start_sec + w.start, 3)
        we = round(start_sec + w.end, 3)
        wmid = 0.5 * (ws + we)
        if keep_from <= wmid < keep_until:
            txt = w.word.strip()
            if txt:
                item = {"start": ws, "end": we, "text": txt, "prob": round(float(w.probability), 3)}
                words.append(item)
                s_words.append(item)
    if s_words:
        segs.append({
            "start": s_words[0]["start"],
            "end": s_words[-1]["end"],
            "text": " ".join(x["text"] for x in s_words)
        })

json.dump({
    "chunk": chunk_idx,
    "total_duration": total_dur,
    "segments": segs,
    "words": words,
}, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"Chunk {chunk_idx}: {len(words)} words ({start_sec:.1f}s..{end_sec:.1f}s)")
