# Deploy web admin lên Vercel (free)

Web admin (React + Vite, thư mục `apps/web`) được deploy lên Vercel từ nhánh **`main`**. Backend chạy trên Render (xem [deploy-render.md](deploy-render.md)).

```
Trình duyệt ──► https://<tên>.vercel.app
                 ├─ /, /stations, /pay/:id ...   → index.html (React Router)
                 ├─ /api/*   ──(Vercel rewrite)──► https://seco-backend-api.onrender.com/api/*
                 └─ WebSocket ───(nối thẳng)────► wss://seco-backend-api.onrender.com/ws
```

- **`/api`** được Vercel chuyển tiếp sang Render (khai báo trong [`apps/web/vercel.json`](../apps/web/vercel.json)). Trình duyệt chỉ thấy một tên miền nên không vướng CORS, và code không cần biết địa chỉ API.
- **WebSocket** không chuyển tiếp qua Vercel được, nên web nối thẳng tới Render qua biến `VITE_WS_URL`. Khi chạy local không đặt biến này, Vite proxy `/ws` như cũ.
- Mọi đường dẫn khác trả về `index.html`, nên bấm F5 ở `/stations` hoặc mở link QR `/pay/<id>` không bị 404.

**Tự deploy:** mỗi commit vào `main` thì Vercel build và deploy lại. Các nhánh khác chỉ tạo bản Preview, không ảnh hưởng bản chính.

## Bước 1: Tạo project trên Vercel

1. Đăng nhập <https://vercel.com> bằng GitHub, chọn gói **Hobby (free)**.
2. **Add New → Project** → **Import** repo `bngbtran/SECO`.
3. Cấu hình:

| Mục | Giá trị |
|---|---|
| Project Name | ví dụ `seco-admin`, sẽ thành `https://seco-admin.vercel.app` nếu tên còn trống |
| Framework Preset | `Vite` |
| **Root Directory** | **`apps/web`** (bấm *Edit* để chọn) |
| Build / Output / Install | để mặc định, `vercel.json` đã khai báo |

4. **Environment Variables:**

| Key | Value |
|---|---|
| `VITE_WS_URL` | `wss://seco-backend-api.onrender.com/ws` |

5. Bấm **Deploy**.

**Production Branch** mặc định là `main`. Kiểm tra lại ở **Settings → Git**.

> Lần build đầu sẽ **lỗi** cho đến khi merge `dev` → `main`, vì `main` chưa có code web admin. Đây là bình thường. Sau khi merge, Vercel tự build lại và thành công.

## Bước 2: Cập nhật backend trên Render

Vào service `seco-backend-api` → **Environment**, sửa rồi **Save**:

| Key | Value |
|---|---|
| `WEB_ORIGIN` | `https://seco-admin.vercel.app` |
| `GUEST_PAYMENT_URL_BASE` | `https://seco-admin.vercel.app/pay` |

Sau đó mã QR trên trụ sạc trỏ tới trang thanh toán trên Vercel, **điện thoại thật quét được** và thanh toán ngay.

Sau khi merge `dev` → `main`, `WEB_ORIGIN` nhận được nhiều địa chỉ, ngăn cách bằng dấu phẩy, ví dụ `https://seco-admin.vercel.app,http://localhost:5173`.

## Bước 3: Kiểm tra

1. Mở `https://seco-admin.vercel.app`, đăng nhập bằng `admin@seco.com` / `seco` (khi Render dùng database `seco`).
2. Góc trên trang hiện trạng thái realtime đã kết nối, số liệu trạm cập nhật trực tiếp.
3. Mở thẳng `https://seco-admin.vercel.app/stations` rồi bấm F5, không được báo 404.

Lỗi thường gặp:

| Hiện tượng | Cách xử lý |
|---|---|
| Đăng nhập lỗi 502/504 | Render đang ngủ, đợi khoảng 1 phút rồi thử lại. Hoặc kiểm tra monitor UptimeRobot. |
| Không có realtime | Kiểm tra `VITE_WS_URL`. Biến này được nhúng lúc build, nên **sửa xong phải Redeploy**. |
| 404 khi F5 | `Root Directory` chưa đặt là `apps/web`, nên Vercel không đọc được `vercel.json`. |
| Build lỗi `Cannot find module ...` | Kiểm tra đã merge `dev` → `main` chưa. |

## Chạy local không đổi

`npm run dev:web` vẫn dùng Vite proxy (`/api`, `/ws` → `localhost:8000`). `vercel.json` và `VITE_WS_URL` chỉ có tác dụng trên Vercel.
