from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from yt_dlp import YoutubeDL

from pathlib import Path
import threading
import sqlite3
import uuid
import time
import os

app = FastAPI()

DOWNLOAD_DIR = "downloads"
DB_FILE = "jobs.db"
COOKIE_FILE = "cookies.txt"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

YDL_BASE_OPTS = {
    "cookiefile": COOKIE_FILE,
    "js_runtimes": {"node": {}},
    "remote_components": ["ejs:github"],
    "extractor_args": {
        "youtube": {
            "player_client": [
                "tv",
                "android",
                "web"
            ]
        }
    }
}

def db():
    return sqlite3.connect(
        DB_FILE,
        check_same_thread=False
    )


def init_db():
    conn = db()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        status TEXT,
        progress REAL DEFAULT 0,
        title TEXT,
        url TEXT,
        error TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()


init_db()


def create_job(job_id):

    conn = db()

    conn.execute(
        """
        INSERT INTO jobs(id,status,progress)
        VALUES(?,?,?)
        """,
        (
            job_id,
            "queued",
            0
        )
    )

    conn.commit()
    conn.close()


def update_job(job_id, **kwargs):

    conn = db()

    fields = []
    values = []

    for key, value in kwargs.items():
        fields.append(f"{key}=?")
        values.append(value)

    values.append(job_id)

    conn.execute(
        f"""
        UPDATE jobs
        SET {",".join(fields)}
        WHERE id=?
        """,
        values
    )

    conn.commit()
    conn.close()


def get_job(job_id):

    conn = db()

    cur = conn.execute(
        """
        SELECT *
        FROM jobs
        WHERE id=?
        """,
        (job_id,)
    )

    row = cur.fetchone()

    conn.close()

    return row


def delete_job(job_id):

    conn = db()

    conn.execute(
        """
        DELETE FROM jobs
        WHERE id=?
        """,
        (job_id,)
    )

    conn.commit()
    conn.close()


# def delete_job_after(job_id, seconds=86400):

#     def worker():

#         time.sleep(seconds)

#         try:
#             delete_job(job_id)
#         except:
#             pass

#     threading.Thread(
#         target=worker,
#         daemon=True
#     ).start()


# def delete_after(path, seconds=3600):

#     def worker():

#         time.sleep(seconds)

#         try:
#             if os.path.exists(path):
#                 os.remove(path)
#         except:
#             pass

#     threading.Thread(
#         target=worker,
#         daemon=True
#     ).start()

def janitor_worker():
    while True:
        time.sleep(600)
        
        try:
            with db() as conn:
                cur_files = conn.execute("""
                    SELECT id, url FROM jobs 
                    WHERE url IS NOT NULL AND created_at <= datetime('now', '-1 hour')
                """)

                for job_id, url in cur_files.fetchall():
                    if url and url.startswith("/files/"):
                        filename = url.replace("/files/", "")
                        filepath = os.path.join(DOWNLOAD_DIR, filename)

                        if os.path.exists(filepath):
                            try:
                                os.remove(filepath)
                            except:
                                pass

                conn.execute("""
                    DELETE FROM jobs 
                    WHERE created_at <= datetime('now', '-1 day')
                """)

                conn.commit()

        except Exception as e:
            print(f"Janitor error: {e}")


def progress_hook(job_id):

    def hook(d):

        try:

            if d["status"] == "downloading":

                total = (
                    d.get("total_bytes")
                    or d.get("total_bytes_estimate")
                )

                downloaded = d.get(
                    "downloaded_bytes",
                    0
                )

                if total:

                    percent = round(
                        (downloaded / total) * 100,
                        2
                    )

                    update_job(
                        job_id,
                        progress=percent
                    )

            elif d["status"] == "finished":

                update_job(
                    job_id,
                    progress=100
                )

        except:
            pass

    return hook


def find_downloaded_file(file_id):

    files = list(
        Path(DOWNLOAD_DIR).glob(
            f"{file_id}.*"
        )
    )

    if not files:
        return None

    return files[0].name

@app.on_event("startup")
def startup_event():
    threading.Thread(
        target=janitor_worker,
        daemon=True
    ).start()
    print("Janitor service started!")

@app.get("/")
def home():

    return {
        "status": "online"
    }


@app.get("/info")
def info(url: str):

    ydl_opts = {
        "quiet": True,
        **YDL_BASE_OPTS
    }

    with YoutubeDL(ydl_opts) as ydl:

        data = ydl.extract_info(
            url,
            download=False
        )

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

def download_video_worker(
    job_id,
    url,
    quality
):

    try:

        update_job(
            job_id,
            status="downloading"
        )

        file_id = str(
            uuid.uuid4()
        )

        ydl_opts = {
            # "cookiefile": COOKIE_FILE,
            **YDL_BASE_OPTS,
            "format":
                f"bestvideo[height<={quality}]"
                f"+bestaudio/best",

            "merge_output_format":
                "mp4",

            "outtmpl":
                f"{DOWNLOAD_DIR}/{file_id}.%(ext)s",

            "progress_hooks":
                [progress_hook(job_id)]
        }

        with YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

        filename = find_downloaded_file(
            file_id
        )

        if not filename:
            raise Exception(
                "Downloaded file not found"
            )

        fullpath = os.path.join(
            DOWNLOAD_DIR,
            filename
        )

        # delete_after(fullpath)

        update_job(
            job_id,
            status="finished",
            progress=100,
            title=info.get("title"),
            url=f"/files/{filename}"
        )

        # delete_job_after(job_id)

    except Exception as e:

        update_job(
            job_id,
            status="error",
            error=str(e)
        )

        delete_job_after(
            job_id
        )

def download_audio_worker(
    job_id,
    url
):

    try:

        update_job(
            job_id,
            status="downloading"
        )

        file_id = str(
            uuid.uuid4()
        )

        ydl_opts = {
            **YDL_BASE_OPTS,
            "format":
                "bestaudio/best",

            "outtmpl":
                f"{DOWNLOAD_DIR}/{file_id}.%(ext)s",

            "progress_hooks":
                [progress_hook(job_id)],

            "postprocessors": [
                {
                    "key":
                        "FFmpegExtractAudio",

                    "preferredcodec":
                        "mp3",

                    "preferredquality":
                        "192"
                }
            ]
        }

        with YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True
            )

        filename = find_downloaded_file(
            file_id
        )

        if not filename:
            raise Exception(
                "Downloaded file not found"
            )

        fullpath = os.path.join(
            DOWNLOAD_DIR,
            filename
        )

        # delete_after(fullpath)

        update_job(
            job_id,
            status="finished",
            progress=100,
            title=info.get("title"),
            url=f"/files/{filename}"
        )

        # delete_job_after(job_id)

    except Exception as e:

        update_job(
            job_id,
            status="error",
            error=str(e)
        )

        delete_job_after(
            job_id
        )

@app.get("/download/video")
def create_video_job(
    url: str,
    quality: int
):

    job_id = str(
        uuid.uuid4()
    )

    create_job(job_id)

    threading.Thread(
        target=download_video_worker,
        args=(
            job_id,
            url,
            quality
        ),
        daemon=True
    ).start()

    return {
        "status": True,
        "job_id": job_id
    }


@app.get("/download/audio")
def create_audio_job(
    url: str
):

    job_id = str(
        uuid.uuid4()
    )

    create_job(job_id)

    threading.Thread(
        target=download_audio_worker,
        args=(
            job_id,
            url
        ),
        daemon=True
    ).start()

    return {
        "status": True,
        "job_id": job_id
    }

@app.get("/status/{job_id}")
def job_status(
    request: Request,
    job_id: str
):

    row = get_job(
        job_id
    )

    if not row:

        return {
            "status": False,
            "message":
                "Job not found"
        }

    data = {
        "id": row[0],
        "status": row[1],
        "progress": row[2],
        "title": row[3],
        "url": row[4],
        "error": row[5]
    }

    if data["url"]:

        data["url"] = (
            f"{request.base_url}"
            f"{data['url'].lstrip('/')}"
        )

    return data

app.mount(
    "/files",
    StaticFiles(
        directory=DOWNLOAD_DIR
    ),
    name="files"
)
