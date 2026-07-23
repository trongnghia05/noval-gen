# Điểm yếu của cách tiếp cận API hiện tại

Ghi lại để đánh giá trước khi đầu tư thêm vào `webapp/`. Dựa trên lỗi thực tế gặp phải khi test end-to-end (xem lịch sử test trong `webapp/CLAUDE.md` mục "Known gotchas"), không phải suy đoán lý thuyết.

## Đã gặp lỗi cụ thể khi test (đã sửa, nhưng cho thấy sự mong manh)

1. **Reasoning-token budget là điểm mù**: model reasoning âm thầm nuốt hết `max_tokens` vào suy luận ẩn, kể cả task tầm thường (đặt tên truyện) — thất bại không rõ ràng, phải tăng budget dự phòng lớn cho MỌI lệnh gọi, không có cách biết trước cần bao nhiêu.
2. **Phải tự implement streaming để tránh mất kết nối** — CLI gốc không phải lo việc này, nhưng gọi API trực tiếp với model chậm (1-5 phút/lệnh) thì bắt buộc, tăng độ phức tạp code.
3. **Rate limit free-tier không đoán trước được** (`32/32 worker slots`) — hiện tại chỉ retry thủ công bằng tay, chưa có backoff tự động trong code.
4. **JSON structured output phụ thuộc hoàn toàn vào chất lượng model** — retry logic chỉ bắt được "JSON sai cú pháp", không bắt được "JSON đúng cú pháp nhưng nội dung tệ" (VD: chỉ tạo 1 nhân vật thay vì 3-6 theo yêu cầu).
5. **Bug thiết kế checkpoint-retry** (đã sửa) cho thấy: tự viết lại orchestration logic dễ tạo lỗ hổng resume mà CLI gốc không có (vì Claude Code CLI tự quản lý ngữ cảnh/resume theo cách khác).
6. **Bug tính độ dài cho REWRITE** (đã sửa, commit `0823bda`): regex phát hiện số chương gốc yêu cầu "Chương/Chapter" phải là ký tự đầu dòng, nhưng header thực tế luôn có `#` markdown đứng trước (`# Chương 1: ...`) — không khớp → tưởng nguồn chỉ có 1 chương → `words_per_chapter` bị tính sai gấp 3 lần. Bug loại này (regex/parser giả định sai định dạng input thực tế) rất dễ tái diễn ở những chỗ khác chưa test tới.
7. **Truncation JSON tái diễn** ở `character_developer` khi rewrite sang tiếng Anh (input dài hơn, output chi tiết hơn) — retry với cùng `max_tokens` vô nghĩa vì cùng nguyên nhân hết ngân sách sẽ lặp lại y hệt. Đã xử lý bằng cách set thẳng `max_tokens=32768` cho toàn bộ agent trả JSON — đây là **giải pháp tạm/không đảm bảo tuyệt đối**, chỉ dời ngưỡng lên cao chứ không loại bỏ khả năng bị cắt về mặt cấu trúc (input/output càng lớn thì ngưỡng nào cũng có thể không đủ).

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

Việc model free (`nvidia/nemotron-3-ultra-550b-a55b:free`) viết lẫn chữ Hán/Hàn vào câu tiếng Việt là **đặc tính của chính model đó với NGÔN NGỮ ĐÍCH cụ thể**, không phải lỗi kiến trúc API, và cũng không phải model "tệ nói chung":

- Tra model card chính thức của NVIDIA: **tiếng Việt không nằm trong danh sách ngôn ngữ được hỗ trợ** của model này (chỉ có English, French, Spanish, Italian, German, Japanese, Korean, Hindi, Brazilian Portuguese, Chinese).
- Test độc lập (không qua pipeline, chỉ 1 prompt đơn giản) xác nhận: viết **tiếng Việt** → lẫn chữ Hán/Hàn khoảng 50-100% số lần thử, kể cả khi cấm rõ ràng trong system prompt. Viết **tiếng Anh** (ngôn ngữ được hỗ trợ chính thức) → **sạch tuyệt đối 3/3 lần thử**, chất lượng văn học tốt.
- Đây cũng là lớp lỗi đã biết trong ngành, không riêng Nemotron (xem issue tương tự trên MiniMax-M2 và cả Gemini của Google — lẫn ký tự CJK khi ngôn ngữ đích ngoài phạm vi huấn luyện chính hoặc do tokenizer BPE artifact).

**Kết luận thực dụng**: model free này dùng tốt nếu truyện viết bằng tiếng Anh (hoặc ngôn ngữ khác trong danh sách hỗ trợ). Muốn viết tiếng Việt chất lượng, phải đổi sang model khác có hỗ trợ tiếng Việt chính thức — không phải vấn đề tiền hay kiến trúc, mà là chọn đúng model theo ngôn ngữ đích của truyện.

## So với CLI gốc

CLI gốc tốn ~30x overhead token (đo bằng `/context` trong phiên làm việc trước) vì phải re-derive orchestration logic bằng LLM mỗi bước — kiến trúc API mới **giải quyết đúng vấn đề đó** (orchestrator giờ là code Python xác định, rẻ, nhanh). Nhưng đổi lại phải tự gánh toàn bộ phần vận hành (queue, auth, observability, retry, cache) mà CLI single-user local tool chưa từng phải lo, vì nó chưa từng là dịch vụ nhiều người dùng.
