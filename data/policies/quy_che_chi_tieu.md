# QUY CHẾ CHI TIÊU BẰNG THẺ MUA SẮM (BẢN MẪU)

> Tài liệu mẫu xây dựng cho đồ án, áp dụng cho dữ liệu thẻ mua sắm (purchasing card) của các đơn vị. Số tiền tính bằng đô la Mỹ (USD) theo đơn vị tiền tệ của dữ liệu. Các ngưỡng trong quy chế này trùng với cấu hình của rule engine (config/rules.toml).
>
> Mỗi điều có một dòng thông tin phụ dạng `<!-- ... -->` ghi mã điều, các rule liên quan, nhóm chi phí và tín hiệu bất thường liên quan, dùng cho việc truy xuất.

## Chương I. Quy định chung

### Điều 1. Phạm vi và đối tượng áp dụng
<!-- id: D01 | rules: | groups: | signals: -->
1. Quy chế này quy định việc sử dụng thẻ mua sắm của đơn vị để thanh toán các khoản chi phục vụ hoạt động chuyên môn.
2. Quy chế áp dụng cho người được cấp thẻ (sau đây gọi là người chi), kế toán, kiểm soát viên và thủ trưởng đơn vị.
3. Thẻ mua sắm chỉ được dùng cho mục đích công việc. Mọi khoản chi cho mục đích cá nhân đều bị nghiêm cấm, kể cả khi người chi có ý định hoàn trả sau.

### Điều 2. Nguyên tắc chi tiêu
<!-- id: D02 | rules: | groups: | signals: -->
1. Mọi khoản chi phải đúng mục đích, có trong dự toán được giao, có đầy đủ chứng từ hợp lệ và được phê duyệt đúng thẩm quyền.
2. Người chi chịu trách nhiệm về tính hợp lý, tiết kiệm của khoản chi. Kế toán chịu trách nhiệm kiểm tra chứng từ trước khi ghi nhận.
3. Hệ thống kiểm tra tự động chỉ có vai trò hỗ trợ phát hiện dấu hiệu cần xem xét. Quyết định cuối cùng về việc chấp nhận, yêu cầu bổ sung hay từ chối khoản chi do con người đưa ra theo Chương VI.

### Điều 3. Trách nhiệm của người chi
<!-- id: D03 | rules: R01,R03 | groups: | signals: -->
1. Bảo quản thẻ, không cho người khác sử dụng thẻ của mình.
2. Lưu giữ hóa đơn, biên nhận và nộp cho kế toán chậm nhất 05 ngày làm việc sau ngày giao dịch.
3. Ghi mô tả mặt hàng, dịch vụ rõ ràng, cụ thể khi thanh toán hoặc khi kê khai.
4. Giải trình kịp thời khi kế toán hoặc kiểm soát viên yêu cầu.

## Chương II. Hạn mức và phê duyệt

### Điều 4. Hạn mức một giao dịch
<!-- id: D04 | rules: R02 | groups: | signals: -->
1. Hạn mức cho mỗi giao dịch thanh toán bằng thẻ mua sắm là 5.000 USD.
2. Khoản chi trên 5.000 USD phải được thủ trưởng đơn vị phê duyệt bằng văn bản trước khi thanh toán, hoặc thực hiện theo quy trình mua sắm thông thường (đặt hàng, hợp đồng) thay vì dùng thẻ.
3. Giao dịch vượt hạn mức mà không có phê duyệt trước được coi là vi phạm và phải được kiểm soát viên xem xét.

### Điều 5. Nghiêm cấm chia nhỏ giao dịch
<!-- id: D05 | rules: R04 | groups: | signals: burst -->
1. Nghiêm cấm việc chia một khoản mua sắm thành nhiều giao dịch nhỏ hơn hạn mức để tránh phê duyệt.
2. Được coi là có dấu hiệu chia nhỏ khi cùng một người chi thực hiện từ hai giao dịch trở lên tại cùng một nhà cung cấp trong vòng 03 ngày, mỗi giao dịch từ 1.000 USD đến dưới 5.000 USD và tổng giá trị đạt từ 5.000 USD trở lên.
3. Khi có dấu hiệu chia nhỏ, người chi phải giải trình các giao dịch có thuộc cùng một đơn hàng hay nhu cầu không. Nếu thuộc cùng một nhu cầu, khoản chi được xử lý như một giao dịch vượt hạn mức theo Điều 4.

### Điều 6. Thẩm quyền phê duyệt
<!-- id: D06 | rules: R02,R05 | groups: | signals: -->
1. Kế toán được quyết định chấp nhận các khoản chi không có dấu hiệu vi phạm hoặc chỉ có ghi chú mức Thấp.
2. Khoản chi có vi phạm mức Cao, khoản chi vượt ngân sách, khoản chi vượt hạn mức phải được thủ trưởng đơn vị (quản lý) phê duyệt.
3. Người phê duyệt không được tự phê duyệt khoản chi của chính mình.

## Chương III. Chứng từ và ghi nhận

### Điều 7. Chứng từ bắt buộc
<!-- id: D07 | rules: R01 | groups: | signals: -->
1. Mỗi khoản chi phải có hóa đơn hoặc biên nhận ghi rõ tên nhà cung cấp, ngày, mặt hàng hoặc dịch vụ, số lượng, đơn giá và tổng tiền.
2. Khoản chi từ 1.000 USD trở lên mà mô tả mặt hàng chỉ ghi chung chung (ví dụ "GENERAL PURCHASE") phải được bổ sung hóa đơn chi tiết hoặc bản kê mặt hàng trước khi được chấp nhận.
3. Trường hợp mất hóa đơn, người chi lập bản giải trình có xác nhận của thủ trưởng đơn vị.

### Điều 8. Hóa đơn trùng
<!-- id: D08 | rules: R03 | groups: | signals: -->
1. Không được thanh toán hai lần cho cùng một hóa đơn hoặc cùng một hàng hóa, dịch vụ.
2. Giao dịch của cùng người chi, tại cùng nhà cung cấp, cùng số tiền (từ 100 USD trở lên) và cách nhau không quá 03 ngày được coi là có dấu hiệu trùng và phải được đối chiếu hóa đơn.
3. Không coi là trùng các khoản thanh toán tiền phòng khách sạn theo từng đêm và vé máy bay mua cho nhiều người trong cùng một chuyến công tác, nếu có danh sách người đi và lịch trình kèm theo.
4. Nếu xác định là thanh toán trùng, người chi phải liên hệ nhà cung cấp để hoàn tiền vào thẻ trong vòng 30 ngày.

### Điều 9. Thời hạn ghi nhận giao dịch
<!-- id: D09 | rules: | groups: | signals: late_post -->
1. Giao dịch thường được ngân hàng ghi sổ trong vòng 01 đến 05 ngày kể từ ngày mua.
2. Giao dịch ghi sổ chậm hơn 30 ngày so với ngày mua phải được kế toán đối chiếu với hóa đơn gốc để xác định ngày phát sinh thực tế, kỳ hạch toán và tránh ghi nhận sai kỳ ngân sách.
3. Trường hợp giao dịch phát sinh trước thời điểm cấp thẻ hoặc sau khi người chi đã chuyển công tác, kế toán phải báo cáo ngay thủ trưởng đơn vị.

### Điều 10. Hoàn tiền và điều chỉnh
<!-- id: D10 | rules: | groups: | signals: refund -->
1. Khoản hoàn tiền từ nhà cung cấp phải được ghi giảm chi phí của đúng nhóm chi phí và kỳ đã ghi nhận ban đầu.
2. Không được nhận hoàn tiền bằng tiền mặt, thẻ quà tặng hoặc chuyển vào tài khoản cá nhân.

## Chương IV. Ngân sách

### Điều 11. Dự toán theo nhóm chi phí
<!-- id: D11 | rules: R05 | groups: | signals: -->
1. Mỗi đơn vị được giao dự toán chi theo tháng cho từng nhóm chi phí.
2. Khi lũy kế chi trong tháng của một nhóm chi phí đạt 90% dự toán, kế toán thông báo cho người chi và thủ trưởng đơn vị để điều chỉnh kế hoạch chi tiêu.
3. Khoản chi làm lũy kế vượt 100% dự toán phải được thủ trưởng đơn vị phê duyệt và phải có phương án điều chỉnh dự toán hoặc lùi thời điểm chi.
4. Các khoản chi phát sinh thêm sau khi đã vượt dự toán được ghi chú để theo dõi và tổng hợp trong báo cáo cuối tháng.

## Chương V. Các khoản chi đặc thù

### Điều 12. Giao dịch tương đương tiền mặt
<!-- id: D12 | rules: R06 | groups: | signals: -->
1. Nghiêm cấm dùng thẻ mua sắm để rút tiền mặt, chuyển tiền, mua lệnh chuyển tiền (money order), nạp tiền vào ví điện tử hoặc tài khoản trả trước.
2. Mọi giao dịch thuộc loại chuyển tiền hoặc nạp tiền phải được giải trình và, nếu không phục vụ công việc, người chi phải hoàn trả toàn bộ số tiền.

### Điều 13. Nhà cung cấp
<!-- id: D13 | rules: R07 | groups: | signals: new_merchant -->
1. Người chi ưu tiên mua sắm tại các nhà cung cấp đã có trong danh sách nhà cung cấp của đơn vị.
2. Khoản chi từ 500 USD trở lên tại nhà cung cấp chưa có trong danh sách phải nêu lý do lựa chọn (giá, tính sẵn có, tính chuyên dụng) và lưu báo giá nếu có.
3. Kế toán đề xuất bổ sung nhà cung cấp vào danh sách sau khi xác minh thông tin pháp lý và lịch sử giao dịch.

### Điều 14. Danh mục không được phép chi
<!-- id: D14 | rules: R08 | groups: FOOD,OTHER | signals: -->
1. Không được dùng thẻ mua sắm để chi cho: đồ uống có cồn (cửa hàng rượu bia, quán bar), thuốc lá, dịch vụ massage, trang sức, đá quý, cá cược và trò chơi có thưởng.
2. Chi phí tiếp khách có đồ uống có cồn chỉ được chấp nhận khi có quyết định phê duyệt tiếp khách riêng của thủ trưởng đơn vị và không thanh toán bằng thẻ mua sắm.
3. Khoản chi thuộc danh mục cấm phải được hoàn trả, và người chi có thể bị tạm khóa thẻ theo Điều 25.

### Điều 15. Lưu trú tại cơ sở có kinh doanh casino
<!-- id: D15 | rules: R08 | groups: LODGING | signals: -->
1. Tiền phòng tại khách sạn có kinh doanh casino được chấp nhận nếu phục vụ chuyến công tác đã được phê duyệt (ví dụ hội nghị tổ chức tại khách sạn đó).
2. Người chi phải cung cấp hóa đơn phòng chi tiết. Mọi khoản chi tại khu vực casino, quầy bar hoặc dịch vụ giải trí của khách sạn không được thanh toán bằng thẻ mua sắm.

### Điều 16. Giao dịch ngoài ngày làm việc
<!-- id: D16 | rules: R09 | groups: | signals: weekend -->
1. Giao dịch vào thứ Bảy, Chủ nhật hoặc ngày lễ được chấp nhận khi phục vụ chuyến công tác, sự kiện của đơn vị hoặc mua sắm trực tuyến có đơn hàng phục vụ công việc.
2. Giao dịch ngoài ngày làm việc chỉ là thông tin ghi chú. Kế toán xem xét kỹ hơn khi giao dịch đồng thời có dấu hiệu khác (số tiền lớn, nhà cung cấp mới, loại hình cửa hàng không phù hợp với công việc).

### Điều 17. Chi phí công tác: vé máy bay
<!-- id: D17 | rules: R03 | groups: AIR_TRAVEL | signals: -->
1. Vé máy bay được mua ở hạng phổ thông, trừ trường hợp được thủ trưởng đơn vị phê duyệt khác.
2. Hồ sơ thanh toán gồm quyết định cử đi công tác, vé điện tử hoặc xác nhận đặt chỗ ghi rõ tên hành khách và lịch trình.
3. Khi mua vé cho nhiều người, phải kèm danh sách hành khách để phân biệt với thanh toán trùng.

### Điều 18. Chi phí công tác: lưu trú
<!-- id: D18 | rules: R03,R08 | groups: LODGING | signals: amount -->
1. Tiền phòng được thanh toán theo số đêm thực tế lưu trú trong thời gian công tác được phê duyệt.
2. Hóa đơn khách sạn phải ghi rõ tên khách, ngày nhận và trả phòng, giá phòng mỗi đêm.
3. Khoản tiền phòng lớn bất thường so với mức thường chi (ví dụ đặt nhiều phòng cho đoàn hoặc lưu trú dài ngày) phải kèm danh sách người lưu trú và quyết định cử đi công tác.

### Điều 19. Chi phí đi lại tại nơi công tác
<!-- id: D19 | rules: | groups: GROUND_TRANSPORT,VEHICLE_FUEL | signals: -->
1. Chi phí thuê xe, taxi, xe công nghệ, gửi xe, phí cầu đường được thanh toán theo thực tế và phải gắn với chuyến công tác hoặc nhiệm vụ cụ thể.
2. Nhiên liệu chỉ được mua cho phương tiện của đơn vị hoặc xe thuê phục vụ công tác.

### Điều 20. Ăn uống, tiếp khách và hội nghị
<!-- id: D20 | rules: R08 | groups: FOOD,FEES_EDU_EVENTS | signals: -->
1. Chi phí ăn uống phục vụ hội nghị, hội thảo, tiếp khách phải có danh sách người tham dự và mục đích.
2. Không thanh toán chi phí ăn uống cá nhân hằng ngày của người chi, trừ khi phát sinh trong chuyến công tác và nằm trong mức phụ cấp được phép.

### Điều 21. Mua sắm thiết bị công nghệ thông tin và phần mềm
<!-- id: D21 | rules: R02 | groups: IT_TELECOM | signals: amount -->
1. Thiết bị công nghệ thông tin (máy tính, màn hình, thiết bị lưu trữ, thiết bị mạng) mua bằng thẻ phải được ghi nhận vào sổ theo dõi tài sản nếu thuộc diện quản lý tài sản.
2. Phần mềm, dịch vụ thuê bao phải được bộ phận công nghệ thông tin xác nhận nhu cầu và tính tương thích trước khi mua.
3. Thuê bao định kỳ phải được rà soát hằng năm để hủy các dịch vụ không còn sử dụng.

### Điều 22. Vật tư phòng thí nghiệm, y tế và hóa chất
<!-- id: D22 | rules: | groups: MEDICAL_LAB | signals: -->
1. Hóa chất, vật tư y tế phải được mua bởi người có chuyên môn phù hợp và tuân thủ quy định về an toàn, lưu trữ.
2. Hồ sơ thanh toán ghi rõ đề tài, dự án hoặc hoạt động sử dụng vật tư.

### Điều 23. Mua sắm tại sàn thương mại điện tử và cửa hàng bán đủ loại
<!-- id: D23 | rules: R01 | groups: RETAIL_GENERAL | signals: -->
1. Khi mua tại sàn thương mại điện tử (ví dụ Amazon) hoặc siêu thị, cửa hàng bán nhiều loại hàng, người chi phải lưu chi tiết đơn hàng thể hiện từng mặt hàng.
2. Loại chi phí của các khoản mua này được xác định theo mặt hàng thực tế, không theo loại hình cửa hàng do ngân hàng ghi nhận.
3. Kế toán điều chỉnh nhóm chi phí khi loại chi phí được hệ thống gợi ý không phù hợp với mặt hàng thực tế.

## Chương VI. Kiểm tra và xử lý

### Điều 24. Kiểm tra giao dịch và mức rủi ro
<!-- id: D24 | rules: | groups: | signals: amount,new_merchant,new_group,burst,late_post -->
1. Hệ thống kiểm tra tự động xếp mỗi giao dịch vào một trong ba mức: Bình thường, Cần review, Cảnh báo cao.
2. Giao dịch có vi phạm mức Cao được xếp Cảnh báo cao. Giao dịch có vi phạm mức Trung bình hoặc có dấu hiệu bất thường so với thói quen chi tiêu được xếp Cần review.
3. Dấu hiệu bất thường gồm: số tiền cao hơn nhiều lần mức thường chi của người chi, tại nhà cung cấp hoặc của đơn vị cho nhóm chi phí đó; lần đầu chi cho một nhóm chi phí hoặc một nhà cung cấp; số giao dịch trong ngày cao bất thường so với nhịp mua thường ngày; ghi sổ chậm bất thường.
4. Dấu hiệu bất thường không phải là kết luận vi phạm. Kế toán xem xét chứng từ, hỏi người chi khi cần và ghi nhận kết luận.

### Điều 25. Quy trình xử lý giao dịch cần review
<!-- id: D25 | rules: | groups: | signals: -->
1. Kế toán xem xét giao dịch Cần review hoặc Cảnh báo cao trong vòng 05 ngày làm việc.
2. Kế toán có thể: chấp nhận; yêu cầu người chi bổ sung chứng từ hoặc giải trình (thời hạn 05 ngày làm việc); hoặc chuyển thủ trưởng đơn vị phê duyệt.
3. Thủ trưởng đơn vị quyết định phê duyệt hoặc từ chối. Khoản chi bị từ chối phải được người chi hoàn trả trong vòng 30 ngày.
4. Người chi vi phạm nghiêm trọng hoặc tái phạm có thể bị tạm khóa hoặc thu hồi thẻ.
5. Mọi bước xử lý được lưu vết gồm thời điểm, người thực hiện và lý do.

### Điều 26. Mua dồn dập trong thời gian ngắn
<!-- id: D26 | rules: R04 | groups: | signals: burst -->
1. Trường hợp người chi thực hiện số lượng giao dịch trong một ngày cao bất thường so với nhịp mua thường ngày của chính người đó, kế toán xem xét toàn bộ các giao dịch trong ngày như một hồ sơ.
2. Người chi giải trình mục đích của đợt mua (ví dụ mua sắm cho sự kiện, đầu năm học), và cung cấp chi tiết đơn hàng.
3. Nếu các giao dịch cùng phục vụ một nhu cầu và tổng giá trị vượt hạn mức, áp dụng Điều 5 về chia nhỏ giao dịch.
