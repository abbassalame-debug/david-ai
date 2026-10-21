# audio_tools.py — David v9.0 Stage 4 audio toolchain
# Demucs: stem separation  (vocals / drums / bass / other)
# librosa: BPM, key, spectral analysis
# Run: python audio_tools.py <command> <file>
#   separate  <audio_file>          -- separate stems into <DAVID_HOME>\stems\
#   analyze   <audio_file>          -- BPM, key, duration, spectral features
#   fingerprint <audio_file>        -- print feature vector for CLAP similarity

from david_config import DAVID_DATA
import sys
import os
import json

sys.stdout.reconfigure(encoding="utf-8")

STEMS_DIR = str(DAVID_DATA / "stems")
os.makedirs(STEMS_DIR, exist_ok=True)


def separate(filepath):
    filepath = filepath.strip().strip('"')
    if not os.path.exists(filepath):
        print(f"  File not found: {filepath}")
        return

    basename = os.path.splitext(os.path.basename(filepath))[0]
    out_dir  = os.path.join(STEMS_DIR, basename)
    print(f"  Separating stems: {os.path.basename(filepath)}")
    print(f"  Output → {out_dir}")
    print("  (This takes 1-3 min on GPU, up to 10 min on CPU)\n")

    import subprocess
    result = subprocess.run(
        ["python", "-m", "demucs", "--out", STEMS_DIR, "--mp3", filepath],
        capture_output=False
    )
    if result.returncode == 0:
        print(f"\n  Stems saved to: {out_dir}")
        stems = ["vocals", "drums", "bass", "other"]
        for s in stems:
            p = os.path.join(out_dir, "htdemucs", basename, f"{s}.mp3")
            if os.path.exists(p):
                print(f"    {s}.mp3")
    else:
        print("  Separation failed. Check Demucs install.")


def analyze(filepath):
    import librosa
    import numpy as np

    filepath = filepath.strip().strip('"')
    if not os.path.exists(filepath):
        print(f"  File not found: {filepath}")
        return

    print(f"  Analyzing: {os.path.basename(filepath)}\n")
    y, sr = librosa.load(filepath, sr=None)

    duration = librosa.get_duration(y=y, sr=sr)
    tempo, _  = librosa.beat.beat_track(y=y, sr=sr)
    tempo     = float(np.asarray(tempo).flat[0])

    chroma   = librosa.feature.chroma_cqt(y=y, sr=sr)
    key_idx  = int(np.argmax(np.mean(chroma, axis=1)))
    KEYS     = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    key_name = KEYS[key_idx]

    zcr      = float(np.mean(librosa.feature.zero_crossing_rate(y)))
    rms      = float(np.mean(librosa.feature.rms(y=y)))
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))

    result = {
        "file":      os.path.basename(filepath),
        "duration":  round(duration, 2),
        "bpm":       round(tempo, 1),
        "key":       key_name,
        "zcr":       round(zcr, 4),
        "rms":       round(rms, 4),
        "centroid_hz": round(centroid, 1),
    }

    print(f"  Duration : {result['duration']}s")
    print(f"  BPM      : {result['bpm']}")
    print(f"  Key      : {result['key']}")
    print(f"  RMS      : {result['rms']}")
    print(f"  Centroid : {result['centroid_hz']} Hz")
    print(f"  ZCR      : {result['zcr']}\n")
    return result


def fingerprint(filepath):
    """Print a compact feature vector for similarity comparison."""
    result = analyze(filepath)
    if result:
        print("  Feature vector:")
        print(f"  {json.dumps(result)}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print("Usage: python audio_tools.py separate|analyze|fingerprint <file>")
        sys.exit(0)

    cmd, path = args[0], " ".join(args[1:])

    if cmd == "separate":
        separate(path)
    elif cmd == "analyze":
        analyze(path)
    elif cmd == "fingerprint":
        fingerprint(path)
    else:
        print(f"Unknown command: {cmd}")
