# Jobs data collection

## Phạm vi và nguồn

Nguồn: [TopCV — việc làm mới nhất](https://www.topcv.vn/tim-viec-lam-moi-nhat?page=1).
Thu thẻ tin danh sách ở các địa điểm và mức kinh nghiệm. Mỗi bản ghi là một tin đăng;
mức lương là thông tin quảng cáo, cần xác minh thành phần thu nhập khi phân tích.
Các ID khác nhau có thể cùng một nhu cầu tuyển dụng.

## Dữ liệu bàn giao

Đối chiếu lúc 08/11/2026 12:54 (giờ Việt Nam), run 20261103T135708605718Z_topcv_browser.

| File | Nội dung |
| --- | --- |
| [Raw CSV](../data/raw/jobs/20261103T135708605718Z_topcv_browser/jobs_topcv.csv) | 11.200 tin duy nhất theo job_id, 10 cột |
| [Processed CSV](../data/processed/jobs/20261103T135708605718Z_topcv_browser_schema_v2/salary_clean.csv) | 11.200 dòng, 21 cột; bổ sung địa điểm, kinh nghiệm và lương |
| [Checkpoint](../data/raw/jobs/20261103T135708605718Z_topcv_browser/checkpoint.json) | Tiến độ, thông tin nguồn, request và lỗi |
| [quality_report.json](preprocessing/jobs/20261103T135708605718Z_topcv_browser/quality_report.json) | Chất lượng dữ liệu, quy tắc và hash truy xuất |

Đã hoàn tất trang 200/200 trong lượt tiếp tục.
Dataset tích lũy qua nhiều phiên/lượt quét và giữ bản thu đầu tiên của mỗi ID.
Phạm vi mục tiêu là 200 trang của danh sách; thứ tự tin thay đổi theo thời điểm truy cập.

## Cách thu thập và chạy lại

Python/Playwright mở Chrome qua CDP; Beautiful Soup trích trường trên thẻ.
Crawler áp dụng robots, chống trùng ID, click Next nguyên bản và chờ document/AJAX.
Khi chạy lại bằng code hiện tại, cards được lưu trước khi xử lý; checkpoint commit
mỗi 10 thẻ, CSV xuất ở cuối trang có tin mới và tại điểm dừng. Cấu hình mặc định:
Chrome có cửa sổ, delay 15 giây + jitter 0–5, timeout 30 giây mỗi bước,
tối đa 2 retry với backoff nền 15/30 giây, cooldown nền 30 giây.
Retry-After xác định thời hạn chờ bổ sung khi nguồn yêu cầu.

~~~powershell
python -m pip install -r requirement.txt
Copy-Item .env.example .env
python src/collection/jobs/crawl_jobs.py
~~~

Thư mục run mới phải chưa tồn tại. Resume tiếp tục tiến độ và giới hạn đã lưu;
Cấu hình đặt trong `.env` theo `.env.example`. JOBS_END đặt trang cuối, JOBS_RESUME
chọn run đang có; JOBS_START mở lượt quét mới và giữ tập ID của dataset.
Lệnh dưới tạo processed và QA:

~~~powershell
python src/preprocessing/preprocess_jobs.py --input data/raw/jobs/20261103T135708605718Z_topcv_browser --output-directory data/processed/jobs/20261103T135708605718Z_topcv_browser_schema_v2
~~~

## Chất lượng dữ liệu chính

Raw và processed đã đối chiếu SHA256; số dòng được giữ nguyên qua preprocessing.

| Trường/điều kiện | Có dữ liệu hoặc đạt | Thiếu/chưa rõ |
| --- | --- | --- |
| Số ô thiếu raw (tổng 10 cột) | — | 0 |
| salary_min | 7.465 | 3.735 |
| salary_max | 7.447 | 3.753 |
| salary_currency | 7.815 | 3.385 |
| salary_period | 11.200 | 0; có dùng mặc định tháng |
| Xác định được is_hanoi | 10.883 | 317 |
| Xác định được is_entry_level | 11.200 | 0 |

Is_eligible có 3.881 true, 7.041 false và 278 chưa rõ.
Trong nhóm true, 3.022 tin có ít nhất một cận lương số;
2.759 tin có đủ hai cận VND/tháng.

Tiêu chí kinh nghiệm là khoảng yêu cầu giao với 0–24 tháng.
Tin đa địa điểm có thể gồm Hà Nội cùng các nơi khác.
Kỳ lương tháng là giả định khi nguồn thiếu kỳ; lương giữ theo tiền tệ và mức quảng cáo.

## Bước tiếp theo

Xác minh mẫu 30–50 tin, rà ngoại lệ lương và địa điểm, phân nhóm nghề,
thống nhất tập phân tích với nhóm. Chọn các bản ghi cùng tiền tệ/kỳ và đủ cận
cho phép tính yêu cầu khoảng lương đầy đủ.
