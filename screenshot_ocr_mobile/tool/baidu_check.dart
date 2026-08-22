/// 联网识别验证脚本：验证百度地图接口 + 加权投票城市解析
/// 运行: dart run tool/baidu_check.dart

import 'dart:convert';
import 'package:http/http.dart' as http;

const landmarkSuffixes = [
  '路', '街', '巷', '道', '广场', '花园', '市场', '商城', '城',
  '苑', '里', '湾', '村', '园', '山', '湖', '江', '河', '桥',
  '站', '门', '岗', '塘', '岭', '大道', '公园',
];

/// 从括号内容提取地标词（与 CityDetector._bracketLandmark 相同）
String bracketLandmark(String shopName) {
  final m = RegExp(r'[（(]([^）)]+)[）)]').firstMatch(shopName);
  if (m == null) return '';
  var s = m.group(1)!;
  s = s.replaceAll(RegExp(r'(店|分店|连锁)$'), '');
  final segments = RegExp(r'[一-龥]{2,}')
      .allMatches(s)
      .map((e) => e.group(0)!)
      .toList();
  if (segments.isEmpty) return '';
  segments.sort((a, b) => b.length.compareTo(a.length));
  var seg = segments.first;
  if (seg.length > 8) seg = seg.substring(0, 8);
  while (seg.length >= 2 &&
      !landmarkSuffixes.any((sf) => seg.endsWith(sf))) {
    seg = seg.substring(0, seg.length - 1);
  }
  return seg;
}

/// 与 CityDetector._searchBaiduMap 相同的请求 + 投票逻辑
Future<List<String>> searchBaidu(String shopName, Set<String> allowed) async {
  var cleaned = shopName.replaceAll(RegExp(r'[（(].*?[）)]'), '');
  cleaned = cleaned
      .replaceAll(RegExp(r'[•·\s]+'), '')
      .replaceAll(RegExp(r'(蜂乌准时达|蜂鸟准时达|商家自配送)'), '')
      .trim();

  final queries = <String>[cleaned];
  final landmark = bracketLandmark(shopName);
  if (landmark.isNotEmpty && landmark != cleaned) {
    queries.add(landmark);
  }
  if (cleaned.length > 6) {
    var short = cleaned.replaceAll(
      RegExp(r'(超市|便利店|便利|百货|商行|量贩|精品|综合).*$'),
      '',
    );
    if (short.isNotEmpty && short != cleaned) queries.add(short);
    if (short.length > 4) queries.add(short.substring(0, 4));
    if (short.length > 3) queries.add(short.substring(0, 3));
  }

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
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://map.baidu.com/',
      }).timeout(const Duration(seconds: 10));

      if (resp.statusCode != 200) continue;
      final data = jsonDecode(utf8.decode(resp.bodyBytes));
      final s = data['s'];
      if (s is! List) continue;

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

      if (qi == 0 && hits.length == 1) {
        print('  [q1=$q] 唯一命中: ${hits.keys.first}');
        return [hits.keys.first];
      }

      final total = hits.values.fold(0, (a, b) => a + b);
      final ratios = hits.entries
          .map((e) => '${e.key}:${(e.value / total * 100).toStringAsFixed(0)}%')
          .join(' ');
      print('  [q${qi + 1}=$q] $ratios');
      for (final entry in hits.entries) {
        final ratio = entry.value / total;
        if (!best.containsKey(entry.key)) {
          best[entry.key] = ratio;
          seenOrder.add(entry.key);
        } else if (ratio > best[entry.key]!) {
          best[entry.key] = ratio;
        }
      }
    } catch (e) {
      print('  [q$q] 请求失败: $e');
    }
  }

  String? topCity;
  var topRatio = -1.0;
  for (final city in seenOrder) {
    final ratio = best[city]!;
    if (ratio > topRatio) {
      topRatio = ratio;
      topCity = city;
    }
  }
  return topCity != null ? [topCity] : [];
}

void main() async {
  final validCities = [
    '广州市', '深圳市', '珠海市', '佛山市', '东莞市', '中山市', '南宁市', '柳州市',
    '桂林市', '贵阳市', '遵义市', '昆明市', '海口市', '三亚市', '贵港市', '玉林市',
    '昆明市', '大理市', '丽江市', '曲靖市', '红河市',
  ].toSet();

  final shops = [
    ('美宜佳（南湾北路粤39426店）', '珠海市'), // 用户确认
    ('闪客蜂超市（海风路6店）', '遵义市'), // 百度实际记录（DB为贵阳，L0优先）
    ('1516酒盒子速配（解放店）', '三亚市'),
    ('老友记便利店（金湖广场店）', '南宁市'),
    ('兴旺达生活超市', '东莞市'),
    ('鲜丰水果（天河路店）', '广州市'),
  ];

  var pass = 0;
  for (final (shop, expect) in shops) {
    print('== $shop （期望: $expect） ==');
    final cities = await searchBaidu(shop, validCities);
    final got = cities.isEmpty ? '无' : cities.join(', ');
    final ok = cities.isNotEmpty && cities.first == expect;
    print('  → ${ok ? "✅" : "❌"} 结果: $got');
    if (ok) pass++;
    print('');
  }
  print('通过: $pass/${shops.length}');
}
