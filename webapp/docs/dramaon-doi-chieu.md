# Đối chiếu `[DramaOn] Cấu trúc truyện 3_8.docx` với pipeline hiện tại

> Tài liệu nguồn: 98 đoạn, 5 mục lớn, 3 bảng, 0 hình. Kết thúc giữa chừng ở mục 4.1 —
> xem "Ghi chú về bản tài liệu" ở cuối.
>
> Cột "Ở đâu" trỏ về file thật. Chi tiết cách pipeline vận hành: `docs/pipeline-map.md`.
>
> Cập nhật: 13/08/2026

---

## Bảng tổng

Ký hiệu: **✅ có** · **🟡 một phần** · **❌ chưa có** · **⚠️ xung đột**

| # | Yêu cầu của tài liệu | TT | Ở đâu trong hệ thống |
|---|---|---|---|
| **B** | **Benchmark** | | |
| B1 | 3 chương free trước tường phí | ❌ | không có khái niệm chương free |
| B2 | Giá 20 coin/chương, đồng giá | ❌ | ngoài phạm vi (không có tầng thanh toán) |
| B3 | Chương dài 1.000–1.500 từ (median 1.186) | ✅ | median thực đo **1.327** và **1.630** — xem ghi chú dưới |
| B4 | Chương 1 dài hơn (~1.300 từ) | ❌ | `words_per_chapter` phẳng cho mọi chương |
| B5 | Truyện hit 200–650+ chương | ❌ | thực tế 20–30; graph khoá `total_chapters == source_chapter_count` |
| B6 | Hook phải nổ trong 3 chương đầu | 🟡 | có "hook mạnh" cho chương 1, không có khái niệm 3-chương-free |
| **1** | **Công thức tên & tiêu đề** | | |
| 1.1 | Slot `[ĐỘNG TỪ] + [DANH XƯNG QUYỀN LỰC] + [TWIST]` | ✅ | `prompts/title_generator.md` — **nguyên văn** |
| 1.1 | Ba khuôn (mệnh lệnh / đảo chiều / cấm kỵ) | ✅ | cùng file, cùng ba khuôn, cùng ví dụ |
| 1.2 | Từ vựng vàng theo slot | ✅ | cùng file — cùng ba danh sách |
| 1.x | Tiêu đề 3–6 từ | 🟡 | prompt ghi 3–6, code `_title_problem` cho **2–8** |
| **2** | **Công thức blurb / hook** | | |
| 2.1 | Blurb 120–180 từ | ✅ | `prompts/novel_metadata.md` — "must be within 120-180 words. Count." |
| 2.1 | Khung 4 nhịp (mồi sốc → bối cảnh → đảo chiều → móc kép) | 🟡 | có 4 thành phần nhưng **không đánh số theo nhịp**, không có "câu mồi sốc" riêng |
| 2.2 | Ngôi nhất, thì hiện tại | 🟡 | prompt ghi "present-tense-ish marketing voice", không ép ngôi nhất |
| **3** | **Kiến trúc nội dung** | | |
| 3.1 | Story Bible bất biến, dạng JSON/markdown | ✅ | `story.story_bible` + `world_bible` (JSON) |
| 3.1 | Premise 1 câu + trope stack | ✅ | `logline` + `tags` (`novel_metadata`) |
| 3.1 | Mọi nhân vật ≥18 | ✅ | `graph_character_enricher.md` — nữ chính 18–20, nam cuối 20–40 |
| 3.1 | Voice sheet mỗi nhân vật | ✅ | `new_voice_profile` — register, từ vựng, nhịp, tật nói, tính cách, 2–3 câu mẫu |
| 3.1 | Bản đồ quyền lực: ai áp chế ai → đảo ra sao | ✅ | cạnh `RELATION` + `ARC_CHANGE` (có `old_val`/`new_val`/`chapter_from`) |
| 3.1 | Luật thế giới | ✅ | `WorldBibleOut.systems[]` (rules / origin / role_in_plot) |
| 3.1 | Heat tier + giới hạn tuyệt đối | ❌ | không tồn tại |
| 3.2 | Archetype nhân vật để reskin nhanh | ✅ | `world_designer` sinh archetype; `graph_character_enricher` gán vai |
| 3.2 | Love triangle / rival thứ hai làm nhiên liệu | ❌ | không có luật nào yêu cầu |
| 3.3 | Chia arc 15–30 chương | ❌ | không có khái niệm arc; checkpoint cứng mỗi 5 chương |
| 3.3 | Mỗi 15–30 ch một "boss fight" cảm xúc | ❌ | — |
| 3.3 | Bảng giai đoạn Hook/Setup/Escalation/Midpoint/Payoff/Climax | 🟡 | có ba hồi 24/28/24/24% cho IDEA/PREMISE; **REWRITE bỏ qua hoàn toàn**, bám EVENT node |
| 3.3 | Slow-burn payoff rải đều | 🟡 | có foreshadowing plant/payoff, không có khái niệm leo thang thân mật |
| 3.3 | Climax + tail để ngỏ spin-off | 🟡 | `plot_architect` yêu cầu epilogue, không yêu cầu để ngỏ |
| **4** | **Chiến lược heat / rating** | | |
| 4.1 | Ba tầng Clean / Steamy / Explicit | ❌ | không có; từ "heat" trong code chỉ nói về ánh sáng ảnh |
| 4.1 | Quy tắc phát hành theo kênh | ❌ | tài liệu cũng chưa viết phần này |

---

## Chi tiết theo mục

### Mục 1 — Tiêu đề: đã cài gần như nguyên văn

Đây là mục khớp nhất. `prompts/title_generator.md` (11,8 KB) dùng **đúng cấu trúc slot,
đúng ba khuôn, đúng ba danh sách từ vựng** như tài liệu:

| Tài liệu | Prompt |
|---|---|
| `[ĐỘNG TỪ MỆNH LỆNH/SỞ HỮU] + [DANH XƯNG QUYỀN LỰC] + [TWIST]` | `[POSSESSIVE / IMPERATIVE VERB] + [POWER TITLE] + [TWIST]` |
| Mệnh lệnh/khát khao — *Claim Me, Alpha* | Mould 1 — Command / craving — *Claim Me, Alpha* |
| Tuyên bố nghịch cảnh — *Reborn to Reject the Alpha* | Mould 2 — Reversal declaration — cùng ví dụ |
| Quan hệ cấm kỵ — *My Stepbrother's Bride* | Mould 3 — Forbidden relationship — cùng ví dụ |
| Alpha, Luna, Don, Boss, CEO, Tycoon, Billionaire, Mafia King, Master, Daddy | + Warlord, Matriarch, Guildmaster, Heir (bỏ *Daddy*) |
| Claim, Breed, Break, Ruin, Own, Tame, Wreck, Crave | + Keep |
| Rebirth/Reborn, Revenge, Rejected, Secret Baby, Fake Marriage, Contract, Substitute Bride, Second Chance, Hidden Identity | + Divorce, Betrayal |

Prompt đi **xa hơn tài liệu** ở ba chỗ, đều rút ra từ lỗi đã xuất xưởng:

1. **Luật chống bịa** — cấm nâng cấp giai đoạn quan hệ ("hẹn hò giả" ≠ "hôn nhân giả"),
   cấm hàn hai quan hệ thành một danh từ (`Rival Sister`, `Enemy Brother` — đã có hai
   tiêu đề xuất xưởng đúng lỗi này).
2. **Ba cách mở cho khuôn 3** — mở từ phía cô / phía anh / từ hành động, kèm cảnh báo
   khuôn này tự sụp về cách mở đầu tiên.
3. **Chốt chặn code** `_title_problem` chạy sau prompt, bất kể user yêu cầu gì.

**Xung đột duy nhất, và nó có thật:**

> Tài liệu xếp `My Stepbrother's Bride` làm ví dụ mẫu mực của khuôn 3.
> Prompt lại có luật cứng: *"Do not open with a possessive pronoun by default. `My …`
> earns its place only when the ownership IS the hook."*

Luật đó thêm vào ở commit `78df7ba`, sau khi đo thấy **một cách mở chiếm toàn bộ kho
tiêu đề**. Nên đây không phải mâu thuẫn tình cờ: tài liệu mô tả khuôn *đúng*, prompt
chặn việc *lạm dụng* khuôn đó. Cả hai đều đúng ở tầng của mình — nhưng nếu lấy tài liệu
làm chuẩn nghiệm thu thì phải biết luật này tồn tại.

**Sai lệch cần sửa:** prompt yêu cầu 3–6 từ, code cho qua 2–8 từ.

```python
if not 2 <= len(words) <= 8:
    return f"do dai {len(words)} tu, phai trong khoang 3-6 tu"
```

Thông báo lỗi nói 3–6 nhưng điều kiện là 2–8. Tiêu đề 2 từ và 7–8 từ lọt qua được, dù
tài liệu và prompt đều cấm.

### Mục B3 — Độ dài chương: khớp, nhưng khớp một cách tình cờ

Phải phân biệt **số mục tiêu** và **số thực tế**, vì chúng lệch nhau đáng kể:

| Truyện | `words_per_chapter` | Median thực tế | Vượt |
|---|---|---|---|
| 98 (30 ch) | 951 | **1.327** (967–1.574) | +40% |
| 101 (20 ch) | 1.477 | **1.630** (1.386–1.884) | +10% |

Số thực tế nằm đúng dải tài liệu đề ra (1.000–1.500 với truyện 98; 101 hơi vượt). Nhưng
đó **không phải vì hệ thống nhắm vào dải đó** — với REWRITE, `words_per_chapter` được
suy ra từ mật độ của truyện gốc (`length_calc.py`), nên nếu nguồn có chương 400 từ thì
mục tiêu sẽ là 400.

Phần vượt đến từ `chapter_writer`: có vòng nới khi cảnh < 60% hoặc chương < 85% mục
tiêu, nhưng **không có vòng nào cắt xuống**. Prompt ghi ±15% mà không có gì ép phía trên.

Kết luận: hạng mục này đang đạt, nhưng đạt do trùng hợp giữa mật độ nguồn và độ lệch một
chiều của vòng nới — không phải do có mục tiêu thị trường nào trong code. Muốn nó là một
đảm bảo thì phải kẹp `words_per_chapter` vào dải 1.000–1.500 thay vì thả theo nguồn.

### Mục 2 — Blurb: đúng độ dài, khác cấu trúc

`novel_metadata.md` ép **đúng 120–180 từ** như tài liệu, và cấm lộ kết. Nhưng nó mô tả
blurb theo **thành phần** chứ không theo **nhịp**:

| Tài liệu — 4 nhịp | `novel_metadata.md` |
|---|---|
| 1. Câu mồi sốc (1 dòng) | *(không có)* |
| 2. Bối cảnh + phản bội/ràng buộc | "Set up the protagonist, the world" |
| 3. Đảo chiều — nhân vật có vũ khí mới | "the inciting situation" |
| 4. Đòn móc kép — câu hỏi lửng/đe doạ | "the central tension" |

Khác biệt thật nằm ở nhịp 1. Tài liệu đòi một **tuyên bố giật mình mở đầu**, ví dụ gốc:
*"I let a stranger wreck me. Monday, he signed my paycheck."* Prompt hiện tại không có
gì tương đương, và cũng không ép ngôi nhất.

Đây là mục **sửa rẻ nhất và tác động rõ nhất** trong toàn bộ danh sách: một prompt, ~5
dòng, không đụng code.

### Mục 3.1 — Story Bible: khớp gần hết

Sáu trên bảy hạng mục đã có, và có ở dạng **cấu trúc** chứ không phải văn xuôi:

- Bản đồ quyền lực không phải một đoạn mô tả mà là cạnh `RELATION` có `strength` +
  `chapter_from`/`chapter_to`, cộng `ARC_CHANGE` ghi `old_val → new_val`. Tức là "ai áp
  chế ai lúc đầu → đảo ra sao" được ghi **có thể truy vấn được**, không phải đọc hiểu.
- Voice sheet giàu hơn tài liệu yêu cầu: ngoài "cách nói" còn có tật nói, tính cách, và
  2–3 câu thoại mẫu; kèm luật "make voices MAXIMALLY DISTINCT across the cast".
- Giới hạn tuổi ≥18 khớp chính xác. Prompt còn chặt hơn: nữ chính **18–20** và phải có
  hoàn cảnh hợp tuổi (sinh viên, thực tập, tập sự), không được nâng tuổi lên chỉ để thu
  hẹp khoảng cách với nam chính.

**Thiếu: heat tier và "giới hạn tuyệt đối".** Không có trường nào trong `Story`, không
có prompt nào nhắc tới. Truyện hiện sinh ra ở mức độ nào là do model tự quyết mỗi lần.

### Mục 3.3 — Cấu trúc macro: đây là khoảng cách lớn nhất

Tài liệu và hệ thống đang nói về hai quy mô khác hẳn nhau.

| | Tài liệu | Hệ thống |
|---|---|---|
| Tổng chương | 200–650+ | 20–30 |
| Đơn vị cấu trúc | **arc 15–30 chương** | không có |
| Nhịp kiểm tra | mỗi arc một "boss fight" | checkpoint cứng mỗi 5 chương |
| Escalation | chương 20–120 | không có giai đoạn tương ứng |

Cấu trúc ba hồi của `plot_architect` (24 / 28 / 24 / 24%) là cấu trúc **một cuốn tiểu
thuyết**, không phải một serial dài. Và với REWRITE — tức là mọi truyện đang chạy — nó
**bị bỏ qua hoàn toàn**:

> *"If `input_type = REWRITE` AND the user message contains a Story Knowledge Graph
> section: … do NOT use the three-act template below."*

Nên hiện tại cấu trúc truyện = cấu trúc truyện gốc. Đó là lựa chọn có chủ ý của chế độ
REWRITE, nhưng nghĩa là **mục 3.3 của tài liệu chưa từng được áp dụng ở bất kỳ truyện
nào đã sinh ra.**

**Rào cản kỹ thuật để kéo dài:** graph giả định `total_chapters == source_chapter_count`
và tra sự kiện chương N bằng khoá `E{N:03d}`. Yêu cầu số chương khác đi → chương nén đọc
nhầm event, chương vượt số gốc không có event node để bám. Suy biến êm, không crash,
nhưng không đúng. Muốn chạm tới quy mô 200+ chương thì đây là thứ phải sửa trước, không
phải prompt.

### Mục 4 — Heat tier: không tồn tại ở cả hai phía

Không có gì trong code. Kiểm tra:

```
"heat"  → chỉ trong image_generator/image_prompt (nhiệt độ ánh sáng ảnh)
"spice" · "18+" · "age_gate" · "rating tier"  → 0 kết quả liên quan
```

Nhưng tài liệu cũng **chưa viết xong mục này** — nó dừng ngay sau bảng ba tầng, phần
"quy tắc phát hành" được hứa ở câu mở đầu không có. Nên đây chưa phải một yêu cầu đủ rõ
để cài đặt.

---

## Những gì pipeline có mà tài liệu không nhắc

Không phải thừa — đây là phần lớn công sức của hệ thống, và nó giải quyết những vấn đề
tài liệu chưa chạm tới.

| Cơ chế | Giải quyết vấn đề gì |
|---|---|
| **Hợp đồng thị trường** (`market.py`) nối vào 5 prompt | Truyện trôi khỏi bối cảnh Mỹ đương đại |
| **Graph tri thức hai lớp** source/new | Giữ cốt truyện gốc trong khi thay toàn bộ bề mặt |
| **Từ điển tên + thay tên xác định** | Tên gốc rò sang truyện mới |
| **Ba cơ chế chống lặp** (`beat_type`, `state_delta`, sổ motif) | Nhiều chương liền cùng một nhịp |
| **Vòng kiểm tra 10 vòng, 5 bộ soát** | Chương cụt, lệch POV, thoại rỗng, sai thời đại |
| **Bộ nhớ 6 lớp + 3 cửa sổ trượt** | Viết chương 30 mà không đọc lại 29 chương |
| **Luật quote nguyên văn** của summarizer | Câu thoại bịa lan thành cốt truyện |
| **Sinh bìa + 2 thumbnail** với 14 trục rút thăm | Ảnh bìa lặp lại giữa các truyện |
| **Trace tái lập** mỗi chương | Dựng lại được vì sao một chương ra như vậy |

Đáng chú ý: tài liệu bàn *cái gì làm nên truyện bán được*, còn hệ thống dành phần lớn
công sức cho *làm sao sinh hàng loạt mà không hỏng*. Hai mối quan tâm bổ sung cho nhau
chứ không chồng lấn — nên bảng đối chiếu này nhiều ô ❌ không có nghĩa hệ thống yếu, mà
có nghĩa hai bên đang giải hai bài toán khác nhau.

---

## Nếu muốn đóng khoảng cách — xếp theo chi phí

Không phải kế hoạch, chỉ là ước lượng công sức từ những gì đã đọc trong code.

**Rẻ — chỉ sửa prompt, không đụng code**

1. Blurb 4 nhịp + câu mồi sốc + ngôi nhất (`novel_metadata.md`, ~5 dòng)
2. Chương 1 dài hơn ~10% (`chapter_writer.md` + một tham số truyền vào)
3. Sửa `_title_problem` cho khớp 3–6 từ như prompt và tài liệu

**Vừa — thêm trường + prompt**

4. Heat tier: một trường trên `Story`, nối vào `chapter_writer` và `image_prompt` — chờ
   tài liệu viết xong mục 4
5. Khái niệm "3 chương free": đánh dấu chương 1–3 và yêu cầu blueprinter dồn hook

**Đắt — đụng kiến trúc**

6. Khái niệm arc 15–30 chương: `smart_planner` phải biết arc, `plot_architect` phải chia
   arc, thêm state cho arc hiện tại
7. **Kéo dài lên 200+ chương**: phải gỡ giả định `total_chapters ==
   source_chapter_count` và dựng ánh xạ tường minh `output_chapter → source_event`. Đây
   là điều kiện tiên quyết cho toàn bộ mục 3.3, và là thứ đắt nhất trong danh sách.

---

## Ghi chú về bản tài liệu

Bản `3_8` **chưa hoàn chỉnh**, hai chỗ dở dang:

- Mục 3.1 viết *"Heat tier (xem Phần 5)"* — **không có Phần 5** trong tài liệu.
- Mục 4 mở đầu *"Tách thành tầng nội dung **và quy tắc phát hành**"* rồi dừng ngay sau
  bảng ba tầng. Phần quy tắc phát hành không có. File kết thúc giữa chừng ở 4.1.

Nên hai mục ❌ nặng nhất trong bảng tổng (heat tier, quy tắc phát hành) chưa đủ rõ để
cài đặt, chứ không phải đã rõ mà bị bỏ.
