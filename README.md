# Audio Cleaner

FastAPI web app to separate and remix audio/video stems using [Demucs](https://github.com/facebookresearch/demucs).

## Features

- Upload audio (wav, mp3, etc.) or video (mp4, mkv, mov, ...)
- Separate into 4 stems: vocals, drums, bass, other
- Adjust volume per stem and apply wind/noise reduction
- Preview mix in-browser, download as wav or muxed mp4

## Requirements

- Python 3.11+
- ffmpeg in PATH

## Run locally

```bash
pip install -r requirements.txt
uvicorn src.app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000

## Run with Docker

```bash
docker build -t audio-cleaner .
docker run -p 8000:8000 audio-cleaner
```

For GPU support:

```bash
docker run --gpus all -p 8000:8000 audio-cleaner
```

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/upload` | Upload file, returns `job_id` |
| POST | `/api/process/{job_id}` | Start separation (`device`: cpu/cuda) |
| GET | `/api/status/{job_id}` | SSE stream of progress |
| POST | `/api/preview/{job_id}` | Mix stems, returns preview URL |
| GET | `/api/audio/{job_id}/{file}` | Serve audio/video file |
| GET | `/api/download/{job_id}` | Download final output |

## Notes

- First run downloads the `htdemucs` model (~80 MB)
- CPU separation takes several minutes; use `device: cuda` if available
- Jobs are stored in-memory and `jobs/` directory — restart clears them
