---
description: Gộp thư mục chương rời thành 1 file input rồi tự động gen truyện (English / REWRITE)
argument-hint: <đường dẫn thư mục truyện, vd input/Ten Truyen>
allowed-tools: PowerShell(powershell -NoProfile -File .claude/scripts/merge-chapters.ps1 *), Read, Write, Edit, Glob, Grep, Task
---

Thư mục truyện cần xử lý: `$ARGUMENTS`

Chạy trọn vẹn 2 việc dưới đây trong MỘT lượt, **không dừng lại hỏi user giữa chừng** — mọi quyết định sáng tạo là của bạn.

## Việc 1 — Gộp chương

Chạy đúng một lệnh:

```powershell
powershell -NoProfile -File .claude/scripts/merge-chapters.ps1 -SourceFolder "$ARGUMENTS" -Force
```

Script tự đặt tên đầu ra theo **tên thư mục** (`<tên folder>.md`, đặt cạnh thư mục đó), tự sắp xếp chương theo số, tự bỏ rác điều hướng (`Next Chapter >>`…), và tự chèn sẵn header `## Ngôn ngữ viết: English` + `## Loại input: REWRITE`.

Đọc khối báo cáo script in ra — nó là nguồn số liệu cho việc 2. Nếu `MISSING_CHAPTERS` hoặc `EMPTY_CHAPTERS` khác `none`, **ghi nhận vào báo cáo cuối rồi vẫn chạy tiếp**, đừng dừng.

## Việc 2 — Gen truyện ngay

Thực hiện luồng trong `CLAUDE.md` (BƯỚC KHỞI ĐỘNG → PHASE 1 → PHASE 2 → PHASE 3) với các thay đổi sau:

**File input là file `.md` vừa gộp (`OUTPUT_FILE` trong báo cáo), KHÔNG phải `input/input.md`.**
Tuyệt đối không sửa, không ghi đè `input/input.md` — file đó đang chứa truyện khác của user.

Vì `.claude/agents/story-analyzer.md` ghi cứng đường dẫn `input/input.md` trong mục Đầu vào của nó, khi gọi `story-analyzer` bạn **phải nói rõ trong prompt**: đọc `<OUTPUT_FILE>` thay cho `input/input.md`. Các agent còn lại không đọc file input nên không cần chỉnh.

Bỏ qua bước tự đếm chương/đếm từ của BƯỚC KHỞI ĐỘNG — script đã đếm rồi. Điền thẳng vào `progress.json`:

| Trường progress.json | Lấy từ báo cáo |
|---|---|
| `language` | `LANGUAGE` (English) |
| `input_type` | `INPUT_TYPE` (REWRITE) |
| `total_chapters` | `TOTAL_CHAPTERS` |
| `words_per_chapter` | `WORDS_PER_CHAPTER` |
| `target_words` | `TOTAL_CHAPTERS` × `WORDS_PER_CHAPTER` |

Mật độ từ/chương lấy từ chính truyện gốc — **không dùng mặc định 4.000 từ/chương**.

Tên truyện: **tự nghĩ tên mới** (2-6 từ, tiếng Anh) theo đúng BƯỚC KHỞI ĐỘNG — đừng lấy `STORY_TITLE` (tên thư mục) làm tên truyện, vì REWRITE phải cho ra một truyện không nhận ra được là viết lại từ bản gốc. Slug hoá tên mới đó thành `stories/<slug>/`.

## Trước khi bắt đầu Phase 1

Quét `stories/*/planning/progress.json`:
- Có truyện `phase != "COMPLETE"` → theo luật RESUME trong `CLAUDE.md`, resume truyện đó trước, báo cho user biết, **đừng** tạo truyện mới từ file vừa gộp.
- Không có → tạo truyện mới như trên.

## Báo cáo cuối

Đường dẫn file gộp, tên truyện mới + `{story_dir}`, tổng chương/tổng từ thực tế, và mọi cảnh báo `MISSING_CHAPTERS` / `EMPTY_CHAPTERS` nếu có.
