# Housing data collection

## Phạm vi và nguồn

Nguồn: [Batdongsan.com.vn — căn hộ chung cư bán tại Hà Nội](https://batdongsan.com.vn/ban-can-ho-chung-cu-ha-noi). Đây là **giá chào bán trong tin đăng**, không phải giá giao dịch. Một căn hộ ngoài đời có thể xuất hiện trong nhiều tin với ID khác nhau.

## Dữ liệu đã thu

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

## Bước detail đầy đủ

Chương trình src/collection/housing_requests_detail_crawler.py bổ sung detail
cho CSV 15.723 tin đã có, không crawl lại 793 trang danh sách. **Chưa chạy
detail toàn bộ**, nên chưa có số liệu bao phủ detail của toàn bộ tập dữ liệu.
Mẫu 46 tin từ hai trang đầu không đại diện cho toàn bộ tin đăng.

Chạy thử 50 tin từ thư mục gốc repo sau khi cài requirements-requests.txt:

~~~powershell
python -B src/collection/housing_requests_detail_crawler.py --max-details 50
~~~

Lệnh tạo data/detail_full/RUN_ID/. Chạy tiếp cùng lượt:

~~~powershell
python -B src/collection/housing_requests_detail_crawler.py --resume data/detail_full/RUN_ID
~~~

Bỏ --max-details để thu đến hết. Mỗi request đợi ít nhất 8 giây hoặc lâu
hơn nếu robots yêu cầu; riêng thời gian chờ 15.723 tin đã khoảng 35 giờ.
Thời gian thực tế còn gồm phản hồi, retry và gián đoạn. Crawler chụp bản
sao CSV đầu vào và SHA256, lưu từng detail thành JSON theo listing_id,
xuất lại CSV hợp nhất sau mỗi 250 lần thử và khi dừng. Resume bỏ qua tin đã
lấy thành công hoặc HTTP 404/410; các tin lỗi khác được thử lại. HTTP
403/429 hoặc robots không cho phép sẽ dừng để kiểm tra.

CSV đầu ra là data/detail_full/RUN_ID/housing_details_raw.csv, kèm
quality_report.json và collection_log.json. Mọi dòng danh sách được giữ lại,
kể cả trang detail mất hoặc lỗi; file raw ban đầu không bị sửa. Thư mục
run được Git bỏ qua vì chứa hàng nghìn checkpoint và file trung gian.
Sau khi crawl thật, báo cáo này sẽ được cập nhật bằng số tin detail lấy
được, số tin lỗi/mất trang và tỷ lệ thiếu của các cột detail để cả nhóm
hiểu chính xác bộ dữ liệu thu được.

## Giải thích các cột

Mỗi dòng tương ứng một listing_id duy nhất, **không chắc tương ứng một căn
hộ vật lý duy nhất**. Hai CSV hiện có cùng 33 cột. Ô trống nghĩa là nguồn
không hiển thị hoặc parser chưa lấy được, không có nghĩa bằng 0. Các cột
đuôi _text giữ cách ghi trên trang; các cột số là giá trị parser suy ra.

| Cột | Ý nghĩa và đơn vị | Nguồn |
| --- | --- | --- |
| source_site | Tên trang nguồn | Thẻ tin |
| listing_id | Mã tin; khóa đối chiếu thẻ tin với detail | Thẻ tin |
| source_url | URL trang chi tiết của tin | Thẻ tin |
| page_url | URL trang kết quả nơi tin xuất hiện lần đầu | Thẻ tin |
| collected_at_utc | Thời điểm thu thẻ tin theo UTC | Crawler |
| title | Tiêu đề quảng cáo; có thể nói về nhiều căn | Thẻ tin |
| price_text | Giá hiển thị nguyên dạng chữ và đơn vị | Thẻ tin |
| area_text | Diện tích hiển thị | Thẻ tin |
| price_per_m2_text | Giá/m² hiển thị nếu có | Thẻ tin |
| bedrooms_text | Số phòng ngủ hiển thị | Thẻ tin |
| bathrooms_text | Số phòng tắm hiển thị | Thẻ tin |
| location_text | Địa điểm hiển thị | Thẻ tin |
| description_text | Mô tả ngắn trên thẻ tin | Thẻ tin |
| posted_date_text | Nhãn thời gian đăng, có thể là ngày tương đối; chưa chuẩn hóa | Thẻ tin |
| price_vnd | Giá tổng được tách thành số, đơn vị VND; có thể trống nếu giá mơ hồ | Suy ra từ price_text |
| area_m2 | Diện tích dạng số, đơn vị m² | Suy ra từ area_text |
| price_per_m2_million | price_vnd / area_m2 / 1.000.000; đơn vị triệu VND/m² | Tính từ hai cột số |
| bedrooms | Số phòng ngủ dạng số nguyên | Suy ra từ bedrooms_text |
| bathrooms | Số phòng tắm dạng số nguyên | Suy ra từ bathrooms_text |
| district | Tên quận/huyện tách từ location_text, chưa chuẩn hóa mã hành chính | Suy ra từ thẻ tin |
| ward | Tên phường/xã tách từ location_text, chưa chuẩn hóa mã hành chính | Suy ra từ thẻ tin |
| project_name | Tên dự án nếu trang chi tiết có | Trang detail |
| floor_text | Tầng; chưa thu được trong mẫu 46 tin | Trang detail |
| furniture_text | Thông tin nội thất | Trang detail |
| legal_status_text | Thông tin pháp lý do người đăng cung cấp | Trang detail |
| house_direction_text | Hướng nhà | Trang detail |
| balcony_direction_text | Hướng ban công | Trang detail |
| detail_price_text | Giá hiển thị trên trang chi tiết | Trang detail |
| detail_area_text | Diện tích hiển thị trên trang chi tiết | Trang detail |
| detail_bedrooms_text | Số phòng ngủ hiển thị trên trang chi tiết | Trang detail |
| detail_bathrooms_text | Số phòng tắm hiển thị trên trang chi tiết | Trang detail |
| detail_text | Mô tả dài của tin | Trang detail |
| quality_flags | Các cờ nhắc xem lại, ngăn bằng dấu chấm phẩy | Crawler |

CSV detail đầy đủ bổ sung bốn cột theo dõi:

| Cột | Ý nghĩa |
| --- | --- |
| detail_status | pending: chưa thử; ok: lấy được; not_found: HTTP 404/410; error: lỗi request hoặc parser |
| detail_checked_at_utc | Thời điểm thử lấy detail gần nhất theo UTC |
| detail_http_status | Mã HTTP khi xác định được |
| detail_error | Nội dung lỗi, trống nếu thành công |

Với một tin có detail_status=ok, từng trường detail vẫn có thể trống nếu
trang không hiển thị thông tin đó. collected_at_utc và
detail_checked_at_utc là **thời điểm crawler quan sát**, không phải ngày
đăng tin; posted_date_text chưa được chuyển thành ngày chuẩn.

quality_flags chỉ đánh dấu để xem xét, **không tự loại dòng**:

| Cờ | Ý nghĩa |
| --- | --- |
| missing_or_ambiguous_price | Chưa tách được một giá tổng rõ ràng |
| missing_area, missing_district, missing_bedrooms | Trường tương ứng thiếu hoặc chưa tách được |
| review_price_per_m2 | Giá tính theo m² dưới 10 hoặc trên 500 triệu VND |
| starting_price | Tiêu đề quảng cáo giá “từ” một mức nào đó |
| possible_multi_unit | Tiêu đề có dấu hiệu quảng cáo nhiều căn hoặc bảng giá |
| review_property_type | Đường dẫn khác nhóm bán căn hộ chung cư dự kiến |
| card_detail_price_mismatch | Giá tổng trên thẻ và detail lệch quá 1% |
| card_detail_area_mismatch | Diện tích trên thẻ và detail lệch quá 2% |

Dữ liệu nhà và dữ liệu tuyển dụng không có khóa nối từng dòng. Việc so
sánh khả năng mua nhà cần dựa trên nhóm nghề, địa bàn và các giả định
mô phỏng được ghi rõ, không ghép tùy ý một tin nhà với một tin tuyển dụng.

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

Lượt chạy chính đã tạo danh sách 50 mẫu QA nhưng chưa xác minh thủ công; file trung gian đó không được giữ sau khi dọn dữ liệu. Vì vậy các cờ giá và những ngoại lệ nêu trên chỉ là tín hiệu để xem xét khi làm sạch dữ liệu, chưa phải kết luận tin đúng hay sai. Với các lượt crawl mới, `--resume` bỏ qua các trang đã lưu nên có thể không bắt được tin mới xuất hiện ở đầu danh sách trong thời gian tạm dừng.
