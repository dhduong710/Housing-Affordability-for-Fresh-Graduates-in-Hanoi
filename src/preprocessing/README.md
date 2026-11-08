# Preprocessing dữ liệu jobs

Dùng pandas để bổ sung địa điểm, kinh nghiệm và lương vào raw CSV.
Kết quả giữ các dòng/cột raw, thêm 11 trường phục vụ chọn tập phân tích.

## Chạy

Từ gốc repository, dùng [môi trường chung](../../docs/environment.md):

```powershell
python src/preprocessing/preprocess_jobs.py --input data/raw/jobs/<run_id>
python src/preprocessing/preprocess_jobs.py --input data/raw/jobs/<run_id>/jobs_topcv.csv --output-directory data/processed/jobs/my_batch
```

Input là thư mục run hoặc CSV. Đường dẫn tương đối tính từ gốc repository.
Run ID lấy từ thư mục chứa jobs_topcv.csv. Mặc định:

```text
data/processed/jobs/<run_id>/salary_clean.csv
reports/preprocessing/jobs/<run_id>/quality_report.json
```

Chạy lại cập nhật processed/báo cáo của run. Mỗi lần xử lý đọc một bản raw CSV tại
thời điểm bắt đầu; chạy sau khi crawl hoàn tất để bàn giao đủ dữ liệu của lượt thu.
Trong lúc crawler chạy, CSV được cập nhật ở cuối trang có tin mới và tại điểm dừng.

## Luồng

preprocess_jobs.py kiểm tra cấu trúc CSV, đọc các cột dạng chuỗi, rồi gọi
parse_location, parse_experience và parse_salary trong job_parsers.py.
Các giá trị số giữ độ chính xác Decimal; boolean dùng kiểu nullable.
CSV đầu ra có 21 cột, true/false viết thường và ô trống cho dữ liệu thiếu.

Chi tiết trường xem [data dictionary](../../docs/jobs_schema.md).

## Địa điểm và kinh nghiệm

Địa điểm được chuẩn hóa khoảng trắng, alias Hà Nội/HN/Hanoi và nhãn (mới).
Hà Nội xuất hiện rõ → true; địa phương khác xác định → false;
phạm vi rộng, phần bị ẩn hoặc địa điểm chưa rõ → trống.
is_multi_location ghi nhận tin nhiều nơi/phần bị rút gọn.

| Experience hiển thị | is_entry_level |
| --- | --- |
| Không yêu cầu, 6 tháng, dưới 1 năm, 1 năm, 2 năm | true |
| 1–3 năm, 18–36 tháng, từ 2 năm trở lên | true |
| Trên 2 năm, 25 tháng, 3 năm | false |
| Trống, “Có kinh nghiệm”, chỉ ghi “Fresher”, chuỗi chưa parse được | trống |

Quy tắc là giao khoảng yêu cầu với [0,24] tháng. Cận thiếu để trống,
“Không yêu cầu” có min=0. Đọc cùng experience raw để diễn giải cận mở “trên/dưới”.

is_eligible = is_hanoi AND is_entry_level: cả hai true → true,
có điều kiện false → false, còn lại → trống.
Nhãn này mô tả tiêu chí dữ liệu; cần xác minh mẫu trước khi dùng cho nghiên cứu.

## Lương

| Ví dụ | Kết quả |
| --- | --- |
| 12–15 triệu | min=12000000, max=15000000, VND, month |
| Từ 10 triệu | min=10000000, max trống |
| Tới 20 triệu | min trống, max=20000000 |
| Thỏa thuận/không lương | Số tiền để trống |

Bốn trường lương là salary_min, salary_max, salary_currency và salary_period.
Đơn vị triệu/tr/nghìn/k/tỷ của TopCV được suy luận VND khi thiếu mã tiền.
Kỳ ghi rõ được ưu tiên; thiếu kỳ mặc định month, kỳ mâu thuẫn để trống.
Lương giữ theo tiền tệ/kỳ được nhận diện và mức quảng cáo của nguồn.

Chuỗi sai, cận đảo, số không dương hoặc nhiều khoản chưa phân biệt được giữ số tiền trống.
Chọn các bản ghi cùng tiền tệ/kỳ và đủ cận cho phép tính cần khoảng lương đầy đủ.

## Báo cáo chất lượng

quality_report.json gồm số dòng, trùng source/job_id, missingness, phân loại
true/false/chưa rõ, số tin có lương số và số khoảng kín VND/tháng.
Báo cáo giữ policy version cùng SHA256 input/output/quy tắc để truy xuất.
Các trường `input_file` và `output_file` trong báo cáo dùng đường dẫn tương đối
với gốc repository và dấu `/` cho file nằm trong repository.
Khoảng kín VND/tháng gồm cả các bản ghi dùng giả định kỳ tháng.

```powershell
python -m unittest tests.preprocessing.test_preprocess_jobs -v
```

Bước tiếp theo: xác minh mẫu, rà ngoại lệ, phân nhóm nghề và thống nhất tập phân tích.
Xem [báo cáo dữ liệu](../../reports/jobs_collection.md).
