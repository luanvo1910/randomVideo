# Ghép video ngẫu nhiên

Chọn một thư mục video, ghép ngẫu nhiên thành nhiều kết quả khác nhau, rồi xuất ra thư mục khác.

## Tải về và dùng

Vào [Releases](https://github.com/luanvo1910/ghep-video-ngau-nhien/releases), tải `GhepVideo.exe`, rồi mở file đó. Không cần cài Python hay FFmpeg.

## Cách dùng

1. Chọn thư mục video nguồn.
2. Chọn thư mục xuất, khác thư mục nguồn.
3. Nhập số video ghép trong mỗi kết quả (tối thiểu 2) và số kết quả muốn xuất.
4. Chọn khổ dọc hoặc ngang.
5. Bấm **Bắt đầu ghép**.

Mỗi kết quả gồm các video khác nhau. Mỗi video nguồn chỉ nằm trong ít hơn 5 kết quả. Video đã đúng khổ dọc hoặc ngang được giữ nguyên.

Nếu số kết quả nhập vào nhiều hơn số cách ghép có thể tạo, app chỉ làm mức tối đa và báo lại.

File `danh_sach_ghep.txt` trong thư mục xuất ghi từng kết quả được ghép từ video nào.

## FFmpeg

Bản exe có kèm FFmpeg. FFmpeg là phần mềm theo giấy phép GPL. Mã nguồn FFmpeg nằm tại [ffmpeg.org](https://ffmpeg.org/download.html).
