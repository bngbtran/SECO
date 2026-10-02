# Deploy backend lên Render (free)

Backend (FastAPI, `apps/api`) được deploy lên Render bằng file Blueprint [`render.yaml`](../render.yaml) ở thư mục gốc repo. Không dùng Docker: Render chạy thẳng `pip install .` rồi `uvicorn app.main:app --port $PORT --loop asyncio`.

**Tự deploy:** mỗi khi có commit vào nhánh `main` (merge pull request) mà commit đó sửa file trong `apps/api/`, Render tự build và deploy lại API. Commit chỉ sửa web, simulator hay tài liệu thì không deploy lại.

```
be_xxx / fe_xxx ──PR──► dev ──PR──► main ──(tự động)──► Render: build → health check /health → chuyển traffic
```

Nếu bản mới lỗi (build hỏng hoặc `/health` không trả về 200), Render **giữ nguyên bản đang chạy**, nên API không bị sập vì một commit hỏng.

## Kiến trúc khi deploy

| Thành phần | Chạy ở đâu | Ghi chú |
|---|---|---|
| API | Render, gói Free, region Singapore | Tự ngủ sau 15 phút không có request HTTP, xem bước 5 |
| MongoDB | Atlas (cluster chung của team) | Cho phép Render truy cập, xem bước 1 |
| MQTT broker | HiveMQ Cloud Serverless (free) | Mosquitto trên laptop thì Render không kết nối tới được |
| Web admin | Chạy local (`npm run dev:web`) hoặc deploy riêng | Khai báo địa chỉ web trong `WEB_ORIGIN` |

## Bước 1: Cho phép Render truy cập MongoDB Atlas

IP của Render free thay đổi liên tục, nên phải mở cho mọi IP:

1. Vào Atlas, mở **Network Access** → **Add IP Address** → **Allow access from anywhere** (`0.0.0.0/0`).
2. Vẫn an toàn vì database được bảo vệ bằng user và mật khẩu trong `MONGO_URI`. Dùng user `seco_app`, đừng dùng user admin.

## Bước 2: Tạo MQTT broker trên cloud (HiveMQ Cloud)

1. Đăng ký tại <https://console.hivemq.cloud>, tạo cluster **Serverless (Free)**.
2. Vào tab **Access Management**, tạo một credential, ví dụ user `seco-api`, kèm mật khẩu mạnh.
3. Ghi lại **Cluster URL**, dạng `xxxxxxxx.s1.eu.hivemq.cloud`. Cổng TLS là **8883**.

Các trụ sạc (Wokwi hoặc phần cứng thật) và đầu đọc thẻ admin cũng phải kết nối tới **cùng broker này** thì mới nói chuyện được với API trên Render. Lưu ý:
- HiveMQ Cloud chỉ nhận kết nối TLS, nên firmware cần chuyển sang kết nối mã hóa (`WiFiClientSecure`, cổng 8883).
- Đừng trỏ API chạy trên laptop vào broker cloud này khi API đó dùng database khác. Hai API cùng nhóm `MQTT_SHARE_GROUP` sẽ chia nhau xử lý các yêu cầu từ trụ.

## Bước 3: Tạo service từ Blueprint

1. `render.yaml` đã nằm trên nhánh **`main`**.
2. Đăng nhập <https://dashboard.render.com> bằng GitHub.
3. Chọn **New** → **Blueprint** → chọn repo **`bngbtran/SECO`**, nhánh `main`. Render đọc `render.yaml` và tạo service `seco-api`.
4. Render yêu cầu nhập các biến bí mật (`sync: false`):

| Biến | Giá trị |
|---|---|
| `MONGO_URI` | URI Atlas, giống trong `apps/api/.env` (`mongodb+srv://seco_app:...`) |
| `MONGO_DATABASE` | **`seco_render_check`** cho lần kiểm tra đầu. Sau khi merge `dev` → `main` thì đổi thành **`seco`** (xem bước 4b). |
| `MQTT_HOST` | Cluster URL của HiveMQ, ví dụ `xxxxxxxx.s1.eu.hivemq.cloud` |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | Credential tạo ở bước 2 |
| `WEB_ORIGIN` | Địa chỉ web admin, ví dụ `http://localhost:5173` |
| `GUEST_PAYMENT_URL_BASE` | `<địa chỉ web>/pay`, ví dụ `http://localhost:5173/pay` |

`JWT_SECRET` được Render tự sinh. Các biến còn lại đã có giá trị sẵn trong `render.yaml`.

5. Bấm **Apply**. Lần build đầu mất khoảng 2–4 phút.

Không commit các giá trị bí mật vào git. Chúng chỉ nằm trong dashboard Render, giống cách `apps/api/.env` đang được gitignore.

## Bước 4: Kiểm tra

Mở `https://seco-api.onrender.com/health` (tên miền thật xem ở trang service). Kết quả mong đợi:

```json
{"status": "ok", "service": "seco-api", "database": "ok", "mqtt": "starting"}
```

(Bản trên `main` hiện tại luôn ghi `"mqtt": "starting"`. Kiểm tra kết nối MQTT bằng tab **Logs**, dòng `MQTT connected to ...`.)

- Log báo lỗi MQTT: kiểm tra `MQTT_HOST`, user/mật khẩu, và `MQTT_TLS=true` / `MQTT_PORT=8883`.
- Lỗi 500 ở `/health`: kiểm tra `MONGO_URI` và Network Access của Atlas.
- Log khởi động xem ở tab **Logs**. Lúc khởi động, API tự áp dụng các migration còn thiếu.
- Tài liệu API: `https://seco-api.onrender.com/docs`.

### Bước 4b: Sau khi merge `dev` → `main`

Vì sao lần đầu dùng database tạm: code cũ trên `main` khi khởi động sẽ tạo index `transactions_session_type_unique` **không có** điều kiện lọc, trong khi database `seco` đã có index này **có** điều kiện lọc (do migration của `dev` tạo). MongoDB sẽ từ chối và API dừng ngay. Dữ liệu không bị ảnh hưởng, nhưng `/health` sẽ báo lỗi. Với `seco_render_check` (database trống) thì không có xung đột này.

1. Merge PR `dev` → `main`. Render tự deploy bản mới, migration tạo đủ collection và index.
2. Vào **Environment**, đổi `MONGO_DATABASE` thành **`seco`** → **Save changes**. Render tự khởi động lại API với database thật của team.
3. Mở lại `/health` để kiểm tra. Đăng nhập web admin bằng tài khoản seed (`admin@seco.com` / `seco`).
4. Database `seco_render_check` không còn dùng. Xóa trên Atlas, hoặc để đó cũng không sao.

## Bước 5: Giữ API luôn thức (UptimeRobot)

Gói Free tắt service sau **15 phút không có request HTTP**. Lần gọi sau phải chờ khoảng 1 phút để khởi động lại, và trong lúc ngủ thì mất MQTT, trụ báo offline, ticker tính tiền dừng. Kết nối MQTT và WebSocket **không** giữ service thức được.

1. Tạo tài khoản miễn phí tại <https://uptimerobot.com>.
2. **Add New Monitor** → loại **HTTP(s)** → URL `https://seco-api.onrender.com/health` → **interval 5 phút**.

Một service chạy 24/7 tốn khoảng 744 giờ/tháng, vừa đủ hạn mức 750 giờ của gói Free.

## Bước 6: Từ nay chỉ cần merge

1. Làm việc trên nhánh `be_xxx`, mở PR vào `dev` như bình thường.
2. Khi muốn phát hành: mở PR **`dev` → `main`** rồi merge.
3. Render tự deploy. Theo dõi ở tab **Events** của service, khi thành công sẽ hiện "Deploy live".

Muốn tạm tắt tự deploy: vào **Settings** → **Auto-Deploy** → `Off`, hoặc đổi `autoDeployTrigger: off` trong `render.yaml`. Muốn deploy tay: bấm **Manual Deploy** → **Deploy latest commit**.

## Chạy local không bị ảnh hưởng

Deploy chỉ thêm `render.yaml` và tài liệu này, không sửa code. Chạy local vẫn như cũ.
