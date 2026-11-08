# Giả định mô phỏng mua nhà bằng tiền mặt

## Phiên bản hiện tại

- Giá nhà, lương thực nhận và chi tiêu hằng tháng giữ cố định.
- Các khoản tiền được nhập dưới dạng số nguyên, đơn vị VND.
- Lương và chi tiêu được cộng, trừ vào cuối mỗi tháng.
- Chương trình kiểm tra tiền tiết kiệm ban đầu ở tháng 0.
- Thời gian khảo sát mặc định là 600 tháng và có thể thay đổi.
- Chưa tính lãi tiết kiệm, phí giao dịch, tăng lương, tăng giá nhà hay vay.

## Công thức

Tiền tiết kiệm sau mỗi tháng = tiền tiết kiệm tháng trước + lương - chi tiêu.

Tháng mua được nhà là tháng đầu tiên tiền tiết kiệm đạt hoặc vượt giá nhà.
Nếu hết thời gian khảo sát mà chưa đạt, hàm trả về `None`; kết quả này chỉ
áp dụng trong khoảng thời gian và các giả định đã chọn.

Khi chi tiêu lớn hơn lương, số dư tính toán có thể âm. Đây chỉ là kết quả
phép tính dòng tiền, không mô phỏng khoản vay hoặc khả năng chi tiêu thực tế.

## Kiểm thử

Chạy từ thư mục gốc của dự án:

```powershell
python -m unittest discover -s tests -p test_simulation.py
```
