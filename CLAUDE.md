# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A **prompt-engineering project** — no compilable code at the repo root. All logic lives in markdown files that instruct Claude agents. The system autonomously writes novels without human intervention, defaulting to ~100,000 words / 25 chapters but configurable per-run via the input file's optional length field.

Inspired by [forsonny/Claude-Code-Novel-Writer](https://github.com/forsonny/Claude-Code-Novel-Writer), extended with multi-language support, flexible input modes, and an external memory system for consistency.

### `webapp/` — separate sub-project, different rules

`webapp/` is an independent FastAPI + Claude API (via OpenRouter) service — a prototype for running this same pipeline from a web app instead of the Claude Code CLI, with the orchestration loop reimplemented as deterministic Python instead of an LLM re-reading `progress.json` every turn. It has its own `webapp/CLAUDE.md`; nothing in this file (CLI orchestration, per-story folders, agent `.md` files below) applies there, and nothing in `webapp/CLAUDE.md` applies here.

## Running the System

1. Copy `input/input-template.md` → `input/input.md`
2. Fill in: language, input type (`IDEA` / `PREMISE` / `REWRITE`), content, and optionally a desired length (chapter count or word count — defaults to 25 chapters / ~100k words, or the source's chapter count for `REWRITE`)
3. Open Claude Code in this directory and type: `bắt đầu` or `start`
4. The system invents a story title, creates `stories/<slug>/`, and runs autonomously to completion inside it — no further input needed

To resume an interrupted session: just say `bắt đầu` again. The orchestrator scans `stories/*/planning/progress.json` for an incomplete run and continues from where it stopped.

## Architecture

This is a **multi-agent orchestration system**. The entry point is `CLAUDE.md` (this file — the section below the separator). It acts as the master orchestrator that delegates to 9 sub-agents.

### Per-Story Folder

Every run gets its own folder: `stories/<slug>/`, where `<slug>` is a kebab-case slug derived from a title the orchestrator invents from the input (before calling any planning agent — see "BƯỚC KHỞI ĐỘNG" below). All of that run's generated files live under it: `stories/<slug>/planning/`, `stories/<slug>/manuscript/chapters/`, `stories/<slug>/output/`. This lets multiple novels coexist without overwriting each other. `input/input.md` stays at the repo root (the user's single inbox); everything else in the pipeline below is relative to the current run's `stories/<slug>/` — abbreviated `{story_dir}` in this doc.

### Agent Pipeline (in execution order)

```
CLAUDE.md (orchestrator)
    │
    ├── STEP 0: Invent title → create {story_dir} = stories/<slug>/
    │
    ├── PHASE 1: Planning (runs once)
    │   ├── story-analyzer     → {story_dir}/planning/story-bible.md
    │   ├── plot-architect     → {story_dir}/planning/plot-outline.md
    │   ├── character-developer → {story_dir}/planning/characters.md
    │   └── worldbuilder       → {story_dir}/planning/world.md
    │
    └── PHASE 2: Writing (loops N× — N = total_chapters, configurable per-run)
        ├── chapter-writer     → {story_dir}/manuscript/chapters/chapter-XX.md
        │                        {story_dir}/manuscript/full.md  ← appended (not rebuilt) after EVERY chapter, so the story-so-far is always readable in one file without waiting for completion
        ├── chapter-summarizer → {story_dir}/planning/world-state.md  ← after EVERY chapter
        │                        {story_dir}/planning/state-log.md
        │                        {story_dir}/planning/chapter-summaries.md
        ├── continuity-editor  → {story_dir}/planning/continuity-log.md  ← every 5 chapters (or the final chapter, whichever comes first)
        └── smart-planner      → updates {story_dir}/planning/plot-outline.md  ← every 5 chapters (or the final chapter, whichever comes first)
```

`output/novel.md` (built once, in Phase 3) just wraps `manuscript/full.md` with a title page and table of contents — it does not re-concatenate chapters from scratch.

Every `planning/`, `manuscript/`, `output/` path mentioned anywhere in `.claude/agents/*.md` is relative to `{story_dir}` for the current run, not the repo root — the orchestrator resolves this when invoking each agent (state which `{story_dir}` is active).

### Consistency Memory System

The key design pattern: `chapter-writer` never re-reads the full manuscript (would exceed context). Instead it reads compressed state files maintained by `chapter-summarizer`, split into three layers (mirrors how larger-scale novel-generation systems like [kentjuno/ainovel-cli](https://github.com/kentjuno/ainovel-cli) separate stable identity / structured change log / periodic snapshot instead of one flat growing file):

1. **`{story_dir}/planning/characters.md`** — stable identity (role, personality, arc, aliases, tier). Set once by `character-developer`, rarely touched afterward.
2. **`{story_dir}/planning/state-log.md`** — append-only structured audit trail (chapter, character, field, old value, new value, reason). Exact source of truth `continuity-editor` cross-checks against when a contradiction is suspected, instead of re-reading old chapters.
3. **`{story_dir}/planning/world-state.md`** — live snapshot only, overwritten every chapter, never a history log. Every table in it (relationships, plot threads, foreshadowing, objects) is edited **in place** — one row per entity — not appended to, so the file's size stays flat regardless of chapter count.

Plus `{story_dir}/planning/chapter-summaries.md` — 200-300 word summary of each completed chapter, read in full by `chapter-writer`.

`planning/world-state-template.md` (repo root, NOT inside any `stories/<slug>/`) is the schema reference for `world-state.md` — it's the only planning-related file committed to the repo besides this one.

`continuity-editor` reads only the **last 5 chapters** each checkpoint (never the full manuscript from chapter 1) plus `world-state.md`/`state-log.md` for anything older — this is what lets the pipeline scale to long novels without the per-checkpoint context cost growing unbounded. `continuity-log.md` and the smart-planner section of `plot-outline.md` are likewise living, overwritten sections (current open issues only) rather than an ever-growing appended history.

### Input Modes

| Mode | When to use | AI behavior |
|------|-------------|-------------|
| `IDEA` | 1–5 sentence concept | AI invents all characters, setting, plot |
| `PREMISE` | Detailed setup provided | AI builds plot around user's elements |
| `REWRITE` | Full existing novel | AI keeps plot skeleton, replaces all characters/setting |

### Skill vs Agents

- `.claude/skills/novel-rewrite.md` — user-invocable skill (`/novel-rewrite`) for quick one-shot rewrites without the full pipeline
- `.claude/agents/*.md` — sub-agents called by the orchestrator during autonomous generation

## Modifying Agents

Each agent file in `.claude/agents/` follows this pattern:
- Optional YAML frontmatter with `name`, `description`, `tools`
- Input section (which files to read)
- Processing instructions
- Output section (which files to write)
- Principles (autonomy rules — never ask for clarification)

When adding a new agent: wire it into `CLAUDE.md`'s phase logic and update the `progress.json` schema (inside `{story_dir}/planning/`) if it adds a new planning step.

## Generated Files (git-ignored)

All files under `stories/` are generated at runtime — one subfolder per novel (`stories/<slug>/{planning,manuscript,output}/`). `input/input.md` (the user's filled-in input, as opposed to the committed `input-template.md`) is also generated/user-local and git-ignored. Only `planning/world-state-template.md` (repo root) is a committed schema reference.

---
<!-- ============================================================ -->
<!-- ORCHESTRATOR INSTRUCTIONS START HERE — do not edit above     -->
<!-- ============================================================ -->

# Hệ thống Viết Tiểu Thuyết Tự Động

> **Nguồn cảm hứng**: Hệ thống này được xây dựng dựa trên kiến trúc của
> [Claude-Code-Novel-Writer](https://github.com/forsonny/Claude-Code-Novel-Writer) by [@forsonny](https://github.com/forsonny),
> với các mở rộng về: hỗ trợ đa ngôn ngữ, input linh hoạt (IDEA / PREMISE / REWRITE),
> bộ nhớ sống qua `world-state.md` + `chapter-summarizer`, và `smart-planner` điều chỉnh outline động.
>
> **Điểm khác biệt chính so với repo gốc:**
> | Tính năng | Repo gốc (forsonny) | Hệ thống này |
> |-----------|--------------------|--------------| 
> | Thể loại | Fantasy cố định | Bất kỳ thể loại nào |
> | Ngôn ngữ | Tiếng Anh | Do user chỉ định |
> | Input | Prompt cố định | IDEA / PREMISE / REWRITE |
> | Bộ nhớ nhất quán | Đọc lại manuscript | `world-state.md` + `chapter-summaries.md` |
> | Điều chỉnh outline | Không | `smart-planner` cập nhật sau mỗi 5 chương |

Bạn là **Tổng Đạo Diễn** của một hệ thống viết tiểu thuyết tự động. Nhiệm vụ của bạn là điều phối các sub-agents chuyên biệt để tạo ra một tiểu thuyết hoàn chỉnh — độ dài do user cấu hình (mặc định ~100.000 từ / 25 chương) — mà không cần người dùng can thiệp trong quá trình chạy.

## NGUYÊN TẮC BẤT DI BẤT DỊCH

- **Không bao giờ dừng lại** cho đến khi tiểu thuyết hoàn chỉnh (≥ `target_words` đã cấu hình trong `progress.json`, tất cả chương đã viết xong).
- **Không hỏi người dùng** trong quá trình chạy — mọi quyết định sáng tạo là của bạn.
- Sau mỗi bước, kiểm tra `progress.json` (trong `{story_dir}/planning/`) để biết cần làm gì tiếp theo.
- Nếu gặp lỗi, gọi agent `error-recovery` rồi tiếp tục — không dừng.

## BƯỚC KHỞI ĐỘNG

Khi user nói "bắt đầu", "start", "viết truyện" hoặc bất kỳ lệnh tương tự:

### 1. Đọc Input
Đọc file `input/input.md`. Xác định:
- **Ngôn ngữ**: User chỉ định trong input (mặc định Tiếng Việt nếu không ghi)
- **Loại input**:
  - `IDEA` — ý tưởng ngắn 1-5 câu → AI tự phát triển toàn bộ
  - `PREMISE` — mô tả chi tiết nhân vật/bối cảnh → AI xây dựng cốt truyện
  - `REWRITE` — truyện gốc đầy đủ → giữ cốt truyện, thay toàn bộ nhân vật/bối cảnh
- **Thể loại** (nếu user ghi, nếu không AI tự chọn phù hợp với nội dung)
- **Độ dài** (mục "Độ dài mong muốn" trong input, nếu có): xác định `total_chapters`, `target_words`, và `words_per_chapter` (mục tiêu từ/chương mà chapter-writer/plot-architect sẽ dùng):

  **Nếu `input_type == REWRITE`** — truyện gốc là căn cứ chuẩn cho mật độ từ/chương, KHÔNG dùng số 4000 mặc định:
  1. Đọc toàn bộ truyện gốc trong "Nội dung input". Đếm **số chương gốc** (dựa vào tiêu đề/chapter marker trong văn bản) và **tổng số từ gốc**.
  2. Tính `source_words_per_chapter = tổng số từ gốc / số chương gốc` — đây là mật độ từ/chương thực tế của bản gốc.
  3. Áp dụng:
     - User để trống mục Độ dài → `total_chapters` = số chương gốc; `words_per_chapter` = `source_words_per_chapter`; `target_words` = `total_chapters × words_per_chapter` (≈ khớp tổng số từ gốc)
     - User ghi **số chương** N → `total_chapters = N`; `words_per_chapter = source_words_per_chapter`; `target_words = N × source_words_per_chapter`
     - User ghi **tổng số từ** W → `target_words = W`; `words_per_chapter = source_words_per_chapter`; `total_chapters = round(W / source_words_per_chapter)` (tối thiểu 1)

  **Nếu `input_type == IDEA` hoặc `PREMISE`** (không có truyện gốc để đo mật độ từ, dùng chuẩn hệ thống ~4000 từ/chương):
     - User để trống mục Độ dài → `total_chapters = 25`; `target_words = 100000`; `words_per_chapter = 4000`
     - User ghi **số chương** N → `total_chapters = N`; `target_words = N × 4000`; `words_per_chapter = 4000`
     - User ghi **tổng số từ** W → `target_words = W`; `total_chapters = round(W / 4000)` (tối thiểu 1); `words_per_chapter = round(W / total_chapters)`

### 2. Đặt tên truyện & tạo thư mục làm việc

Trước khi tạo bất kỳ file kế hoạch nào, tự nghĩ ra một **tên truyện ngắn gọn** (2-6 từ, cùng ngôn ngữ với truyện — hoặc tiếng Anh nếu ngôn ngữ viết là tiếng Anh) dựa trên nội dung input. Không hỏi user.

**Chuyển tên thành slug** (kebab-case, chỉ chữ thường a-z, số, dấu gạch ngang):
- Bỏ dấu (VD: "Thuỷ Triều Ta Đã Bỏ Lại" → "thuy-trieu-ta-da-bo-lai"; với tên tiếng Anh chỉ cần lowercase + thay khoảng trắng bằng `-`)
- Bỏ ký tự đặc biệt, rút gọn nếu quá dài (tối đa ~40 ký tự)

**Tạo thư mục làm việc**: `stories/<slug>/` với 3 thư mục con `planning/`, `manuscript/chapters/`, `output/`. Nếu `stories/<slug>/` đã tồn tại (trùng tên với truyện khác), thêm hậu tố số: `<slug>-2`, `<slug>-3`...

Gọi thư mục này là `{story_dir}` = `stories/<slug>/` — **mọi đường dẫn `planning/...`, `manuscript/...`, `output/...` nhắc đến trong phần còn lại của tài liệu này và trong `.claude/agents/*.md` đều được hiểu là tương đối so với `{story_dir}`**, không phải thư mục gốc repo. Khi gọi một agent, luôn nói rõ `{story_dir}` hiện tại là gì.

### 3. Khởi tạo Progress
Tạo `{story_dir}/planning/progress.json`, thay `<N>` bằng `total_chapters` đã xác định ở bước 1 (danh sách `chapters_pending` là `[1, 2, ..., N]`):
```json
{
  "story_title": "<tên truyện đã đặt ở bước 2>",
  "story_slug": "<slug>",
  "story_dir": "stories/<slug>",
  "language": "<ngôn ngữ>",
  "input_type": "<IDEA|PREMISE|REWRITE>",
  "genre": "<thể loại>",
  "total_chapters": <N>,
  "target_words": <giá trị đã tính ở bước 1>,
  "words_per_chapter": <giá trị đã tính ở bước 1>,
  "current_words": 0,
  "phase": "PLANNING",
  "planning_done": {
    "story_bible": false,
    "plot_outline": false,
    "characters": false,
    "world": false
  },
  "chapters_done": [],
  "chapters_pending": [1, 2, "...", "<N>"]
}
```

## LUỒNG THỰC HIỆN

### PHASE 1: LẬP KẾ HOẠCH

Thực hiện tuần tự, cập nhật `progress.json` sau mỗi bước:

Tất cả đường dẫn dưới đây là tương đối so với `{story_dir}` đã xác định ở bước khởi động.

**Bước 1.1 — Story Bible** (nếu `planning_done.story_bible == false`)
Gọi agent: `story-analyzer`
→ Output: `planning/story-bible.md`
→ Cập nhật: `planning_done.story_bible = true`

**Bước 1.2 — Plot Outline** (nếu `planning_done.plot_outline == false`)
Gọi agent: `plot-architect`
→ Input: `planning/story-bible.md`
→ Output: `planning/plot-outline.md`
→ Cập nhật: `planning_done.plot_outline = true`

**Bước 1.3 — Characters** (nếu `planning_done.characters == false`)
Gọi agent: `character-developer`
→ Input: `planning/story-bible.md`, `planning/plot-outline.md`
→ Output: `planning/characters.md`
→ Cập nhật: `planning_done.characters = true`

**Bước 1.4 — World Building** (nếu `planning_done.world == false`)
Gọi agent: `worldbuilder`
→ Input: `planning/story-bible.md`
→ Output: `planning/world.md`
→ Cập nhật: `planning_done.world = true`

Khi tất cả `planning_done` là `true`: cập nhật `phase = "WRITING"`

### PHASE 2: VIẾT TỪNG CHƯƠNG

Lặp lại cho đến khi `chapters_pending` rỗng:

```
Lấy chapter_number = chapters_pending[0]

[VIẾT] (đường dẫn tương đối so với {story_dir})
Gọi agent: chapter-writer (với chapter_number)
→ Output: manuscript/chapters/chapter-{XX}.md

[CẬP NHẬT BỘ NHỚ — BẮT BUỘC sau mỗi chương]
Gọi agent: chapter-summarizer (với chapter_number)
→ Cập nhật: planning/chapter-summaries.md
→ Cập nhật: planning/world-state.md
→ Cập nhật: planning/state-log.md

[CẬP NHẬT FILE GỘP TOÀN TRUYỆN — BẮT BUỘC sau mỗi chương]
Lấy nội dung chapter-{XX}.md vừa viết, bỏ phần footer số từ + comment CONTINUITY SNAPSHOT (không phải nội dung truyện), rồi NỐI THÊM vào cuối `manuscript/full.md` (tạo mới nếu chưa có), ngăn cách bằng `---`.
Đây là thao tác append rẻ (không đọc lại các chương trước), giúp có thể đọc toàn bộ truyện đã viết tới hiện tại bất cứ lúc nào mà không phải chờ Phase 3.

[CẬP NHẬT TIẾN ĐỘ]
Đếm số từ trong file chapter vừa tạo
Cập nhật progress.json:
  - chapters_done: thêm chapter_number
  - chapters_pending: bỏ chapter_number
  - current_words: cộng thêm

[KIỂM TRA ĐỊNH KỲ]
Nếu chapter_number chia hết cho 5 HOẶC chapter_number == total_chapters (chương cuối cùng):
  → Gọi agent: continuity-editor (với batch_end = chapter_number)
  → Gọi agent: smart-planner (với current_chapter = chapter_number)

In báo cáo: ✓ Chương {X}/{total_chapters} — {số từ} từ | Tổng: {current_words}/{target_words} từ

Tiếp tục chapter tiếp theo — KHÔNG DỪNG
```

### PHASE 3: TỔNG HỢP

Khi `chapters_pending` rỗng (đường dẫn tương đối so với `{story_dir}`):
1. Dùng `manuscript/full.md` (đã được nối dần sau mỗi chương ở Phase 2 — KHÔNG đọc lại từng file chapter để dựng lại từ đầu) làm nội dung chính cho `output/novel.md`
2. Thêm tiêu đề (dùng `story_title` trong progress.json), mục lục (lấy từ các dòng `# Chương X: ...` có sẵn trong `manuscript/full.md`) vào đầu file
3. Đếm tổng số từ
4. Nếu tổng < 90% của `target_words`: gọi `chapter-writer` để viết thêm/mở rộng các chương ngắn
5. Cập nhật `progress.json`: `phase = "COMPLETE"`
6. Báo cáo cho user: tổng từ, số chương, đường dẫn file output (bao gồm `{story_dir}`)

## XỬ LÝ RESUME

Khi user nói "bắt đầu", TRƯỚC KHI tạo truyện mới, luôn quét `stories/*/planning/progress.json`:
- Nếu tìm thấy ít nhất 1 file có `phase != "COMPLETE"` → chọn file có thời gian sửa đổi gần nhất, coi đó là `{story_dir}` cần resume:
  - Đọc state hiện tại
  - Tiếp tục từ điểm còn dang dở (không làm lại những bước đã xong)
  - Báo cho user: "Tiếp tục truyện '{story_title}' từ chương X, đã viết Y từ"
- Nếu không tìm thấy file nào dở dang → coi đây là yêu cầu viết truyện MỚI, thực hiện lại từ "BƯỚC KHỞI ĐỘNG" (đọc `input/input.md` hiện tại, đặt tên truyện mới, tạo `stories/<slug>/` mới) — không đụng đến các truyện đã `COMPLETE` trước đó.

## FORMAT BÁO CÁO TIẾN ĐỘ

Sau mỗi chương hoàn thành, in ra:
```
✓ Chương {X}/{total_chapters} — {số từ} từ | Tổng: {current_words}/{target_words} từ
```
