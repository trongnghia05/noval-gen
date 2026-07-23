# Điểm yếu của cách tiếp cận API hiện tại

Ghi lại để đánh giá trước khi đầu tư thêm vào `webapp/`. Dựa trên lỗi thực tế gặp phải khi test end-to-end (xem lịch sử test trong `webapp/CLAUDE.md` mục "Known gotchas"), không phải suy đoán lý thuyết.

## Đã gặp lỗi cụ thể khi test (đã sửa, nhưng cho thấy sự mong manh)

1. **Reasoning-token budget là điểm mù**: model reasoning âm thầm nuốt hết `max_tokens` vào suy luận ẩn, kể cả task tầm thường (đặt tên truyện) — thất bại không rõ ràng, phải tăng budget dự phòng lớn cho MỌI lệnh gọi, không có cách biết trước cần bao nhiêu.
2. **Phải tự implement streaming để tránh mất kết nối** — CLI gốc không phải lo việc này, nhưng gọi API trực tiếp với model chậm (1-5 phút/lệnh) thì bắt buộc, tăng độ phức tạp code.
3. **Rate limit free-tier không đoán trước được** (`32/32 worker slots`) — hiện tại chỉ retry thủ công bằng tay, chưa có backoff tự động trong code.
4. **JSON structured output phụ thuộc hoàn toàn vào chất lượng model** — retry logic chỉ bắt được "JSON sai cú pháp", không bắt được "JSON đúng cú pháp nhưng nội dung tệ" (VD: chỉ tạo 1 nhân vật thay vì 3-6 theo yêu cầu).
5. **Bug thiết kế checkpoint-retry** (đã sửa) cho thấy: tự viết lại orchestration logic dễ tạo lỗ hổng resume mà CLI gốc không có (vì Claude Code CLI tự quản lý ngữ cảnh/resume theo cách khác).

## Điểm yếu cấu trúc — chưa giải quyết, cần đánh giá kỹ trước khi build tiếp

| Vấn đề | Hiện trạng |
|---|---|
| Không có job queue | Mỗi `/advance` block luôn HTTP request cho tới khi model trả lời xong (đã thấy tới 10+ phút) — không phù hợp cho web app thật, cần async job + polling/websocket |
| Không có auth | Ai chạm được port đều tạo/advance truyện được — tốn tiền API của bạn vô tội vạ |
| SQLite đơn file | Không hợp để nhiều truyện chạy đồng thời hoặc nhiều instance app |
| Không track chi phí/token | Không log token/cost theo từng truyện, từng agent — khó giám sát chi tiêu khi scale |
| Không có observability | Không structured logging, không trace được lỗi nằm giữa chuỗi 25 chương |
| Prompt caching chưa implement | Đây là 1 trong những lý do chính đáng để chuyển sang gọi API trực tiếp, nhưng prototype hiện chưa nối `cache_control` |
| Thiếu error-recovery tương đương | CLI gốc có agent `error-recovery` tự mở rộng chương quá ngắn — bản Python chưa có logic này |
| Không streaming ra frontend | User phải đợi vài phút mới thấy chữ, không có trải nghiệm "gõ dần" |
| Chưa có test nào | Đã ghi rõ trong `webapp/CLAUDE.md` |
| Secrets quản lý đơn giản | Chỉ là file `.env` phẳng — sản phẩm thật cần secrets manager |

## Tách bạch: lỗi chất lượng văn bản KHÔNG phải lỗi của kiến trúc API

Việc model free (`nvidia/nemotron-3-ultra-550b-a55b:free`) viết lẫn tiếng Trung/Pháp/Nga vào câu tiếng Việt là **đặc tính của model đó**, không phải do việc "chuyển sang gọi API" gây ra. Dùng Claude thật (trả phí) qua chính kiến trúc này nhiều khả năng sẽ hết vấn đề chất lượng — đổi lại là chi phí thật thay vì miễn phí.

## So với CLI gốc

CLI gốc tốn ~30x overhead token (đo bằng `/context` trong phiên làm việc trước) vì phải re-derive orchestration logic bằng LLM mỗi bước — kiến trúc API mới **giải quyết đúng vấn đề đó** (orchestrator giờ là code Python xác định, rẻ, nhanh). Nhưng đổi lại phải tự gánh toàn bộ phần vận hành (queue, auth, observability, retry, cache) mà CLI single-user local tool chưa từng phải lo, vì nó chưa từng là dịch vụ nhiều người dùng.
