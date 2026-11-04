# Housing data collection

## Phạm vi và nguồn

Nguồn: [Batdongsan.com.vn — căn hộ chung cư bán tại Hà Nội](https://batdongsan.com.vn/ban-can-ho-chung-cu-ha-noi). Đây là **giá chào bán trong tin đăng**, không phải giá giao dịch. Một căn hộ ngoài đời có thể xuất hiện trong nhiều tin với ID khác nhau.

## Dữ liệu bàn giao

| File | Nội dung |
| --- | --- |
| `data/housing_listings_raw.csv` | 15.723 tin không trùng `listing_id`, thu từ 793 trang danh sách |
| `data/housing_details_sample.csv` | 46 tin mẫu từ 2 trang đầu, đã bổ sung dữ liệu trang chi tiết |

Lượt crawl chính: `20261104T095731409845Z`, hoàn tất 793/793 trang trong 7.437,844 giây (khoảng 2 giờ 4 phút), 23.770 thẻ tin, 0 lỗi trong log. Trong lúc chạy, crawler lưu snapshot JSONL từng trang để tiếp tục khi gián đoạn. Sau khi kiểm tra kết quả, các snapshot và log trung gian của lượt này đã được dọn; hai CSV trên là dữ liệu còn giữ. Muốn thu lại dữ liệu cần bắt đầu một lượt crawl mới.

## Cách thu thập và chạy lại

Crawler chính là `src/collection/housing_requests_crawler.py`. Nó kiểm tra `robots.txt`, giữ một `requests.Session`, nghỉ mặc định 8 giây giữa các request, thử lại lỗi tạm thời, lưu từng trang, lọc trùng theo `listing_id`, và xuất CSV/JSONL cùng báo cáo chất lượng. `src/collection/housing_playwright_crawler.py` dùng Playwright là phương án dự phòng. Parser, schema và các hàm lưu dữ liệu nằm trong `src/collection/housing_common.py`; cả hai crawler dùng chung phần này và ghi thời gian chạy vào `collection_log.json`.

```powershell
python -m pip install -r requirements-requests.txt
python -B src/collection/housing_requests_crawler.py --all --max-pages 20
python -B src/collection/housing_requests_crawler.py --resume 'data/raw/RUN_ID'
```

Thu một mẫu detail độc lập:

```powershell
python -B src/collection/housing_requests_detail_sample.py --pages 2
```

Lệnh detail cũng hỗ trợ `--input data/housing_listings_raw.csv` để chọn tin từ CSV đã có và `--resume data/detail_sample/RUN_ID` để tiếp tục một lượt mới đang dở. Kết quả được lưu sau từng tin. Lượt mẫu đã hoàn tất 46/46 tin trong 408,534 giây, không có lỗi: 46 tin có `project_name` và `detail_text`, 34 có thông tin nội thất, 39 có thông tin pháp lý, nhưng không tin nào có `floor_text` theo selector hiện tại. Có 1 cờ lệch giá và 1 cờ lệch diện tích giữa thẻ tin và detail cần xem lại.

## Chất lượng dữ liệu chính

| Trường | Có dữ liệu | Thiếu |
| --- | ---: | ---: |
| Giá dạng số (`price_vnd`) | 13.740 | 1.983 |
| Diện tích, quận/huyện, phường/xã, tiêu đề, URL | 15.723 | 0 |
| Phòng ngủ | 14.528 | 1.195 |
| Phòng tắm | 13.960 | 1.763 |
| Mô tả trên thẻ tin | 429 | 15.294 |
| Tên dự án trong lượt crawl chính | 0 | 15.723 |

Có 739 tin bị gắn cờ `possible_multi_unit`, 105 tin `starting_price` và 23 giá/m² cần xem lại. Sau khi tạm loại giá thiếu và ba nhóm cờ giá này, còn 13.024 ứng viên có giá; **đây chưa phải tập dữ liệu đã làm sạch**. Giá chào bán dạng số có trung vị 6,3 tỷ đồng, trải từ 115 triệu đến 240 tỷ đồng; giá trị cực trị cần kiểm tra trước khi mô hình hóa.

793 snapshot liên tục, không có trang nào chỉ lặp toàn bộ ID cũ. 399 ID chung với lượt thử 20 trang khớp hoàn toàn ở giá, diện tích, quận/huyện và số phòng. Mô tả trên thẻ tin chỉ xuất hiện ở 22 trang đầu trong snapshot. Kiểm tra trang nguồn cho thấy các trang sau không có phần mô tả trên thẻ, nhưng trang chi tiết có mô tả. Nếu dự án cần mô tả và tên dự án cho nhiều tin hơn, cần một lượt bổ sung từ trang chi tiết.

Lượt chạy chính đã tạo danh sách 50 mẫu QA nhưng chưa xác minh thủ công; file trung gian đó không được giữ sau khi dọn dữ liệu. Trước khi chốt bàn giao cho bước làm sạch, cần chọn và xác minh thủ công 30–50 tin, thống nhất cách xử lý quảng cáo nhiều căn, giá không rõ và ngoại lệ. Với các lượt crawl mới, `--resume` bỏ qua các trang đã lưu nên có thể không bắt được tin mới xuất hiện ở đầu danh sách trong thời gian tạm dừng.
