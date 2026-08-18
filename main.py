import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Literal

import srt
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl

APP_NAME = "ClipForge by Nabigh Ahmed"
ROOT = Path(tempfile.gettempdir()) / "clipforge_jobs"
ROOT.mkdir(parents=True, exist_ok=True)

MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", "10800"))
MAX_CLIPS = int(os.getenv("MAX_CLIPS", "8"))
MAX_TOTAL_CLIP_SECONDS = int(os.getenv("MAX_TOTAL_CLIP_SECONDS", "900"))
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "1800"))
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))
ALLOWED_ORIGINS = [x.strip() for x in os.getenv("ALLOWED_ORIGINS", "*").split(",") if x.strip()]

app = FastAPI(title=APP_NAME, version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"] ,
    allow_headers=["*"] ,
)

jobs: dict[str, dict] = {}
job_lock = threading.Lock()
job_slots = threading.Semaphore(MAX_CONCURRENT_JOBS)

YOUTUBE_RE = re.compile(r"^(https?://)?(www\.)?(youtube\.com|youtu\.be)/", re.I)

class AnalyzeRequest(BaseModel):
    url: HttpUrl

class Clip(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    label: str | None = Field(default=None, max_length=80)

class ProcessRequest(BaseModel):
    url: HttpUrl
    clips: list[Clip]
    quality: Literal["original", "2160", "1440", "1080", "720", "480"] = "original"
    captions: bool = True
    caption_language: str = Field(default="en", min_length=2, max_length=24)
    caption_mode: Literal["soft", "burn"] = "soft"


def safe_youtube_url(url: str) -> str:
    if not YOUTUBE_RE.match(url):
        raise HTTPException(400, "Only youtube.com and youtu.be URLs are accepted.")
    return url


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("Processing timed out on the current host.")
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or e.stdout or "Unknown processing error").strip()
        raise RuntimeError(msg[-2500:])


def ytdlp_json(url: str) -> dict:
    cp = run([
        "yt-dlp", "--no-playlist", "--skip-download", "--dump-single-json",
        "--no-warnings", url
    ], timeout=90)
    return json.loads(cp.stdout)


def fmt_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def extract_chapters(info: dict) -> list[dict]:
    chapters = []
    for idx, c in enumerate(info.get("chapters") or []):
        start = float(c.get("start_time") or 0)
        end = float(c.get("end_time") or start)
        if end > start:
            chapters.append({
                "title": (c.get("title") or f"Chapter {idx+1}")[:100],
                "start": start,
                "end": end,
            })
    return chapters[:40]


def public_info(info: dict) -> dict:
    formats = info.get("formats") or []
    heights = sorted({int(f["height"]) for f in formats if f.get("height")}, reverse=True)
    return {
        "id": info.get("id"),
        "title": info.get("title") or "YouTube video",
        "uploader": info.get("uploader") or info.get("channel") or "Unknown channel",
        "duration": float(info.get("duration") or 0),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url"),
        "resolutions": heights[:12],
        "chapters": extract_chapters(info),
    }


def quality_selector(q: str) -> str:
    if q == "original":
        return "bv*+ba/b"
    h = int(q)
    return f"bv*[height<={h}]+ba/b[height<={h}]"


def find_downloaded_media(folder: Path, prefix: str) -> Path:
    candidates = [p for p in folder.glob(prefix + ".*") if p.suffix.lower() not in {".part", ".ytdl", ".srt", ".vtt", ".ass", ".json"}]
    if not candidates:
        raise RuntimeError("yt-dlp completed but no media file was produced.")
    return max(candidates, key=lambda p: p.stat().st_size)


def download_section(url: str, start: float, end: float, quality: str, outprefix: Path) -> Path:
    section = f"*{fmt_time(start)}-{fmt_time(end)}"
    cmd = [
        "yt-dlp", "--no-playlist", "--no-warnings",
        "-f", quality_selector(quality),
        "--download-sections", section,
        "--merge-output-format", "mp4",
        "--remux-video", "mp4",
        "-o", str(outprefix) + ".%(ext)s",
        url,
    ]
    run(cmd, timeout=1200)
    return find_downloaded_media(outprefix.parent, outprefix.name)


def download_subtitles(url: str, lang: str, folder: Path) -> Path | None:
    prefix = folder / "source_subs"
    cmd = [
        "yt-dlp", "--no-playlist", "--skip-download", "--no-warnings",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", f"{lang}.*,{lang}",
        "--convert-subs", "srt",
        "-o", str(prefix) + ".%(ext)s",
        url,
    ]
    try:
        run(cmd, timeout=120)
    except Exception:
        return None
    srts = sorted(folder.glob("source_subs*.srt"))
    return srts[0] if srts else None


def crop_srt(source: Path, start: float, end: float, target: Path) -> bool:
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
        items = list(srt.parse(text))
    except Exception:
        return False
    import datetime as dt
    start_td = dt.timedelta(seconds=start)
    end_td = dt.timedelta(seconds=end)
    kept = []
    for item in items:
        if item.end <= start_td or item.start >= end_td:
            continue
        ns = max(item.start, start_td) - start_td
        ne = min(item.end, end_td) - start_td
        if ne > ns:
            kept.append(srt.Subtitle(index=len(kept)+1, start=ns, end=ne, content=item.content))
    if not kept:
        return False
    target.write_text(srt.compose(kept), encoding="utf-8")
    return True


def ffmpeg_escape_path(path: Path) -> str:
    s = str(path).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'")


def apply_soft_subs(media: Path, subs: Path, output: Path) -> None:
    run([
        "ffmpeg", "-y", "-i", str(media), "-i", str(subs),
        "-map", "0:v:0", "-map", "0:a?", "-map", "1:0",
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text",
        "-metadata:s:s:0", "language=eng", "-movflags", "+faststart",
        str(output)
    ], timeout=600)


def apply_burn_subs(media: Path, subs: Path, output: Path) -> None:
    style = "FontName=Arial,FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,BorderStyle=1,Outline=3,Shadow=1,Alignment=2,MarginV=42"
    vf = f"subtitles='{ffmpeg_escape_path(subs)}':force_style='{style}'"
    run([
        "ffmpeg", "-y", "-i", str(media), "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(output)
    ], timeout=1800)


def sanitize_label(label: str | None, idx: int) -> str:
    label = (label or f"clip-{idx}").strip()
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-._")
    return (label or f"clip-{idx}")[:50]


def update_job(job_id: str, **fields):
    with job_lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def process_job(job_id: str, req: ProcessRequest):
    with job_slots:
        folder = ROOT / job_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            update_job(job_id, status="running", progress=2, message="Checking video…")
            info = ytdlp_json(str(req.url))
            duration = float(info.get("duration") or 0)
            if duration <= 0:
                raise RuntimeError("Could not determine video duration.")
            if duration > MAX_VIDEO_DURATION:
                raise RuntimeError(f"Video is longer than this host's limit ({MAX_VIDEO_DURATION//60} minutes).")

            source_subs = None
            if req.captions:
                update_job(job_id, progress=8, message="Finding captions…")
                source_subs = download_subtitles(str(req.url), req.caption_language, folder)

            outputs = []
            total = len(req.clips)
            for i, clip in enumerate(req.clips, 1):
                if clip.end > duration + 1:
                    raise RuntimeError(f"Clip {i} ends after the video duration.")
                base_progress = 10 + int((i - 1) / max(total, 1) * 78)
                update_job(job_id, progress=base_progress, message=f"Downloading clip {i}/{total}…")
                raw_prefix = folder / f"raw_{i:02d}"
                media = download_section(str(req.url), clip.start, clip.end, req.quality, raw_prefix)
                label = sanitize_label(clip.label, i)
                final = folder / f"{i:02d}-{label}.mp4"

                if req.captions and source_subs:
                    clipped_subs = folder / f"subs_{i:02d}.srt"
                    if crop_srt(source_subs, clip.start, clip.end, clipped_subs):
                        update_job(job_id, progress=base_progress + 5, message=f"Applying captions to clip {i}/{total}…")
                        if req.caption_mode == "soft":
                            apply_soft_subs(media, clipped_subs, final)
                        else:
                            apply_burn_subs(media, clipped_subs, final)
                    else:
                        shutil.move(str(media), str(final))
                else:
                    shutil.move(str(media), str(final))

                if media.exists() and media != final:
                    media.unlink(missing_ok=True)
                outputs.append(final)

            update_job(job_id, progress=92, message="Packing your clips…")
            zip_path = folder / "ClipForge-clips.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
                for p in outputs:
                    zf.write(p, arcname=p.name)

            update_job(
                job_id,
                status="done",
                progress=100,
                message="Ready to download",
                download_url=f"/api/download/{job_id}",
                size_bytes=zip_path.stat().st_size,
            )
        except Exception as e:
            update_job(job_id, status="error", progress=100, message=str(e)[:2000])


def cleanup_loop():
    while True:
        now = time.time()
        stale = []
        with job_lock:
            for jid, data in list(jobs.items()):
                if now - data.get("created_at", now) > JOB_TTL_SECONDS:
                    stale.append(jid)
                    jobs.pop(jid, None)
        for jid in stale:
            shutil.rmtree(ROOT / jid, ignore_errors=True)
        time.sleep(60)

threading.Thread(target=cleanup_loop, daemon=True).start()

@app.get("/api/health")
def health():
    return {"ok": True, "name": APP_NAME, "jobs": len(jobs)}

@app.post("/api/analyze")
def analyze(req: AnalyzeRequest):
    url = safe_youtube_url(str(req.url))
    try:
        info = ytdlp_json(url)
    except Exception as e:
        raise HTTPException(422, f"Could not analyze this video: {str(e)[:1200]}")
    data = public_info(info)
    if data["duration"] and data["duration"] > MAX_VIDEO_DURATION:
        data["warning"] = f"This free-host profile is configured for videos up to {MAX_VIDEO_DURATION//60} minutes."
    return data

@app.post("/api/process")
def process(req: ProcessRequest):
    safe_youtube_url(str(req.url))
    if not req.clips:
        raise HTTPException(400, "Add at least one clip.")
    if len(req.clips) > MAX_CLIPS:
        raise HTTPException(400, f"Maximum {MAX_CLIPS} clips per job.")
    total = 0.0
    for i, clip in enumerate(req.clips, 1):
        if clip.end <= clip.start:
            raise HTTPException(400, f"Clip {i}: end must be after start.")
        total += clip.end - clip.start
    if total > MAX_TOTAL_CLIP_SECONDS:
        raise HTTPException(400, f"Total selected duration is limited to {MAX_TOTAL_CLIP_SECONDS//60} minutes on this host profile.")

    job_id = uuid.uuid4().hex[:16]
    jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "Queued",
        "created_at": time.time(),
    }
    threading.Thread(target=process_job, args=(job_id, req), daemon=True).start()
    return jobs[job_id]

@app.get("/api/job/{job_id}")
def job_status(job_id: str):
    with job_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found or expired.")
        return {k: v for k, v in job.items() if k != "created_at"}

@app.get("/api/download/{job_id}")
def download(job_id: str):
    with job_lock:
        job = jobs.get(job_id)
        if not job or job.get("status") != "done":
            raise HTTPException(404, "Download is not ready or has expired.")
    path = ROOT / job_id / "ClipForge-clips.zip"
    if not path.exists():
        raise HTTPException(404, "File expired.")
    return FileResponse(path, media_type="application/zip", filename="ClipForge-clips.zip")
