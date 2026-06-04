from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from yt_dlp import YoutubeDL

from pathlib import Path
import threading
import uuid
import time
import os

app = FastAPI()

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
COOKIE_FILE = "cookies.txt"

jobs = {}

def delete_job_after(job_id, seconds=7200):

    def worker():

        time.sleep(seconds)

        jobs.pop(job_id, None)

    threading.Thread(
        target=worker,
        daemon=True
    ).start()

def delete_after(path, seconds=3600):

    def worker():

        time.sleep(seconds)

        try:
            os.remove(path)
        except:
            pass

    threading.Thread(
        target=worker,
        daemon=True
    ).start()

def progress_hook(job_id):

    def hook(d):

        if d["status"] == "downloading":

            total = d.get("total_bytes") or d.get("total_bytes_estimate")

            downloaded = d.get("downloaded_bytes", 0)

            if total:
                percent = round((downloaded / total) * 100, 2)

                jobs[job_id]["progress"] = percent

        elif d["status"] == "finished":

            jobs[job_id]["progress"] = 100

    return hook

def find_downloaded_file(file_id):
    files = list(Path(DOWNLOAD_DIR).glob(f"{file_id}.*"))

    if not files:
        return None

    return files[0].name


@app.get("/")
def home():
    return {
        "status": "online"
    }


@app.get("/info")
def info(url: str):

    with YoutubeDL({"quiet": True}) as ydl:
        data = ydl.extract_info(url, download=False)

    qualities = set()

    for f in data.get("formats", []):

        height = f.get("height")

        if height and f.get("vcodec") != "none":
            qualities.add(height)

    return {
        "status": True,
        "title": data.get("title"),
        "thumbnail": data.get("thumbnail"),
        "duration": data.get("duration"),
        "video": sorted(list(qualities)),
        "audio": ["best"]
    }


def download_video_worker(job_id, url, quality):

    try:

        jobs[job_id]["status"] = "downloading"

        file_id = str(uuid.uuid4())

        ydl_opts = {
            "cookiefile": COOKIE_FILE,
            "format": f"bestvideo[height<={quality}]+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": f"{DOWNLOAD_DIR}/{file_id}.%(ext)s",
            "progress_hooks": [progress_hook(job_id)]
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        filename = find_downloaded_file(file_id)

        fullpath = os.path.join(
            DOWNLOAD_DIR,
            filename
        )

        delete_after(fullpath)

        jobs[job_id] = {
            "status": "finished",
            "progress": 100,
            "title": info.get("title"),
            "url": f"/files/{filename}"
        }

        delete_job_after(job_id)

    except Exception as e:

        jobs[job_id] = {
            "status": "error",
            "message": str(e)
        }

        delete_job_after(job_id, 86400)


def download_audio_worker(job_id, url):

    try:

        jobs[job_id]["status"] = "downloading"

        file_id = str(uuid.uuid4())

        ydl_opts = {
            "cookiefile": COOKIE_FILE,
            "format": "bestaudio/best",
            "outtmpl": f"{DOWNLOAD_DIR}/{file_id}.%(ext)s",
            "progress_hooks": [progress_hook(job_id)],
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192"
            }]
        }

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        filename = find_downloaded_file(file_id)

        fullpath = os.path.join(
            DOWNLOAD_DIR,
            filename
        )

        delete_after(fullpath)

        jobs[job_id] = {
            "status": "finished",
            "progress": 100,
            "title": info.get("title"),
            "url": f"/files/{filename}"
        }

        delete_job_after(job_id)

    except Exception as e:

        jobs[job_id] = {
            "status": "error",
            "message": str(e)
        }

        delete_job_after(job_id, 86400)


@app.get("/download/video")
def create_video_job(url: str, quality: int):

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "status": "queued",
        "progress": 0
    }

    threading.Thread(
        target=download_video_worker,
        args=(job_id, url, quality),
        daemon=True
    ).start()

    return {
        "status": True,
        "job_id": job_id
    }


@app.get("/download/audio")
def create_audio_job(url: str):

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "status": "queued",
        "progress": 0
    }

    threading.Thread(
        target=download_audio_worker,
        args=(job_id, url),
        daemon=True
    ).start()

    return {
        "status": True,
        "job_id": job_id
    }


@app.get("/status/{job_id}")
def job_status(request: Request, job_id: str):

    if job_id not in jobs:
        return {
            "status": False,
            "message": "Job not found"
        }

    data = jobs[job_id].copy()

    if data.get("url"):
        data["url"] = f"{request.base_url}{data['url'].lstrip('/')}"

    return data


app.mount(
    "/files",
    StaticFiles(directory=DOWNLOAD_DIR),
    name="files"
)