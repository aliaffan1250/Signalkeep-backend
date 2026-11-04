import os
import subprocess
import tempfile
import uuid

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="Signalkeep Backend")

# Allow your frontend (the published page) to call this API from the browser.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # for personal use; you can lock this to your site's URL later
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB safety cap, change if you want
ALLOWED_EXT = {".mp4", ".mov"}


@app.get("/")
def health():
    return {"status": "ok", "message": "Signalkeep backend is running"}


@app.post("/optimize")
async def optimize(file: UploadFile = File(...)):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Only .mp4 or .mov files are supported")

    work_dir = tempfile.mkdtemp()
    in_path = os.path.join(work_dir, f"input{ext}")
    out_name = f"optimized_{uuid.uuid4().hex[:8]}.mp4"
    out_path = os.path.join(work_dir, out_name)

    # Save the uploaded file to disk, enforcing the size cap as we stream it.
    size = 0
    with open(in_path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "File too large")
            f.write(chunk)

    # Core step: remux the container and strip metadata that tends to
    # trigger extra re-compression, without touching the video/audio streams.
    # -c copy means no re-encode: original frames and quality are untouched.
    cmd = [
        "ffmpeg", "-y",
        "-i", in_path,
        "-map_metadata", "-1",
        "-c", "copy",
        "-movflags", "+faststart",
        out_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0 or not os.path.exists(out_path):
        # Remux without re-encode can fail on some streams (e.g. HEVC in
        # certain containers). Fall back to a clean re-encode instead.
        cmd_fallback = [
            "ffmpeg", "-y",
            "-i", in_path,
            "-map_metadata", "-1",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_path,
        ]
        result = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0 or not os.path.exists(out_path):
            raise HTTPException(500, f"Processing failed: {result.stderr[-500:]}")

    return FileResponse(
        out_path,
        media_type="video/mp4",
        filename=out_name,
    )
