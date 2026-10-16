# Bộ nhận diện thương hiệu SECO

SECO là hệ thống trạm sạc xe điện thông minh. Thư mục này chứa logo, icon và bộ màu, chữ chuẩn dùng cho trạm sạc, web quản trị, ứng dụng di động, slide, báo cáo và ấn phẩm in.

Bản xem trực tuyến (có bìa, mẫu áp dụng và bảng token): https://claude.ai/artifact/CLCD7WT3MgQ5aMVDPZVaSq

## Thư mục

| Đường dẫn | Nội dung |
|---|---|
| `logo/seco-logo.png` | Logo đầy đủ (tia sét + chữ SECO), màu gốc, **nền trong suốt**. Bản mặc định trên nền sáng. |
| `logo/seco-logo-white.png` | Logo đầy đủ màu trắng, nền trong suốt. Trên nền `seco-teal`, `seco-graphite`, nền tối và ảnh tối. |
| `logo/seco-mark.png` | Chỉ biểu tượng tia sét, màu gốc, nền trong suốt, khung vuông. |
| `logo/seco-mark-white.png` | Biểu tượng màu trắng, nền trong suốt. Ô logo trên nền xanh (web, app). |
| `logo/seco-mark-graphite.png` | Biểu tượng một màu `seco-graphite`, cho in một màu trên nền sáng. |
| `icons/seco-icon-16/32/48/180/192/512.png` | Icon vuông: biểu tượng màu gốc trên nền trắng (favicon, icon ứng dụng, apple-touch-icon). |
| `icons/seco-icon-teal-192/512.png` | Icon vuông: biểu tượng trắng trên nền `seco-teal`. |
| `source/` | Ba file gốc do nhóm thiết kế (nền trắng / đen đặc, 2000 × 2000). Không sửa, chỉ dùng để xuất lại. |
| `tokens.json` | Màu, chữ, khoảng cách, bo góc (nguồn duy nhất). |
| `tokens.css` | Biến CSS sinh từ `tokens.json`, có giao diện sáng và tối. |

Các bản nền trong suốt được tách nền từ chính file gốc: giữ nguyên hình và mã màu, chỉ bỏ nền.

## Logo

Biểu tượng là một tia sét ghép từ các mảnh **xanh** và **xám** đan nhau thành chữ S: dòng điện đi qua trạm.

- **Chọn bản:** nền sáng dùng `seco-logo.png`; nền xanh, nền tối hoặc ảnh tối dùng `seco-logo-white.png`; chỗ nhỏ hơn 96px dùng biểu tượng (`seco-mark.png` hoặc `seco-mark-white.png`).
- **Khoảng trống:** chừa quanh logo ít nhất bằng 1/4 chiều rộng biểu tượng. Logo rộng 128px chừa 32px (`space-8`).
- **Kích thước tối thiểu:** logo đầy đủ rộng 96px trên màn hình, 25mm khi in; biểu tượng 24px trên màn hình, 8mm khi in.
- **Không được:** đổi màu từng mảnh, xoay, kéo méo, thêm bóng hay viền, đặt logo màu gốc lên nền xanh (mảng xanh của logo sẽ chìm), viết lại chữ SECO bằng font khác.

## Màu sắc

Thương hiệu chỉ có một màu chủ đạo là xanh logo, đi cùng xám logo. Hai mã này lấy đúng từ file logo và không được chỉnh.

| Token | Mã | Dùng cho |
|---|---|---|
| `seco-teal` | `#3E7168` | Màu chủ đạo: nút chính, dải thương hiệu, mảng màu lớn. Chữ trắng trên nền này đạt 5,6:1. |
| `seco-graphite` | `#545454` | Mảnh tối của biểu tượng, mảng phụ, ấn phẩm in. |
| `seco-graphite-soft` | `#606060` | Màu chữ C, O trong logo. Chỉ có trong file logo, không dùng cho giao diện. |
| `seco-black` / `seco-white` | `#000000` / `#FFFFFF` | Nền của logo đảo màu và logo chính. |

- Tỷ lệ trên một trang hay màn hình: khoảng 60% nền, 30% chữ và mảng xám, 10% xanh. Xanh là điểm nhấn, không phủ cả trang.
- Chữ màu xanh trên nền tối dùng `brand-text` (`#7FB3A6`), không dùng thẳng `#3E7168` (chỉ đạt 3,3:1).
- Bộ màu giao diện (`surface`, `ink`, `line`, `brand-soft`…) và bản giao diện tối nằm trong `tokens.json`.

### Trạng thái trạm

Mỗi trạng thái có một màu riêng và **luôn đi kèm chữ**:

| Trạng thái | Chữ hiển thị | Token | Sáng / Tối |
|---|---|---|---|
| READY | Sẵn sàng | `status-ready` | `#3E7168` / `#7FB3A6` |
| CHARGING | Đang sạc | `status-charging` | `#2E6FB7` / `#7AABE6` |
| WAITING_PAYMENT | Chờ thanh toán | `status-payment` | `#9A6512` / `#E2AE52` |
| MAINTENANCE | Bảo trì | `status-maintenance` | `#B4531A` / `#F09A5E` |
| ERROR | Lỗi | `status-error` | `#B83A37` / `#F0837F` |
| OFFLINE | Mất kết nối | `status-offline` | `#5F6F6B` / `#8FA19B` |

## Chữ

- Tên thương hiệu và tiêu đề lớn (bìa, poster, tem trạm): **Bungee**, viết hoa, ít chữ.
- Giao diện và văn bản: **Be Vietnam Pro** (đủ dấu tiếng Việt). Tiêu đề 32 / 24 / 18px đậm, nội dung 16px, bảng 14px, nhãn 12px viết hoa.
- Số liệu và mã (kWh, số tiền đang chạy, `EVCS-01`, UID thẻ): **JetBrains Mono**.
- Cả ba font lấy từ Google Fonts.

## Giọng văn

- Tiếng Việt, câu ngắn, nói thẳng việc cần làm: "Quẹt thẻ để bắt đầu", "Cắm sạc rồi nhấn START", "Quét mã QR để thanh toán".
- Lỗi nói rõ chuyện gì xảy ra và làm gì tiếp: "Thẻ đã bị khóa. Liên hệ quầy để được cấp thẻ mới."
- Không dùng emoji trong giao diện, màn hình trạm hay tài liệu.
- Tiền: `10.000 đ`; giá điện: `4.000 đ/kWh`; mã trạm: `EVCS-01`; tên thương hiệu luôn viết hoa: SECO.

## Hình khối

- Lấy từ biểu tượng: các mảnh vát chéo, cạnh sắc. Mảng màu lớn và tem trạm không bo góc; thẻ và nút bo 8px; ô nhập và chip bo 4px; viên trạng thái bo tròn.
- Phân lớp bằng viền và nền nhạt, không dùng bóng đổ, không dùng gradient.
- Icon giao diện: bộ Lucide, nét 2px.

## Đã áp dụng trong dự án

- Web quản trị (`apps/web`): favicon, apple-touch-icon, ô logo ở thanh bên, trang đăng nhập và trang thanh toán của khách; màu chính đổi từ `#437066` sang `#3E7168`.
- Ứng dụng mobile (`apps/mobile`): favicon, logo ở màn hình đăng nhập; màu chính đổi từ `#3F796E` sang `#3E7168`.
- Ứng dụng Android (APK) đã dùng `SECO - Logo.png` làm icon; cần build lại APK để nhận màu mới.
