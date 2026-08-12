# Playground — giao diện Streamlit cho Novel-Gen

Một front end mỏng cho backend FastAPI. Nó **không giữ state** — mọi dữ liệu nằm
trong Postgres của backend; app này chỉ gọi API qua HTTP.

## Chạy

Trước tiên bật backend:
```bash
cd ..            # webapp/
docker compose up -d --build
```

Rồi chạy playground (máy host, cần Python 3.10+):
```bash
cd playground
pip install -r requirements.txt
streamlit run app.py
```
Mở http://localhost:8501. Nếu API không nằm ở `http://localhost:8001/api`,
đổi trong tab **⚙️ Cài đặt**.

## Chức năng

| Màn hình | Làm gì |
|---|---|
| ✍️ **Tạo truyện** | Chọn kiểu (IDEA / PREMISE / REWRITE), ngôn ngữ, thể loại, độ dài; dán nội dung hoặc tải file gốc. Bấm một nút là tạo + tự gen (title & ảnh bìa tự động). |
| 📚 **Thư viện** | Danh sách mọi truyện kèm trạng thái + thanh tiến độ; lọc theo trạng thái, tìm theo tên. |
| 📖 **Chi tiết** | Tiến độ trực tiếp (tự cập nhật khi đang gen), đọc toàn bộ hoặc từng chương, xem ảnh bìa, tải `novel.md` khi hoàn thành, tiếp tục gen nếu bị dừng. |
| ⚙️ **Cài đặt** | Địa chỉ API, ngôn ngữ mặc định. (Cấu hình model đặt ở `.env` của backend.) |

## Ghi chú

- Ảnh bìa đọc trực tiếp từ `webapp/output/<slug>/image/` nên playground cần chạy
  trên cùng máy có thư mục `output/` (mặc định khi chạy local).
- Chọn model cho từng agent / nhà cung cấp LLM là cấu hình phía máy chủ
  (`MODEL_*`, `LLM_PROVIDER` trong `.env`) — xem `webapp/app/config.py`.
