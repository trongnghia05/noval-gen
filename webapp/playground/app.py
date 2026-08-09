"""Novel-Gen playground - Streamlit control room for the FastAPI backend.

Run: streamlit run app.py
"""
from __future__ import annotations

import html as _html
import io
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

st.set_page_config(page_title="Xưởng Gen Truyện", page_icon="📖", layout="wide")


def _base() -> str:
    return st.session_state.get("api_base", DEFAULT_API).rstrip("/")


def api_get(path: str, **kw):
    r = requests.get(f"{_base()}{path}", timeout=kw.pop("timeout", 30), **kw)
    r.raise_for_status()
    return r.json()


def api_post(path: str, json=None, timeout=180):
    r = requests.post(f"{_base()}{path}", json=json, timeout=timeout)
    r.raise_for_status()
    return r.json()


def api_online() -> bool:
    try:
        requests.get(f"{_base()}/stories", timeout=4).raise_for_status()
        return True
    except Exception:
        return False


_IMG_EXTS = ("webp", "png", "jpg", "jpeg")


def poster_images(slug: str) -> dict[str, Path]:
    img_dir = OUTPUT_DIR / slug / "image"
    out: dict[str, Path] = {}
    if img_dir.is_dir():
        for stem in ("cover", "thumbnail1", "thumbnail2"):
            for ext in _IMG_EXTS:
                p = img_dir / f"{stem}.{ext}"
                if p.exists():
                    out[stem] = p
                    break
    return out


def cover_for(slug: str) -> Path | None:
    return poster_images(slug).get("cover")


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


def merge_chapter_zip(data: bytes) -> tuple[str, dict]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    found: dict[int, dict] = {}
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
    report = {
        "count": len(parts),
        "words": total_words,
        "wpc": round(total_words / len(parts)) if parts else 0,
        "missing": missing,
        "empty": empty,
        "from_html": from_html,
        "range": (nums[0], nums[-1]),
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
      }
      .table-cell {
        min-height: 58px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        padding: .45rem .35rem;
        border-bottom: 1px solid rgba(43,54,64,.78);
      }
      .table-title {font-weight: 800; color: var(--text); line-height: 1.25;}
      .status-pill {
        display: inline-flex;
        width: fit-content;
        border: 1px solid var(--line);
        border-radius: 999px;
        padding: 2px 9px;
        font-size: 12px;
        font-weight: 750;
        color: var(--text);
        background: rgba(21,27,34,.72);
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
      .library-table .title-col {width: 31%;}
      .library-table .state-col {width: 12%;}
      .library-table .phase-col {width: 12%;}
      .library-table .small-col {width: 10%;}
      .library-table .progress-col {width: 18%;}
      .library-table .action-col {width: 8%;}
      .lib-title {font-weight: 850; color: var(--text); line-height: 1.25;}
      .lib-sub {color: var(--muted); font-size: .8rem; margin-top: .2rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
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
      .mini-progress {
        height: 8px;
        border-radius: 999px;
        background: #111820;
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
      .badge {
        display: inline-flex;
        align-items: center;
        gap: .35rem;
        min-height: 24px;
        padding: 2px 10px;
        border-radius: 999px;
        font-size: 12px;
        font-weight: 750;
        border: 1px solid;
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
st.session_state.setdefault("default_language", "Tiếng Việt")
st.session_state.setdefault("launch_payload", None)

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

    if st.button("✍️  Tạo truyện mới", use_container_width=True, type="primary"):
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


def view_create():
    page_header(
        "Tạo truyện mới",
        "Nhập chất liệu, chọn độ dài, rồi để hệ thống tự đặt tên, lập kế hoạch, viết chương và tạo ảnh bìa.",
    )

    itype = st.radio(
        "Kiểu nội dung",
        list(INPUT_TYPES.keys()),
        captions=list(INPUT_TYPES.values()),
        horizontal=True,
    )

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
                elif msg and msg[0] == "err":
                    st.error(f"Không gộp được .zip: {msg[1]}")
            else:
                up = st.file_uploader("Tải truyện gốc (.md / .txt)", type=["md", "txt"])
                if up is not None:
                    sig = ("file", up.name, up.size)
                    if st.session_state.get("_src_sig") != sig:
                        st.session_state["_src_sig"] = sig
                        st.session_state["src_content"] = up.getvalue().decode("utf-8", "ignore")
                        st.session_state.pop("_src_msg", None)

            st.session_state.setdefault("src_content", "")
            content = st.text_area(
                "Nội dung truyện gốc",
                key="src_content",
                height=340,
                placeholder="Dán truyện gốc, hoặc tải file / .zip ở trên...",
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
    if st.button("🚀  Tạo và bắt đầu gen", type="primary", disabled=disabled, use_container_width=True):
        payload = {"language": language, "input_type": itype, "genre": genre or None, "content": content}
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
    query = f2.text_input("Tìm theo tên", placeholder="Gõ tên truyện...").strip().lower()

    rows = []
    shown = 0
    for s in reversed(stories):
        if phase_filter and s["phase"] not in phase_filter:
            continue
        if query and query not in s["title"].lower():
            continue
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

        rows.append((s, run_state, done, total_ch, words, target, pct_row))
        shown += 1

    if not shown:
        st.info("Không có truyện nào khớp bộ lọc hiện tại.")
        return

    table_rows = []
    for s, run_state, done, total_ch, words, target, pct_row in rows:
        table_rows.append(
            "<tr>"
            f"<td><div class='lib-title'>{_html.escape(s['title'])}</div><div class='lib-sub'>#{s['id']} · {_html.escape(s['slug'])}</div></td>"
            f"<td><span class='status-pill'>{run_state}</span></td>"
            f"<td>{badge(s['phase'])}</td>"
            f"<td><strong>{done}/{total_ch}</strong></td>"
            f"<td><span class='nowrap'><strong>{words:,}</strong> <span class='muted'>/ {target:,}</span></span></td>"
            f"<td><div class='mini-progress'><span style='width:{round(pct_row * 100)}%'></span></div></td>"
            f"<td><a class='open-link' href='?story_id={s['id']}'>Mở</a></td>"
            "</tr>"
        )

    st.markdown(
        "<table class='library-table'>"
        "<colgroup><col class='title-col'><col class='state-col'><col class='phase-col'><col class='small-col'><col class='small-col'><col class='progress-col'><col class='action-col'></colgroup>"
        "<thead><tr><th>Truyện</th><th>Trạng thái</th><th>Phase</th><th>Chương</th><th>Số từ</th><th>Tiến độ</th><th></th></tr></thead>"
        f"<tbody>{''.join(table_rows)}</tbody></table>",
        unsafe_allow_html=True,
    )


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

    st.markdown(
        f"""
        <div class="detail-shell">
          <div class="eyebrow">Chi tiết truyện</div>
          <div class="detail-title">{_html.escape(d['title'])}</div>
          <div class="detail-meta">
            {badge(d['phase'])}
            <span>#{d['id']}</span>
            <span>{_html.escape(d['slug'])}</span>
            <span>{run_label}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    imgs = poster_images(d["slug"])
    left, right = st.columns([1.65, 1.35], gap="large")
    with left:
        st.markdown(
            f"""
            <div class="stat-grid">
              <div class="stat-card">
                <div class="stat-label">Chương</div>
                <div class="stat-value">{done}/{total}</div>
                <div class="stat-sub">đã hoàn thành</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Số từ</div>
                <div class="stat-value">{words:,}</div>
                <div class="stat-sub">mục tiêu {target:,}</div>
              </div>
              <div class="stat-card">
                <div class="stat-label">Tiến độ</div>
                <div class="stat-value">{round(pct * 100)}%</div>
                <div class="stat-sub">{run_label}</div>
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

        st.markdown("<div style='height:.35rem'></div>", unsafe_allow_html=True)
        a = st.columns([1.35, 1.05, 1.05, 1], gap="medium")
        if running and stop_requested:
            a[0].button("⏳ Đang dừng...", use_container_width=True, disabled=True)
        elif running:
            if a[0].button("⏸ Dừng gen", use_container_width=True):
                try:
                    api_post(f"/stories/{sid}/stop")
                    st.toast("Đã yêu cầu dừng. Pipeline sẽ dừng sau bước hiện tại.")
                    time.sleep(1)
                    st.rerun()
                except requests.HTTPError as e:
                    st.error(f"{e.response.status_code}: {e.response.text}")
        elif d["phase"] != "COMPLETE":
            if a[0].button("▶️ Tiếp tục gen", type="primary", use_container_width=True):
                try:
                    api_post(f"/stories/{sid}/run")
                    st.toast("Đã khởi động lại pipeline.")
                    time.sleep(1)
                    st.rerun()
                except requests.HTTPError as e:
                    st.error(f"{e.response.status_code}: {e.response.text}")
        else:
            a[0].button("✓ Đã hoàn thành", use_container_width=True, disabled=True)

        if d["phase"] == "COMPLETE":
            try:
                r = requests.get(f"{_base()}/stories/{sid}/export", timeout=30)
                if r.ok:
                    a[1].download_button("⬇️ Tải bản thảo", r.content, file_name=f"{d['slug']}.md", mime="text/markdown", use_container_width=True)
            except Exception:
                a[1].button("⬇️ Tải bản thảo", use_container_width=True, disabled=True)
        else:
            a[1].button("⬇️ Tải bản thảo", use_container_width=True, disabled=True)

        if a[2].button("🗑 Xóa", disabled=bool(running), use_container_width=True, help="Dừng gen trước khi xóa"):
            st.session_state["confirm_delete"] = sid
            st.rerun()

        auto = a[3].toggle("Tự cập nhật", value=bool(running), help="Tự làm mới mỗi 5 giây khi đang gen")

    with right:
        st.markdown("<div class='eyebrow'>Hình ảnh</div>", unsafe_allow_html=True)
        if imgs:
            img_cols = st.columns(3, gap="medium")
            for col, (stem, label) in zip(img_cols, (("cover", "Bìa"), ("thumbnail1", "Thumb 1"), ("thumbnail2", "Thumb 2"))):
                if stem in imgs:
                    col.image(str(imgs[stem]), caption=label, use_container_width=True)
                else:
                    col.markdown(f"<div class='empty-cover'>{label}<br>chưa có</div>", unsafe_allow_html=True)
        else:
            st.markdown(
                "<div class='empty-cover'>Ảnh bìa và thumbnail sẽ xuất hiện ở đây khi bước gen ảnh hoàn tất.</div>",
                unsafe_allow_html=True,
            )
            if d["phase"] == "COMPLETE":
                st.caption("Chưa thấy ảnh bìa. Có thể bước gen ảnh lỗi hoặc output chưa được mount vào playground.")

    if st.session_state.get("confirm_delete") == sid:
        st.warning(f"Xóa vĩnh viễn **{d['title']}** cùng toàn bộ chương, graph, ảnh và bản thảo.")
        cc = st.columns([1, 1, 4])
        if cc[0].button("Xác nhận xóa", type="primary"):
            try:
                requests.delete(f"{_base()}/stories/{sid}", timeout=30).raise_for_status()
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
        tab_full, tab_ch = st.tabs(["📄 Toàn bộ", "📑 Theo chương"])
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
    if c[0].button("Lưu"):
        st.session_state.api_base = new_base
        st.toast("Đã lưu địa chỉ API.")
    if c[1].button("Kiểm tra kết nối"):
        st.success("Kết nối OK") if api_online() else st.error("Không kết nối được")

    st.divider()
    st.subheader("Mặc định khi tạo truyện")
    dl = st.selectbox(
        "Ngôn ngữ mặc định",
        LANGUAGES,
        index=LANGUAGES.index(st.session_state.default_language) if st.session_state.default_language in LANGUAGES else 0,
    )
    if st.button("Lưu mặc định"):
        st.session_state.default_language = dl
        st.toast("Đã lưu.")

    st.divider()
    st.subheader("Cấu hình mô hình")
    st.info(
        "Model cho từng agent và nhà cung cấp LLM được cấu hình ở backend qua `.env` "
        "(`MODEL_*`, `LLM_PROVIDER`). Playground chỉ điều khiển workflow để tránh lệch cấu hình giữa người dùng."
    )


PAGE = st.session_state.page
if PAGE == "create":
    view_create()
elif PAGE == "launching":
    view_launching()
elif PAGE == "detail":
    view_detail()
elif PAGE == "settings":
    view_settings()
else:
    view_library()
