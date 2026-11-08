# TopCV jobs crawler

Thu thẻ tin từ [danh sách việc làm TopCV](https://www.topcv.vn/tim-viec-lam-moi-nhat?page=1).
Phạm vi bao gồm các địa điểm và mức kinh nghiệm hiển thị trên danh sách.
Python/Playwright điều khiển Chrome qua CDP; Beautiful Soup trích dữ liệu.

## Chạy

Chạy từ gốc repository, dùng Python 3.12.4 theo `.python-version`.
Cài Google Chrome trên máy để dùng `JOBS_CHANNEL=chrome` mặc định.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirement.txt
Copy-Item .env.example .env
python src/collection/jobs/crawl_jobs.py
```

Trên Linux/macOS, activate bằng `source .venv/bin/activate` và sao chép cấu hình
bằng `cp .env.example .env`. Với browser do Playwright quản lý, chạy
`python -m playwright install chromium` rồi đặt `JOBS_CHANNEL=chromium` trong `.env`.

Chỉnh `.env` trước khi chạy. File được đọc tại gốc repository kể cả khi gọi script
ở thư mục khác. Biến môi trường hệ thống ưu tiên hơn `.env`; giá trị trống dùng mặc định.

| Biến | Giá trị mặc định và ý nghĩa |
| --- | --- |
| `JOBS_END` | Trống, thu đến hết Next; resume giữ mục tiêu đã lưu |
| `JOBS_START` | Trống, run mới từ trang 1, resume tiếp cards/offset đang dở; đặt số để mở lượt quét mới |
| `JOBS_MAX_JOBS` | Trống, thu toàn bộ ID trong phạm vi trang |
| `JOBS_OUTPUT_DIRECTORY` | Trống, tạo `data/raw/jobs/<run_id>`; nếu đặt thì thư mục phải chưa tồn tại |
| `JOBS_RESUME` | Trống, tạo run mới; đặt đường dẫn run để tiếp tục dataset |
| `JOBS_DELAY` | 15 giây, tối thiểu 3 |
| `JOBS_TIMEOUT` | 30 giây cho mỗi bước điều hướng/chờ nội dung |
| `JOBS_RESUME_COOLDOWN` | 30 giây giữa phiên, phạm vi 30–1800 |
| `JOBS_CHANNEL` | `chrome`; hỗ trợ `chromium`, `msedge` |
| `JOBS_HEADLESS` | `false`, hiển thị cửa sổ; đặt `true` để chạy headless |

Chọn một trong `JOBS_RESUME` và `JOBS_OUTPUT_DIRECTORY`. Đường dẫn tương đối tính
từ gốc repository. Khóa dataset bảo vệ việc ghi của các tiến trình trên cùng máy.

Ví dụ thu một run mới đến trang 10.

```dotenv
JOBS_END=10
JOBS_START=
JOBS_HEADLESS=false
JOBS_RESUME=
JOBS_OUTPUT_DIRECTORY=data/raw/jobs/my_run
```

Tiếp tục run đó bằng cùng lệnh Python sau khi chỉnh cấu hình.

```dotenv
JOBS_END=20
JOBS_START=
JOBS_RESUME=data/raw/jobs/my_run
JOBS_OUTPUT_DIRECTORY=
```

Đặt thêm `JOBS_START=1` để quét lại từ trang 1 vào dataset này, giữ các ID đã thu.

## Luồng và tiến độ

1. Mở browser với profile tạm, đọc và áp dụng robots.
2. Dùng goto cho trang đầu hoặc trang cần khôi phục.
3. Trích các thẻ, lưu cards/thông tin phân trang vào checkpoint.
4. Cập nhật rows, ID và offset trong RAM; commit checkpoint mỗi 10 thẻ, gồm cả thẻ trùng.
5. Hoàn tất trang: lưu checkpoint và xuất CSV nếu có tin mới.
6. Click Next nguyên bản và chờ document/AJAX đúng trang.
7. Kết thúc tại giới hạn chỉ định hoặc khi nguồn hết Next/kết quả rỗng.

Checkpoint v5/schema v7 giữ rows, queue, cards, offset và checksum.
Resume yêu cầu checkpoint v5 và fingerprint parser khớp với code hiện tại.
Resume dùng thứ tự thẻ đã lưu. `seen_ids` chứa ID đã quan sát,
`stored_ids` chứa ID trong rows của phiên; trang toàn ID cũ vẫn chuyển tiếp.
Checkpoint là mốc commit bền vững. Khi tiến trình tắt đột ngột, resume xử lý lại
phần chưa commit từ cards đã lưu, tối đa một batch 10 thẻ nếu bị ngắt trong lúc ghi.
Ctrl+C, lỗi và giới hạn thu thập lưu ngay phần đang dở cùng CSV.
Resume tái tạo CSV từ checkpoint trước khi tiếp tục. CSV trong lúc crawl phản ánh
trang hoặc điểm dừng đã xuất gần nhất.

Đường dẫn dataset trong checkpoint được lưu tương đối với gốc repository, dùng `/`.
Khi chuyển checkout sang máy khác, đặt `JOBS_RESUME` theo vị trí run trong checkout đó.

`JOBS_RESUME=PATH` cùng `JOBS_START=N` lưu queue cũ vào lịch sử rồi mở lượt mới từ N.
Dataset giữ bản thu đầu tiên của mỗi ID. Phần nhận diện tin đăng xem
[data dictionary](../../../docs/jobs_schema.md).

## Chờ và phục hồi lỗi

| Tình huống | Xử lý |
| --- | --- |
| Giữa request | Delay + jitter 0–5 giây; thời gian xử lý được tính vào khoảng nghỉ |
| Timeout, 429, 5xx | Tối đa 2 retry trong phiên; backoff nền 15, 30 giây |
| 429 | Tăng delay, giữ giá trị qua resume |
| 403/challenge | Lưu tiến độ và hạn chờ, đóng phiên, cooldown rồi mở phiên mới |
| Retry-After | Ưu tiên thời hạn server; chờ vượt 60 giây giao cho bộ điều phối |
| Lỗi browser ngoài nhóm retry, quyền truy cập, robots, parser/checksum hoặc ghi file | Dừng để kiểm tra |
| Ctrl+C | Giữ checkpoint và hạn chờ |

Số phiên tự tiếp tục được mở theo nhu cầu. Lỗi browser cần kiểm tra trước khi
chủ động tạo lượt mới bằng `JOBS_RESUME=PATH` cùng `JOBS_START=N`.
Chọn resume đơn thuần để giữ chính xác cards/offset đang dở.

Mỗi phiên dùng profile mới. Phản hồi 403 là lỗi truy cập, cần kiểm tra browser và mạng.
Browser mặc định có cửa sổ để quan sát. Khả năng truy cập phụ thuộc chính sách nguồn.

Mỗi trang có tối đa 3 lần thử trong phiên. Chờ `domcontentloaded` tối đa 10 giây;
các bước điều hướng và chờ danh sách dùng `JOBS_TIMEOUT` riêng cho mỗi bước.
Sau lần lỗi cuối, deadline backoff nền 60 giây được lưu để phục hồi phiên.
Thời gian chờ thực tế lấy mức lớn nhất của delay hiện tại, backoff và Retry-After.

## Dữ liệu đầu ra

```text
data/raw/jobs/<run_id>/
  jobs_topcv.csv
  checkpoint.json
```

CSV UTF-8 BOM có 10 cột raw. Checkpoint là nguồn phục hồi; các file đầu ra được
tái xuất từ trạng thái trong RAM. Checkpoint dùng JSON nhiều dòng, thụt lề 2 dấu cách,
giữ thông tin run, tiến độ, request, số liệu chất lượng và lỗi chi tiết.
Header được ghi theo danh sách cho phép;
401/403 có thêm access_diagnostic phục vụ kiểm tra truy cập.

Nguồn là thẻ tin danh sách; dữ liệu detail cần một bước thu thập riêng.
Thứ tự tin thay đổi theo thời gian, nên kết quả là mẫu quan sát tại các lần truy cập.

## Các file chính

| File | Trách nhiệm |
| --- | --- |
| crawl_jobs.py | CLI và cấu hình |
| resume_controller.py | Cooldown và phục hồi phiên |
| collection_runner.py | Điều phối trang/thẻ, điều kiện kết thúc |
| listing_client.py | Điều hướng browser và native Next/AJAX |
| cdp_browser.py | Vòng đời browser và kết nối CDP |
| sources/topcv_parser.py | Trích trường raw |
| sources/pagination.py | Nhận diện trang đích và Next |
| utils/collection_state.py | Checkpoint, chống trùng và xuất dữ liệu |
| utils/rate_limit.py | Delay, jitter, backoff, Retry-After |
| utils/dataset_lock.py | Khóa ghi dataset |

## Sau crawl

```powershell
python src/preprocessing/preprocess_jobs.py --input data/raw/jobs/<run_id>
python -m unittest discover -s tests -t . -v
```

Xem [preprocessing](../../preprocessing/README.md) và
[báo cáo bàn giao](../../../reports/jobs_collection.md).
