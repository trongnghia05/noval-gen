# Pipeline sinh truyện — bản đồ từ source code

> Tài liệu này mô tả **hệ thống đang chạy thực tế**, không phải thiết kế mong muốn.
> Mọi con số, tên hàm và quy tắc đều dẫn về file cụ thể. Chỗ nào code và prompt mâu
> thuẫn nhau thì ghi rõ cả hai.
>
> Phạm vi: thư mục `webapp/` (FastAPI + LangGraph + Vertex AI Gemini). Hệ thống CLI ở
> gốc repo là dự án khác, không áp dụng ở đây.
>
> Cập nhật: 13/08/2026 · nhánh `refactor-code`

---

## 0. Logic nằm ở đâu

Đây là điểm quan trọng nhất để đọc hệ thống này: **phần lớn "cách viết truyện" không
nằm trong Python**. Python chỉ quyết định *bước nào chạy khi nào* và *dữ liệu đi đâu*.
Còn *viết thế nào* nằm trong 37 file prompt.

| Loại logic | Ở đâu | Ví dụ |
|---|---|---|
| Thứ tự bước, điều kiện, vòng lặp, ngân sách sửa | Python | `_decide_next_step`, `_MAX_LOCAL_REVISE = 5` |
| Công thức sáng tác | Prompt | 3 khuôn đặt tiêu đề, cấu trúc 3 hồi, quy tắc show-don't-tell |
| Ràng buộc cứng không tin được LLM | Python (sau prompt) | `_title_problem`, thay tên bằng regex, `UniqueConstraint` |

Mẫu lặp lại xuyên suốt: **một quy tắc quan trọng được viết trong prompt, rồi được ép
lại bằng code**. Vì prompt bị bỏ qua đủ nhiều lần nên phải có chốt chặn.

---

## 1. Đầu vào và tính độ dài

### 1.1 Ba loại input

Khai báo ở `Story.input_type`, quyết định toàn bộ nhánh planning:

| Loại | Nội dung | Hành vi |
|---|---|---|
| `IDEA` | ý tưởng 1–5 câu | AI tự nghĩ toàn bộ nhân vật, bối cảnh, cốt truyện |
| `PREMISE` | mô tả chi tiết | AI tôn trọng chi tiết user cho, tự dựng cốt truyện |
| `REWRITE` | truyện gốc đầy đủ | Giữ bộ xương cốt truyện, thay toàn bộ bề mặt |

**Thực tế: mọi truyện đang chạy đều là `REWRITE`.** Nhánh IDEA/PREMISE tồn tại trong
code nhưng chưa dùng tới trong 6 truyện gần nhất.

### 1.2 Tính độ dài — `app/length_calc.py`

```python
compute_length(input_type, source_content, desired_chapters, desired_words)
    → (total_chapters, target_words, words_per_chapter)
```

**REWRITE** — mật độ từ lấy từ chính truyện gốc, không dùng số mặc định:

```python
source_words_per_chapter = round(source_words / source_chapter_count)
```

- user để trống → `total_chapters` = số chương gốc
- user ghi số chương N → `total_chapters = N`, mật độ vẫn giữ của gốc
- user ghi tổng số từ W → `total_chapters = round(W / mật_độ_gốc)`

**IDEA / PREMISE** — dùng hằng số hệ thống:

```python
DEFAULT_TOTAL_CHAPTERS   = 25
DEFAULT_TARGET_WORDS     = 100_000
DEFAULT_WORDS_PER_CHAPTER = 4_000
```

Đếm chương gốc bằng regex `_CHAPTER_MARKER_RE`, khớp `Chapter 12` / `CHƯƠNG 5` /
`# Chương 5:` / `#1 Chapter 1 - Title`.

**Số thực đo được** (6 truyện gần nhất, đều REWRITE):

| Truyện | Chương | `words_per_chapter` (mục tiêu) | Median thực tế |
|---|---|---|---|
| 63, 95, 98 | 30 | 951 | **1.327** (min 967 · max 1.574) |
| 101, 102, 103 | 20 | 1.477 | **1.630** (min 1.386 · max 1.884) |

Chương viết ra **luôn dài hơn mục tiêu** — truyện 98 vượt ~40%. Nguyên nhân nằm trong
`chapter_writer`: vòng nới chỉ đẩy lên (`< 60%` mỗi cảnh, `< 85%` cả chương) và **không
có vòng nào cắt xuống**. Prompt ghi ±15% nhưng không có gì ép phía trên.

---

## 2. Bộ máy điều phối

Hai lớp, không lớp nào lặp lại logic của lớp kia.

### 2.1 `app/orchestrator.py` — nguồn sự thật duy nhất

```python
_decide_next_step(session, story) -> str     # thuần quyết định, không thực thi
run_<tên>_step(session, story) -> dict       # thân thực thi, gom trong _STEP_EXECUTORS
advance(session, story)                      # quyết định + gọi, cho API thủ công
```

14 bước có thể trả về:

```
story_bible · graph_extract · verify_source_graph · new_graph · verify_graph
plot_outline · characters · world · verify_planning · planning_complete
checkpoint · blueprint · write_chapter · complete
```

**Hai bất biến trong `_decide_next_step`:**

1. **Checkpoint trước pending.** Kiểm tra checkpoint đang treo *trước khi* quét chương
   pending. Nếu quét pending trước, một checkpoint lỗi sẽ bị bỏ qua vĩnh viễn khi mọi
   chương đã `done`. Đây là bug thật đã gặp khi test.
2. **Ba bước một chương.** `pending → blueprinted → done` là ba lần gọi.

### 2.2 `app/graph.py` — chạy liên tục

LangGraph `StateGraph`: một node `router` với cạnh điều kiện tới mỗi bước; mọi node làm
việc quay về `router`; `complete` nối `END`.

```python
config={"recursion_limit": 10000}   # mặc định 25 quá thấp: 25 chương = 100+ lần hop
_RETRY_DELAYS = (5, 20, 60)         # giây, backoff cho 429/5xx
```

- Mỗi node tự mở `SessionLocal()`, gọi `_STEP_EXECUTORS[step]`, commit, đóng.
- **Không có checkpointer.** Postgres là kho state duy nhất. Chạy lại sau crash chỉ cần
  suy ra "bước gì tiếp" từ `Story.phase` / `Chapter.status`.
- Khi retry, **suy lại bước** chứ không gọi mù executor cũ — vì một bước có thể commit
  một phần rồi mới hỏng.
- `Story.is_running` chặn chạy song song; reset trong `try/finally`.

---

## 3. PHASE PLANNING

### 3.1 Hai đường khác nhau

```
REWRITE:
  story_bible → graph_extract ×N → verify_source_graph → new_graph → verify_graph
             → plot_outline → characters → world → verify_planning → planning_complete

IDEA / PREMISE:
  story_bible → plot_outline → characters → world → verify_planning → planning_complete
```

Bốn bước graph chỉ chạy cho REWRITE. Đó là toàn bộ khác biệt.

### 3.2 `story_analyzer` — đọc truyện gốc

`app/agents/story_analyzer.py` · `prompts/story_analyzer.md` (13 KB)

Xuất ra:

- `story.story_bible` — tóm tắt tường thuật
- `story.source_spirit` — **5 phần, chỉ REWRITE**:
  1. `TONE & GENRE` — sắc thái, nhịp, cách tác giả gốc dựng căng thẳng
  2. `POV` — ngôi thứ mấy, mấy POV, luân phiên ra sao, thì quá khứ hay hiện tại
  3. `STORY ARC` — hành trình, viết bằng **vai** không tên
  4. `NARRATIVE TEXTURE` — tỷ lệ thoại/tường thuật, cách đan xen, show hay tell
  5. `SYNTHETIC EXAMPLES` — 2–3 đoạn văn mẫu **tự viết**, nội dung vô can nhưng tái
     tạo đúng POV và texture
- `story.source_chapter_count`
- Node graph `source`: CHARACTER / LOCATION / FACTION / OBJECT / THEME — **dùng tên
  gốc**, **không tạo EVENT node**

Hai quy tắc mang tải trọng lớn:

> **`label` của CHARACTER phải là TÊN RIÊNG, không phải mô tả quan hệ.** Bước đổi tên
> sau này chỉ đọc `label` — nên một label kiểu "chồng của X" sẽ để tên thật sống sót
> nguyên vẹn sang truyện mới.

> `source_chapter_count` **luôn** đặt bằng `len(split_source_chapters(...))`, không bao
> giờ tin số model tự khai. Tin model đã gây IndexError thật (đếm thừa) và bỏ chương âm
> thầm (đếm thiếu).

`SYNTHETIC EXAMPLES` là thứ `chapter_writer` bắt chước sát nhất — sai ngôi kể hoặc sai
show/tell ở đây thì cả tiểu thuyết đi theo.

### 3.3 `chapter_graph_extractor` — mỗi chương gốc một lần gọi

`app/agents/chapter_graph_extractor.py` (500 dòng)

Mỗi lần `advance` xử lý **một** chương gốc: thêm EVENT node của chương đó + các cạnh.

> **Khoá EVENT được chuẩn hoá thành `E{chapter:03d}`** bất kể model trả về id gì; các
> cạnh và trigger được ánh xạ lại theo. Vì `context_builder`, `source_graph_verifier`,
> `chapter_writer` và `quality_reviewer` đều tra cứu theo `E{N:03d}`.

### 3.4 `source_graph_verifier` — soát graph gốc theo từng chương

`MAX_ITERATIONS = 10` mỗi chương. Chỉ soát **phần chương đó thêm vào** (EVENT + node
mới + cạnh mới) cộng ngữ cảnh tối thiểu — chi phí O(số thứ thêm), không phải O(toàn
graph). Gặp lỗi critical thì trích xuất lại đúng chương đó (an toàn vì các chương sau
chưa áp dụng). Xong thì đặt `story.source_graph_verified = True`.

### 3.5 `new_graph_builder` — dựng thế giới mới

`app/agents/new_graph_builder.py` (1.976 dòng — file lớn nhất hệ thống)

Chín pha, chạy theo thứ tự này trong `run()`:

| Pha | Loại | Việc |
|---|---|---|
| **1** | Python | Copy nguyên văn toàn bộ node + cạnh từ graph `source` sang `new` |
| **0** | LLM | Thiết kế thế giới mới (`world_designer`) |
| **1b** | LLM | Dựng từ điển tên `source_key → new_label` (`name_lexicon`) |
| **1.5** | Python | Thay tên xác định trên mọi trường text |
| **2a–2e** | LLM ×5 | Làm giàu bề mặt theo nhóm |
| **3** | LLM | Làm giàu sáng tạo — thêm node phụ (chỉ lần chạy đầu) |
| **3.5** | LLM | Viết lại `story_bible` từ tên của graph mới |

**Pha 0 — `world_designer`** (`prompts/world_designer.md`, 7,3 KB)

Ràng buộc cứng nhất:

> **KHÔNG ĐƯỢC ĐẶT BẤT KỲ TÊN RIÊNG NÀO.** Mô tả bằng vai trò và chức năng: "the
> protagonist", "the riverside gambling house". Chỉ ngoại lệ cho địa danh có thật theo
> hợp đồng thị trường.

Lý do: chỉ một bước duy nhất được đúc tên (pha 1b). Tên lọt vào đây là nguồn của mọi vụ
trùng tên và rò tên gốc.

Prompt còn ép chọn **thế giới hào nhoáng có chênh lệch quyền lực dốc**, liệt kê 8 lựa
chọn (private equity, khách sạn hạng sang, gia tộc tội phạm, nhà mốt, hãng đĩa, đế chế
bất động sản, hãng luật xử scandal, đội thể thao) và ba thứ thế giới đó phải cung cấp:
chênh lệch quyền lực, buộc phải ở gần nhau trong không gian riêng tư đắt tiền, và tủ đồ
đáng nhìn.

Sau pha 0 có `world_name_check` — một lần gọi LLM soát xem còn tên riêng nào lọt không;
còn thì sinh lại.

**Pha 1b — `name_lexicon`** (`prompts/name_lexicon.md`)

Đây là **nơi duy nhất tên mới được đúc ra**. 13 quy tắc, quan trọng nhất:

- Rule 0: tên nào world design đã đặt thì tái sử dụng, không đặt tên khác
- Rule 1: **không bao giờ dùng lại danh từ riêng của nguồn** — danh sách `FORBIDDEN
  NAMES` gom từ mọi label node gốc *cộng* mọi từ viết hoa trong tóm tắt sự kiện. Cấm cả
  khi làm một từ trong tên dài, cả khi thêm đuôi
- Rule 5: cấm đổi tên bằng cách sửa chính tả (đổi nguyên âm, `-th`→`-t`)
- Rule 7: tên mới **phải khớp giới tính** của node gốc
- Rule 10: **giữ họ hàng** — nhiều nhân vật gốc chung họ là một gia đình, phải chung họ
  mới và khác tên riêng
- Rule 13: đổi register đặt tên giữa các truyện

Có vòng validate bằng code: tên nào vi phạm thì sinh lại.

**Pha 1.5 — thay tên bằng Python**

Xác định, an toàn theo ranh giới từ, phủ cả `aliases`. Mở rộng cặp họ-tên đầy đủ ra
**token tên riêng** (thay "Juniper" chứ không chỉ "Juniper Kennedy"), bỏ họ dùng chung
gây nhập nhằng.

**Pha 2 — năm lần gọi nhỏ, không phải một lần lớn**

`characters` / `events` / `arc_changes` / `relations` / `causes`. Tách ra là thứ khiến
nó hội tụ — một lần gọi khổng lồ thì không. Mỗi nhóm đi qua `graph_enrich_verifier`.

`graph_character_enricher` (11,7 KB) là prompt nặng nhất nhóm này, sinh ra:

- `new_role` — **bước duy nhất nhìn thấy cả dàn nhân vật cùng lúc**, nên là nơi duy
  nhất quyết định được ai là chính. Đúng một `protagonist`, ít nhất một `antagonist`,
  romance thì đúng một `love_interest`
- `new_voice_profile` — hồ sơ giọng nói giàu: register, từ vựng, nhịp câu, tật nói,
  **tính cách và mùi vị**, cộng 2–3 câu thoại mẫu
- `new_appearance` — ngoại hình cho poster
- **Quy tắc tuổi**: nữ chính **18 đến 20**; nam chính cuối 20 đến 40; cha mẹ hơn con
  22–32 tuổi; bạn cùng lứa chênh nhau vài tuổi. Bắt đọc lại toàn bộ tuổi đã gán rồi đối
  chiếu quan hệ trước khi trả về

**Pha 3.5 — viết lại `story_bible`**

Từ tên của graph mới, **không** phân tích lại nguồn. Có `story_bible_leak_check` soát rò
tên. Chạy trong `finalize_after_verify()`, tức là **sau** khi verifier xong — để
`story_bible` / `plot_outline` / `world` / `characters` dùng chung một bộ tên.

### 3.6 `graph_verifier` — soát graph mới

`MAX_ITERATIONS = 10`, `_MAX_RESKIN_REWRITE = 25`

Định tuyến sửa theo loại lỗi — đây là điểm thiết kế cốt lõi:

| Loại lỗi | Cách sửa |
|---|---|
| **reskin** (rò tên gốc, copy gần nguyên văn) | Thay tên bằng Python — **không bao giờ dựng lại toàn bộ** |
| **narrative-logic** | `graph_surface_rewriter` vá node/cạnh có đích |
| **enrichment** | Xoá node gây lỗi |

Dựng lại toàn bộ là đường không hội tụ cũ, đã bỏ.

### 3.7 `plot_architect` — dàn ý

`prompts/plot_architect.md` · xuất JSON `PlotOutlineOut`

**REWRITE**: bám EVENT node làm xương sống, ánh xạ theo đúng thứ tự
`chapter_introduced`, mở rộng tóm tắt sự kiện thành cảnh cụ thể. Không thêm, không bỏ,
không đảo thứ tự sự kiện lớn. Tên = **đúng label trong graph**, tuyệt đối không đặt mới.

**IDEA / PREMISE**: cấu trúc ba hồi theo tỷ lệ:

```
ACT 1  — Setup       ~24%   hook → inciting incident → bị buộc phải hành động
ACT 2A — Escalation  ~28%   thử thách đầu → thích nghi → MIDPOINT ở ~0.5×N
ACT 2B — Collapse    ~24%   rối hơn → Dark Night of the Soul → quyết tâm mới
ACT 3  — Resolution  ~24%   dồn về climax → đối đầu quyết định → hệ quả → epilogue
```

Nguyên tắc chống lặp, dùng lại nguyên văn ở nhiều agent khác:

> **Không hai chương cùng mục đích hoặc cùng beat-type mà không leo thang.**
> **Xương sống đi lên đơn điệu** — quan hệ trung tâm phải lên cấp, không có cao nguyên
> phẳng dài.
> Tuyến phụ không được biến mất quá 5 chương liên tiếp.

### 3.8 `characters` — hai nguồn khác nhau

```python
if story.new_graph_built:
    new_graph_builder.build_characters_from_graph(session, story)   # REWRITE
else:
    character_developer.run(session, story)                          # IDEA/PREMISE
```

REWRITE dựng `Character` + CSV graph **xác định từ graph**, không có lần gọi LLM thứ hai
nào có thể trôi khỏi graph.

### 3.9 `worldbuilder` — world bible

Xuất JSON `WorldBibleOut`: `overview`, `locations[]`, `systems[]`, `factions[]`,
`power_structure`, `culture`, `history`, `glossary[]`.

> **NAMES = THE EXACT LABEL.** Được đặt tên cho địa điểm phụ *hoàn toàn mới*, nhưng
> không được đổi tên bất cứ thứ gì đã có tên.

### 3.10 `planning_verifier` — cổng chất lượng một lần

`MAX_REWRITES = 3` · `MAX_ITERATIONS = 5`

Soát cả bốn artifact theo tiêu chí nghề biên tập: A. story-bible (hook, câu hỏi kịch
tính, stakes, theme) · B. plot-outline (ba hồi, nhân quả, nhịp, **quét toàn dàn ý chống
lặp**, **cung trọn vẹn**, **xương sống đi lên**, setup–payoff) · C. characters
(want vs need, flaw, cung biến đổi, giọng riêng) · D. world (nhất quán nội tại) ·
**E. nhất quán chéo bốn file** (mục quan trọng nhất) · F. ngôn ngữ.

Ngân sách sửa **tính riêng cho từng artifact**:

```
critical → tối đa 3 lần viết lại có feedback (verify lại giữa mỗi lần)
        → vẫn critical → 1 lần sinh lại từ đầu (không feedback)
        → vẫn critical → chấp nhận + ghi log, đi tiếp (dừng cứng, không lặp vô hạn)
```

Thứ tự sinh lại theo phụ thuộc: `story_bible → plot_outline → characters → world`.

---

## 4. PHASE WRITING — ba bước mỗi chương

### 4.1 Bước 1 — `blueprint`

`app/agents/chapter_blueprinter.py` · `prompts/chapter_blueprinter.md` (10,7 KB)

Nhận vào: character-graph, relationships, open-plot-threads, chapter graph constraints,
**chapter-summaries (toàn bộ chương đã viết)**, plot-outline, continuity-log, motif
ledger, và `_recent_beats(n=3)`.

Xuất `ChapterBlueprintOutput`:

| Trường | Ý nghĩa |
|---|---|
| `purpose` | một câu: chương này TỒN TẠI để làm gì cho cả truyện |
| `beat_type` | `setup\|escalation\|revelation\|setback\|turning_point\|confrontation\|aftermath\|resolution` |
| `state_delta` | **sản phẩm bắt buộc** — cụ thể cái gì khác đi ở dòng cuối so với dòng đầu |
| `emotional_arc_start/end` | phải khác nhau |
| `pov_character` / `pov_characters` | POV đơn / POV đa (danh sách) |
| `scenes[]` | mỗi cảnh: goal, conflict, outcome, **disaster**, characters, location, `speaking_characters`, `dialogue_nuance`, `dialogue_intent` |
| `dialogue_intensity` | `heavy \| balanced \| sparse` |
| `motifs_used` | tag ≤5 từ, **tái dùng tag cũ nguyên văn** |
| `hook` | móc câu cuối chương |
| `foreshadowing_to_plant` | hạt gieo (Act 1 / 2a) |

**Ba cơ chế chống lặp cùng lúc:**

1. `beat_type` — không lặp cùng loại quá 2 chương liền
2. `state_delta` — không nêu được delta thật thì chương RỖNG, phải thiết kế lại
3. `motifs_used` + sổ motif — `MOTIF_CAP = 3`; tag chạm trần thì phải bỏ hoặc **leo
   thang thành kiểu biểu đạt khác** (lời tuyên bố chiếm hữu phải thành hành động lãnh
   thổ), không được lặp phẳng

Quy tắc cảnh: **kết cục không bao giờ là "mọi thứ ổn"** — luôn có gì đó hỏng, hoặc đúng
theo cách không ai muốn.

### 4.2 Bước 2 — `write_chapter`

`app/agents/chapter_writer.py` (639 dòng) · `prompts/chapter_writer.md` (18 KB, prompt
lớn nhất hệ thống)

**Đường chính: viết từng cảnh một.**

```python
for scene in blueprint["scenes"]:
    _write_single_scene(...)          # 1 lần gọi LLM / cảnh
                                       # max_tokens=40000, thinking=True
    if len(scene) < 0.60 * words_per_scene:
        _expand_scene(...)             # nới ngay tại chỗ trước khi sang cảnh sau
```

Mỗi lần gọi, **toàn bộ cảnh đã viết được đưa vào prompt** dưới nhãn:

```
## CONTENT ALREADY WRITTEN THIS CHAPTER
(read carefully — do NOT repeat, mirror, or re-describe any of these events)
```

Ngăn model quên cảnh trước ở giữa chừng — bug thật khi sinh 4.000 từ một lần gọi, hỏng
ở khoảng 2.000 từ.

Ghép xong, nếu tổng vẫn < 85% `words_per_chapter` thì chạy vòng nới, tối đa
`MAX_EXPAND_ATTEMPTS = 2`. Prompt nới ghi rõ "same order, no new events".

**Đường dự phòng:** không có blueprint, hoặc exception trong vòng cảnh → gọi một lần.

**Các quy tắc viết đáng chú ý trong prompt:**

- **POV bám nguồn** — đọc mục POV của `source_spirit` và viết đúng ngôi đó. Ghi rõ đây
  là lỗi hay gặp nhất: nguồn ngôi nhất lấp lánh bị viết lại thành ngôi ba phẳng lì
- **Toàn bộ tường thuật phải mang GIỌNG của nhân vật POV**, không riêng lời thoại —
  đọc `voice_profile` và để register nhuộm cả phần kể
- **Bám ĐỘ NĂNG LƯỢNG của nguồn** — nguồn tươi, hài, đá xoáy thì chương phải tươi;
  đừng làm phẳng nguồn sáng thành u ám trang nghiêm
- **Mọi chi tiết phải thuộc thời đại truyện** — cả hai chiều, và trượt nhiều nhất ở
  **ẩn dụ và cách diễn đạt trang trọng**, không chỉ đồ vật
- **SHOW, DON'T TELL** — cấm đích danh các khuôn: "a wave of dread washed over her",
  "the knot tightened in her stomach", "a chill ran down her spine"
- **Từ ngữ bình dân** — bảng thay thế trực tiếp: `ostentatious→showy`,
  `cerulean→deep blue`, `myriad→countless`, `visage→face`, `cacophony→noise`
- **Ngắt đoạn theo nhịp** — mỗi lượt thoại một đoạn riêng; đoạn 1–2 câu; câu nhấn đứng
  riêng một dòng. Kèm một khối ví dụ cụ thể, ghi rõ *chỉ lấy nhịp ngắt dòng, không lấy
  nội dung*
- **KHÔNG THAM CHIẾU META** — không viết "Chapter X", "scene", "blueprint", "as
  established earlier". Muốn nhắc lại thì **mô tả lại**

Tỷ lệ chương: mở ~10% · thân ~75% · kết ~15% của `words_per_chapter`, ±15%.

### 4.3 Vòng kiểm tra — `_verify_chapter_loop`

Chạy ngay sau khi viết, **trước** khi vào bộ nhớ.

```python
_MAX_LOCAL_REVISE = 5      # chapter_reviser — vá tại chỗ, giữ phần văn tốt
_MAX_FULL_REWRITE = 5      # chapter_writer  — viết lại toàn chương
total_iters = 10
```

Mỗi vòng chạy **năm bộ soát**, ba trong đó không tốn LLM:

| Bộ soát | LLM? | Bắt gì |
|---|---|---|
| `dialogue_check` | không | Chương có kế hoạch người nói nhưng gần như không có lời thoại; tỷ lệ thoại thấp hơn ngưỡng của `dialogue_intensity` |
| `prose_check` | không | Rò văn bản meta / phân tích của AI vào văn |
| `pov_check` | **lai** | Đếm đại từ ngôi nhất/ba trong phần tường thuật *sau khi bỏ thoại*; chỉ khi tiền lọc bằng code kêu (`third >= 12 and third > first * 1.5`) mới gọi LLM xác nhận |
| `chapter_verifier` | có | Đối chiếu chương với world.md, story-bible, hồ sơ nhân vật, world-state, blueprint, subgraph + **3 chương gần nhất nguyên văn** |
| `quality_reviewer` | có | 5 trục — xem dưới |

`quality_reviewer` (`prompts/quality_reviewer.md`, 8,6 KB), năm trục:

1. **quality** — cụt giữa chừng, lặp, lủng củng, lệch chương, rò phân tích AI, đoạn văn
   khối dày
2. **world_consistency** — lệch thời đại (cả hai chiều), sai thế giới, sai bối cảnh,
   **`gender_mismatch`** (đại từ mâu thuẫn giới tính đã ghi — luôn `critical`)
3. **graph_consistency** (chỉ REWRITE) — chương có bám **sự kiện đã hoạch định của
   graph mới** không. *Không* đối chiếu với truyện gốc: chống sao chép bề mặt đã làm ở
   tầng graph, không làm lúc viết
4. **dialogue** — `dialogue_too_thin`, `content_not_conveyed`, `invalid_character`
   (tên không có trong roster hợp lệ), `voice_mismatch`, `dialogue_imbalance`
5. **pov** — `head_hopping`, `missing_pov`, `unmarked_switch`

Mọi vấn đề tìm được ghi vào `ChapterVerifyLog` (append-only). Chỉ `critical` mới kích
hoạt sửa. Feedback **tích luỹ qua các vòng** — mỗi lần sửa nhìn thấy toàn bộ lịch sử đã
hỏng những gì.

> **Quan sát thực tế:** truyện 101, cả chương 19 và 20 đều chạm trần 10 lần sửa. Dãy
> issue của ch20 là `3 → 1 → 3 → 2 → 2 → 2 → 1 → 1 → 0 → 3` — dao động chứ không giảm
> dần, và kết thúc bằng việc đi tiếp với lỗi vẫn còn. Mỗi chương như vậy tốn ~8 phút
> thay vì ~3.

### 4.4 `chapter_summarizer` — cập nhật bộ nhớ

Chạy **sau** khi vòng kiểm tra xong. Xuất:

- `ChapterSummary.short_summary` (1–2 câu) và `.summary_text` (200–300 từ)
- `WorldState` rows — **ghi đè, không tích luỹ** (ép bằng
  `UniqueConstraint(story_id, entity_type, entity_key, field)`, là ràng buộc DB chứ
  không phải lời dặn trong prompt)
- `StateLog` — nhật ký append-only: entity, field, old, new, reason
- CSV graph

> **Quy tắc lời thoại — luật cứng:** nhắc lại lời nhân vật thì hoặc trích **nguyên văn**
> từ chương, hoặc diễn giải **không có ngoặc kép**. Một truyện đã xuất bản xoay quanh
> câu "Daddy's here" mà nam chính chưa từng nói — thực tế anh ta nói "I've got you,
> little one". Summarizer diễn giải trong ngoặc kép, rồi mười chương sau trích lại lời
> diễn giải đó như thật, dựng bí ẩn trung tâm của cả cuốn sách trên một câu không ai nói.

### 4.5 Bước 3 — `checkpoint`

```python
_is_checkpoint_chapter = chapter_number % 5 == 0 or chapter_number == total_chapters
```

Hai agent chạy nối nhau:

**`continuity_editor`** — `thinking=True`, `max_tokens=32768`. Chỉ nhận `world-state`,
danh sách tên + alias, và **5 chương gần nhất nguyên văn** (`n=5` = đúng kích thước lô
kể từ checkpoint trước). Ghi ra `ContinuityLog` — một dòng mỗi truyện, ghi đè mỗi lần.

> Nghi ngờ mâu thuẫn với lịch sử cũ hơn 5 chương thì **chỉ đích danh entity + field cần
> tra trong `state_log`**, không đoán. Đây là cơ chế giữ chi phí checkpoint không phình
> theo độ dài truyện.

**`smart_planner`** — đánh giá nhịp (chiếu tổng từ dự kiến so với 85% / 120% của mục
tiêu), cung nhân vật bị bỏ quên, tuyến truyện quên quá 4 chương, foreshadowing chưa trả,
và vị trí cấu trúc theo tỷ lệ `current_chapter / N`. Ghi `SmartPlannerState` — cũng một
dòng, ghi đè.

**Mắt xích nối hai tầng:** `chapter_verifier` đọc lại `ContinuityLog` trong prompt của
nó. Nên editor phát hiện vấn đề sâu mỗi 5 chương, verifier mang nó theo suốt 5 chương
kế tiếp.

---

## 5. PHASE COMPLETE

`run_complete_step` — `orchestrator.py:771`

```python
out_path = _compile_manuscript_to_file(session, story)   # full.md + summarize.txt
                                                          # + N chapter .txt + trace/
image_generator.generate(session, story, image_dir)       # best-effort, không chặn
story.phase = "COMPLETE"
```

Sinh ra dưới `output/<slug>/`:

```
full.md            toàn bộ bản thảo
summarize.txt      front matter: author, tags, logline, summary, cast blurbs
chapter/*.txt      từng chương
trace/ch-NNN.json  bản ghi tái lập
image/             cover.webp, thumbnail1.webp, thumbnail2.webp
```

**`novel_metadata`** (`prompts/novel_metadata.md`) sinh front matter: bút danh, 3–6 tag,
logline 1–2 câu, và **blurb bìa sau 120–180 từ** — "Set up the protagonist, the world,
the inciting situation, and the central tension. Do NOT reveal the ending."

**Ảnh** — `image_generator.py` (909 dòng). Ba ảnh: `cover` (4–6 nhân vật), `thumbnail1`
(nữ chính một mình, **không cười, miệng khép**), `thumbnail2` (cặp đôi, ảnh nóng nhất).
Phong cách rút thăm từ 14 menu mỗi lần chạy: `_COMPOSITIONS`, `_LENSES`, `_LIGHTING`,
`_SIGNATURE_HUE`, `_PALETTES`, `_WARDROBE`, `_TYPOGRAPHY`, `_EMOTION`, `_EYELINE`,
`_SOLO_POSE`, `_MALE_BUILD`, `_MALE_FACE`, `_MALE_MARK`, `_INTIMACY`.

---

## 6. Bộ nhớ — sáu lớp

Nguyên tắc trung tâm: **`chapter_writer` không bao giờ đọc lại toàn bộ bản thảo.**

| Lớp | Kho | Ghi kiểu gì | Ai đọc | Kích thước ở ch.30 |
|---|---|---|---|---|
| `Character` | DB | đặt một lần, hiếm khi đụng | writer, verifier, blueprinter | ~27 KB |
| `WorldState` | DB | **ghi đè tại chỗ** mỗi chương | writer, verifier, editor, planner | ~5 KB |
| `StateLog` | DB | **append-only** | editor (khi cần tra lịch sử cũ) | tăng dần |
| `ChapterSummary.short_summary` | DB | mỗi chương một dòng | **writer** (`format_chapter_list`) | ~12 KB |
| `ChapterSummary.summary_text` | DB | mỗi chương một dòng | **blueprinter**, `smart_planner` | ~35 KB |
| CSV graph | file | 3 file ghi đè + 2 file append | writer, blueprinter | — |

Cộng thêm hai cửa sổ trượt lấy **nguyên văn chương**:

```
chapter_verifier     last_n_chapters_text(n=3)
continuity_editor    last_n_chapters_text(n=5)
chapter_blueprinter  _recent_beats(n=3)          ← blueprint, không phải văn bản
```

**Điểm chưa khớp thiết kế:** hai khối tóm tắt (`format_chapter_list` và
`format_chapter_summaries`) đọc **mọi chương, không giới hạn** — truy vấn không có
`limit`. Chúng phình tuyến tính: 9 KB / 24 KB ở chương 16, lên 12 KB / 35 KB ở chương
30. Nguyên tắc "không đọc lại toàn bộ" đang giữ cho *văn bản chương* nhưng không giữ cho
*tóm tắt*.

`format_chapter_list(session, story_id, current_chapter)` còn **không dùng
`current_chapter` để lọc** — tham số chỉ để in dòng tiêu đề. Vô hại trong luồng chạy
xuôi (tóm tắt chương sau chưa tồn tại), nhưng bất kỳ đường nào viết lại chương cũ sau
khi chương sau đã xong sẽ nhét tóm tắt tương lai vào prompt. Hiện chưa có đường như vậy.

---

## 7. Thị trường và đặt tên

### 7.1 `app/market.py` — một chỗ duy nhất

```python
MARKET = os.getenv("MARKET", "US")
```

Khối hợp đồng thị trường được **nối vào system prompt** của đúng 5 agent
(`prompts/loader.py`):

```python
_MARKET_AWARE = {
    "world_designer",            # quyết định bối cảnh
    "name_lexicon",              # đúc mọi tên riêng
    "graph_character_enricher",  # viết ngoại hình
    "image_prompt",              # mô tả người trên bìa
    "image_prompt_verifier",     # soát mô tả đó
}
```

Nội dung hợp đồng US: **Mỹ đương đại, luôn luôn**; phải nêu rõ vùng nào của Mỹ; tên
người/nơi/tổ chức phải nghe ra Mỹ; liệt kê đích danh những tên **đã từng xuất xưởng sai**
("Choreia Movement Hall", "Thorne Manor"); **không tước hiệu quý tộc**; ngoại hình phải
khớp tên mà không cần giải thích.

> Lý do gom về một chỗ nằm ngay trong docstring: khi luật nằm rải trong từng prompt,
> các bản sao trôi lệch nhau — world designer được dặn ở lại nước Mỹ trong khi
> `name_lexicon`, thứ *thực sự* đúc mọi địa danh, chưa từng được dặn.

### 7.2 Đặt tiêu đề

`prompts/title_generator.md` (11,8 KB) + chốt chặn code `_title_problem` trong
`app/slug.py`.

**Cấu trúc slot:**

```
[POSSESSIVE / IMPERATIVE VERB] + [POWER TITLE] + [TWIST]
```

Mọi tiêu đề phải chứa **ít nhất một POWER TITLE và một TWIST**.

**Ba khuôn, chọn đúng một, điền trọn khuôn đó:**

| # | Khuôn | Hình dạng |
|---|---|---|
| 1 | Command / craving | `[Possessive verb] Me, [Power title]` |
| 2 | Reversal declaration | `I [Reversal verb] the [Power title] First` · `Reborn to [Reject/Ruin] the [Power title]` |
| 3 | Forbidden relationship | mở từ phía **cô**: `My [Forbidden relation]'s [Role]` · từ phía **anh**: `The [Power title] Wants His [Role]` · từ **hành động**: `Sleeping with the [Obstacle]'s [Power title]` |

**Từ vựng slot:** Power titles (Alpha, Luna, Don, Boss, CEO, Tycoon, Billionaire, Mafia
King, Master, Warlord, Matriarch, Guildmaster, Heir) · Possessive verbs (Claim, Breed,
Break, Ruin, Own, Tame, Wreck, Crave, Keep) · Twists (Rebirth/Reborn, Revenge, Rejected,
Secret Baby, Fake Marriage, Contract, Substitute Bride, Second Chance, Hidden Identity,
Divorce, Betrayal).

**Luật chống bịa** — nhóm luật dài nhất trong prompt:

- Không bịa quan hệ không có trong bản đồ quan hệ
- **Không nâng cấp giai đoạn quan hệ** — hẹn hò giả không phải đính hôn giả, càng không
  phải hôn nhân. Ghi rõ đây là cách sai phổ biến nhất
- Không bịa nghề, cấp bậc, bối cảnh
- **Một sự thật một danh từ** — không hàn "quan hệ vĩnh viễn" với "trạng thái nhất thời"
  thành `Rival Sister`, `Enemy Brother`. Hai tiêu đề trong kho đã xuất xưởng đúng lỗi này
- Tiêu đề quan hệ phải trỏ vào **nhân vật truyện thực sự nói về**, không phải love
  interest bị gọi là em trai của ai đó

**Luật cứng:** 3–6 từ · **không mặc định mở đầu bằng đại từ sở hữu** ("Measured across
this catalogue, one opening currently accounts for every single title") · tối đa **một**
`'s` · **không dấu hai chấm**, không phụ đề · phải rõ ngay lập tức, cấm danh từ tâm
trạng một từ (*Reckoning*, *Haven*) và cặp không khí hai từ (*Clockwork Masquerade*).

**Chốt chặn code** (`_title_problem`) — chạy sau, bất kể user yêu cầu gì:

```python
if not 2 <= len(words) <= 8:            # ⚠ prompt nói 3-6, code cho 2-8
if words[0] in _COMMAND_VERBS and words[1] in ("my","the","his","her"):
if title.count("'s") + title.count("’s") >= 2:
if ":" in title:
```

Có 2 lần thử lại, kèm lý do từ chối vào prompt. Hỏng vẫn chấp nhận — "a flawed title is
fixable later, a crash is not".

`_clean_title` xử lý hai kiểu hỏng đã gặp: model suy luận trả về cả đoạn văn (**quét
ngược từ dưới lên** vì model đặt kết luận ở cuối), và tiêu đề hai vế có dấu hai chấm
(giữ vế nào nhiều chữ hơn).

**Tiêu đề được sinh hai lần:** một lần lúc tạo truyện (`routes.py:94`), một lần sau khi
graph mới xong (`new_graph_builder.py:1956` — "retitled for new world").

---

## 8. Ngôn ngữ

Mọi prompt sinh nội dung đều mang cùng một khối, viết theo cùng một cách:

> **OUTPUT LANGUAGE — HARD RULE:** the user message carries a `language` field. This
> prompt is written in English; that does NOT make English the output language.

Có lý do cụ thể, ghi trong `worldbuilder.md`:

> Điều này đã xuất xưởng: một truyện viết bằng tiếng Anh được cấp world bible tiếng
> Việt, vì agent theo ngôn ngữ của chỉ dẫn thay vì ngôn ngữ được bảo phải viết.

`planning_verifier` mục F soát lại điều này và đánh `critical`.

---

## 9. Bảng tổng agent

| Agent | Prompt (KB) | Chạy khi nào | Ghi ra |
|---|---|---|---|
| `story_analyzer` | 13,1 | 1× | `story_bible`, `source_spirit`, node graph source |
| `chapter_graph_extractor` | 8,9 | ×N chương gốc | EVENT node + cạnh |
| `source_graph_verifier` | 3,3 | 1× (≤10 vòng/chương) | `PlanningVerifyLog` |
| `world_designer` | 7,3 | 1× (pha 0) | thiết kế thế giới |
| `world_name_check` | 1,5 | sau pha 0 | soát tên riêng lọt |
| `name_lexicon` | 5,9 | 1× (pha 1b) | từ điển tên |
| `graph_character_enricher` | 11,8 | 1× (pha 2a) | vai, giọng, ngoại hình, tuổi |
| `graph_event/arc/relation/causes_enricher` | 2,2–2,7 | 1× mỗi cái | làm giàu graph |
| `graph_enrich_verifier` | 2,8 | mỗi nhóm pha 2 | cổng chất lượng |
| `graph_enricher` | 4,3 | 1× (pha 3) | node/cạnh phụ |
| `story_bible_leak_check` | 1,7 | pha 3.5 | soát rò tên |
| `graph_verifier` | 7,7 | ≤10 vòng | định tuyến sửa |
| `graph_surface_rewriter` | 3,2 | khi có lỗi logic | vá node/cạnh |
| `graph_repair` | 3,6 | khi có lỗi critical | sửa có đích |
| `plot_architect` | 5,7 | 1× | `plot_outline` (JSON) |
| `character_developer` | 3,0 | 1× (chỉ IDEA/PREMISE) | `Character` rows |
| `worldbuilder` | 2,8 | 1× | `world_bible` (JSON) |
| `planning_verifier` | 7,3 | 1× (≤5 vòng/artifact) | `PlanningVerifyLog` |
| `chapter_blueprinter` | 10,7 | **mỗi chương** | `chapter.blueprint` (JSON) |
| `chapter_writer` | 18,0 | **mỗi cảnh** | `chapter.content` |
| `chapter_reviser` | 3,5 | ≤5 lần/chương | vá tại chỗ |
| `chapter_verifier` | 5,6 | **mỗi vòng kiểm tra** | `ChapterVerifyLog` |
| `quality_reviewer` | 8,6 | **mỗi vòng kiểm tra** | `ChapterVerifyLog` |
| `chapter_summarizer` | 5,0 | **mỗi chương** | `ChapterSummary`, `WorldState`, `StateLog` |
| `continuity_editor` | 2,8 | **mỗi 5 chương** | `ContinuityLog` |
| `smart_planner` | 2,2 | **mỗi 5 chương** | `SmartPlannerState` |
| `title_generator` | 11,8 | 2× | `story.title` |
| `novel_metadata` | 2,6 | 1× (cuối) | front matter |
| `image_prompt` + verifier | 16,8 + 11,2 | 1× (cuối) | prompt ảnh |

Mọi agent chạy trên `gemini-2.5-flash` mặc định, đổi được từng cái qua biến môi trường
`MODEL_<TÊN_AGENT>` (`app/config.py`). Ảnh dùng `gemini-3-pro-image`, chỉ có ở region
`global`.

---

## 10. Giới hạn đã biết

Ghi lại đúng hiện trạng, không phải danh sách việc phải làm.

**Kiến trúc**

- **Không kéo dài truyện ra được.** Graph giả định `total_chapters ==
  source_chapter_count`; chương N tra `E{N:03d}` ở mọi nơi. User yêu cầu số chương khác
  → chương nén đọc nhầm event, chương vượt quá số gốc không tìm thấy event node. Suy
  biến êm (văn bản placeholder, không crash) nhưng không đúng.
- **Không có khái niệm arc.** `smart_planner` chạy mỗi 5 chương và không biết arc là gì.
- **`orchestrator` có 16 lần `commit()` rải rác**, không có ranh giới giao dịch một-bước.

**Bộ nhớ và trace**

- Hai khối tóm tắt phình tuyến tính (mục 6).
- `_snapshot_inputs` khai là "every input the writer sees" nhưng thiếu `chapter_list`,
  subgraph chương và gender roster.
- Trace ~180 KB/chương, `plot_outline`/`csv_graph`/`characters` gần như giống hệt nhau
  ở mọi chương. Truyện 30 chương tốn ~6 MB. Chưa có cơ chế dọn.
- **Lỗ trace vĩnh viễn:** commit ở giữa `run_write_chapter_step` chia bước thành hai
  giao dịch. Container chết giữa chừng → chương `done` nhưng không có trace, và resume
  không quay lại được. Truyện 101 thiếu đúng chương 15 vì việc này. Cùng cơ chế với lỗi
  "summarizer failure after commit" đã ghi trong `CLAUDE.md`.

**Hội tụ**

- Vòng kiểm tra chương có thể **dao động thay vì giảm dần** và kết thúc bằng cách đi
  tiếp với lỗi còn nguyên (mục 4.3).
- `world_name_check` từ chối cả địa danh Mỹ có thật.

**Vặt**

- `_title_problem` cho 2–8 từ trong khi prompt yêu cầu 3–6.
- OCR chạy trước crop trong `image_generator.py`.
- Không có quét khởi động dọn cờ `is_running` mồ côi.
- Không có test suite, không có linter.
