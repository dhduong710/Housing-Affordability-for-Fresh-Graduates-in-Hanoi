# Thu thập dữ liệu việc làm

Thu thẻ tin TopCV thành raw CSV, sau đó chuẩn hóa bằng preprocessing.

```text
TopCV → thẻ tin → chống trùng job_id → raw CSV/checkpoint → preprocessing
```

| Đường dẫn | Vai trò |
| --- | --- |
| [jobs/](jobs/README.md) | Truy cập browser, parser và điều phối thu thập |
| `common/storage.py` | Ghi CSV/JSON nguyên tử |
| `data/raw/jobs/<run_id>/` | Dữ liệu, tiến độ và thông tin nguồn |
| [Preprocessing](../preprocessing/README.md) | Phân loại địa điểm, kinh nghiệm và chuẩn hóa lương |
| [Báo cáo dữ liệu](../../reports/jobs_collection.md) | Kết quả bàn giao và chất lượng |

Chạy từ gốc repository:

```powershell
Copy-Item .env.example .env
python src/collection/jobs/crawl_jobs.py
```

Đặt JOBS_END, JOBS_RESUME, JOBS_OUTPUT_DIRECTORY và các giá trị khác trong `.env`.
Chi tiết xem [cấu hình crawler](jobs/README.md).

Run mới có thư mục và tập ID riêng. Resume tiếp tục dataset được chỉ định.
Cards được lưu trước khi xử lý trang; checkpoint commit mỗi 10 thẻ, CSV cập nhật
ở cuối trang có tin mới hoặc khi dừng. Resume khôi phục từ mốc đã commit.
Định nghĩa trường và quy tắc dữ liệu nằm trong [data dictionary](../../docs/jobs_schema.md).
