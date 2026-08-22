/// 城市识别服务
/// 移植自桌面版 city_detector.py
///
/// 识别层级：
///   L0: 本地数据库查询（shop_city.db，预置619条）
///   L1: 店名直接含城市名（如"三亚昌运超市"）
///   L3: 分店名关键词推断（路名/地标，如"美兰"->海口）
///   L2: 百度地图搜索（联网识别，限定城市集合）

import 'dart:convert';
import 'package:http/http.dart' as http;

import 'shop_city_db.dart';

class CityDetector {
  CityDetector._();

  /// 所有有效城市（61个地级市，带"市"后缀）
  static const validCities = [
    // 广东
    '广州市', '深圳市', '珠海市', '汕头市', '佛山市', '韶关市', '湛江市',
    '肇庆市', '江门市', '茂名市', '惠州市', '梅州市', '汕尾市', '河源市',
    '阳江市', '清远市', '东莞市', '中山市', '潮州市', '揭阳市', '云浮市',
    // 广西
    '南宁市', '柳州市', '桂林市', '梧州市', '北海市', '防城港市',
    '钦州市', '贵港市', '玉林市', '百色市', '贺州市', '河池市',
    '来宾市', '崇左市',
    // 海南
    '海口市', '三亚市', '三沙市', '儋州市',
    // 贵州
    '贵阳市', '六盘水市', '遵义市', '安顺市', '毕节市', '铜仁市',
    // 云南
    '昆明市', '曲靖市', '玉溪市', '保山市', '昭通市', '丽江市',
    '普洱市', '临沧市', '楚雄市', '红河市', '文山市', '西双版纳市',
    '大理市', '德宏市', '怒江市', '迪庆市',
  ];

  /// 城市简称 -> 标准名
  static Map<String, String> get cityKeys {
    return {
      for (final c in validCities) c.replaceAll('市', ''): c,
    };
  }

  /// 分店名关键词 -> 城市（与桌面版 BRANCH_KEYWORD_MAP 完全一致，含云南各市）
  static const Map<String, String> branchKeywordMap = {
    // --- 南宁 ---
    '东葛': '南宁市', '民族大道': '南宁市', '金湖': '南宁市', '埌西': '南宁市',
    '佛子岭': '南宁市', '地王': '南宁市', '艺术学院': '南宁市', '仙葫': '南宁市',
    '五象': '南宁市', '万象城': '南宁市', '航洋': '南宁市', '世纪金源': '南宁市',
    '阳光100': '南宁市', '阳光一百': '南宁市', '大学东': '南宁市', '民族广场': '南宁市',
    '海风路': '南宁市',
    // --- 柳州 ---
    '盛丰国际': '柳州市', '柳北': '柳州市', '柳南': '柳州市', '城中': '柳州市',
    '鱼峰': '柳州市', '柳江': '柳州市', '阳和': '柳州市', '万象': '柳州市',
    '步步高': '柳州市', '广场路': '柳州市',
    // --- 桂林 ---
    '七星': '桂林市', '象山': '桂林市', '叠彩': '桂林市', '秀峰': '桂林市',
    '临桂': '桂林市',
    // --- 广州 ---
    '水荫': '广州市', '昌岗': '广州市', '珠江新城': '广州市',
    '天河': '广州市', '越秀': '广州市', '海珠': '广州市',
    '番禺': '广州市', '黄埔': '广州市', '花都': '广州市', '南沙': '广州市',
    '北京南': '广州市', '白云区': '广州市', '白云': '广州市',
    '京溪': '广州市', '萧岗': '广州市', '石牌': '广州市', '海堤': '广州市',
    '荔湾': '广州市', '北京路': '广州市', '南洲': '广州市', '滨江东': '广州市',
    '区庄': '广州市', '黄花岗': '广州市', '淘金': '广州市', '客村': '广州市',
    '棠东': '广州市', '华景': '广州市', '滨江东路': '广州市', '牛利岗': '广州市',
    // --- 深圳 ---
    '华强北': '深圳市', '景田': '深圳市', '福田': '深圳市', '南山': '深圳市',
    '罗湖': '深圳市', '宝安': '深圳市', '龙华': '深圳市', '龙岗': '深圳市',
    '光明': '深圳市', '坪山': '深圳市', '下沙': '深圳市', '新洲': '深圳市',
    '福星': '深圳市', '东林公园': '深圳市', '水围': '深圳市', '石厦': '深圳市',
    '民治': '深圳市', '车公庙': '深圳市', '新安': '深圳市', '天安云谷': '深圳市',
    '大芬': '深圳市', '布吉': '深圳市', '八卦岭': '深圳市', '上水径': '深圳市',
    // --- 东莞 ---
    '东岸村': '东莞市', '长安': '东莞市', '虎门': '东莞市', '厚街': '东莞市',
    '南城': '东莞市', '东城': '东莞市',
    '石排': '东莞市', '石美': '东莞市', '兴塘口袋公园': '东莞市',
    '华南MALL': '东莞市', '万江': '东莞市',
    // --- 佛山 ---
    '禅城': '佛山市', '南海': '佛山市', '顺德': '佛山市', '三水': '佛山市',
    '高明': '佛山市', '佛山一中': '佛山市', '佛山乐从大道': '佛山市',
    '依云上城': '佛山市', '宏宇景裕豪园': '佛山市', '桂城': '佛山市',
    '大沥': '佛山市', '乐从': '佛山市', '千灯湖': '佛山市', '御酌桂城街道': '佛山市',
    // --- 海口 ---
    '美兰': '海口市', '龙华区': '海口市', '秀英': '海口市', '琼山': '海口市',
    '海垦': '海口市', '南宝路': '海口市', '华庭南区': '海口市', '华庭': '海口市',
    '海甸岛': '海口市', '海甸': '海口市', '海秀': '海口市', '滨海': '海口市',
    '国贸': '海口市', '美苑': '海口市', '龙昆': '海口市', '凤翔': '海口市',
    '生生': '海口市', '生生国际': '海口市',
    // --- 三亚 ---
    '解放': '三亚市', '天涯': '三亚市', '吉阳': '三亚市', '海棠': '三亚市',
    '黄金广场': '三亚市', '榆亚': '三亚市', '海虹路': '三亚市', '胜利路': '三亚市',
    '金陵路口': '三亚市', '金陵': '三亚市', '龙湖天街': '三亚市', '大东海': '三亚市',
    '商品街': '三亚市', '春河路': '三亚市', '万达广场': '三亚市', '跃进街': '三亚市',
    '金鸡岭': '三亚市', '老鸿港': '三亚市', '三亚中心': '三亚市', '三亚': '三亚市',
    // --- 贵阳 ---
    '南明': '贵阳市', '云岩': '贵阳市', '观山湖': '贵阳市', '花溪': '贵阳市',
    '乌当': '贵阳市', '金融城': '贵阳市', '金阳': '贵阳市', '汇都国际': '贵阳市',
    '花果园': '贵阳市', '北站': '贵阳市',
    // --- 遵义 ---
    '红花岗': '遵义市', '汇川': '遵义市', '播州': '遵义市', '新蒲': '遵义市',
    '老城': '遵义市', '东风': '遵义市', '奥特莱斯': '遵义市', '广州路': '遵义市',
    '沙河': '遵义市', '港澳': '遵义市', '白杨小区': '遵义市',
    // --- 昆明 ---
    '五华': '昆明市', '盘龙': '昆明市', '官渡': '昆明市', '西山': '昆明市',
    '呈贡': '昆明市', '晋宁': '昆明市', '安宁': '昆明市', '云纺': '昆明市',
    '关南路': '昆明市', '关上': '昆明市', '富康城': '昆明市', '南屏街': '昆明市',
    '翠湖': '昆明市', '高原明珠': '昆明市', '关东': '昆明市', '西坝': '昆明市',
    '南湖': '昆明市', '莲都山超': '昆明市',
    // --- 曲靖 ---
    '麒麟': '曲靖市', '沾益': '曲靖市', '马龙': '曲靖市', '宣威': '曲靖市',
    // --- 玉溪 ---
    '红塔': '玉溪市', '江川': '玉溪市', '澄江': '玉溪市',
    // --- 大理 ---
    '下关': '大理市', '古城区': '大理市', '大理古城': '大理市',
    // --- 丽江 ---
    '古城': '丽江市', '玉龙': '丽江市',
    // --- 西双版纳 ---
    '景洪': '西双版纳市', '勐海': '西双版纳市', '勐腊': '西双版纳市',
    // --- 红河 ---
    '蒙自': '红河市', '个旧': '红河市', '开远': '红河市', '弥勒': '红河市',
    // --- 文山 ---
    '文山城': '文山市', '砚山': '文山市', '丘北': '文山市',
    // --- 楚雄 ---
    '鹿城': '楚雄市', '楚雄市': '楚雄市',
    // --- 昭通 ---
    '昭阳': '昭通市', '鲁甸': '昭通市',
    // --- 保山 ---
    '隆阳': '保山市', '腾冲': '保山市',
    // --- 普洱 ---
    '思茅': '普洱市', '宁洱': '普洱市',
    // --- 临沧 ---
    '临翔': '临沧市', '凤庆': '临沧市',
    // --- 德宏 ---
    '芒市': '德宏市', '瑞丽': '德宏市', '盈江': '德宏市',
    // --- 迪庆 ---
    '香格里拉': '迪庆市', '德钦': '迪庆市',
    // --- 怒江 ---
    '泸水': '怒江市', '福贡': '怒江市',
  };

  /// 综合城市识别（L0 -> L1 -> L3 -> L2）
  /// [useNetwork] 是否允许联网搜索（百度地图）
  /// [restrictCities] 限定城市集合（如 {"广州市","佛山市"}），null 表示不限
  static Future<String> detectCity(
    String shopName, {
    bool useNetwork = false,
    Set<String>? restrictCities,
  }) async {
    if (shopName.isEmpty) return '';

    // L0: 本地数据库
    final cached = await ShopCityDb.instance.lookup(shopName);
    if (cached.isNotEmpty &&
        (restrictCities == null || restrictCities.contains(cached))) {
      return cached;
    }

    // L1: 店名含城市名
    final cities = _extractCityFromName(shopName);
    final inRange1 = restrictCities == null
        ? cities
        : cities.where((c) => restrictCities.contains(c)).toList();
    if (inRange1.isNotEmpty) {
      final city = inRange1.first;
      await ShopCityDb.instance.save(shopName, city, source: 'name');
      return city;
    }

    // L3: 分店名关键词
    final branchCities = _inferFromBranch(shopName);
    final inRange3 = restrictCities == null
        ? branchCities
        : branchCities.where((c) => restrictCities.contains(c)).toList();
    if (inRange3.isNotEmpty) {
      final city = inRange3.first;
      await ShopCityDb.instance.save(shopName, city, source: 'keyword');
      return city;
    }

    // L2: 百度地图搜索（联网）
    if (useNetwork) {
      final cities = await _searchBaiduMap(
        shopName,
        restrictCities: restrictCities,
      );
      // 与桌面版一致：多城市结果取排序第一个
      if (cities.isNotEmpty) {
        final city = cities.first;
        await ShopCityDb.instance.save(shopName, city, source: 'baidu');
        return city;
      }
    }

    return '';
  }

  /// 批量识别（并发执行，避免串行联网太慢）
  static Future<Map<String, String>> detectCityBatch(
    List<String> shopNames, {
    bool useNetwork = false,
    Set<String>? restrictCities,
    void Function(int current, int total, String? shopName)? progressCallback,
  }) async {
    final results = <String, String>{};
    // 先批量查本地数据库。限定区域时绝不能把范围外缓存直接写回结果，
    // 否则会绕过 detectCity 的范围校验并造成跨城匹配。
    final cached = await ShopCityDb.instance.batchLookup(shopNames);
    results.addAll(filterCitiesWithinScope(cached, restrictCities));

    final pending = shopNames.where((n) => !results.containsKey(n)).toList();
    final total = pending.length;
    if (total == 0) return results;

    // 并发执行（限制 4 个同时进行）
    const concurrency = 4;
    var next = 0;
    Future<void> worker() async {
      while (true) {
        final i = next++;
        if (i >= total) break;
        final name = pending[i];
        try {
          final city = await detectCity(
            name,
            useNetwork: useNetwork,
            restrictCities: restrictCities,
          );
          if (city.isNotEmpty) {
            results[name] = city;
          }
        } catch (_) {}
        if (progressCallback != null) {
          progressCallback(i, total, name);
        }
      }
    }

    final workers = <Future<void>>[
      for (var k = 0; k < concurrency && k < total; k++) worker(),
    ];
    await Future.wait(workers);

    if (progressCallback != null) {
      progressCallback(total, total, null);
    }
    return results;
  }

  /// 仅保留限定范围内的本地命中；未限定时保留全部命中。
  /// 单独保留为纯函数，便于防止批量流程再次绕过范围校验。
  static Map<String, String> filterCitiesWithinScope(
    Map<String, String> cities,
    Set<String>? restrictCities,
  ) {
    if (restrictCities == null) return Map<String, String>.from(cities);
    return {
      for (final entry in cities.entries)
        if (restrictCities.contains(entry.value)) entry.key: entry.value,
    };
  }

  /// L1: 从店名中提取城市关键词
  static List<String> _extractCityFromName(String shopName) {
    final cities = <String>{};
    for (final entry in cityKeys.entries) {
      if (shopName.contains(entry.key)) {
        cities.add(entry.value);
      }
    }
    return cities.toList()..sort();
  }

  /// L3: 从分店名关键词推断城市
  static List<String> _inferFromBranch(String shopName) {
    final m = RegExp(r'[（(]([^）)]+)[）)]').firstMatch(shopName);
    final branch = m != null ? m.group(1)! : shopName;

    final cities = <String>{};
    final sortedKeys = branchKeywordMap.keys.toList()
      ..sort((a, b) => b.length.compareTo(a.length));
    for (final kw in sortedKeys) {
      if (branch.contains(kw)) {
        cities.add(branchKeywordMap[kw]!);
      }
    }
    return cities.toList()..sort();
  }

  /// 常见地标后缀（用于清理括号内容中的门店编号等噪声）
  static const _landmarkSuffixes = [
    '路',
    '街',
    '巷',
    '道',
    '广场',
    '花园',
    '市场',
    '商城',
    '城',
    '苑',
    '里',
    '湾',
    '村',
    '园',
    '山',
    '湖',
    '江',
    '河',
    '桥',
    '站',
    '门',
    '岗',
    '塘',
    '岭',
    '大道',
    '公园',
  ];

  /// 从括号内容提取地标词（如 "南湾北路粤39426店" -> "南湾北路"）
  static String _bracketLandmark(String shopName) {
    final m = RegExp(r'[（(]([^）)]+)[）)]').firstMatch(shopName);
    if (m == null) return '';
    var s = m.group(1)!;
    s = s.replaceAll(RegExp(r'(店|分店|连锁)$'), '');
    // 提取最长连续中文段（跳过数字/字母等门店编号）
    final segments =
        RegExp(r'[一-龥]{2,}').allMatches(s).map((e) => e.group(0)!).toList();
    if (segments.isEmpty) return '';
    segments.sort((a, b) => b.length.compareTo(a.length));
    var seg = segments.first;
    if (seg.length > 8) seg = seg.substring(0, 8);
    // 若末尾不是常见地标后缀，去掉尾部字符（如 "南湾北路粤" -> "南湾北路"）
    while (
        seg.length >= 2 && !_landmarkSuffixes.any((sf) => seg.endsWith(sf))) {
      seg = seg.substring(0, seg.length - 1);
    }
    return seg;
  }

  /// L2: 百度地图搜索建议接口
  /// 与桌面版 _search_baidu_map 相同接口，改进多词条加权投票：
  ///   1. 完整店名（去括号/配送后缀）命中单一城市 -> 直接返回
  ///   2. 否则所有查询词（完整名/地标词/去后缀/前N字）按城市票数占比投票，
  ///      取占比最高的城市（地标词如"金湖广场"占比高时胜出）
  /// 返回排序后的命中城市列表，调用方取第一个
  static Future<List<String>> _searchBaiduMap(
    String shopName, {
    Set<String>? restrictCities,
    Duration timeout = const Duration(seconds: 10),
  }) async {
    // 允许的城市集合
    final allowed = restrictCities ?? validCities.toSet();

    // 清理店名：去括号及内容、特殊字符
    var cleaned = shopName.replaceAll(RegExp(r'[（(].*?[）)]'), '');
    cleaned = cleaned
        .replaceAll(RegExp(r'[•·\s]+'), '')
        .replaceAll(RegExp(r'(蜂乌准时达|蜂鸟准时达|商家自配送)'), '')
        .trim();

    // 生成搜索词列表：完整名 -> 括号地标词 -> 逐步缩短
    final queries = <String>[cleaned];
    final landmark = _bracketLandmark(shopName);
    if (landmark.isNotEmpty && landmark != cleaned) {
      queries.add(landmark);
    }
    if (cleaned.length > 6) {
      var short = cleaned.replaceAll(
        RegExp(r'(超市|便利店|便利|百货|商行|量贩|精品|综合).*$'),
        '',
      );
      if (short.isNotEmpty && short != cleaned) {
        queries.add(short);
      }
      if (short.length > 4) queries.add(short.substring(0, 4));
      if (short.length > 3) queries.add(short.substring(0, 3));
    }

    // 跨查询词投票：城市 -> 最高票数占比
    final best = <String, double>{};
    final seenOrder = <String>[];

    for (var qi = 0; qi < queries.length; qi++) {
      final q = queries[qi];
      if (q.isEmpty || q.length < 2) continue;
      try {
        final uri = Uri.parse(
          'https://map.baidu.com/su?wd=${Uri.encodeComponent(q)}'
          '&cid=1&type=0&newmap=1&from=webmap&prod=0',
        );
        final resp = await http.get(uri, headers: {
          'User-Agent':
              'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          'Referer': 'https://map.baidu.com/',
        }).timeout(timeout);

        if (resp.statusCode != 200) continue;
        final data = jsonDecode(utf8.decode(resp.bodyBytes));
        final s = data['s'];
        if (s is! List) continue;

        // 统计该查询词内每个城市的命中次数
        final hits = <String, int>{};
        for (final item in s.take(10)) {
          final parts = (item as String).split(r'$');
          if (parts.length >= 5) {
            final name = parts.length > 3 ? parts[3] : '';
            final city = parts.isNotEmpty
                ? parts[0]
                : (parts.length > 5 ? parts[5] : '');
            if (allowed.contains(city) && name.isNotEmpty) {
              hits[city] = (hits[city] ?? 0) + 1;
            }
          }
        }
        if (hits.isEmpty) continue;

        // 完整店名（首个查询词）只命中一个城市 -> 直接返回（置信度最高）
        if (qi == 0 && hits.length == 1) {
          return [hits.keys.first];
        }

        // 投票：该查询词内的票数占比
        final total = hits.values.fold(0, (a, b) => a + b);
        for (final entry in hits.entries) {
          final ratio = entry.value / total;
          if (!best.containsKey(entry.key)) {
            best[entry.key] = ratio;
            seenOrder.add(entry.key);
          } else if (ratio > best[entry.key]!) {
            best[entry.key] = ratio;
          }
        }
      } catch (_) {
        continue; // 网络错误继续尝试下一个关键词
      }
    }

    // 取占比最高的城市（平局取先出现的）
    String? topCity;
    var topRatio = -1.0;
    for (final city in seenOrder) {
      final ratio = best[city]!;
      if (ratio > topRatio) {
        topRatio = ratio;
        topCity = city;
      }
    }
    return topCity != null ? [topCity] : <String>[];
  }
}
