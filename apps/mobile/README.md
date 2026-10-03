# SECO Mobile FE

Prototype mobile frontend cho các task được gán `HieuLN` trong `SECO - MasterSheet.xlsx`.

## Cấu trúc dự án

```text
public/
  index.html
  assets/template/       # ảnh thiết kế tham chiếu
  vendor/                # Leaflet và Lucide được vendored local
src/
  app.js                 # state, routing và màn hình mobile
  config.js              # API base URL, WebSocket, polling
  styles.css             # design system và responsive layout
server/
  server.js              # static server + proxy /api/v1 và /ws sang backend
docs/
  SECO - Design Document.docx
  SECO - MasterSheet.xlsx
package.json
README.md
```

## Chạy local

```bash
npm run dev                                         # API đã deploy (Render)
SECO_API_ORIGIN=http://localhost:8000 npm run dev   # API chạy local
```

Mở `http://localhost:4173` (giao diện khung điện thoại; trên điện thoại thật thì toàn màn hình). `server/server.js` phục vụ `public/` và `src/`, đồng thời proxy `/api/v1` (REST) và `/ws` (WebSocket realtime) sang backend, nên trình duyệt không gặp CORS. Không cần cài dependency npm.

Tài khoản demo (mật khẩu `seco`): `user@seco.com` (member), `tech@seco.com` (kỹ thuật viên). Tài khoản ADMIN dùng web admin, app sẽ từ chối.

## Phạm vi theo nghiệp vụ

Mọi màn hình đọc API thật; không còn dữ liệu demo cứng.

**Member (USER)**: ứng dụng chỉ **theo dõi và báo cáo**, không điều khiển việc sạc. Sạc làm hoàn toàn tại trạm: cắm sạc, quẹt thẻ RFID, chọn ngân sách trên màn hình trạm (tuỳ chọn), rút sạc để kết thúc.

| Tab | Màn hình | API |
|---|---|---|
| Trạm | Bản đồ (Leaflet + OpenStreetMap), danh sách, lọc Sẵn sàng / Yêu thích, chi tiết trạm (trạng thái, giá hiện hành, địa chỉ, chỉ đường), **báo trạm hư hỏng**, đánh dấu yêu thích | `GET /stations`, `GET/PUT/DELETE /member/favorite-stations`, `POST /member/support-tickets` |
| Phiên sạc | Phiên đang sạc theo thời gian thực (điện năng, chi phí, ngân sách đã chọn trên trạm), kết quả khi phiên kết thúc, lịch sử và chi tiết phiên | `GET /member/charging/current`, `GET /member/charging/{id}`, `GET /member/sessions` |
| Ví | Số dư, nạp tiền sandbox, giao dịch (lọc), chi tiết giao dịch | `GET /member/wallet`, `POST /member/topup`, `GET /member/transactions[/{id}]` |
| Tôi | Thông tin cá nhân, thẻ RFID (tạm khoá / mở / **báo mất**), trạm yêu thích, các báo cáo sự cố đã gửi, thông báo, đổi mật khẩu, FAQ | `PATCH /auth/me`, `/member/rfid-cards/*`, `/member/support-tickets/*`, `/notifications/*`, `POST /auth/change-password` |

Phiên sạc hiện trên ứng dụng ngay khi trạm báo bắt đầu (WebSocket `member_session_updated`, polling dự phòng 5 giây) và chuyển sang màn kết quả khi trạm báo kết thúc.

**Kỹ thuật viên (TECHNICIAN)**:

| Tab | Màn hình | API |
|---|---|---|
| Yêu cầu | Hàng đợi yêu cầu hỗ trợ (đang chờ / của tôi / đã xong) → chi tiết: member (gọi điện), trạng thái trạm lúc gửi, telemetry, lỗi đang mở → Nhận xử lý → Hoàn tất kèm ghi chú | `/technician/support-tickets/*` |
| Trạm | Trạm ưu tiên cần đến (lỗi, bảo trì, offline, có yêu cầu) → chi tiết: telemetry, lỗi đang mở, yêu cầu, lịch sử bảo trì, chỉ đường | `GET /technician/stations[/{id}]` |
| Thông báo | Lỗi trạm, yêu cầu hỗ trợ mới | `/notifications/*` |
| Tôi | Thông tin cá nhân, lịch sử công việc (yêu cầu đã xử lý, bảo trì), thẻ kỹ thuật viên, đổi mật khẩu | `GET /technician/maintenance`, `/technician/rfid-cards/*` |

Vào / ra bảo trì làm **tại trạm** (quẹt thẻ kỹ thuật viên + giữ STOP 3 giây), không làm trong app.

**Chung:** đăng nhập, quên mật khẩu (`POST /auth/forgot-password` trả mật khẩu tạm 10 phút, rồi `POST /auth/reset-password` như đổi mật khẩu), hộp thư thông báo realtime qua `/ws`.

Đã bỏ khỏi bản prototype vì ngoài phạm vi dự án hoặc backend không có: đặt ngân sách / bắt đầu / dừng sạc trong ứng dụng (làm tại trạm), xe của tôi, QR ví, chia sẻ trạm, cài đặt ngôn ngữ / bật tắt thông báo, thiết bị đã đăng nhập, checklist / ảnh / biên bản sửa chữa và nhận "nhiệm vụ" lỗi giả lập phía kỹ thuật viên.
