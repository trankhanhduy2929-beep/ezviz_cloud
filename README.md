# EZVIZ Cloud Auto

Custom integration cho [Home Assistant](https://www.home-assistant.io/) dùng tài khoản EZVIZ Cloud để tự phát hiện camera, nhận cảnh báo MQTT, hiển thị hình ảnh/sensor và điều khiển các tính năng mà từng model hỗ trợ.

> Đây là **custom integration**, không phải add-on. Integration chạy trong Home Assistant; add-on restream/NVR chỉ cần khi bạn muốn dùng Frigate, go2rtc hoặc nhiều luồng camera liên tục.

[![Mở EZVIZ Cloud Auto trong HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=trankhanhduy2929-beep&repository=ezviz_cloud&category=integration)

- Repository: <https://github.com/trankhanhduy2929-beep/ezviz_cloud>
- Domain: `ezviz_cloud`
- Phiên bản hiện tại: `0.3.6`
- Giấy phép: GPL-3.0

## Tính năng

- Đăng nhập EZVIZ một lần bằng Config Flow; hỗ trợ MFA/OTP khi EZVIZ yêu cầu.
- Tự phát hiện toàn bộ camera trong tài khoản và gom entity theo từng thiết bị.
- Tự lấy encryption key camera theo batch; một mã `DEVICE_ENCRYPTION` có thể áp dụng cho nhiều camera.
- Nhận alarm/motion từ MQTT ngay khi push đầu tiên đến, không phải chờ ảnh Cloud hoàn tất.
- Ảnh cảnh báo dùng preview URL đến sớm rồi tự cập nhật sang ảnh hoàn chỉnh.
- Cấu hình thời gian motion tự tắt từ `1–3600` giây, mặc định `60` giây.
- Hỗ trợ camera local RTSP, mặc định substream `/Streaming/Channels/102`; có thể chọn main stream `/Streaming/Channels/101`.
- Tự tạo camera, image, sensor, binary sensor, switch, light, siren, number, select, button, alarm panel, update và MQTT event theo capability của thiết bị.
- Có Repair issue khi camera cần encryption key/verification code cho RTSP.
- Có diagnostics redaction; không ghi mật khẩu tài khoản vào Config Entry.
- Có icon integration tại `custom_components/ezviz_cloud/brand/icon.png`.
- Đóng gói riêng `pyezvizapi 1.0.5.0` trong namespace nội bộ, không xung đột với EZVIZ integration mặc định của Home Assistant.

## Yêu cầu

- Home Assistant có thể cài custom integration.
- Tài khoản EZVIZ Cloud đang hoạt động.
- Nếu dùng RTSP: Home Assistant phải truy cập được IP/port RTSP nội bộ của camera.
- `ffmpeg` và thành phần `stream` của Home Assistant cần hoạt động để xem RTSP.
- EZVIZ API là private API; backend hoặc yêu cầu xác minh có thể thay đổi theo khu vực/tài khoản.

## Cài đặt bằng HACS

### Cách nhanh

Bấm nút bên dưới để mở trang thêm repository trong HACS:

[![Mở trong HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=trankhanhduy2929-beep&repository=ezviz_cloud&category=integration)

Sau đó chọn **Download** và khởi động lại Home Assistant.

### Thêm Custom repository thủ công

1. Mở **HACS → Integrations**.
2. Chọn menu ba chấm ở góc trên bên phải → **Custom repositories**.
3. Nhập URL:

   ```text
   https://github.com/trankhanhduy2929-beep/ezviz_cloud
   ```

4. Chọn loại **Integration** → **Add**.
5. Tìm `EZVIZ Cloud Auto` trong HACS → **Download**.
6. Khởi động lại Home Assistant.
7. Vào **Settings → Devices & services → Add integration** và chọn `EZVIZ Cloud Auto`.

> Nếu HACS không hiện bản mới, mở trang integration trong HACS, chọn **Redownload**, rồi restart Home Assistant.

## Cài đặt thủ công

1. Tải ZIP từ repository GitHub hoặc mục Releases.
2. Giải nén thư mục `custom_components/ezviz_cloud` vào:

   ```text
   <Home Assistant config>/custom_components/ezviz_cloud/
   ```

3. Đảm bảo các file nằm trực tiếp trong thư mục integration, ví dụ:

   ```text
   config/
   └── custom_components/
       └── ezviz_cloud/
           ├── __init__.py
           ├── manifest.json
           ├── brand/icon.png
           └── ...
   ```

4. Khởi động lại Home Assistant.
5. Thêm integration từ **Settings → Devices & services → Add integration**.

## Thiết lập lần đầu

1. Chọn khu vực `Automatic` nếu không chắc EZVIZ account thuộc máy chủ nào.
2. Nhập username và password trong Config Flow.
3. Nhập mã OTP tài khoản nếu EZVIZ yêu cầu.
4. Nếu cần mở khóa stream, nhập một mã `DEVICE_ENCRYPTION`; integration tự áp dụng cho camera hiện có.
5. Nếu bỏ qua lấy key, mở **Configure → Auto-configure all cameras** sau.
6. Chờ integration phát hiện thiết bị và tạo entity.

Mật khẩu tài khoản chỉ dùng trong lúc đăng nhập và không được lưu vào Config Entry. Account identifier, session token và camera key cần thiết vẫn được Home Assistant lưu nội bộ để integration hoạt động.

## Cấu hình motion và ảnh cảnh báo

Vào:

```text
Settings → Devices & services → EZVIZ Cloud Auto → Configure → Cloud Settings
```

Tại đây có:

- `Motion auto-off delay (seconds)` / `Motion tự tắt sau (giây)`: từ `1` đến `3600` giây.
- Mặc định: `60` giây.
- Giá trị áp dụng cho tất cả camera trong cùng tài khoản.
- Sau khi lưu, Home Assistant tự reload integration.

Motion được bật ngay khi MQTT alarm đầu tiên đến. Nếu ảnh cuối chưa sẵn sàng, entity image dùng ảnh preview trước rồi cập nhật lại khi EZVIZ gửi URL hoàn chỉnh. Nếu EZVIZ gửi MQTT chậm từ phía Cloud, integration không thể rút ngắn thời gian xử lý của máy chủ EZVIZ.

## Cấu hình camera RTSP

Mở **Configure → Camera Settings**, chọn camera và thiết lập:

- `Local RTSP`: bật/tắt stream nội bộ.
- RTSP path mặc định: `/Streaming/Channels/102`.
- Main stream: `/Streaming/Channels/101`.
- Username RTSP: thường là `admin`, tùy model.
- Encryption key hoặc verification code theo camera.
- `Test RTSP now`: đánh thức camera và kiểm tra DESCRIBE nếu cần.

Điều kiện để xem được camera:

- HA và camera cùng LAN/VLAN hoặc có route/VPN phù hợp.
- Không bị firewall chặn RTSP.
- IP camera mà EZVIZ trả về phải truy cập được từ máy chạy HA.
- Nếu HA ở xa site camera, dùng VPN/site-to-site hoặc media relay tại site camera.

Cloud VTM/P2P không được bật làm đường stream production mặc định. Dùng go2rtc/FFmpeg add-on khi cần restream, NVR hoặc nhiều client xem đồng thời.

## Entity và điều khiển

Tùy model/capability, integration có thể tạo:

- `camera`: local RTSP.
- `image`: ảnh motion/alarm gần nhất.
- `binary_sensor`: motion, alarm schedule, encryption status.
- `sensor`: pin, Wi-Fi, IP, trạng thái PIR, loại alarm, thời gian alarm gần nhất và metadata thiết bị.
- `switch`, `light`, `siren`, `number`, `select`, `text`, `button`.
- `alarm_control_panel`, `update` và MQTT event.

Không phải model EZVIZ nào cũng hỗ trợ tất cả entity. Số lượng entity phụ thuộc dữ liệu capability mà Cloud trả về.

### Gọi service đánh thức camera

```yaml
service: ezviz_cloud.wake_device
target:
  entity_id: camera.ten_camera
```

## Xử lý lỗi thường gặp

### Không đăng nhập được

- Kiểm tra khu vực máy chủ; thử `Automatic`, `Europe`, `Russia` hoặc custom host nếu tài khoản yêu cầu.
- Hoàn tất OTP tài khoản trong thời gian mã còn hiệu lực.
- Nếu session hết hạn, dùng **Reconfigure/Re-authenticate** và nhập lại password; password không được lưu.

### Không xem được RTSP

- Chạy **Camera Settings → Test RTSP now**.
- Kiểm tra HA có ping/truy cập IP camera không.
- Chọn đúng path `/101` hoặc `/102`.
- Chạy **Auto-configure all cameras** nếu Repair issue báo thiếu key.
- Kiểm tra log `ffmpeg` và firewall/VLAN.

### Motion vẫn trễ

- Kiểm tra MQTT handler và log integration.
- Đặt thời gian tự tắt phù hợp trong **Cloud Settings**.
- Nếu MQTT và toàn bộ URL ảnh đều đến trễ từ EZVIZ Cloud, cần chờ backend EZVIZ xử lý; integration đã không chờ ảnh trước khi bật motion.

### Icon chưa hiện

- Restart Home Assistant sau khi cài bản mới.

### Xung đột với EZVIZ integration mặc định

- Có thể giữ đồng thời EZVIZ integration mặc định và `EZVIZ Cloud Auto`.
- Bản `0.3.6` không dùng chung module global `pyezvizapi`; mỗi integration giữ phiên bản thư viện riêng.
- Sau khi nâng cấp, xóa thư mục custom cũ nếu cần, chép lại `custom_components/ezviz_cloud`, rồi restart Home Assistant.
- Tải lại frontend không dùng cache.
- Kiểm tra file `custom_components/ezviz_cloud/brand/icon.png` tồn tại đúng vị trí.

## Bảo mật

- Không commit username, password, OTP, session token, camera encryption key hoặc file `.storage` lên GitHub.
- Không đưa thông tin đăng nhập vào README, issue, log, screenshot hoặc file test.
- Không chia sẻ file `config/.storage/core.config_entries` hoặc bản backup HA có chứa Config Entry.
- Nếu thông tin tài khoản từng xuất hiện trong chat/issue công khai, hãy đổi password EZVIZ và đăng nhập lại.
- Diagnostics của integration redacts account identifier, token, serial, IP, SSID, URL ảnh và camera key.

## Cập nhật

- **HACS:** mở integration → **Update/Redownload** → restart Home Assistant.
- **Thủ công:** ghi đè thư mục `custom_components/ezviz_cloud`, giữ nguyên Config Entry, rồi restart.
- Sau cập nhật, kiểm tra **Settings → Devices & services → EZVIZ Cloud Auto** và Repair issue nếu có.

## Phát triển và kiểm thử

Từ thư mục repository:

```bash
python3 -m pytest -q
ruff check custom_components tests
python3 -m compileall -q custom_components tests
```

Mọi thay đổi nên giữ nguyên nguyên tắc: không lưu password tài khoản và không đưa credential thật vào test/issue/README.

## Cấu trúc chính

```text
custom_components/ezviz_cloud/
├── __init__.py             # lifecycle, platforms và migration
├── auth.py                 # login, MFA và camera-key bootstrap
├── camera_config.py        # RTSP defaults và per-camera options
├── config_flow.py          # setup, reauth và options flow
├── coordinator.py          # polling, MQTT overlay và motion timer
├── push.py                 # chuẩn hóa MQTT alarm/image push
├── mqtt.py                 # MQTT client/handler
├── brand/icon.png          # icon integration
└── ...                     # các platform/entity còn lại
```

## Giấy phép

Phát hành theo **GNU General Public License v3.0**. Xem file `LICENSE` để biết đầy đủ điều khoản.
