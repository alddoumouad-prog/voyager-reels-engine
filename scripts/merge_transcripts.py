#!/usr/bin/env python3
import glob, json, sys

files = sorted(glob.glob(sys.argv[1]))
out_path = sys.argv[2]

all_words = []
all_segs = []
total_dur = 0.0
for f in files:
    d = json.load(open(f, encoding="utf-8"))
    total_dur = max(total_dur, d.get("total_duration", 0.0))
    all_words.extend(d.get("words", []))
    all_segs.extend(d.get("segments", []))

all_words.sort(key=lambda x: x["start"])
all_segs.sort(key=lambda x: x["start"])

json.dump({
    "total_duration": total_dur,
    "word_count": len(all_words),
    "segments": all_segs,
    "words": all_words,
}, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"Merged {len(all_words)} words across {len(all_segs)} segments -> {out_path}")
