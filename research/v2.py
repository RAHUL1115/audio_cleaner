#!/usr/bin/env python3
"""
v2.py - Voice / Music / Background / Wind volume controller
Usage: python v2.py input_video.mp4 [output.mp4]

Demucs 4-stem separation:
  Voice      = vocals
  Music      = drums + bass
  Background = other (ambient, sfx, misc)
  Wind       = ffmpeg afftdn denoiser applied to the other stem
"""
import os, sys, subprocess, shutil, tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:])
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python v2.py input.mp4 [output.mp4]")
        sys.exit(1)

    input_path = os.path.abspath(sys.argv[1])
    output_path = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else \
        str(Path(input_path).with_stem(Path(input_path).stem + "_mixed"))

    tmp = tempfile.mkdtemp(prefix="v2_")
    try:
        # 1. Extract audio
        print("Extracting audio...")
        wav = os.path.join(tmp, "audio.wav")
        ffmpeg("-i", input_path, "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", wav)

        # 2. Demucs 4-stem separation
        print("Separating stems (Demucs 4-stem)...")
        proc = subprocess.Popen(
            [sys.executable, "-m", "demucs",
             "--name", "htdemucs", "--out", tmp, wav],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace"
        )
        for line in proc.stdout:
            line = line.strip()
            if line:
                print(f"  {line}")
        proc.wait()
        if proc.returncode != 0:
            print("Demucs failed")
            sys.exit(1)

        stem = Path(wav).stem
        base = os.path.join(tmp, "htdemucs", stem)
        stems = {
            "vocals": os.path.join(base, "vocals.wav"),
            "drums":  os.path.join(base, "drums.wav"),
            "bass":   os.path.join(base, "bass.wav"),
            "other":  os.path.join(base, "other.wav"),
        }

        for name, path in stems.items():
            if not os.path.exists(path):
                print(f"Not found: {path}")
                sys.exit(1)

        # Show info
        dur = sf.info(stems["vocals"]).duration
        print(f"\nDuration: {dur:.1f}s")
        print("  Voice      = vocals")
        print("  Music      = drums + bass")
        print("  Background = other (wind lives here)\n")

        # 3. Ask volumes
        def ask(label, default=100):
            while True:
                raw = input(f"{label} volume % [{default}]: ").strip()
                if raw == "": return default / 100.0
                try:
                    v = float(raw)
                    if 0 <= v <= 200: return v / 100.0
                except ValueError:
                    pass
                print("  Enter 0-200")

        def ask_reduction(label):
            while True:
                raw = input(f"{label} reduction % [0]: ").strip()
                if raw == "": return 0.0
                try:
                    v = float(raw)
                    if 0 <= v <= 100: return v
                except ValueError:
                    pass
                print("  Enter 0-100")

        voice_vol  = ask("Voice")
        music_vol  = ask("Music")
        bg_vol     = ask("Background")
        wind_red   = ask_reduction("Wind")   # 0 = off, 100 = max reduction

        # Apply wind reduction to the other stem via ffmpeg afftdn
        other_stem = stems["other"]
        if wind_red > 0:
            print(f"\nApplying wind reduction ({wind_red:.0f}%)...")
            nr_val = wind_red * 0.97          # map 0-100 → 0-97 dB (afftdn max)
            cleaned = os.path.join(tmp, "other_clean.wav")
            ffmpeg("-i", other_stem,
                   "-af", f"afftdn=nr={nr_val:.1f}:nf=-25",
                   cleaned)
            other_stem = cleaned

        # 4. Mix
        print("\nMixing...")
        def load(path):
            data, sr = sf.read(path, dtype="float32")
            return data, sr

        v_data,  sr = load(stems["vocals"])
        dr_data, _  = load(stems["drums"])
        ba_data, _  = load(stems["bass"])
        ot_data, _  = load(other_stem)

        n = min(len(v_data), len(dr_data), len(ba_data), len(ot_data))
        mixed = (
            v_data[:n]  * voice_vol +
            dr_data[:n] * music_vol +
            ba_data[:n] * music_vol +
            ot_data[:n] * bg_vol
        )

        peak = np.max(np.abs(mixed))
        if peak > 1.0:
            mixed /= peak

        mixed_wav = os.path.join(tmp, "mixed.wav")
        sf.write(mixed_wav, mixed, sr)

        # 5. Mux back to video
        print("Muxing video...")
        ffmpeg(
            "-i", input_path, "-i", mixed_wav,
            "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
            "-shortest", output_path
        )

        print(f"\nDone: {output_path}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
