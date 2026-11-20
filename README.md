# Ghép video ngẫu nhiên

App Windows đọc một thư mục video, trộn ngẫu nhiên rồi ghép thành nhiều video kết quả.

## Tải về và dùng

Vào [Releases](https://github.com/luanvo1910/randomVideo/releases), tải `GhepVideo.exe`, rồi mở file đó. Không cần cài Python hay FFmpeg.

Từ bản 1.1.0, mỗi lần mở app sẽ kiểm tra GitHub. Nếu có bản mới, app tự tải `GhepVideo.exe` và khởi động lại.

## Cách dùng

1. Chọn thư mục video nguồn.
2. Chọn thư mục xuất, khác thư mục nguồn.
3. Nhập số video ghép trong mỗi kết quả (tối thiểu 2) và số kết quả muốn xuất.
4. Chọn khổ dọc hoặc ngang.
5. Bấm **Bắt đầu ghép**.

Mỗi kết quả gồm ít nhất 2 video khác nhau. App trộn toàn bộ video nguồn rồi chia ngẫu nhiên, không theo tên file hay một thứ tự nào khác. Mỗi video nguồn chỉ được dùng đúng 1 lần trong toàn bộ kết quả. Video dư, không đủ để ghép thêm một kết quả, sẽ không được dùng.

Nếu số kết quả nhập vào nhiều hơn số video chia được, app chỉ làm mức tối đa và báo lại.

Video đã đúng khổ dọc hoặc ngang được giữ nguyên.

File `danh_sach_ghep.txt` trong thư mục xuất ghi từng kết quả được ghép từ video nào.

## FFmpeg

Bản exe có kèm FFmpeg. FFmpeg là phần mềm theo giấy phép GPL. Mã nguồn FFmpeg nằm tại [ffmpeg.org](https://ffmpeg.org/download.html).
