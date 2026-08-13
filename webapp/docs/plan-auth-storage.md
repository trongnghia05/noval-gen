# Plan: đăng nhập 2 vai + chuyển ảnh lên object storage

> Hai việc gộp một đợt vì chúng chạm cùng chỗ (endpoint ảnh, playground, docker-compose).
>
> Quyết định đã chốt: **GCS bucket `ai-novel`, KHÔNG public — dùng signed URL** ·
> **dùng service account sẵn có, không thêm credential** · **key theo `story_id`, mỗi
> phiên bản một key** · **2 tài khoản username + password, lấy từ `.env`** ·
> **mọi người thấy chung mọi truyện** (không có cột chủ sở hữu).
>
> Tổng: **~3,75 ngày**. Lập 13/08/2026.
>
> **Tiến độ:** GĐ1 ✅ xong · GĐ2 ✅ xong · GĐ3 ⬜ chưa bắt đầu
>
> **Hai điều học được khi làm, khác với plan ban đầu:**
> 1. **Không dùng prefix riêng cho preview.** Plan viết `<id>/preview/<uuid>/...` để
>    xoá cả bộ bằng một `delete_prefix`. Sai: duyệt một preview là **đổi trạng thái,
>    không phải di chuyển** — nên object vẫn nằm dưới `preview/` trong khi đã là ảnh
>    chính thức, và bất kỳ đoạn dọn dẹp nào xoá theo prefix đó sẽ xoá luôn ảnh live.
>    Phát hiện khi test accept. Nay live và preview dùng **chung một không gian key**;
>    thứ phân biệt chúng là cột `state`, và chỉ cột đó.
> 2. **`_live_image_bytes` là mục bắt buộc, không phải tuỳ chọn.** Gen lại một
>    thumbnail nạp cover đang có làm tham chiếu khuôn mặt. Đọc từ đĩa sau khi ảnh lên
>    bucket sẽ âm thầm trả None và mọi nhân vật đổi mặt.

---

## Điều cần biết trước khi bắt đầu

**Storage là Google Cloud Storage, không phải AWS S3.** `auth/ml-ikame.json` là service
account của Google Cloud (`type: service_account`, `project_id: ml-ikame`), đang mount vào
`/auth:ro` để chạy Vertex AI. Nó không ký được request AWS — hai hệ xác thực khác nhau,
không bắc cầu. Dùng lại SA đó nghĩa là bucket `ai-novel` nằm trên GCS.

Nếu về sau thực sự chuyển sang AWS S3 thì phải cấp access key/secret riêng, và chỉ mục
1.1–1.2 đổi thư viện (`boto3` thay `google-cloud-storage`); toàn bộ phần còn lại của plan
giữ nguyên vì đã tách qua `app/storage.py`.

**Bucket public nghĩa là ảnh preview cũng public.** Bản ảnh vừa gen, chưa duyệt cũng có
URL đọc được như ảnh chính thức. Ai biết link là xem được. Chấp nhận được với công cụ nội
bộ; nếu muốn giảm rủi ro mà không đổi kiến trúc thì đặt preview dưới prefix ngẫu nhiên
(`<story_id>/preview/<uuid4>/cover.webp`) — link không đoán được, tốn thêm ~15 phút.
**Ghi ở đây một lần, sẽ không nhắc lại.**

**Cổng API 8001 đang publish ra host.** Nên chỉ khoá Streamlit là vô nghĩa — người dùng gõ
thẳng `curl -X DELETE localhost:8001/api/stories/101` là xong. Cả hai tầng đều phải gate.
Đây là lý do giai đoạn 3 không được bỏ.

**Streamlit chạy trên websocket**, nút bấm không phải HTTP verb. Nên không thể chặn viewer
bằng reverse proxy lọc method — phải ẩn/khoá nút trong code.

---

## Giai đoạn 1 — Lớp lưu trữ (~0,75 ngày)

Làm trước vì mọi thứ khác phụ thuộc vào nó, và làm được độc lập.

### 1.1 Phụ thuộc và cấu hình

- `requirements.txt`: thêm `google-cloud-storage>=2.14`
- `.env.example` + `.env`: `GCS_BUCKET=ai-novel`
- Xác thực: **không thêm biến mới.** `GOOGLE_APPLICATION_CREDENTIALS` đã trỏ vào SA dưới
  `/auth` (mount `../auth:/auth:ro` trong `docker-compose.yml`), và `google-cloud-storage`
  đọc đúng biến đó — cùng cơ chế Vertex AI đang dùng
- `app/config.py`: đọc `GCS_BUCKET`
- **Việc cần làm ngoài code:** cấp cho service account quyền `roles/storage.objectAdmin`
  trên bucket `ai-novel`, và bật đọc công khai (`allUsers` → `roles/storage.objectViewer`)

### 1.2 `app/storage.py` (file mới, ~70 dòng)

Bốn hàm, không hơn — mọi thứ khác trong hệ thống chỉ gọi qua đây, nên đổi nhà cung cấp
sau này chỉ phải sửa đúng file này:

```
put(key, data, content_type) -> str      # upload, trả về public URL
delete(key)                              # xoá một object
delete_prefix(prefix)                    # xoá cả preview set
public_url(key) -> str                   # https://storage.googleapis.com/ai-novel/<key>
```

Quy ước key — **đánh theo `story_id`**, không theo slug:

```
<story_id>/cover.webp
<story_id>/thumbnail1.webp
<story_id>/thumbnail2.webp
<story_id>/preview/<uuid4>/cover.webp
```

Dùng `story_id` thay `slug` là đúng hơn: `slug` bị đóng băng lúc tạo truyện **chính vì**
thư mục export khoá theo nó, nên đổi tiêu đề là sinh lệch giữa tên hiển thị và tên thư
mục. `story_id` không bao giờ đổi, nên vấn đề đó biến mất.

### 1.3 Nghiệm thu

Upload một ảnh, mở URL trả về bằng trình duyệt (phải xem được khi chưa đăng nhập gì),
xoá, xác nhận 404.

---

## Giai đoạn 2 — Bảng `story_images` + đổi luồng ảnh (~1,25 ngày)

### 2.1 Vì sao cần bảng

Filesystem hiện gánh **4 việc**, GCS chỉ thay được việc thứ nhất:

| Việc | Đang làm bằng | Sau khi lên GCS |
|---|---|---|
| Lưu bytes | file | GCS |
| Phân biệt live / preview | tên thư mục `.preview/` | **cột `state`** |
| Biết ảnh nào mới | `mtime` của file | **cột `updated_at`** |
| Báo job đang chạy / lỗi | file mốc `.running` / `.error` | **cột trên `stories`** (mục 2.5) |

Ba chỗ trong code đang đọc `mtime` và sẽ hỏng nếu không thay:

```
playground/app.py  preview_images(slug, newer_than)   # tách ảnh vừa gen khỏi ảnh copy sẵn
playground/app.py  _image_stamp(slug)                 # key cache zip
```

`_image_stamp` tồn tại vì gen lại ảnh không làm đổi số chương — bỏ nó đi là zip tiếp tục
phát bản có ảnh cũ.

### 2.2 Bảng

```
story_images
  id           PK
  story_id     FK -> stories.id, ON DELETE CASCADE
  stem         cover | thumbnail1 | thumbnail2
  state        live | preview
  object_key   text
  width, height, content_type
  created_at, updated_at
  UNIQUE(story_id, stem, state)
```

Migration Alembic `0007_add_story_images`.

### 2.3 `image_generator.generate()`

Hiện ghi ra `out_dir` (dòng ~898: `img.save(out_dir / filename, ...)`). Đổi thành: dựng
ảnh trong bộ nhớ → `storage.put(...)` → upsert dòng `story_images`.

Giữ nguyên toàn bộ phần sinh prompt, rút thăm 14 trục, và vòng verify — **không đụng**.

### 2.4 accept / discard thành giao dịch DB

Hiện là vòng `shutil.move` (`routes.py:503`) — hỏng giữa chừng để lại bộ ảnh nửa mới nửa
cũ. Sau khi đổi:

```
accept  = xoá object live cũ trên GCS
        + UPDATE story_images SET state='live' WHERE state='preview'   (1 giao dịch)
discard = DELETE các dòng preview + storage.delete_prefix(...)
```

**Đây là cải thiện thật, không phải chi phí di trú**: accept trở thành nguyên tử.

### 2.5 Trạng thái job thay file mốc

`.running` / `.error` là **trạng thái job**, không phải trạng thái ảnh — không nhét vào
`story_images`. Theo đúng khuôn `stories` đã có (`is_running`, `stop_requested`):

- thêm `image_job_running` (bool) + `image_job_error` (text) vào `stories`
- `_regen_images_job` ghi vào DB thay vì ghi file
- thêm hai trường đó vào `GET /stories/{id}`
- playground đọc từ API thay vì đọc file mốc

Việc này **bắt buộc dù có GCS hay không**, vì playground đang đọc file mốc qua volume mount
`./output:/output:ro`, mà GCS thì không mount được.

### 2.6 Các chỗ tiêu thụ ảnh

| Chỗ | Sửa gì |
|---|---|
| `playground` hiển thị ảnh | `st.image(url)` thay vì `st.image(path)` — bỏ `image_data_uri` |
| `GET /stories/{id}` | trả thêm `images: {cover, thumbnail1, thumbnail2}` = URL public |
| `download-zip` (`routes.py:429`) | đang `glob` thư mục rồi `read_bytes` → đổi sang tải từ GCS theo `object_key` trong DB |
| `_image_stamp` | dùng `max(updated_at)` từ DB |

### 2.7 Script chuyển ảnh cũ

`scripts/migrate_images_to_gcs.py` — quét `output/*/image/*`, upload, tạo dòng
`story_images`. Idempotent, có `--dry-run`. Không xoá file cũ (giữ làm bản lùi).

---

## Giai đoạn 3 — Đăng nhập và phân quyền (~1,5 ngày)

### 3.1 Luật phân quyền là một dòng

Ranh giới xem/sửa **trùng khít HTTP method** — không có endpoint nào vừa đọc vừa ghi:

| Nhóm | Số | Ai được |
|---|---|---|
| `GET` | 8 | admin + viewer |
| `POST` / `PUT` / `PATCH` / `DELETE` | 11 | **chỉ admin** |

Nên gate được bằng **một dependency ở cấp router**, không phải sửa 19 endpoint.

**Một ngoại lệ:** `GET /settings/{key}` trả `cms_upload_url` — về method là đọc, nhưng nên
để admin-only. Xử lý riêng, ~3 dòng.

### 3.2 Cấu hình tài khoản

```
ADMIN_USERNAME=...     ADMIN_PASSWORD=...
VIEWER_USERNAME=...    VIEWER_PASSWORD=...
AUTH_SECRET=...        # ký token
```

Không có bảng user, không có màn quản lý. So sánh mật khẩu bằng `secrets.compare_digest`
(chống timing attack), không phải `==`.

### 3.3 API

- `app/auth.py` (mới): `POST /api/auth/login` nhận `{username, password}` → trả JWT ký
  bằng `AUTH_SECRET`, payload `{sub, role, exp}`, hạn 12 giờ
- Dependency `current_user` (token hợp lệ) và `require_admin` (role == admin)
- Gắn vào router: mọi request không phải `GET` đi qua `require_admin`
- `GET /api/auth/me` → `{username, role}` để UI biết vẽ gì

### 3.4 Playground

- Màn đăng nhập trước khi vẽ bất cứ thứ gì; token giữ trong `st.session_state`
- Truyền `Authorization: Bearer` vào **8 chỗ gọi API**: 4 helper (`api_get/post/put/patch`)
  + 4 chỗ gọi thẳng `requests.*` (dòng 74, 170, 1399, 1509)
- Ẩn hoặc khoá **16 nút** khi `role == viewer`. Viewer vẫn giữ: xem danh sách, xem chi
  tiết, đọc chương, xem trace, **tải zip**
- Xử lý 401 → đá về màn đăng nhập

### 3.5 Nghiệm thu

- Đăng nhập viewer → mọi nút sửa biến mất, tải zip vẫn chạy
- Với token viewer, gọi thẳng `curl -X DELETE .../stories/1` → **403**
- Không token → **401** trên mọi endpoint
- Đăng nhập admin → mọi thứ như hiện tại

---

## Thứ tự thực hiện

```
GĐ1 (GCS)  →  GĐ2 (bảng + luồng ảnh)  →  GĐ3 (auth)
```

GĐ1 và GĐ2 buộc phải nối tiếp. GĐ3 độc lập, làm song song được nếu có hai người, nhưng
**làm sau thì sạch hơn** vì GĐ2 đã đụng vào playground và endpoint rồi.

| Giai đoạn | Công |
|---|---|
| 1 — Lớp lưu trữ | 0,75 |
| 2 — Bảng + luồng ảnh | 1,25 |
| 3 — Auth | 1,5 |
| **Tổng** | **3,5 ngày** |

---

## Rủi ro và điểm cần canh

| Rủi ro | Xử lý |
|---|---|
| Ảnh preview public, ai có link đều xem được | prefix `uuid4` (mục đầu tài liệu) |
| Truyện đang chạy khi deploy → ghi ảnh vào chỗ cũ | deploy khi không có truyện nào `is_running` |
| Migration `story_images` chạy trên DB có sẵn 6 truyện | script chuyển ảnh (2.7) chạy ngay sau migration, có `--dry-run` |
| `AUTH_SECRET` lộ → giả token được | không commit `.env`; đổi secret là vô hiệu mọi token |
| Streamlit không phải nền tảng bảo mật | đủ cho công cụ nội bộ; nếu cần ranh giới thật thì đặt sau reverse proxy — việc riêng, không nằm trong plan này |

---

## Ngoài phạm vi (cố ý)

- **Cột chủ sở hữu truyện** — đã chốt mọi người thấy chung mọi truyện
- **Bảng user, đổi mật khẩu, OIDC** — đã chốt dùng `.env`
- **Sửa `GET /export` đang trả 404** — lỗi có sẵn, không liên quan; nên vá riêng
- **Xoá thư mục rỗng `webapp/app;C`** — việc dọn dẹp riêng
- **Đồng bộ hai prompt fork `poster_image_*`** — việc riêng
