"""Novel-Gen playground - Streamlit control room for the FastAPI backend.

Run: streamlit run app.py
"""
from __future__ import annotations

import html as _html
import hashlib
import base64
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path

import requests
import streamlit as st

DEFAULT_API = os.getenv("API_BASE", "http://localhost:8001/api")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(Path(__file__).resolve().parent.parent / "output")))

LANGUAGES = ["Tiếng Việt", "English", "中文", "日本語", "한국어", "Français", "Español", "Deutsch", "Khác..."]

INPUT_TYPES = {
    "IDEA": "Ý tưởng ngắn 1-5 câu. AI tự dựng nhân vật, bối cảnh, cốt truyện.",
    "PREMISE": "Bạn đã có nhân vật hoặc bối cảnh. AI xây cấu trúc truyện quanh chất liệu đó.",
    "REWRITE": "Dán truyện gốc. AI giữ khung cốt truyện, thay tên và bối cảnh.",
}

PHASE_META = {
    "PLANNING": ("Lập kế hoạch", "#DCA45F"),
    "WRITING": ("Đang viết", "#5AB9AA"),
    "COMPLETE": ("Hoàn thành", "#75C58E"),
}

# Rows per page in the library. Each row costs an API call and a zip download, so
# this caps the work per rerun as much as it caps the height of the table.
PAGE_SIZE = 20

st.set_page_config(page_title="Xưởng Gen Truyện", page_icon="📖", layout="wide")


def _base() -> str:
    return st.session_state.get("api_base", DEFAULT_API).rstrip("/")


def auth_headers() -> dict:
    """The session token, on every call. The API is reachable on its own port, so
    the token — not this app's login screen — is what actually protects it."""
    tok = st.session_state.get("token", "")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def is_admin() -> bool:
    """Viewers may read and download; everything that changes something is hidden
    from them here AND refused by the API, which is the half that counts."""
    return st.session_state.get("role", "admin") == "admin"


def _headers(kw: dict) -> dict:
    h = dict(auth_headers())
    h.update(kw.pop("headers", None) or {})
    return h


def api_get(path: str, **kw):
    r = requests.get(f"{_base()}{path}", timeout=kw.pop("timeout", 30),
                     headers=_headers(kw), **kw)
    r.raise_for_status()
    return r.json()


def api_post(path: str, json=None, timeout=180):
    r = requests.post(f"{_base()}{path}", json=json, timeout=timeout, headers=auth_headers())
    r.raise_for_status()
    return r.json()


def api_put(path: str, json=None, timeout=30):
    r = requests.put(f"{_base()}{path}", json=json, timeout=timeout, headers=auth_headers())
    r.raise_for_status()
    return r.json()


def api_patch(path: str, json=None, timeout=30):
    r = requests.patch(f"{_base()}{path}", json=json, timeout=timeout, headers=auth_headers())
    r.raise_for_status()
    return r.json()


def api_online() -> bool:
    try:
        requests.get(f"{_base()}/stories", timeout=4, headers=auth_headers()).raise_for_status()
        return True
    except Exception:
        return False


def login_screen() -> None:
    """Shown instead of the app until a token is held. Returns nothing — it calls
    st.stop(), so nothing below it in the script runs for a signed-out visitor."""
    st.markdown("### 📖 Xưởng Gen Truyện")
    st.caption("Đăng nhập để tiếp tục.")
    with st.form("login"):
        u = st.text_input("Tài khoản")
        p = st.text_input("Mật khẩu", type="password")
        if st.form_submit_button("Đăng nhập", use_container_width=True):
            try:
                r = requests.post(f"{_base()}/auth/login",
                                  json={"username": u, "password": p}, timeout=20)
            except Exception as e:  # noqa: BLE001
                st.error(f"Không gọi được API: {e}")
            else:
                if r.status_code == 401:
                    st.error("Sai tài khoản hoặc mật khẩu.")
                elif not r.ok:
                    st.error(f"Lỗi {r.status_code}: {r.text[:200]}")
                else:
                    d = r.json()
                    st.session_state.token = d.get("token", "")
                    st.session_state.role = d.get("role", "admin")
                    st.session_state.username = d.get("username", "")
                    st.rerun()
    st.stop()


def require_login() -> None:
    """Gate the whole app. A server with auth switched off hands back a role of
    admin and no token, so a local run needs no login at all."""
    if st.session_state.get("token") or st.session_state.get("role"):
        return
    try:
        me = requests.get(f"{_base()}/auth/me", timeout=10).json()
    except Exception:  # noqa: BLE001 — API down: let the app show its own error
        return
    if not me.get("auth_enabled", True):
        st.session_state.role = "admin"
        st.session_state.username = "anonymous"
        return
    login_screen()


_IMG_EXTS = ("webp", "png", "jpg", "jpeg")


STEMS = (("cover", "Bìa"), ("thumbnail1", "Thumb 1"), ("thumbnail2", "Thumb 2"))

# Art lives in a bucket, and the API hands out signed URLs for it: `story["images"]`
# and `story["preview_images"]`, minted per request because they expire. When no
# bucket is configured those come back empty and the helpers below fall back to the
# files under the mounted output dir, which is how this app always used to read them.
# st.image() takes a URL string or a Path either way, so callers do not care which.


def _local_images(slug: str, sub: str = "") -> dict[str, Path]:
    d = OUTPUT_DIR / slug / "image" / sub if sub else OUTPUT_DIR / slug / "image"
    out: dict[str, Path] = {}
    if d.is_dir():
        for stem, _ in STEMS:
            for ext in _IMG_EXTS:
                p = d / f"{stem}.{ext}"
                if p.exists():
                    out[stem] = p
                    break
    return out


def poster_images(story: dict) -> dict:
    """The live set: {stem: signed URL} from the API, or {stem: Path} from disk."""
    return story.get("images") or _local_images(story["slug"])


def preview_images(story: dict) -> dict:
    """The regenerated set waiting to be accepted."""
    return story.get("preview_images") or _local_images(story["slug"], ".preview")


def preview_status(story: dict) -> tuple[bool, str]:
    """(still generating, error message).

    Came off `.running` / `.error` marker files while the art was on a mounted disk;
    now it is two columns on the story row, because a bucket cannot be mounted and
    watched the way a folder could.
    """
    return bool(story.get("image_job_running")), story.get("image_job_error") or ""


def cover_for(story: dict):
    return poster_images(story).get("cover")


def img_src(ref) -> str:
    """A value usable as an <img src>. A signed URL goes in as-is; a local Path has
    to be inlined as base64, because this app serves no static files of its own."""
    if isinstance(ref, str):
        return ref
    path = Path(ref)
    mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else f"image/{path.suffix.lstrip('.').lower()}"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def output_text(slug: str, name: str) -> str | None:
    """Read a text file the backend wrote into output/<slug>/ (e.g. summarize.txt,
    full.md). Only present once the story reached COMPLETE."""
    p = OUTPUT_DIR / slug / name
    if p.exists():
        return p.read_text(encoding="utf-8", errors="ignore")
    return None


def _image_stamp(story: dict) -> str:
    """A token that changes exactly when the poster art changes, and not otherwise.

    The zip carries image/ as well as the prose, so the chapter count alone cannot
    key the cache: regenerating the art leaves the count unchanged and would keep
    serving a zip with the old covers in it. Used to be the newest file mtime.

    With signed URLs, the PATH part is what carries the version (each new image gets
    a new object key) while the query string carries the expiry and would otherwise
    churn every window and bust this cache for no reason — so only the path counts.
    """
    refs = poster_images(story)
    if not refs:
        return "0"
    parts = sorted(str(r).split("?", 1)[0] for r in refs.values())
    if not story.get("images"):  # local files: fall back to mtimes
        parts = sorted(str(int(Path(r).stat().st_mtime)) for r in refs.values())
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def cached_story_zip(story_id: int, chapters_done: int, story: dict | None = None) -> bytes | None:
    if chapters_done <= 0:
        return None
    zkey = f"zip_{story_id}_{chapters_done}_{_image_stamp(story) if story else '0'}"
    if zkey not in st.session_state:
        try:
            r = requests.get(f"{_base()}/stories/{story_id}/download-zip", timeout=180, headers=auth_headers())
            r.raise_for_status()
            st.session_state[zkey] = r.content
        except Exception:
            st.session_state[zkey] = None
    return st.session_state.get(zkey)


def upload_zip_to_cms(upload_url: str, story: dict, data: bytes):
    files = {"file": (f"{story['slug']}.zip", data, "application/zip")}
    form = {"story_id": str(story["id"]), "slug": story["slug"], "title": story["title"]}
    r = requests.post(upload_url, data=form, files=files, timeout=180)
    r.raise_for_status()
    return r


_NAV_RE = re.compile(
    r"^\s*(next\s+chapter\s*>>|>>\s*next\s+chapter|"
    r"<<\s*previous\s+chapter|previous\s+chapter\s*<<)\s*$",
    re.I,
)
_CH_NUM_RE = re.compile(r"chapter\s+(\d+)\b", re.I)


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", "", raw)
    return _html.unescape(raw)


# The "Chapter 12 - " / "Chapter 12: " lead-in of a scraped chapter title. Only the
# numbering is stripped; whatever the site appends to the book name is left alone,
# exactly as the folder-name path leaves "... Book PDF Free" alone.
_CH_TITLE_LEAD_RE = re.compile(r"^\s*#?\s*chapter\s+\d+\s*[-–—:.|]*\s*", re.I)


def _title_from_chapter_json(zf: zipfile.ZipFile, found: dict[int, dict]) -> str:
    """Book title recovered from the per-chapter `chapter.json`, for a .zip with no
    wrapping folder to take the name from.

    Those files carry `"title": "Chapter 12 - <book> <site suffix>"`, so dropping the
    chapter numbering leaves the same string for every chapter of one book. It is
    accepted ONLY if every chapter agrees. One chapter disagreeing means the title
    carries per-chapter text (a chapter subtitle, two books in one archive), and then
    no chapter's title is the book's name — better an empty box the user fills in than
    a wrong original recorded against the story forever.
    """
    titles = set()
    for entry in found.values():
        name = entry.get("json")
        if not name:
            return ""  # a chapter with no metadata: unanimity cannot be established
        try:
            meta = json.loads(zf.read(name).decode("utf-8", "ignore"))
            title = _CH_TITLE_LEAD_RE.sub("", str(meta.get("title") or "")).strip()
        except (ValueError, KeyError, AttributeError):
            return ""
        if not title:
            return ""
        titles.add(title)
        if len(titles) > 1:
            return ""
    return titles.pop() if titles else ""


def merge_chapter_zip(data: bytes) -> tuple[str, dict]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    found: dict[int, dict] = {}
    roots: dict[str, int] = {}  # folder that CONTAINS the "Chapter N" folders = source title
    for name in zf.namelist():
        if name.endswith("/"):
            continue
        m = _CH_NUM_RE.search(name)
        if not m:
            continue
        base = name.rsplit("/", 1)[-1].lower()
        entry = found.setdefault(int(m.group(1)), {})
        if base == "content.txt":
            entry["txt"] = name
        elif base == "content.html":
            entry["html"] = name
        elif base == "chapter.json":
            entry["json"] = name
        segs = name.split("/")
        for i, seg in enumerate(segs):
            if re.match(r"(?i)^\s*#?\d*\s*chapter\s+\d+", seg):
                if i > 0:
                    roots[segs[i - 1]] = roots.get(segs[i - 1], 0) + 1
                break

    if not found:
        raise ValueError("Không thấy thư mục 'Chapter <số>' chứa content.txt/html trong .zip")

    parts, from_html, empty = [], [], []
    total_words = 0
    for n in sorted(found):
        e = found[n]
        if "txt" in e:
            lines = zf.read(e["txt"]).decode("utf-8", "ignore").splitlines()
        elif "html" in e:
            from_html.append(n)
            lines = _html_to_text(zf.read(e["html"]).decode("utf-8", "ignore")).splitlines()
        else:
            continue
        body = "\n".join(ln for ln in lines if not _NAV_RE.match(ln)).strip()
        if not body:
            empty.append(n)
        total_words += len(body.split())
        parts.append(f"Chapter {n}\n\n{body}")

    nums = sorted(found)
    missing = [i for i in range(nums[0], nums[-1] + 1) if i not in found]
    # The wrapping folder is the primary source of the original title; chapter.json is
    # only consulted when the archive has none (chapters zipped at the top level).
    folder_title = max(roots, key=roots.get) if roots else ""
    source_title = folder_title or _title_from_chapter_json(zf, found)
    report = {
        "count": len(parts),
        "words": total_words,
        "wpc": round(total_words / len(parts)) if parts else 0,
        "missing": missing,
        "empty": empty,
        "from_html": from_html,
        "range": (nums[0], nums[-1]),
        "source_title": source_title,
        "title_source": ("folder" if folder_title else "chapter.json" if source_title else ""),
    }
    return "\n\n".join(parts), report


st.markdown(
    """
    <style>
      :root {
        --bg: #0E1318;
        --panel: #151B22;
        --panel-2: #1B232B;
        --line: #2B3640;
        --text: #ECE8DF;
        --muted: #A7B0B8;
        --gold: #DCA45F;
        --teal: #5AB9AA;
        --green: #75C58E;
        --red: #E4776F;
      }
      .stApp {background: radial-gradient(circle at 20% 0%, #1B2530 0, #0E1318 34rem);}
      .block-container {max-width: 1240px; padding: 3.75rem 2rem 3rem;}
      [data-testid="stSidebar"] {background: #0B1015; border-right: 1px solid var(--line);}
      h1, h2, h3 {letter-spacing: 0; color: var(--text);}
      h1 {font-size: 2rem !important; margin-bottom: .25rem !important;}
      p, label, span, div {letter-spacing: 0;}
      .app-title {font-size: 1.15rem; font-weight: 800; color: var(--text); margin-bottom: .15rem;}
      .eyebrow {color: var(--gold); font-size: .78rem; font-weight: 700; text-transform: uppercase;}
      .muted {color: var(--muted); font-size: .86rem;}
      .soft-panel {
        border: 1px solid var(--line);
        background: rgba(21, 27, 34, .82);
        border-radius: 8px;
        padding: 1rem;
      }
      .story-card {
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: .95rem 1rem;
        background: linear-gradient(180deg, rgba(27,35,43,.96), rgba(18,24,30,.96));
        margin-bottom: .75rem;
      }
      .story-title {font-size: 1rem; font-weight: 800; color: var(--text); margin-bottom: .35rem;}
      .table-head {
        color: var(--muted);
        font-size: .75rem;
        font-weight: 800;
        text-transform: uppercase;
        padding: .45rem .35rem;
        border-bottom: 1px solid var(--line);
        margin-top: .85rem;
        white-space: nowrap;
      }
      .table-cell {
        min-height: 54px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        padding: .45rem .35rem;
        border-bottom: 1px solid rgba(43,54,64,.78);
      }
      .row-cell {
        min-height: 48px;
        display: flex;
        align-items: center;
      }
      .row-cell p {margin: 0 !important;}
      .row-cell .mini-progress {width: 100%; margin-top: 0;}
      .table-title {font-weight: 800; color: var(--text); line-height: 1.25;}
      .status-pill {
        display: inline-flex;
        width: fit-content;
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 2px 9px;
        font-size: 11px;
        font-weight: 750;
        color: var(--text);
        background: rgba(21,27,34,.72);
        white-space: nowrap;
      }
      .library-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 1rem;
        table-layout: fixed;
      }
      .library-table th {
        color: var(--muted);
        font-size: .72rem;
        font-weight: 850;
        text-transform: uppercase;
        text-align: left;
        padding: .55rem .7rem;
        border-bottom: 1px solid var(--line);
      }
      .library-table td {
        padding: .75rem .7rem;
        border-bottom: 1px solid rgba(43,54,64,.82);
        vertical-align: middle;
      }
      .library-table tbody tr:hover {background: rgba(255,255,255,.025);}
      .library-table .title-col {width: 22%;}
      .library-table .source-col {width: 18%;}
      .library-table .state-col {width: 11%;}
      .library-table .phase-col {width: 11%;}
      .library-table .small-col {width: 9%;}
      .library-table .progress-col {width: 14%;}
      .library-table .action-col {width: 6%;}
      .library-table .lib-src {color: var(--muted); font-size: .86rem; line-height: 1.3;}
      .lib-title {font-weight: 850; color: var(--text); line-height: 1.25;}
      .lib-sub {color: var(--muted); font-size: .8rem; margin-top: .2rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
      .truncate {
        display: block;
        width: 100%;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
      .story-cell {max-width: 270px;}
      .source-cell {max-width: 150px;}
      .nowrap {white-space: nowrap;}
      .open-link {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        min-height: 38px;
        border: 1px solid var(--line);
        border-radius: 7px;
        color: var(--text) !important;
        text-decoration: none !important;
        font-weight: 750;
        background: rgba(21,27,34,.72);
      }
      .open-link:hover {border-color: var(--gold); background: rgba(220,164,95,.12);}
      [data-testid="column"] .stButton > button,
      [data-testid="column"] .stDownloadButton > button {
        min-height: 2.2rem;
        padding: .35rem .42rem;
        font-size: 12px;
        white-space: nowrap;
      }
      .mini-progress {
        height: 8px;
        border-radius: 999px;
        background-color: #111820;
        overflow: hidden;
        margin-top: .3rem;
      }
      .mini-progress > span {
        display: block;
        height: 100%;
        border-radius: inherit;
        background: var(--teal);
      }
      .detail-shell {
        border: 1px solid var(--line);
        background: linear-gradient(180deg, rgba(27,35,43,.92), rgba(14,19,24,.96));
        border-radius: 8px;
        padding: .85rem 1rem;
        margin: .65rem 0 1.1rem;
      }
      .detail-title {
        color: var(--text);
        font-size: 1.65rem;
        line-height: 1.25;
        font-weight: 850;
        margin: .15rem 0 .45rem;
      }
      .detail-meta {
        display: flex;
        align-items: center;
        gap: .55rem;
        flex-wrap: wrap;
        color: var(--muted);
        font-size: .86rem;
      }
      .status-note {
        border-left: 3px solid var(--teal);
        background: rgba(90,185,170,.10);
        padding: .58rem .75rem;
        border-radius: 6px;
        color: #DDEEEA;
        margin: .95rem 0 .9rem;
      }
      .danger-note {
        border-left-color: var(--red);
        background: rgba(228,119,111,.10);
      }
      .cover-frame {
        border: 1px solid var(--line);
        background: rgba(21,27,34,.72);
        border-radius: 8px;
        padding: .75rem;
        min-height: 180px;
      }
      .empty-cover {
        min-height: 86px;
        border: 1px dashed #3A4752;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--muted);
        text-align: center;
        padding: .75rem;
      }
      .stat-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: .75rem;
        margin-bottom: .9rem;
      }
      .stat-card {
        height: 92px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: rgba(21,27,34,.74);
        padding: .85rem .95rem;
        display: flex;
        flex-direction: column;
        justify-content: space-between;
      }
      .stat-label {color: var(--muted); font-size: .78rem; font-weight: 700;}
      .stat-value {color: var(--text); font-size: 1.35rem; font-weight: 850; line-height: 1;}
      .stat-sub {color: var(--muted); font-size: .78rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
      .image-strip {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: .85rem;
        align-items: start;
      }
      .image-slot {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: rgba(21,27,34,.72);
        padding: .55rem;
        min-height: 96px;
      }
      .detail-actions {
        border: 1px solid var(--line);
        background: rgba(21,27,34,.66);
        border-radius: 8px;
        padding: .65rem;
        margin-top: .7rem;
      }
      .reader-head {
        display: flex;
        align-items: end;
        justify-content: space-between;
        gap: 1rem;
        margin: 2rem 0 .7rem;
      }
      .reader-title {font-size: 1.15rem; font-weight: 800; color: var(--text);}
      .detail-hero {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: linear-gradient(180deg, rgba(27,35,43,.94), rgba(14,19,24,.98));
        padding: 1rem 1.1rem;
        margin: .6rem 0 1.25rem;
      }
      .detail-hero-top {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 1rem;
      }
      .detail-hero-title {
        color: var(--text);
        font-size: 1.7rem;
        line-height: 1.18;
        font-weight: 900;
        margin: .22rem 0 .6rem;
      }
      .detail-hero-meta {
        display: flex;
        flex-wrap: wrap;
        gap: .5rem;
        align-items: center;
      }
      .detail-grid {
        display: grid;
        grid-template-columns: 1.45fr 1fr;
        gap: 1.25rem;
        align-items: start;
      }
      .detail-panel {
        border: 1px solid var(--line);
        border-radius: 8px;
        background: rgba(21,27,34,.52);
        padding: 1rem;
      }
      .panel-title {
        color: var(--text);
        font-size: .92rem;
        font-weight: 850;
        margin-bottom: .8rem;
      }
      .detail-stat-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: .8rem;
        margin-bottom: 1rem;
      }
      .detail-stat {
        min-height: 86px;
        border: 1px solid rgba(43,54,64,.86);
        border-radius: 8px;
        background: rgba(14,19,24,.55);
        padding: .85rem;
      }
      .detail-stat .label {color: var(--muted); font-size: .75rem; font-weight: 800;}
      .detail-stat .value {color: var(--text); font-size: 1.45rem; font-weight: 900; line-height: 1.15; margin-top: .4rem;}
      .detail-stat .sub {color: var(--muted); font-size: .78rem; margin-top: .22rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
      .action-caption {
        color: var(--muted);
        font-size: .8rem;
        margin: .55rem 0 .35rem;
      }
      .action-rule {
        height: 1px;
        background: rgba(43,54,64,.72);
        margin: 1rem 0 .75rem;
      }
      .danger-row {
        margin-top: .75rem;
        padding-top: .75rem;
        border-top: 1px solid rgba(228,119,111,.22);
      }
      .image-empty {
        min-height: 104px;
        border: 1px dashed #3A4752;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--muted);
        text-align: center;
        padding: .85rem;
        background: rgba(14,19,24,.38);
      }
      .art-gallery {
        display: grid;
        grid-template-columns: 1.35fr .82fr .82fr;
        gap: 1rem;
        align-items: stretch;
        margin-bottom: .85rem;
      }
      .art-tile {
        min-width: 0;
      }
      .art-img {
        width: 100%;
        height: 228px;
        object-fit: contain;
        object-position: center;
        border-radius: 8px;
        border: 1px solid var(--line);
        background-color: #111820;
        display: block;
      }
      .art-label {
        color: var(--muted);
        font-size: .82rem;
        font-weight: 750;
        text-align: center;
        margin-top: .45rem;
      }
      .detail-reader {
        margin-top: 1.5rem;
        border-top: 1px solid var(--line);
        padding-top: 1.1rem;
      }
      .badge {
        display: inline-flex;
        align-items: center;
        gap: .35rem;
        min-height: 24px;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 11px;
        font-weight: 750;
        border: 1px solid;
        white-space: nowrap;
      }
      .run-dot {
        display:inline-block; width:8px; height:8px; border-radius:50%;
        background:var(--teal); margin-right:7px; animation:pulse 1.2s infinite;
      }
      @keyframes pulse {0%{opacity:.35} 50%{opacity:1} 100%{opacity:.35}}
      .manuscript {
        background: #11171D;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 1.25rem 1.45rem;
        max-height: 620px;
        overflow-y: auto;
        line-height: 1.78;
        color: #F2EDE3;
        font-size: 1rem;
      }
      .section-caption {color: var(--muted); margin-bottom: 1rem;}
      div[data-testid="stMetric"] {
        background: rgba(21,27,34,.72);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: .85rem 1rem;
      }
      .stButton > button, .stDownloadButton > button {border-radius: 7px; min-height: 2.45rem;}
      .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"] > div {
        border-radius: 7px;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


def badge(phase: str) -> str:
    label, color = PHASE_META.get(phase, (phase, "#A7B0B8"))
    return f'<span class="badge" style="color:{color};border-color:{color};background:{color}1F">{label}</span>'


def page_header(title: str, caption: str | None = None):
    st.markdown(f"<div class='eyebrow'>Novel-Gen Playground</div>", unsafe_allow_html=True)
    st.title(title)
    if caption:
        st.markdown(f"<div class='section-caption'>{caption}</div>", unsafe_allow_html=True)


st.session_state.setdefault("api_base", DEFAULT_API)
st.session_state.setdefault("page", "library")
st.session_state.setdefault("story_id", None)
st.session_state.setdefault("default_language", "English")
st.session_state.setdefault("launch_payload", None)
st.session_state.setdefault("cms_upload_url", "")
st.session_state.setdefault("_settings_loaded", False)

# Before any API call, including the settings load below — those go through the
# gate too, and an un-authenticated one would 401 and paint an error over the
# login form.
require_login()

if not st.session_state._settings_loaded:
    try:
        st.session_state.cms_upload_url = api_get("/settings/cms_upload_url").get("value", "")
        st.session_state._settings_loaded = True
    except Exception:
        pass

story_q = st.query_params.get("story_id")
if story_q:
    try:
        st.session_state.story_id = int(story_q)
        st.session_state.page = "detail"
        st.query_params.clear()
    except ValueError:
        st.query_params.clear()


def go(page: str, story_id: int | None = None):
    st.session_state.page = page
    if story_id is not None:
        st.session_state.story_id = story_id


with st.sidebar:
    st.markdown("<div class='app-title'>📖 Xưởng Gen Truyện</div>", unsafe_allow_html=True)
    st.caption("Bảng điều khiển pipeline tạo tiểu thuyết")
    st.divider()

    # A viewer has no create page to go to — hidden rather than disabled, so the
    # sidebar shows what they can actually do instead of what they cannot.
    if is_admin() and st.button("✍️  Tạo truyện mới", use_container_width=True, type="primary"):
        go("create")
    if st.button("📚  Thư viện", use_container_width=True):
        go("library")
    if st.button("⚙️  Cài đặt", use_container_width=True):
        go("settings")

    st.divider()
    online = api_online()
    st.markdown(f"{'🟢' if online else '🔴'} **API** {'sẵn sàng' if online else 'không kết nối'}")
    st.caption(_base())
    if not online:
        st.caption("Chạy backend rồi kiểm tra lại URL trong Cài đặt.")

    if st.session_state.get("username"):
        st.divider()
        role_label = "Quản trị" if is_admin() else "Chỉ xem"
        st.caption(f"👤 {st.session_state.username} · {role_label}")
        if st.button("Đăng xuất", use_container_width=True):
            for k in ("token", "role", "username"):
                st.session_state.pop(k, None)
            st.rerun()


def view_create():
    page_header(
        "Tạo truyện mới",
        "Nhập chất liệu, chọn độ dài, rồi để hệ thống tự đặt tên, lập kế hoạch, viết chương và tạo ảnh bìa.",
    )

    # Only REWRITE is enabled for now — IDEA/PREMISE are temporarily locked.
    itype = st.radio(
        "Kiểu nội dung",
        list(INPUT_TYPES.keys()),
        index=list(INPUT_TYPES).index("REWRITE"),
        captions=list(INPUT_TYPES.values()),
        horizontal=True,
        disabled=True,
    )
    itype = "REWRITE"
    st.caption("Hiện chỉ hỗ trợ **REWRITE**. IDEA và PREMISE tạm thời bị khóa.")

    col_l, col_r = st.columns([3, 2], gap="large")
    with col_l:
        st.markdown("#### Chất liệu đầu vào")
        if itype == "REWRITE":
            method = st.radio(
                "Nguồn truyện gốc",
                ["Dán / tải 1 file", "Tải thư mục chương (.zip)"],
                horizontal=True,
            )
            if method == "Tải thư mục chương (.zip)":
                st.caption("Nén folder truyện thành .zip. Mỗi chương nên nằm trong `Chapter N/` và có `content.txt` hoặc `content.html`.")
                z = st.file_uploader("Tải .zip thư mục chương", type=["zip"])
                if z is not None:
                    sig = ("zip", z.name, z.size)
                    if st.session_state.get("_src_sig") != sig:
                        try:
                            merged, rep = merge_chapter_zip(z.getvalue())
                            st.session_state["_src_sig"] = sig
                            st.session_state["src_content"] = merged
                            st.session_state["src_title"] = rep.get("source_title", "")
                            st.session_state["_src_msg"] = ("ok", rep)
                        except Exception as e:
                            st.session_state["_src_msg"] = ("err", str(e))
                msg = st.session_state.get("_src_msg")
                if msg and msg[0] == "ok":
                    r = msg[1]
                    st.success(
                        f"Đã gộp {r['count']} chương (Chapter {r['range'][0]}-{r['range'][1]}) · "
                        f"{r['words']:,} từ · ~{r['wpc']:,} từ/chương."
                    )
                    if r["missing"]:
                        st.warning(f"Thiếu chương: {r['missing']}")
                    if r["empty"]:
                        st.warning(f"Chương rỗng: {r['empty']}")
                    if r["from_html"]:
                        st.caption(f"Khôi phục từ HTML: {r['from_html']}")
                    # Say where the original title came from — an empty box after an
                    # upload used to look like the upload worked when in fact the one
                    # link back to the source book had just been lost.
                    if r.get("title_source") == "chapter.json":
                        st.caption(
                            "Tên truyện gốc đọc từ `chapter.json` (mọi chương khớp nhau) "
                            "— .zip không có thư mục bọc ngoài."
                        )
                    elif not r.get("source_title"):
                        st.warning(
                            "Không xác định được tên truyện gốc: .zip không có thư mục bọc "
                            "ngoài, và `chapter.json` thiếu hoặc không khớp giữa các chương. "
                            "Điền tay ở ô **Tên truyện gốc** bên dưới."
                        )
                elif msg and msg[0] == "err":
                    st.error(f"Không gộp được .zip: {msg[1]}")
            else:
                up = st.file_uploader("Tải truyện gốc (.md / .txt)", type=["md", "txt"])
                if up is not None:
                    sig = ("file", up.name, up.size)
                    if st.session_state.get("_src_sig") != sig:
                        st.session_state["_src_sig"] = sig
                        st.session_state["src_content"] = up.getvalue().decode("utf-8", "ignore")
                        st.session_state["src_title"] = Path(up.name).stem
                        st.session_state.pop("_src_msg", None)

            st.session_state.setdefault("src_content", "")
            content = st.text_area(
                "Nội dung truyện gốc",
                key="src_content",
                height=300,
                placeholder="Dán truyện gốc, hoặc tải file / .zip ở trên...",
            )
            st.session_state.setdefault("src_title", "")
            st.text_input(
                "Tên truyện gốc",
                key="src_title",
                help="Tên truyện gốc trước khi reskin — hiển thị ở Thư viện. "
                     "Tự điền từ tên thư mục .zip, hoặc `chapter.json` nếu .zip không có "
                     "thư mục bọc ngoài, hoặc tên file.",
            )
        else:
            ph = (
                "VD: Một nữ pháp sư mất trí nhớ thức dậy trong thành phố bị phong ấn..."
                if itype == "IDEA"
                else "Mô tả nhân vật chính, bối cảnh, xung đột và các mối quan hệ then chốt..."
            )
            content = st.text_area("Nội dung", height=360, placeholder=ph)

    with col_r:
        st.markdown("#### Cấu hình")
        default_lang = st.session_state.default_language
        idx = LANGUAGES.index(default_lang) if default_lang in LANGUAGES else 0
        lang_choice = st.selectbox("Ngôn ngữ truyện", LANGUAGES, index=idx)
        language = st.text_input("Nhập ngôn ngữ", value="") if lang_choice == "Khác..." else lang_choice
        genre = st.text_input("Thể loại", placeholder="lãng mạn / kinh dị / trinh thám...")

        mode = st.radio("Độ dài", ["Mặc định", "Theo số chương", "Theo số từ"], horizontal=True)
        desired_chapters = desired_words = None
        if mode == "Theo số chương":
            desired_chapters = st.number_input("Số chương", 1, 200, 25)
        elif mode == "Theo số từ":
            desired_words = st.number_input("Tổng số từ", 2000, 500_000, 100_000, step=5000)
        else:
            st.info("REWRITE bám theo độ dài truyện gốc. IDEA/PREMISE mặc định 25 chương, khoảng 100k từ.")

    st.divider()
    disabled = not content.strip() or not str(language).strip()
    if st.button("🚀  Tạo và bắt đầu gen", type="primary", disabled=disabled or not is_admin(), use_container_width=True):
        payload = {"language": language, "input_type": itype, "genre": genre or None, "content": content}
        if itype == "REWRITE" and st.session_state.get("src_title", "").strip():
            payload["source_title"] = st.session_state["src_title"].strip()
        if desired_chapters:
            payload["desired_chapters"] = int(desired_chapters)
        if desired_words:
            payload["desired_words"] = int(desired_words)
        st.session_state.launch_payload = payload
        go("launching")
        st.rerun()


def view_launching():
    payload = st.session_state.get("launch_payload")
    if not payload:
        go("create")
        st.rerun()

    page_header(
        "Đang khởi tạo truyện",
        "Playground đang tạo hồ sơ truyện, đặt tên và đưa pipeline sang màn gen. Bạn không còn ở form tạo nữa.",
    )
    st.markdown(
        "<div class='soft-panel'><strong>Trạng thái:</strong> đang chuẩn bị job gen truyện...</div>",
        unsafe_allow_html=True,
    )

    progress = st.progress(0, text="Tạo truyện và đặt tên")
    try:
        created = api_post("/stories", json=payload)
        progress.progress(65, text=f"Đã tạo: {created['title']}")
        api_post(f"/stories/{created['id']}/run")
        progress.progress(100, text="Pipeline đã bắt đầu")
        st.session_state.launch_payload = None
        go("detail", created["id"])
        time.sleep(0.4)
        st.rerun()
    except requests.HTTPError as e:
        st.session_state.launch_payload = None
        st.error(f"Lỗi từ API: {e.response.status_code} - {e.response.text}")
        if st.button("Quay lại form tạo truyện", type="primary"):
            go("create")
            st.rerun()
    except Exception as e:
        st.session_state.launch_payload = None
        st.error(f"Không gọi được API: {e}")
        if st.button("Quay lại form tạo truyện", type="primary"):
            go("create")
            st.rerun()


@st.dialog("Chỉnh sửa truyện", width="large")
def edit_dialog(story: dict):
    """Retitle and re-art a finished story, deciding each time whether to keep the
    result. Nothing here overwrites anything until the accept button is pressed:
    a title is drafted without being saved, and art is generated into a preview
    folder — so a worse result costs nothing but the time it took."""
    sid = story["id"]
    st.markdown(f"**#{sid}** · `{story['slug']}`")

    # ── Title ────────────────────────────────────────────────────────────────
    st.markdown("#### Tiêu đề")
    st.markdown(f"Hiện tại: **{story['title']}**")

    def apply_title(new_title: str) -> None:
        """Both paths land on the same endpoint — the API never cared whether a title
        came from the model or from the keyboard."""
        try:
            r = api_patch(f"/stories/{sid}/title", json={"title": new_title})
            st.session_state.pop(f"ed_tcand_{sid}", None)
            files = ", ".join(r.get("updated_files") or []) or "chưa có file xuất bản"
            st.toast(f"Đã đổi tiêu đề. Đã cập nhật: {files}")
            st.rerun()  # RerunException is a BaseException — the handlers below miss it
        except requests.HTTPError as e:
            st.error(f"{e.response.status_code}: {e.response.text}")
        except Exception as e:
            st.error(f"Lỗi: {e}")

    modes = ("✨ Gen lại bằng AI", "✏️ Nhập tay")
    mode = st.radio("Cách đổi tiêu đề", modes, key=f"ed_tmode_{sid}",
                    horizontal=True, label_visibility="collapsed")

    if mode == modes[1]:
        # Deliberately unkeyed: a widget's identity includes `value`, so after a save
        # the box reopens holding the NEW title. A key would pin it to the text typed
        # last time and quietly offer a stale title as the next edit.
        manual = st.text_input("Tiêu đề mới", value=story["title"],
                               placeholder="Nhập tiêu đề...").strip()
        if st.button("💾 Lưu tiêu đề", key=f"ed_tsave_{sid}", type="primary",
                     use_container_width=True, disabled=not manual or not is_admin()):
            apply_title(manual)
        # Saving an unchanged title is allowed on purpose: it re-syncs the title line
        # in files exported under an older name.
    else:
        t_notes = st.text_area(
            "Yêu cầu cho tiêu đề mới (để trống vẫn gen được)",
            key=f"ed_tnotes_{sid}", height=70,
            placeholder="VD: nhấn vào yếu tố mafia, bớt uỷ mị, ngắn hơn",
        )
        if st.button("✨ Gen tiêu đề mới", key=f"ed_tgen_{sid}", disabled=not is_admin(), use_container_width=True):
            try:
                with st.spinner("Đang nghĩ tiêu đề…"):
                    r = api_post(f"/stories/{sid}/suggest-title",
                                 json={"notes": t_notes}, timeout=180)
                st.session_state[f"ed_tcand_{sid}"] = r.get("title", "")
            except Exception as e:
                st.error(f"Lỗi: {e}")

        cand = st.session_state.get(f"ed_tcand_{sid}")
        if cand:
            st.success(f"Tiêu đề đề xuất: **{cand}**")
            tc = st.columns([1, 1])
            if tc[0].button("✓ Dùng tiêu đề này", key=f"ed_tok_{sid}", disabled=not is_admin(),
                            type="primary", use_container_width=True):
                apply_title(cand)
            if tc[1].button("✕ Bỏ, giữ tiêu đề cũ", key=f"ed_tno_{sid}", disabled=not is_admin(),
                            use_container_width=True):
                st.session_state.pop(f"ed_tcand_{sid}", None)
                st.rerun()

    st.caption("Tiêu đề mới được ghi vào summarize.txt và full.md, "
               "và các file tải về sẽ mang tên mới.")

    st.divider()

    # ── Images ───────────────────────────────────────────────────────────────
    st.markdown("#### Ảnh")
    live, prev = poster_images(story), preview_images(story)
    if not live:
        st.caption("Chưa có ảnh nào.")
    else:
        cols = st.columns(3)
        for col, (stem, label) in zip(cols, (("cover", "Bìa"), ("thumbnail1", "Thumb 1"),
                                             ("thumbnail2", "Thumb 2"))):
            if stem in live:
                col.image(str(live[stem]), caption=f"{label} (hiện tại)",
                          use_container_width=True)

    i_notes = st.text_area(
        "Yêu cầu cho ảnh mới (để trống vẫn gen được)",
        key=f"ed_inotes_{sid}", height=70,
        placeholder="VD: tông xanh lạnh, bối cảnh ngoài trời\nthumb2: đứng xa nhau hơn",
    )
    which = st.selectbox(
        "Gen lại ảnh nào", ["all", "cover", "thumbnail1", "thumbnail2"],
        format_func=lambda v: {"all": "Tất cả", "cover": "Bìa",
                               "thumbnail1": "Thumb 1", "thumbnail2": "Thumb 2"}[v],
        key=f"ed_iwhich_{sid}",
    )
    if st.button("🎨 Gen ảnh mới (xem trước)", key=f"ed_igen_{sid}", disabled=not is_admin(),
                 use_container_width=True):
        started = time.time() - 1          # 1s of slack for clock skew across mounts
        try:
            api_post(f"/stories/{sid}/regenerate-images",
                     json={"which": which, "notes": i_notes,
                           "preview": True, "background": True},
                     timeout=60)
        except Exception as e:
            st.error(f"Lỗi: {e}")
        else:
            # Draw into one placeholder and keep redrawing it. Each poster appears as
            # soon as it is stored, so a finished image stops spinning while the
            # others carry on — instead of one spinner covering all three for two
            # minutes with no sign of which is done. One API read per pass gives both
            # the art and the job state, so the two can never disagree.
            wanted = [s for s, _ in STEMS] if which == "all" else [which]
            slot = st.empty()
            deadline = time.time() + 900
            while time.time() < deadline:
                snap = api_get(f"/stories/{sid}")
                fresh = preview_images(snap)
                running, err = preview_status(snap)
                with slot.container():
                    cols = st.columns(3)
                    for col, (stem, label) in zip(cols, STEMS):
                        if stem in fresh:
                            col.image(str(fresh[stem]), caption=f"{label} ✓",
                                      use_container_width=True)
                        elif stem in wanted:
                            col.markdown(f"**{label}**")
                            col.caption("⏳ đang vẽ…")
                        else:
                            col.markdown(f"**{label}**")
                            col.caption("— giữ nguyên")
                if err:
                    st.error(f"Gen ảnh lỗi: {err}")
                    break
                if not running and all(s in fresh for s in wanted):
                    break
                if not running:
                    break
                time.sleep(2)
            st.rerun()

    if prev:
        st.info("Ảnh mới đang chờ duyệt — ảnh hiện tại chưa bị thay.")
        pcols = st.columns(3)
        for col, (stem, label) in zip(pcols, (("cover", "Bìa"), ("thumbnail1", "Thumb 1"),
                                              ("thumbnail2", "Thumb 2"))):
            if stem in prev:
                col.image(str(prev[stem]), caption=f"{label} (mới)",
                          use_container_width=True)
        ic = st.columns([1, 1])
        if ic[0].button("✓ Dùng ảnh mới", key=f"ed_iok_{sid}", disabled=not is_admin(),
                        type="primary", use_container_width=True):
            try:
                api_post(f"/stories/{sid}/images/accept", timeout=60)
                st.toast("Đã thay ảnh.")
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi: {e}")
        if ic[1].button("✕ Bỏ, giữ ảnh cũ", key=f"ed_ino_{sid}", disabled=not is_admin(),
                        use_container_width=True):
            try:
                api_post(f"/stories/{sid}/images/discard", timeout=60)
                st.rerun()
            except Exception as e:
                st.error(f"Lỗi: {e}")


def view_library():
    page_header("Thư viện truyện", "Theo dõi tiến độ, mở bản thảo và tiếp tục các truyện đang dở.")
    toolbar = st.columns([1, 5])
    if toolbar[0].button("🔄 Làm mới", use_container_width=True):
        st.rerun()

    try:
        stories = api_get("/stories")
    except Exception as e:
        st.error(f"Không tải được danh sách: {e}")
        return

    if not stories:
        st.info("Chưa có truyện nào. Bấm Tạo truyện mới để bắt đầu.")
        return

    total = len(stories)
    complete = sum(1 for s in stories if s["phase"] == "COMPLETE")
    writing = sum(1 for s in stories if s["phase"] == "WRITING")
    planning = sum(1 for s in stories if s["phase"] == "PLANNING")
    m = st.columns(4)
    m[0].metric("Tổng truyện", total)
    m[1].metric("Đang viết", writing)
    m[2].metric("Lập kế hoạch", planning)
    m[3].metric("Hoàn thành", complete)

    f1, f2 = st.columns([2, 3])
    phase_filter = f1.multiselect("Lọc theo trạng thái", list(PHASE_META.keys()), default=[])
    query = f2.text_input("Tìm theo tên", placeholder="Gõ tên truyện...",
                          key="lib_query").strip().lower()

    # Filter on the cheap list payload FIRST. Everything below costs one API call and
    # one zip download per row, so only the page actually on screen may pay it — with
    # ninety-odd stories the old "fetch every row, then draw" cost a hundred round
    # trips and several MB of zips on every rerun.
    matches = [
        s for s in stories
        if (not phase_filter or s["phase"] in phase_filter)
        and (not query or query in s["title"].lower())
    ]
    if not matches:
        st.info("Không có truyện nào khớp bộ lọc hiện tại.")
        return

    # /stories comes back newest-first; keep that order so page 1 is the newest work.
    pages = max(1, -(-len(matches) // PAGE_SIZE))
    st.session_state.setdefault("lib_page", 1)
    # Changing the search or the filter starts again at page 1 — staying on page 5
    # after a search that matches three stories shows an empty table.
    sig = (query, tuple(sorted(phase_filter)))
    if st.session_state.get("_lib_sig") != sig:
        st.session_state._lib_sig = sig
        st.session_state.lib_page = 1
    # A narrowed filter can still leave the stored page past the end.
    page_no = min(max(1, int(st.session_state.lib_page)), pages)

    if pages > 1:
        nav = st.columns([1, 1, 3, 2])
        if nav[0].button("← Trước", disabled=page_no <= 1, use_container_width=True):
            st.session_state.lib_page = page_no - 1
            st.rerun()
        if nav[1].button("Sau →", disabled=page_no >= pages, use_container_width=True):
            st.session_state.lib_page = page_no + 1
            st.rerun()
        nav[2].markdown(
            f"<div class='row-cell muted'>Trang {page_no}/{pages} — "
            f"{len(matches)} truyện khớp, hiện {PAGE_SIZE} mỗi trang. "
            f"Tìm theo tên để thấy truyện cũ hơn.</div>",
            unsafe_allow_html=True,
        )
    elif len(matches) < len(stories):
        st.caption(f"{len(matches)} truyện khớp bộ lọc.")

    start = (page_no - 1) * PAGE_SIZE
    rows = []
    for s in matches[start:start + PAGE_SIZE]:
        try:
            d = api_get(f"/stories/{s['id']}")
        except Exception:
            d = None

        stop_requested = bool(d and d.get("stop_requested"))
        is_running = bool(d and d.get("is_running"))
        if s["phase"] == "COMPLETE":
            run_state = "Hoàn thành"
        elif stop_requested:
            run_state = "Đang dừng"
        elif is_running:
            run_state = "Đang gen"
        else:
            run_state = "Tạm dừng"

        done, total_ch = (d["chapters_done"], d["total_chapters"]) if d else (0, 0)
        words, target = ((d.get("current_words") or 0), (d.get("target_words") or 0)) if d else (0, 0)
        pct_row = done / total_ch if total_ch else 0.0

        rows.append((s, d, run_state, done, total_ch, words, target, pct_row))

    header = st.columns([2.15, .85, .9, 1.05, .72, .95, .72, 3.0], gap="medium")
    for col, label in zip(header, ("Truyện", "Truyện gốc", "Trạng thái", "Phase", "Chương", "Số từ", "Tiến độ", "Thao tác")):
        col.markdown(f"<div class='table-head'>{label}</div>", unsafe_allow_html=True)

    for s, d, run_state, done, total_ch, words, target, pct_row in rows:
        src = _html.escape(s.get("source_title") or "—") if s.get("input_type") == "REWRITE" else "—"
        zip_data = cached_story_zip(s["id"], done, d)
        c = st.columns([2.15, .85, .9, 1.05, .72, .95, .72, 3.0], gap="medium")
        c[0].markdown(f"<div class='row-cell story-cell'><div class='table-title truncate'>{_html.escape(s['title'])}</div></div>", unsafe_allow_html=True)
        c[1].markdown(f"<div class='row-cell'><div class='lib-src source-cell truncate'>{src}</div></div>", unsafe_allow_html=True)
        c[2].markdown(f"<div class='row-cell'><span class='status-pill'>{run_state}</span></div>", unsafe_allow_html=True)
        c[3].markdown(f"<div class='row-cell'>{badge(s['phase'])}</div>", unsafe_allow_html=True)
        c[4].markdown(f"<div class='row-cell'><strong>{done}/{total_ch}</strong></div>", unsafe_allow_html=True)
        c[5].markdown(f"<div class='row-cell'><span class='nowrap'><strong>{words:,}</strong> <span class='muted'>/ {target:,}</span></span></div>", unsafe_allow_html=True)
        c[6].markdown(f"<div class='row-cell'><div class='mini-progress'><span style='width:{round(pct_row * 100)}%'></span></div></div>", unsafe_allow_html=True)
        with c[7]:
            st.markdown("<div style='height:.22rem'></div>", unsafe_allow_html=True)
            act = st.columns([1, 1, 1, 1], gap="small")
            if act[0].button("Mở", key=f"open{s['id']}", use_container_width=True):
                go("detail", s["id"])
                st.rerun()
                st.stop()
            if act[1].button("Sửa", key=f"edit{s['id']}", use_container_width=True,
                             disabled=s["phase"] != "COMPLETE" or not is_admin(),
                             help="Đổi tiêu đề / ảnh — chỉ khi truyện đã hoàn thành"):
                edit_dialog(s)
            if zip_data:
                act[2].download_button("Tải", zip_data, file_name=f"{s.get('download_name') or s['slug']}.zip", mime="application/zip", key=f"zip{s['id']}", use_container_width=True)
            else:
                act[2].button("Tải", key=f"zip_disabled{s['id']}", disabled=True, use_container_width=True)
            cms_url = st.session_state.get("cms_upload_url", "").strip()
            can_export = bool(zip_data and cms_url)
            if act[3].button("CMS", key=f"cms{s['id']}", disabled=not can_export or not is_admin(), use_container_width=True,
                             help=None if cms_url else "Nhập CMS upload URL trong Cài đặt"):
                try:
                    upload_zip_to_cms(cms_url, s, zip_data)
                    st.toast(f"Đã export {s['title']} lên CMS.")
                except requests.HTTPError as e:
                    st.error(f"Export lỗi: {e.response.status_code} - {e.response.text}")
                except Exception as e:
                    st.error(f"Export lỗi: {e}")
        st.markdown("<div style='height:.35rem; border-bottom:1px solid rgba(43,54,64,.62); margin-bottom:.55rem'></div>", unsafe_allow_html=True)


def view_detail():
    sid = st.session_state.story_id
    if sid is None:
        st.warning("Chưa chọn truyện.")
        return
    try:
        d = api_get(f"/stories/{sid}")
    except Exception as e:
        st.error(f"Không tải được truyện: {e}")
        return

    running = d.get("is_running")
    stop_requested = bool(d.get("stop_requested"))
    done, total = d["chapters_done"], d["total_chapters"]
    words, target = d.get("current_words") or 0, d.get("target_words") or 0
    pct = done / total if total else 0.0
    run_label = "Đang dừng" if stop_requested else ("Đang gen" if running else "Tạm dừng")
    if d["phase"] == "COMPLETE":
        run_label = "Hoàn thành"

    nav_l, nav_r = st.columns([1, 5])
    if nav_l.button("← Thư viện", use_container_width=True):
        go("library")
        st.rerun()
    nav_r.empty()

    # For a reskin, say which original it came from — a REWRITE title is deliberately
    # unrecognisable, so without this there is nothing on the page tying it back.
    # Kept on ONE line with the slug below: an empty value on a line of its own leaves
    # a blank line inside the HTML block, and Markdown ends the block there — which
    # printed the trailing </div></div> as visible text on any story without a source.
    src_title = (d.get("source_title") or "").strip()
    source_note = (
        f' <span class="muted">(gen từ: {_html.escape(src_title)})</span>'
        if src_title and d.get("input_type") == "REWRITE" else ""
    )

    st.markdown(
        f"""
        <div class="detail-hero">
          <div class="detail-hero-top">
            <div>
              <div class="eyebrow">Chi tiết truyện</div>
              <div class="detail-hero-title">{_html.escape(d['title'])}</div>
              <div class="detail-hero-meta">
                {badge(d['phase'])}
                <span class="status-pill">{run_label}</span>
                <span class="muted">#{d['id']}</span>
                <span class="muted">{_html.escape(d['slug'])}</span>{source_note}
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    imgs = poster_images(d)
    control_col, image_col = st.columns([1.45, 1], gap="large")
    with control_col:
        st.markdown("<div class='panel-title'>Tiến trình gen</div>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div class="detail-stat-grid">
              <div class="detail-stat">
                <div class="label">Chương</div>
                <div class="value">{done}/{total}</div>
                <div class="sub">đã hoàn thành</div>
              </div>
              <div class="detail-stat">
                <div class="label">Số từ</div>
                <div class="value">{words:,}</div>
                <div class="sub">mục tiêu {target:,}</div>
              </div>
              <div class="detail-stat">
                <div class="label">Tiến độ</div>
                <div class="value">{round(pct * 100)}%</div>
                <div class="sub">{run_label}</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.progress(pct, text=f"{done}/{total} chương · {words:,}/{target:,} từ")

        if running and stop_requested:
            st.markdown(
                "<div class='status-note'>Đã gửi yêu cầu dừng. Pipeline sẽ dừng sạch sau khi hoàn tất bước hiện tại.</div>",
                unsafe_allow_html=True,
            )
        elif running:
            st.markdown(
                "<div class='status-note'>Pipeline đang chạy. Có thể dừng sau bước hiện tại nếu cần kiểm tra bản thảo.</div>",
                unsafe_allow_html=True,
            )
        elif d["phase"] != "COMPLETE":
            st.markdown(
                "<div class='status-note'>Pipeline đang tạm dừng. Bấm tiếp tục gen để chạy tiếp từ trạng thái hiện tại.</div>",
                unsafe_allow_html=True,
            )

        zip_data = cached_story_zip(sid, done, d)

        st.markdown("<div class='action-rule'></div>", unsafe_allow_html=True)
        if running and stop_requested:
            st.button("⏳ Đang dừng...", use_container_width=True, disabled=True)
        elif running:
            if st.button("⏸ Dừng gen", disabled=not is_admin(), use_container_width=True):
                try:
                    api_post(f"/stories/{sid}/stop")
                    st.toast("Đã yêu cầu dừng. Pipeline sẽ dừng sau bước hiện tại.")
                    time.sleep(1)
                    st.rerun()
                except requests.HTTPError as e:
                    st.error(f"{e.response.status_code}: {e.response.text}")
        elif d["phase"] != "COMPLETE":
            if st.button("▶️ Tiếp tục gen", type="primary", disabled=not is_admin(), use_container_width=True):
                try:
                    api_post(f"/stories/{sid}/run")
                    st.toast("Đã khởi động lại pipeline.")
                    time.sleep(1)
                    st.rerun()
                except requests.HTTPError as e:
                    st.error(f"{e.response.status_code}: {e.response.text}")

        dl = st.columns([1.35, 1.0, 1.15, 1.65], gap="medium")
        if zip_data:
            dl[0].download_button("⬇️ Tải truyện (.zip)", zip_data, file_name=f"{d.get('download_name') or d['slug']}.zip", mime="application/zip", use_container_width=True)
        else:
            dl[0].button("⬇️ Tải truyện (.zip)", use_container_width=True, disabled=True)

        if dl[1].button("🗑 Xóa truyện", disabled=bool(running) or not is_admin(), use_container_width=True, help="Dừng gen trước khi xóa truyện." if running else None):
            st.session_state["confirm_delete"] = sid
            st.rerun()

        if d["phase"] == "COMPLETE":
            try:
                r = requests.get(f"{_base()}/stories/{sid}/export", timeout=30, headers=auth_headers())
                if r.ok:
                    dl[2].download_button("⬇️ Bản thảo .md", r.content, file_name=f"{d.get('download_name') or d['slug']}.md", mime="text/markdown", use_container_width=True)
            except Exception:
                dl[2].button("⬇️ Bản thảo .md", use_container_width=True, disabled=True)
        else:
            dl[2].button("⬇️ Bản thảo .md", use_container_width=True, disabled=True)

        auto = False
        if running:
            auto_cols = st.columns([1.2, 3])
            auto = auto_cols[0].toggle("Tự cập nhật", value=True, help="Tự làm mới mỗi 5 giây khi đang gen")
            auto_cols[1].caption("Theo dõi tiến trình khi pipeline đang chạy.")

    with image_col:
        st.markdown("<div class='panel-title'>Hình ảnh</div>", unsafe_allow_html=True)
        if imgs:
            tiles = []
            for stem, label in (("cover", "Bìa"), ("thumbnail1", "Thumb 1"), ("thumbnail2", "Thumb 2")):
                if stem in imgs:
                    tiles.append(
                        f"<div class='art-tile'><img class='art-img' src='{img_src(imgs[stem])}' alt='{label}'>"
                        f"<div class='art-label'>{label}</div></div>"
                    )
                else:
                    tiles.append(f"<div class='art-tile'><div class='image-empty'>{label}<br>chưa có</div><div class='art-label'>{label}</div></div>")
            st.markdown(f"<div class='art-gallery'>{''.join(tiles)}</div>", unsafe_allow_html=True)

            dl_cols = st.columns(3, gap="medium")
            for col, (stem, label) in zip(dl_cols, (("cover", "Bìa"), ("thumbnail1", "Thumb 1"), ("thumbnail2", "Thumb 2"))):
                if stem in imgs:
                    p = imgs[stem]
                    col.download_button(
                        f"⬇️ {label}",
                        p.read_bytes(),
                        file_name=f"{d.get('download_name') or d['slug']}-{stem}{p.suffix}",
                        mime="image/" + p.suffix.lstrip(".").lower().replace("jpg", "jpeg"),
                        key=f"dl_{stem}", use_container_width=True,
                    )
                else:
                    col.button(f"⬇️ {label}", disabled=True, use_container_width=True)
        else:
            st.markdown(
                "<div class='image-empty'>Ảnh bìa và thumbnail sẽ xuất hiện ở đây khi bước gen ảnh hoàn tất.</div>",
                unsafe_allow_html=True,
            )
            if d["phase"] == "COMPLETE":
                st.caption("Chưa thấy ảnh bìa. Có thể bước gen ảnh lỗi hoặc output chưa được mount vào playground.")

        # Regenerate controls — only meaningful once images exist (COMPLETE).
        if d["phase"] == "COMPLETE":
            if st.button("🎨 Tạo lại ảnh", key="regen_toggle", disabled=not is_admin(), use_container_width=True):
                st.session_state["show_regen"] = not st.session_state.get("show_regen", False)

            if st.session_state.get("show_regen"):
                notes = st.text_area(
                    "Yêu cầu riêng cho lần tạo này",
                    key="regen_notes",
                    height=90,
                    placeholder="VD: tông vàng ấm, bối cảnh ngoài trời\n"
                                "thumb2: đứng xa nhau hơn, đừng chạm vào nhau",
                    help="Ghi đè phần phong cách bốc ngẫu nhiên (màu, bố cục, trang phục, "
                         "tư thế). Không ghi đè được thời đại, nhân vật chính ở tiền cảnh "
                         "bìa, tên nhân vật hay quy tắc chữ tiêu đề. "
                         "Mở đầu dòng bằng cover: / thumb1: / thumb2: để chỉ áp cho ảnh đó.",
                )
                sc = st.columns([1, 1])
                use_seed = sc[0].checkbox(
                    "Cố định phong cách", key="regen_use_seed",
                    help="Dùng cùng một seed thì bộ màu / bố cục / ống kính lặp lại y hệt "
                         "giữa các lần tạo. Bỏ trống thì mỗi lần một kiểu.",
                )
                seed = sc[1].number_input(
                    "Seed", min_value=0, max_value=10**9, value=12345, step=1,
                    key="regen_seed", disabled=not use_seed, label_visibility="collapsed",
                )

                def _regen(which: str, label: str):
                    body = {"which": which, "notes": st.session_state.get("regen_notes", "")}
                    if st.session_state.get("regen_use_seed"):
                        body["seed"] = int(st.session_state.get("regen_seed") or 0)
                    try:
                        with st.spinner(f"Đang tạo lại {label}… (có thể mất 1–2 phút)"):
                            api_post(f"/stories/{sid}/regenerate-images",
                                     json=body, timeout=600)
                        st.toast(f"Đã tạo lại {label}.")
                        st.rerun()
                    except requests.HTTPError as e:
                        st.error(f"{e.response.status_code}: {e.response.text}")
                    except Exception as e:
                        st.error(f"Lỗi: {e}")

                if st.button("↻ Tạo lại tất cả ảnh", key="rg_all", disabled=not is_admin(),
                             type="primary", use_container_width=True):
                    _regen("all", "tất cả ảnh")
                rc = st.columns(3, gap="small")
                if rc[0].button("Bìa", key="rg_cover", disabled=not is_admin(), use_container_width=True):
                    _regen("cover", "ảnh bìa")
                if rc[1].button("Thumb 1", key="rg_t1", disabled=not is_admin(), use_container_width=True):
                    _regen("thumbnail1", "thumbnail 1")
                if rc[2].button("Thumb 2", key="rg_t2", disabled=not is_admin(), use_container_width=True):
                    _regen("thumbnail2", "thumbnail 2")
                st.caption("Tạo lại Thumb 1/2 sẽ dùng **ảnh bìa hiện tại** làm tham chiếu "
                           "để giữ khuôn mặt nhân vật nhất quán.")

    if st.session_state.get("confirm_delete") == sid:
        st.warning(f"Xóa vĩnh viễn **{d['title']}** cùng toàn bộ chương, graph, ảnh và bản thảo.")
        cc = st.columns([1, 1, 4])
        if cc[0].button("Xác nhận xóa", type="primary"):
            try:
                requests.delete(f"{_base()}/stories/{sid}", timeout=30, headers=auth_headers()).raise_for_status()
                st.session_state.pop("confirm_delete", None)
                st.toast("Đã xóa truyện.")
                go("library")
                st.rerun()
            except Exception as e:
                st.error(f"Xóa thất bại: {e}")
        if cc[1].button("Hủy"):
            st.session_state.pop("confirm_delete", None)
            st.rerun()

    st.markdown(
        "<div class='reader-head'><div><div class='eyebrow'>Bản thảo</div><div class='reader-title'>Nội dung truyện</div></div></div>",
        unsafe_allow_html=True,
    )

    if not done:
        st.markdown(
            "<div class='soft-panel'><span class='muted'>Chưa có chương nào hoàn thành. Bản thảo sẽ xuất hiện ở đây khi chương đầu tiên viết xong.</span></div>",
            unsafe_allow_html=True,
        )
    else:
        # Fill the tabs in the order st.tabs() declared them. Streamlit assigns each
        # `with` block to a tab by the order the blocks RUN, not by which variable
        # names it — so filling tab_full first put the manuscript under the "Tóm tắt"
        # label and pushed every tab's content one place along.
        tab_sum, tab_full, tab_ch = st.tabs(["📝 Tóm tắt", "📄 Toàn bộ", "📑 Theo chương"])
        with tab_sum:
            summ = output_text(d["slug"], "summarize.txt")
            if summ and summ.strip():
                st.markdown(f"<div class='manuscript'>{_html.escape(summ)}</div>", unsafe_allow_html=True)
            elif d["phase"] == "COMPLETE":
                st.caption("Không tìm thấy summarize.txt (output chưa mount hoặc bước tổng hợp lỗi).")
            else:
                st.caption("Tóm tắt được tạo khi truyện hoàn thành (bước tổng hợp cuối).")
        with tab_full:
            try:
                man = api_get(f"/stories/{sid}/manuscript")
                body = man.get("manuscript") or ""
                if body.strip():
                    st.markdown(f"<div class='manuscript'>{body}</div>", unsafe_allow_html=True)
                else:
                    st.caption("Chưa có chương nào hoàn thành.")
            except Exception as e:
                st.caption(f"Chưa đọc được bản thảo: {e}")
        with tab_ch:
            n = st.selectbox("Chọn chương", list(range(1, done + 1)))
            try:
                ch = api_get(f"/stories/{sid}/chapters/{n}")
                st.subheader(ch.get("title") or f"Chương {n}")
                st.caption(f"{ch.get('word_count') or 0:,} từ · {ch.get('status')}")
                st.markdown(f"<div class='manuscript'>{ch.get('content') or ''}</div>", unsafe_allow_html=True)
            except Exception as e:
                st.caption(f"Không tải được chương: {e}")

    # Poll by sleeping, then rerunning. Do NOT replace this with an
    # `@st.fragment(run_every=n)` that calls `st.rerun()`: a fragment's body executes
    # immediately on the call as well as on the timer, so the rerun fires instantly and
    # the app spins in a tight loop hammering the API several times a second.
    if stop_requested:
        time.sleep(3)
        st.rerun()
    if auto and running:
        time.sleep(5)
        st.rerun()


def view_settings():
    page_header("Cài đặt", "Thiết lập kết nối playground và giá trị mặc định khi tạo truyện.")

    st.subheader("Kết nối API")
    new_base = st.text_input("Địa chỉ API", value=st.session_state.api_base, help="Mặc định http://localhost:8001/api.")
    c = st.columns(2)
    if c[0].button("Lưu", disabled=not is_admin()):
        st.session_state.api_base = new_base
        st.toast("Đã lưu địa chỉ API.")
    if c[1].button("Kiểm tra kết nối"):
        st.success("Kết nối OK") if api_online() else st.error("Không kết nối được")

    st.divider()
    st.subheader("Export CMS")
    cms_url = st.text_input(
        "CMS upload URL",
        value=st.session_state.cms_upload_url,
        placeholder="https://cms.example.com/api/upload",
        help="Khi bấm Export ở Thư viện, playground sẽ POST file .zip lên URL này bằng multipart field `file`.",
    )
    if st.button("Lưu CMS URL", disabled=not is_admin()):
        try:
            saved = api_put("/settings/cms_upload_url", json={"value": cms_url})
            st.session_state.cms_upload_url = saved.get("value", "")
            st.session_state._settings_loaded = True
            st.toast("Đã lưu CMS upload URL vào DB.")
        except Exception as e:
            st.error(f"Không lưu được CMS URL: {e}")

    st.divider()
    st.subheader("Mặc định khi tạo truyện")
    dl = st.selectbox(
        "Ngôn ngữ mặc định",
        LANGUAGES,
        index=LANGUAGES.index(st.session_state.default_language) if st.session_state.default_language in LANGUAGES else 0,
    )
    if st.button("Lưu mặc định", disabled=not is_admin()):
        st.session_state.default_language = dl
        st.toast("Đã lưu.")

    st.divider()
    st.subheader("Cấu hình mô hình")
    st.info(
        "Model cho từng agent và nhà cung cấp LLM được cấu hình ở backend qua `.env` "
        "(`MODEL_*`, `LLM_PROVIDER`). Playground chỉ điều khiển workflow để tránh lệch cấu hình giữa người dùng."
    )


PAGE = st.session_state.page
_VIEWS = {
    "create": view_create,
    "launching": view_launching,
    "detail": view_detail,
    "settings": view_settings,
    "library": view_library,
}

# Each page renders inside a container KEYED BY THE PAGE NAME. Without the key,
# Streamlit matches elements between runs by their position in the tree, and since
# every page here is built from the same st.columns/st.markdown shapes it kept the
# previous page's nodes alive instead of replacing them — which is why the library's
# table was still on screen after opening a story. A different key makes the old
# container a different element, so it is discarded outright.
with st.container(key=f"page_{PAGE}"):
    _VIEWS.get(PAGE, view_library)()
