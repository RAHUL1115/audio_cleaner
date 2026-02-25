#!/usr/bin/env python3
"""Core audio separation logic used by the web app."""
import os, sys, subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def _ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-y", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-2000:])


def extract_audio(video_path, out_wav):
    _ffmpeg("-i", video_path, "-vn", "-acodec", "pcm_s16le",
            "-ar", "44100", "-ac", "2", out_wav)


def run_demucs(audio_path, job_dir, device="cpu", progress_cb=None):
    """Run htdemucs 4-stem. Returns dict of stem paths."""
    if progress_cb:
        progress_cb(0, "Starting Demucs...")

    cmd = [
        sys.executable, "-m", "demucs",
        "--name", "htdemucs",
        "--out", job_dir,
        "--device", device,
        audio_path,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace"
    )
    lines = []
    for line in proc.stdout:
        line = line.strip()
        if line:
            lines.append(line)
            # demucs prints something like "  0%|..." — try to parse a %
            if "%" in line:
                try:
                    pct = float(line.split("%")[0].strip().split()[-1])
                    if progress_cb:
                        progress_cb(pct, f"Separating... {pct:.0f}%")
                except Exception:
                    pass
    proc.wait()

    if proc.returncode != 0:
        raise RuntimeError("Demucs failed:\n" + "\n".join(lines[-20:]))

    stem = Path(audio_path).stem
    base = os.path.join(job_dir, "htdemucs", stem)
    stems = {
        "vocals": os.path.join(base, "vocals.wav"),
        "drums":  os.path.join(base, "drums.wav"),
        "bass":   os.path.join(base, "bass.wav"),
        "other":  os.path.join(base, "other.wav"),
    }
    for name, path in stems.items():
        if not os.path.exists(path):
            raise RuntimeError(f"Expected stem not found: {path}")

    if progress_cb:
        progress_cb(100, "Separation complete")
    return stems


def mix_and_export(stems, voice_vol, music_vol, bg_vol, wind_red, out_wav, tmp_dir):
    """
    Mix stems with given volume multipliers.
    voice_vol, music_vol, bg_vol: 0.0–2.0
    wind_red: 0–100 (reduction strength applied to the other/background stem)
    """
    other_path = stems["other"]

    if wind_red > 0:
        nr_val = min(wind_red * 0.97, 97.0)
        cleaned = os.path.join(tmp_dir, "other_clean.wav")
        _ffmpeg("-i", other_path,
                "-af", f"afftdn=nr={nr_val:.1f}:nf=-25",
                cleaned)
        other_path = cleaned

    def load(path):
        data, sr = sf.read(path, dtype="float32")
        return data, sr

    v_data,  sr = load(stems["vocals"])
    dr_data, _  = load(stems["drums"])
    ba_data, _  = load(stems["bass"])
    ot_data, _  = load(other_path)

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

    sf.write(out_wav, mixed, sr)


def mux_video(input_video, mixed_wav, output_path):
    _ffmpeg(
        "-i", input_video, "-i", mixed_wav,
        "-c:v", "copy", "-map", "0:v:0", "-map", "1:a:0",
        "-shortest", output_path,
    )
