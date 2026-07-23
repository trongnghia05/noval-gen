# World State — [Tên Truyện]
<!-- File này được chapter-summarizer cập nhật sau MỖI chương -->
<!-- chapter-writer đọc file này thay vì đọc lại toàn bộ manuscript -->

**Cập nhật lần cuối sau**: Chương 0 (khởi tạo)
**Timeline trong truyện**: [Ngày/Tháng/Năm hoặc mô tả thời điểm]

<!--
QUY TẮC BẮT BUỘC CHO TOÀN BỘ FILE NÀY:
Mọi bảng dưới đây (quan hệ, plot threads, foreshadowing, bí mật, vật thể) là bảng SỐNG —
khi một dòng đã tồn tại (cùng nhân vật/cặp/thread/vật thể), SỬA ĐÈ lên dòng đó, KHÔNG thêm dòng mới trùng lặp.
File này phản ánh đúng-và-chỉ trạng thái NGAY SAU chương gần nhất — không phải lịch sử tích luỹ.
Lịch sử chi tiết (ai đổi gì, lúc nào, tại sao) thuộc về `planning/state-log.md`, không lặp lại ở đây.
-->

---

## TRẠNG THÁI NHÂN VẬT (snapshot sống — ghi đè mỗi chương)

### [Tên nhân vật chính] <!-- nếu có bí danh, ghi kèm: (còn gọi là: ...) khớp với Aliases trong characters.md -->
- **Vị trí**: ...
- **Thể trạng**: [khỏe / bị thương / ốm / kiệt sức]
- **Trang phục / Vật sở hữu**: ...
- **Tâm trạng / Cảm xúc**: ...
- **Biết những gì** (thông tin quan trọng nhân vật này đã nắm):
  - ...
- **KHÔNG biết** (thông tin quan trọng nhân vật chưa biết):
  - ...
- **Mục tiêu hiện tại**: ...
- **Xung đột nội tâm hiện tại**: ...

### [Tên nhân vật phụ 1]
- **Vị trí**: ...
- **Thể trạng**: ...
- **Tâm trạng**: ...
- **Biết những gì**: ...
- **Mục tiêu hiện tại**: ...

### [Tên nhân vật phụ 2]
[tương tự]

<!--
Nhân vật Tier "decorative"/xuất hiện thoáng qua (xem characters.md) KHÔNG cần mục riêng ở đây —
chỉ track chi tiết cho nhân vật core/important để tránh phình file khi truyện có nhiều chương.
-->

---

## QUAN HỆ NHÂN VẬT (bảng sống — mỗi cặp CHỈ có 1 dòng, sửa đè khi thay đổi)

| Cặp | Trạng thái hiện tại | Cập nhật lần cuối (chương) |
|-----|---------------------|------------------------------|
| A ↔ B | [thù địch / trung lập / thân thiết / yêu nhau] | Ch.X |
| A ↔ C | ... | ... |

---

## PLOT THREADS ĐANG HOẠT ĐỘNG (bảng sống — mỗi thread CHỈ có 1 dòng)

| Thread | Trạng thái | Xuất hiện lần cuối | Cần giải quyết trước |
|--------|-----------|-------------------|---------------------|
| [Tên thread] | [mới mở / đang tiến / gần kết / đã đóng] | Ch.X | Ch.Y |

<!-- Thread đã "đã đóng" giữ lại 1-2 checkpoint để continuity-editor đối chiếu, sau đó có thể xoá dòng khỏi bảng này. -->

---

## FORESHADOWING (sổ theo dõi — mỗi chi tiết CHỈ có 1 dòng, cập nhật trạng thái tại chỗ)

| ID | Chi tiết đã gieo | Gieo ở chương | Trạng thái | Dự kiến payoff trước | Mức độ ưu tiên |
|----|-------------------|---------------|------------|------------------------|----------------|
| F1 | [Chi tiết] | Ch.X | [planted / advanced / resolved] | Ch.Y | [Cao/Trung/Thấp] |

<!-- Khi trạng thái = resolved, ghi thêm "(đã payoff ở Ch.Z)" vào cột Trạng thái — KHÔNG xoá dòng, để continuity-editor biết đã xử lý, tránh nhắc lại. -->

---

## BÍ MẬT & THÔNG TIN BẤT CÂN XỨNG (bảng sống)

| Thông tin | Độc giả biết? | Nhân vật A biết? | Nhân vật B biết? |
|-----------|--------------|-----------------|-----------------|
| [Bí mật X] | Có | Không | Có |

---

## VẬT THỂ QUAN TRỌNG (bảng sống — vị trí hiện tại, không phải lịch sử di chuyển)

| Vật thể | Hiện đang ở | Ai giữ | Ghi chú |
|---------|------------|--------|---------|
| [Tên vật] | [địa điểm] | [tên nhân vật] | ... |

---

## ĐỊA ĐIỂM & TIMELINE

- **Địa điểm chính hiện tại của câu chuyện**: ...
- **Thời gian đã trôi qua từ đầu truyện**: ...
- **Sự kiện sắp xảy ra** (đã được thiết lập): ...

---

## GHI CHÚ CONTINUITY QUAN TRỌNG (chỉ giữ chi tiết còn actionable)

<!-- Những chi tiết nhỏ nhưng phải nhất quán, đang còn hiệu lực. Chi tiết nào không còn liên quan (nhân vật đã rời truyện, vết thương đã lành...) thì XOÁ khỏi đây, đừng giữ mãi. -->
- [Ví dụ: mắt nhân vật A màu xanh, không phải nâu]
- [Ví dụ: thành phố X cách thành phố Y 3 ngày đường ngựa]
- [Ví dụ: nhân vật B bị thương tay phải từ Ch.7, chưa lành]

---

## Ghi chú kiến trúc bộ nhớ (đọc để hiểu, không phải nội dung truyện)

Hệ thống bộ nhớ của dự án này có 3 lớp, tách trách nhiệm rõ ràng:

1. **`planning/characters.md`** — hồ sơ nhân vật CỐ ĐỊNH (vai trò, tính cách, arc, bí danh). Hiếm khi đổi, không phải chỗ ghi trạng thái tạm thời.
2. **`planning/state-log.md`** — NHẬT KÝ THAY ĐỔI có cấu trúc, append-only, chính xác tuyệt đối (chương nào, ai, đổi gì, từ đâu → đâu, vì sao). Đây là nguồn sự thật để tra cứu khi có nghi ngờ mâu thuẫn — không bao giờ bị nén/tóm tắt mất chi tiết.
3. **`planning/world-state.md`** (file này) — SNAPSHOT SỐNG, ghi đè mỗi chương, phản ánh trạng thái NGAY LÚC NÀY. Được suy ra từ state-log.md nhưng viết dạng dễ đọc cho chapter-writer, không cần chứa lịch sử.

`chapter-writer` chỉ cần đọc lớp 3 (nhanh, luôn ngắn gọn). `continuity-editor` khi nghi ngờ mâu thuẫn thì tra lớp 2 (`state-log.md`) để xác minh chính xác thay vì đọc lại toàn bộ manuscript.
