import '../models/form_fields.dart';

/// Returns groups of duplicate row indexes, keeping the first row as the
/// default record. The key matches the desktop definition.
List<List<int>> findDuplicateGroups(List<FormFields> fields) {
  final groups = <String, List<int>>{};
  for (var i = 0; i < fields.length; i++) {
    final f = fields[i];
    if (f.shopName.trim().isEmpty || f.region.trim().isEmpty) continue;
    final theory = f.theoryPrice.toStringAsFixed(2);
    final key = '${f.shopName.trim()}\u0000${f.platform.trim()}\u0000'
        '${f.region.trim()}\u0000$theory';
    groups.putIfAbsent(key, () => <int>[]).add(i);
  }
  return groups.values.where((rows) => rows.length > 1).toList();
}
