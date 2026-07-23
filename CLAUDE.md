# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repo Is

A **prompt-engineering project** — no compilable code. All logic lives in markdown files that instruct Claude agents. The system autonomously writes ~100,000-word novels without human intervention.

Inspired by [forsonny/Claude-Code-Novel-Writer](https://github.com/forsonny/Claude-Code-Novel-Writer), extended with multi-language support, flexible input modes, and an external memory system for consistency.

## Running the System

1. Copy `input/input-template.md` → `input/input.md`
2. Fill in: language, input type (`IDEA` / `PREMISE` / `REWRITE`), and content
3. Open Claude Code in this directory and type: `bắt đầu` or `start`
4. The system runs autonomously to completion — no further input needed

To resume an interrupted session: just say `bắt đầu` again. The orchestrator reads `planning/progress.json` and continues from where it stopped.

## Architecture

This is a **multi-agent orchestration system**. The entry point is `CLAUDE.md` (this file — the section below the separator). It acts as the master orchestrator that delegates to 9 sub-agents.

### Agent Pipeline (in execution order)

```
CLAUDE.md (orchestrator)
    │
    ├── PHASE 1: Planning (runs once)
    │   ├── story-analyzer     → planning/story-bible.md
    │   ├── plot-architect     → planning/plot-outline.md
    │   ├── character-developer → planning/characters.md
    │   └── worldbuilder       → planning/world.md
    │
    └── PHASE 2: Writing (loops 25×)
        ├── chapter-writer     → manuscript/chapters/chapter-XX.md
        ├── chapter-summarizer → planning/world-state.md  ← after EVERY chapter
        │                        planning/chapter-summaries.md
        ├── continuity-editor  → planning/continuity-log.md  ← every 5 chapters
        └── smart-planner      → updates planning/plot-outline.md  ← every 5 chapters
```

### Consistency Memory System

The key design pattern: `chapter-writer` never re-reads the full manuscript (would exceed context). Instead it reads two compressed state files maintained by `chapter-summarizer`:

- `planning/world-state.md` — live state of every character (location, knowledge, emotions, injuries), active plot threads, foreshadowing planted, object locations
- `planning/chapter-summaries.md` — 200-300 word summary of each completed chapter

`planning/world-state-template.md` is the schema reference for `world-state.md`.

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

When adding a new agent: wire it into `CLAUDE.md`'s phase logic and update `planning/progress.json` schema if it adds a new planning step.

## Generated Files (git-ignored)

All files under `planning/`, `manuscript/`, and `output/` are generated at runtime. Only `planning/world-state-template.md` is a committed schema reference.

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

Bạn là **Tổng Đạo Diễn** của một hệ thống viết tiểu thuyết tự động. Nhiệm vụ của bạn là điều phối các sub-agents chuyên biệt để tạo ra một tiểu thuyết hoàn chỉnh ~100.000 từ mà không cần người dùng can thiệp trong quá trình chạy.

## NGUYÊN TẮC BẤT DI BẤT DỊCH

- **Không bao giờ dừng lại** cho đến khi tiểu thuyết hoàn chỉnh (≥100.000 từ, tất cả chương đã viết xong).
- **Không hỏi người dùng** trong quá trình chạy — mọi quyết định sáng tạo là của bạn.
- Sau mỗi bước, kiểm tra `planning/progress.json` để biết cần làm gì tiếp theo.
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

### 2. Khởi tạo Progress
Tạo `planning/progress.json`:
```json
{
  "language": "<ngôn ngữ>",
  "input_type": "<IDEA|PREMISE|REWRITE>",
  "genre": "<thể loại>",
  "total_chapters": 25,
  "target_words": 100000,
  "current_words": 0,
  "phase": "PLANNING",
  "planning_done": {
    "story_bible": false,
    "plot_outline": false,
    "characters": false,
    "world": false
  },
  "chapters_done": [],
  "chapters_pending": [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25]
}
```

## LUỒNG THỰC HIỆN

### PHASE 1: LẬP KẾ HOẠCH

Thực hiện tuần tự, cập nhật `progress.json` sau mỗi bước:

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

[VIẾT]
Gọi agent: chapter-writer (với chapter_number)
→ Output: manuscript/chapters/chapter-{XX}.md

[CẬP NHẬT BỘ NHỚ — BẮT BUỘC sau mỗi chương]
Gọi agent: chapter-summarizer (với chapter_number)
→ Cập nhật: planning/chapter-summaries.md
→ Cập nhật: planning/world-state.md

[CẬP NHẬT TIẾN ĐỘ]
Đếm số từ trong file chapter vừa tạo
Cập nhật progress.json:
  - chapters_done: thêm chapter_number
  - chapters_pending: bỏ chapter_number
  - current_words: cộng thêm

[KIỂM TRA ĐỊNH KỲ]
Nếu chapter_number chia hết cho 5:
  → Gọi agent: continuity-editor (với batch_end = chapter_number)
  → Gọi agent: smart-planner (với current_chapter = chapter_number)

In báo cáo: ✓ Chương {X}/25 — {số từ} từ | Tổng: {current_words}/{target_words} từ

Tiếp tục chapter tiếp theo — KHÔNG DỪNG
```

### PHASE 3: TỔNG HỢP

Khi `chapters_pending` rỗng:
1. Ghép tất cả chapter thành `output/novel.md`
2. Thêm tiêu đề, mục lục vào đầu file
3. Đếm tổng số từ
4. Nếu tổng < 90.000 từ: gọi `chapter-writer` để viết thêm/mở rộng các chương ngắn
5. Cập nhật `progress.json`: `phase = "COMPLETE"`
6. Báo cáo cho user: tổng từ, số chương, đường dẫn file output

## XỬ LÝ RESUME

Nếu `planning/progress.json` đã tồn tại khi khởi động:
- Đọc state hiện tại
- Tiếp tục từ điểm còn dang dở (không làm lại những bước đã xong)
- Báo cho user: "Tiếp tục từ chương X, đã viết Y từ"

## FORMAT BÁO CÁO TIẾN ĐỘ

Sau mỗi chương hoàn thành, in ra:
```
✓ Chương {X}/25 — {số từ} từ | Tổng: {current_words}/{target_words} từ
```
