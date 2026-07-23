# Novel Rewrite

Viết lại tiểu thuyết từ một file gốc: giữ nguyên cốt truyện nhưng xây dựng hoàn toàn mới về nhân vật, bối cảnh, tên gọi, không khí và phong cách.

## Cách dùng

```
/novel-rewrite <đường_dẫn_file_gốc> [đường_dẫn_output]
```

- `<đường_dẫn_file_gốc>`: File tiểu thuyết gốc cần viết lại (bắt buộc)
- `[đường_dẫn_output]`: File đầu ra (tuỳ chọn, mặc định thêm `_rewritten` vào tên file gốc)

Ví dụ:
```
/novel-rewrite truyen_goc.txt
/novel-rewrite truyen_goc.txt truyen_moi.txt
```

## Quy trình thực hiện

Khi skill này được gọi, hãy thực hiện theo đúng các bước sau:

### Bước 1 — Đọc và phân tích file gốc

Đọc toàn bộ nội dung file gốc. Sau đó phân tích và ghi chú nội bộ (KHÔNG hiển thị ra cho người dùng) các yếu tố sau:

**Cốt truyện (GIỮ NGUYÊN):**
- Trình tự các sự kiện chính (plot beats)
- Xung đột trung tâm và cách giải quyết
- Cấu trúc: mở đầu → phát triển → cao trào → kết thúc
- Các bước ngoặt và twist quan trọng
- Thông điệp / chủ đề cốt lõi của câu chuyện

**Yếu tố cần thay thế hoàn toàn:**
- Tên và danh sách tất cả nhân vật
- Bối cảnh địa lý, thời đại, thế giới
- Nghề nghiệp, địa vị xã hội của nhân vật
- Tên địa danh, tổ chức, vật thể đặc trưng
- Không khí, giọng văn, phong cách kể chuyện

### Bước 2 — Thiết kế thế giới mới

Tạo ra một bộ yếu tố mới hoàn toàn khác biệt so với bản gốc:

1. **Nhân vật mới**: Đặt tên mới, ngoại hình, cá tính, động lực riêng — phải tương ứng về vai trò với nhân vật gốc nhưng không được giống về bất kỳ chi tiết nào
2. **Bối cảnh mới**: Thay đổi thời đại, địa điểm hoặc thế giới (ví dụ: nếu gốc là hiện đại Việt Nam → có thể chuyển sang cổ đại châu Á, hoặc tương lai khoa học viễn tưởng, hoặc thế giới fantasy)
3. **Không khí mới**: Nếu gốc u ám → có thể làm nhẹ nhàng hơn hoặc thậm chí u ám theo kiểu khác; thay đổi giọng văn

Trình bày bảng thiết kế này cho người dùng xem trước khi viết, với định dạng:

```
## Bản thiết kế thế giới mới

### Nhân vật
| Vai trò | Nhân vật gốc | Nhân vật mới |
|---------|-------------|--------------|
| ...     | ...         | ...          |

### Bối cảnh
- Gốc: ...
- Mới: ...

### Giọng văn / Không khí
- Gốc: ...
- Mới: ...
```

Hỏi người dùng: **"Bạn có muốn điều chỉnh thiết kế này trước khi tôi bắt đầu viết không? (Trả lời 'ok' để tiến hành, hoặc nêu các thay đổi bạn muốn)"**

### Bước 3 — Viết lại

Sau khi người dùng xác nhận (hoặc sau khi điều chỉnh theo yêu cầu), viết lại toàn bộ câu chuyện:

**Quy tắc bắt buộc:**
- Cốt truyện phải đi theo đúng skeleton đã phân tích — không thêm, không bớt sự kiện chính
- Mọi tên riêng (người, nơi, vật) phải là tên mới hoàn toàn
- Không sao chép câu văn gốc — toàn bộ phải được viết lại từ đầu
- Độ dài: cố gắng tương đương hoặc dài hơn bản gốc
- Giữ nguyên số chương / phần nếu bản gốc có chia chương

**Khi viết:**
- Xây dựng nhân vật mới có chiều sâu qua hành động và đối thoại, không qua mô tả suông
- Bối cảnh mới phải được dệt vào câu chuyện tự nhiên
- Giữ nguyên nhịp điệu cảm xúc của từng cảnh (căng thẳng vẫn căng thẳng, lãng mạn vẫn lãng mạn — nhưng qua lăng kính mới)

### Bước 4 — Lưu file và báo cáo

Sau khi viết xong:
1. Lưu nội dung vào file output đã xác định
2. Báo cáo cho người dùng:
   - Đường dẫn file output
   - Số chương / phần đã viết
   - Tóm tắt ngắn những thay đổi lớn nhất so với bản gốc

---

## Lưu ý quan trọng

- Nếu file gốc quá dài (>50.000 từ), hỏi người dùng có muốn xử lý từng chương một không
- Nếu không tìm thấy file gốc, thông báo lỗi rõ ràng và dừng lại
- Nếu người dùng không cung cấp đường dẫn file, hỏi lại
- Ngôn ngữ viết lại: mặc định giống ngôn ngữ của file gốc, trừ khi người dùng yêu cầu khác
