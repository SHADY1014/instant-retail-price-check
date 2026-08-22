/// 店铺城市数据库（SQLite）
/// 移植自桌面版 shop_city_db.py
/// 预置 619 条店铺-城市映射（assets/shop_city.db），首次启动复制到应用文档目录

import 'dart:io';
import 'package:flutter/services.dart';
import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';
import 'package:sqflite/sqflite.dart';

class ShopCityDb {
  ShopCityDb._();
  static final ShopCityDb instance = ShopCityDb._();

  Database? _db;

  /// 初始化数据库（首次启动从 assets 复制预置库）
  Future<void> init() async {
    if (_db != null) return;

    final docsDir = await getApplicationDocumentsDirectory();
    final dbDir = Directory(p.join(docsDir.path, 'LQPriceCheck', 'data'));
    if (!dbDir.existsSync()) {
      dbDir.createSync(recursive: true);
    }
    final dbPath = p.join(dbDir.path, 'shop_city.db');

    // 首次启动：从 assets 复制预置数据库
    if (!File(dbPath).existsSync()) {
      final data = await rootBundle.load('assets/shop_city.db');
      File(dbPath).writeAsBytesSync(
        data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes),
      );
    }

    _db = await openDatabase(dbPath);
  }

  /// 查询店铺对应的城市
  Future<String> lookup(String shopName) async {
    if (shopName.isEmpty || _db == null) return '';
    final rows = await _db!.query(
      'shop_city',
      columns: ['city'],
      where: 'shop_name = ?',
      whereArgs: [shopName],
      limit: 1,
    );
    if (rows.isNotEmpty) {
      return rows.first['city'] as String? ?? '';
    }
    return '';
  }

  /// 批量查询店铺城市
  Future<Map<String, String>> batchLookup(List<String> shopNames) async {
    final result = <String, String>{};
    if (shopNames.isEmpty || _db == null) return result;

    // SQLite has a bound-parameter limit. Split large batches while avoiding
    // one SQL round trip per store during city re-matching.
    const chunkSize = 500;
    final uniqueNames = shopNames.where((name) => name.isNotEmpty).toSet().toList();
    for (var start = 0; start < uniqueNames.length; start += chunkSize) {
      final end = (start + chunkSize).clamp(0, uniqueNames.length).toInt();
      final names = uniqueNames.sublist(start, end);
      final placeholders = List.filled(names.length, '?').join(', ');
      final rows = await _db!.query(
        'shop_city',
        columns: ['shop_name', 'city'],
        where: 'shop_name IN ($placeholders)',
        whereArgs: names,
      );
      for (final row in rows) {
        final shopName = row['shop_name'] as String?;
        final city = row['city'] as String?;
        if (shopName != null && city != null && city.isNotEmpty) {
          result[shopName] = city;
        }
      }
    }
    return result;
  }

  /// 保存店铺->城市映射（UPSERT）
  Future<void> save(String shopName, String city, {String source = 'manual'}) async {
    if (shopName.isEmpty || city.isEmpty || _db == null) return;
    await _db!.insert(
      'shop_city',
      {
        'shop_name': shopName,
        'city': city,
        'source': source,
        'updated': DateTime.now().toIso8601String(),
      },
      conflictAlgorithm: ConflictAlgorithm.replace,
    );
  }

  /// 批量保存
  Future<void> batchSave(Map<String, String> mappings,
      {String source = 'manual'}) async {
    if (mappings.isEmpty || _db == null) return;
    final updated = DateTime.now().toIso8601String();
    await _db!.transaction((txn) async {
      final batch = txn.batch();
      for (final entry in mappings.entries) {
        if (entry.key.isEmpty || entry.value.isEmpty) continue;
        batch.insert(
          'shop_city',
          {
            'shop_name': entry.key,
            'city': entry.value,
            'source': source,
            'updated': updated,
          },
          conflictAlgorithm: ConflictAlgorithm.replace,
        );
      }
      await batch.commit(noResult: true);
    });
  }

  /// 统计信息
  Future<Map<String, dynamic>> getStats() async {
    if (_db == null) return {'total': 0, 'by_city': <String, int>{}};
    final total = Sqflite.firstIntValue(
      await _db!.rawQuery('SELECT COUNT(*) FROM shop_city'),
    );
    final rows = await _db!.rawQuery(
      'SELECT city, COUNT(*) as cnt FROM shop_city GROUP BY city ORDER BY cnt DESC',
    );
    final byCity = <String, int>{};
    for (final row in rows) {
      byCity[row['city'] as String] = row['cnt'] as int;
    }
    return {'total': total ?? 0, 'by_city': byCity};
  }

  /// 关闭数据库
  Future<void> close() async {
    await _db?.close();
    _db = null;
  }
}
