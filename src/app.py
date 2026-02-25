#!/usr/bin/env python3
"""FastAPI web app for voice/music/background/wind audio mixer."""
import asyncio, json, os, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Audio Mixer")
jobs: dict = {}
executor = ThreadPoolExecutor(max_workers=2)

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts", ".mpg"}

os.makedirs("jobs", exist_ok=True)
os.makedirs("static", exist_ok=True)


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())
    job_dir = os.path.abspath(os.path.join("jobs", job_id))
    os.makedirs(job_dir, exist_ok=True)

    filename = file.filename or "input"
    file_path = os.path.join(job_dir, filename)
    with open(file_path, "wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)

    jobs[job_id] = {
        "status": "uploaded",
        "progress": 0,
        "message": "File uploaded",
        "input_path": file_path,
        "job_dir": job_dir,
    }
    return {"job_id": job_id}


class ProcessRequest(BaseModel):
    device: str = "cpu"


@app.post("/api/process/{job_id}")
async def process(job_id: str, body: ProcessRequest):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    job = jobs[job_id]
    job.update({"status": "processing", "progress": 0, "message": "Starting..."})

    def progress_cb(pct, msg):
        job["progress"] = int(pct)
        job["message"] = msg

    def run():
        try:
            from .separator import extract_audio, run_demucs

            job_dir = job["job_dir"]
            input_path = job["input_path"]
            is_video = Path(input_path).suffix.lower() in VIDEO_EXTS

            progress_cb(5, "Extracting audio...")
            if is_video:
                wav = os.path.join(job_dir, "audio.wav")
                extract_audio(input_path, wav)
            else:
                wav = input_path

            progress_cb(10, "Separating stems — this may take a few minutes...")
            stems = run_demucs(
                wav, job_dir, body.device,
                lambda p, m: progress_cb(10 + int(p * 0.88), m),
            )

            job["stems"] = stems
            job["duration_s"] = round(__import__("soundfile").info(stems["vocals"]).duration, 1)
            job["status"] = "ready"
            progress_cb(100, "Ready")
        except Exception as e:
            job["status"] = "error"
            job["message"] = str(e)

    executor.submit(run)
    return {"status": "processing"}


@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    async def gen():
        while True:
            job = jobs.get(job_id)
            if not job:
                yield f"data: {json.dumps({'error': 'not found'})}\n\n"
                break
            payload = json.dumps({
                "status":     job["status"],
                "progress":   job.get("progress", 0),
                "message":    job.get("message", ""),
                "duration_s": job.get("duration_s"),
            })
            yield f"data: {payload}\n\n"
            if job["status"] in ("ready", "error"):
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


class PreviewRequest(BaseModel):
    voice:          float = 100
    music:          float = 100
    background:     float = 100
    wind_reduction: float = 0


@app.post("/api/preview/{job_id}")
async def preview(job_id: str, body: PreviewRequest):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    job = jobs[job_id]
    if job.get("status") != "ready":
        raise HTTPException(400, "Job not ready — wait for processing to finish")

    from .separator import mix_and_export, mux_video

    job_dir    = job["job_dir"]
    input_path = job["input_path"]
    is_video   = Path(input_path).suffix.lower() in VIDEO_EXTS

    mixed_wav = os.path.join(job_dir, "preview.wav")
    mix_and_export(
        job["stems"],
        body.voice          / 100,
        body.music          / 100,
        body.background     / 100,
        body.wind_reduction,
        mixed_wav,
        job_dir,
    )

    job["mixed_wav"] = mixed_wav
    return {"preview_url": f"/api/audio/{job_id}/preview.wav"}


@app.get("/api/audio/{job_id}/{file}")
async def serve_audio(job_id: str, file: str):
    if job_id not in jobs:
        raise HTTPException(404)
    path = os.path.join(jobs[job_id]["job_dir"], file)
    if not os.path.isfile(path):
        raise HTTPException(404, "File not found")
    ext = Path(file).suffix.lower()
    mt  = {".mp4": "video/mp4", ".wav": "audio/wav"}.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=mt)


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404)
    job = jobs[job_id]
    mixed_wav = job.get("mixed_wav")
    if not mixed_wav or not os.path.isfile(mixed_wav):
        raise HTTPException(404, "No preview yet — click Preview first")

    input_path = job["input_path"]
    is_video   = Path(input_path).suffix.lower() in VIDEO_EXTS

    if is_video:
        from .separator import mux_video
        out = os.path.join(job["job_dir"], "output.mp4")
        mux_video(input_path, mixed_wav, out)
        return FileResponse(out, media_type="video/mp4", filename="output.mp4")
    else:
        return FileResponse(mixed_wav, media_type="audio/wav", filename="output.wav")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
