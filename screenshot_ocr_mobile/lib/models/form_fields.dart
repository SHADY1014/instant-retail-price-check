/// 表单字段（对应 Excel A~P 列）
/// 与桌面版 FormFields dataclass 完全一致
class FormFields {
  // A: 分公司
  String branchCompany;
  // B: 所属主要区域（"XX市"）
  String region;
  // C: 平台
  String platform;
  // D: 店铺名称
  String shopName;
  // E: 产品名称
  String productName;
  // F: 原价
  double originalPrice;
  // G: 成交价
  double finalPrice;
  // H: 商品优惠
  double shopDiscount;
  // I: 满减
  double fullReduction;
  // J: 优惠券
  double coupon;
  // K: 红包
  double redPacket;
  // L: 配送费
  double deliveryFee;
  // P: 备注
  String remark;
  // 图片路径（移动端用）
  String? imagePath;

  FormFields({
    this.branchCompany = '漓泉销售公司',
    this.region = '',
    this.platform = '美团闪购',
    this.shopName = '',
    this.productName = '',
    this.originalPrice = 0.0,
    this.finalPrice = 0.0,
    this.shopDiscount = 0.0,
    this.fullReduction = 0.0,
    this.coupon = 0.0,
    this.redPacket = 0.0,
    this.deliveryFee = 0.0,
    this.remark = '',
    this.imagePath,
  });

  /// 理论成交价 = G - L（M 列）
  double get theoryPrice => (finalPrice - deliveryFee);

  /// 去除平台优惠价 = G + J + K - L（N 列）
  double get netPrice => (finalPrice + coupon + redPacket - deliveryFee);

  Map<String, dynamic> toMap() {
    return {
      'branch_company': branchCompany,
      'region': region,
      'platform': platform,
      'shop_name': shopName,
      'product_name': productName,
      'original_price': originalPrice,
      'final_price': finalPrice,
      'shop_discount': shopDiscount,
      'full_reduction': fullReduction,
      'coupon': coupon,
      'red_packet': redPacket,
      'delivery_fee': deliveryFee,
      'remark': remark,
    };
  }
}
