# EZVIZ Cloud Auto cho Home Assistant

Custom integration cho Home Assistant/HACS, thiết kế theo luồng của ứng dụng EZVIZ:

- Đăng nhập tài khoản một lần bằng Config Flow.
- Tự phát hiện toàn bộ camera trong tài khoản.
- Tự thử lấy encryption key theo batch endpoint của EZVIZ.
- Nếu EZVIZ yêu cầu quyền nâng cao, chỉ nhập **một mã DEVICE_ENCRYPTION**; integration áp dụng cho tất cả camera.
- Motion cập nhật ngay từ MQTT alarm đầu tiên, không chờ ảnh cloud; ảnh dùng preview URL đến sớm rồi tự thay bằng ảnh hoàn chỉnh khi EZVIZ gửi tiếp. Motion tự tắt theo thời gian tùy chỉnh, mặc định 60 giây.
- Tự tạo camera, image, sensor, binary sensor, switch, light, siren, number, select, button, alarm panel, update và MQTT event.
- Camera dùng **local RTSP** mặc định; Home Assistant phải truy cập được mạng LAN của camera.
- Icon EZVIZ được đóng gói tại `custom_components/ezviz_cloud/brand/icon.png` để Home Assistant hiển thị nhận diện integration.

## Cài đặt thủ công

1. Sao chép thư mục `custom_components/ezviz_cloud` vào thư mục `config/custom_components/` của Home Assistant.
2. Khởi động lại Home Assistant.
3. Vào `Settings → Devices & services → Add integration` và chọn `EZVIZ Cloud Auto`.
4. Nhập tài khoản, mật khẩu và để `Region = Automatic` nếu không chắc vùng máy chủ.
5. Nếu xuất hiện bước xác minh tài khoản hoặc khóa camera, nhập mã dùng một lần EZVIZ.

Không đặt mật khẩu trong `secrets.yaml`, README, issue, log hoặc file test. Config entry chỉ lưu token, account identifier, area id và các camera key cần thiết cho hoạt động local RTSP.

## Cài bằng HACS

Đưa repository này lên GitHub, sau đó thêm repository dạng **Custom repository → Integration** trong HACS. HACS sẽ lấy thư mục `custom_components/ezviz_cloud` và `hacs.json`.

## Sau khi cài

Mỗi camera được gom thành một device. Các entity được tạo theo capability mà EZVIZ trả về, vì vậy model khác nhau có thể có số lượng sensor/nút khác nhau. Vào `Configure` của integration để:

- Vào `Cloud Settings` để đặt **Motion tự tắt sau** từ 1 đến 3600 giây; giá trị này áp dụng cho tất cả camera trong tài khoản.
- Chạy `Auto-configure all cameras` khi thêm camera mới hoặc khi key cũ hết quyền.
- Chọn `Local RTSP` hoặc tắt stream cho từng camera.
- Chọn substream `/Streaming/Channels/102` (mặc định) hoặc main stream `/Streaming/Channels/101`.
- Đổi username RTSP, chọn verification code thay encryption key và chạy test DESCRIBE tùy chọn.

Encryption key không được tạo thành text entity mặc định. Key chỉ nằm trong options nội bộ và được dùng để mở ảnh báo động/RTSP.

## Điều kiện RTSP

- Máy chạy Home Assistant phải nhìn thấy IP/port RTSP nội bộ của camera.
- `ffmpeg` và thành phần `stream` của Home Assistant phải hoạt động.
- EZVIZ cloud có thể trả metadata local nhưng điều đó không đảm bảo HA đang cùng VLAN hoặc không bị firewall chặn.
- Nếu HA ở xa site camera, dùng VPN/site-to-site hoặc đặt HA/media relay tại site đó.

Cloud VTM/P2P không được bật làm đường stream production trong bản này. Add-on go2rtc/FFmpeg chỉ nên thêm khi cần restream, Frigate/NVR hoặc nhiều luồng chạy liên tục; add-on không thay thế integration xác thực/entity.

## Bảo mật

- Mật khẩu tài khoản chỉ tồn tại trong bộ nhớ của Config Flow trong lúc login, không ghi vào config entry.
- OTP tài khoản và OTP DEVICE_ENCRYPTION không được persist.
- Diagnostics loại bỏ token, account, serial, IP, SSID, URL ảnh và key.
- Nếu thông tin tài khoản đã từng được gửi vào chat/issue công khai, hãy đổi mật khẩu EZVIZ trước khi dùng production.

## Giới hạn

EZVIZ API là private API và `pyezvizapi` được pin ở `1.0.5.0`. Backend có thể đổi mã lỗi hoặc yêu cầu xác minh lại. Khi đó hãy chạy lại `Auto-configure all cameras`, re-authenticate account, rồi kiểm tra Repair issue của integration.

## Cấu trúc

```text
custom_components/ezviz_cloud/
├── auth.py             # login + areaId + batch/per-camera key bootstrap
├── camera_config.py    # defaults, merge và kiểm tra RTSP
├── config_flow.py      # account MFA + device-key MFA + options
├── coordinator.py      # polling cloud state
├── mqtt.py             # alarm/motion push
└── ...                 # các Home Assistant entity platforms
```
