---
description: Gen truyện qua webapp API (FastAPI + LangGraph) từ 1 file truyện .md đã gộp
argument-hint: <đường dẫn file truyện .md, vd input/Ten Truyen.md>
allowed-tools: PowerShell, Bash, Read, Write, Edit, Glob, Grep
---

File truyện cần gen: `$ARGUMENTS`

Chạy trọn vẹn các bước dưới đây trong MỘT lượt, **không dừng lại hỏi user giữa chừng** — mọi quyết định là của bạn.

Luồng này dùng **sub-project `webapp/`** (FastAPI + Python state machine), KHÔNG dùng pipeline agent CLI trong `CLAUDE.md` gốc. Đừng tạo `stories/<slug>/`, đừng gọi `story-analyzer`/`chapter-writer`/... — toàn bộ orchestration nằm trong service.

Nếu `$ARGUMENTS` là **thư mục** chứa chương rời chứ không phải file `.md` → dừng và bảo user chạy `/merge-noval <thư mục>` trước.

## Bước 1 — Đảm bảo API sống

```powershell
try { Invoke-RestMethod -Uri "http://localhost:8001/api/stories" -Method Get -TimeoutSec 10 } catch { "DOWN" }
```

Nếu DOWN → `docker compose -f webapp\docker-compose.yml up -d --build`, rồi poll lại `GET /api/stories` tối đa ~30s cho tới khi 200. Vẫn lỗi → `docker logs webapp-api-1 --tail 30` để xem nguyên nhân (thường là thiếu `webapp/.env`) và báo user.

## Bước 2 — Resume trước, tạo mới sau

Từ `GET /api/stories`, với mỗi truyện gọi `GET /api/stories/{id}` xem `phase` + `is_running`:

- Có truyện `phase != "COMPLETE"`:
  - `is_running == true` → nó đang chạy dở, **đừng tạo truyện mới**, chỉ gắn watcher (bước 6) rồi báo user.
  - `is_running == false` → run trước đó chết giữa chừng. `POST /api/stories/{id}/run` để resume (state machine tự suy ra bước kế từ DB), rồi gắn watcher. **Đừng tạo truyện mới.**
- Không có truyện dở dang → tạo mới theo bước 3.

## Bước 3 — Dựng payload từ file truyện

File gộp bởi `/merge-noval` có header `## Ngôn ngữ viết` / `## Loại input` rồi tới `## Nội dung input`. Chỉ phần **sau** marker `## Nội dung input` mới là `content` gửi lên API — không gửi cả header. Nếu file không có marker đó thì lấy nguyên file.

```powershell
$src = "$ARGUMENTS"
$raw = Get-Content -LiteralPath $src -Raw -Encoding UTF8
$marker = "## Nội dung input"
$idx = $raw.IndexOf($marker)
$content = if ($idx -ge 0) { $raw.Substring($idx + $marker.Length).Trim() } else { $raw.Trim() }
$payload = [ordered]@{
  language = "English"; input_type = "REWRITE"; genre = $null
  content = $content; desired_chapters = $null; desired_words = $null
}
$out = "<scratchpad>\payload.json"
$payload | ConvertTo-Json -Depth 5 -Compress | Out-File -LiteralPath $out -Encoding utf8
```

- `language` / `input_type`: lấy đúng giá trị ghi trong header file (mặc định `English` + `REWRITE`).
- **`desired_chapters` và `desired_words` luôn để `null` khi `input_type == REWRITE`.** Đây không phải tuỳ chọn: graph pipeline map chương N của bản mới sang event `E{N:03d}` của bản gốc, nên `total_chapters` bắt buộc bằng `source_chapter_count`. Ép số chương khác sẽ làm chương lệch grounding (xem "Known limitation" trong `webapp/CLAUDE.md`). Service tự tính `words_per_chapter` theo mật độ thật của bản gốc — không bao giờ là mặc định 4.000.

POST bằng **bytes** để không hỏng UTF-8 (PowerShell 5.1 hay mã hoá sai body string):

```powershell
$bytes = [System.IO.File]::ReadAllBytes($out)
Invoke-RestMethod -Uri "http://localhost:8001/api/stories" -Method Post -Body $bytes `
  -ContentType "application/json; charset=utf-8" -TimeoutSec 300
```

Call này **đồng bộ và gọi LLM để đặt tên truyện** → có thể mất tới vài chục giây, để timeout rộng. Trả về `id`, `title`, `slug`, `total_chapters`, `target_words`, `words_per_chapter` — ghi lại hết cho báo cáo cuối.

## Bước 4 — Đừng patch tên truyện trước khi run (với REWRITE)

Title lúc `POST /api/stories` chỉ là **tạm**. Với REWRITE, `new_graph_builder.finalize_after_verify()` (`app/agents/new_graph_builder.py:1760-1777`) tự **đặt lại tên** từ `story_bible` của thế giới mới sau khi `verify_graph` xong, rồi `slugify()` lại luôn. Mọi patch title/slug trước `POST /run` **sẽ bị ghi đè** — đừng làm, mất thời gian vô ích.

Tên tạm xấu cỡ nào cũng bỏ qua, đi thẳng sang bước 5. Chỉ soát tên **sau khi run xong**:

- Tên cuối hợp lý → xong.
- Tên cuối vô lý, hoặc là cả đoạn reasoning dài (gotcha `generate_title` trong `webapp/CLAUDE.md`) → lúc này thư mục output đã ghi theo slug cũ, nên phải sửa **cả DB lẫn thư mục**:

```powershell
docker exec webapp-api-1 python -c "
from app.db.session import SessionLocal
from app.db.models import Story
s = SessionLocal(); st = s.get(Story, <id>)
st.title = '<Tên Mới>'; st.slug = '<slug-moi>'
s.commit(); print(st.id, st.title, st.slug)
"
Rename-Item webapp\output\<slug-cu> <slug-moi>
```

Nhớ sửa cả dòng tiêu đề đầu `full.md` + `summarize.txt` trong thư mục đó, và ảnh trong `image/` đã render tên **cũ** lên poster — muốn khớp thì phải gen lại ảnh.

Với `IDEA`/`PREMISE` thì không có bước graph nên không bị retitle: tên lúc tạo là tên cuối, patch trước `POST /run` có tác dụng.

## Bước 5 — Chạy

```powershell
Invoke-RestMethod -Uri "http://localhost:8001/api/stories/<id>/run" -Method Post -TimeoutSec 60
```

Trả về ngay `{"status":"started"}` — job chạy nền trong container, có auto-retry backoff cho 429/5xx.

## Bước 6 — Watcher, không block

Truyện REWRITE dài chạy hàng giờ (mỗi chương gốc 1 lượt `graph_extract`, rồi mỗi chương mới ~5 bước LLM). **Đừng** ngồi poll đồng bộ. Gắn watcher nền bằng Bash `run_in_background: true`:

```bash
while true; do
  s=$(curl -s --max-time 30 http://localhost:8001/api/stories/<id>)
  echo "$(date +%H:%M:%S) $s" >> "<scratchpad>/run.log"
  echo "$s" | grep -q '"is_running":false' && { echo "STOPPED: $s"; break; }
  echo "$s" | grep -q '"phase":"COMPLETE"' && { echo "COMPLETE: $s"; break; }
  sleep 120
done
```

Khi watcher báo về:
- `phase == "COMPLETE"` → xong, báo user đường dẫn output.
- `is_running == false` mà `phase != "COMPLETE"` → run chết giữa chừng. Xem `docker logs webapp-api-1 --tail 50` tìm nguyên nhân, rồi `POST /api/stories/<id>/run` lần nữa để resume từ commit cuối (an toàn, DB là state store duy nhất). Lặp lại tối đa 3 lần; vẫn chết thì báo user kèm log lỗi.

## Báo cáo

Sau khi start (và lại sau khi watcher báo xong): đường dẫn file nguồn, `story_id`, tên truyện + slug, `total_chapters` / `words_per_chapter` / `target_words`, tiến độ hiện tại (`chapters_done`/`total_chapters`, `current_words`), và đường dẫn output `webapp/output/<slug>/novel.md`.
