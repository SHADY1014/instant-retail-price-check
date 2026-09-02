import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:share_plus/share_plus.dart';

import '../models/form_fields.dart';
import '../services/city_detector.dart';
import '../services/excel_exporter.dart';
import '../services/field_parser.dart';
import '../services/ocr_service.dart';
import '../services/shop_city_db.dart';
import '../services/review_rules.dart';
import '../services/duplicate_checker.dart';
import '../utils/constants.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final _picker = ImagePicker();

  List<FormFields> _results = [];
  bool _isOcrRunning = false;
  bool _cancelOcrRequested = false;
  String _status = '';
  int _ocrCurrent = 0;
  int _ocrTotal = 0;

  // 选中的限定城市
  Set<String> _restrictCities = {};
  bool _useNetwork = false;
  // 仅显示未识别城市的记录
  bool _showUnmatchedOnly = false;
  bool _showReviewOnly = false;
  final Set<String> _failedImagePaths = {};

  @override
  void initState() {
    super.initState();
    _init();
  }

  Future<void> _init() async {
    await ShopCityDb.instance.init();
  }

  /// 当前显示的记录（按筛选条件）
  List<FormFields> get _visibleResults {
    return _results.where((f) {
      if (_showUnmatchedOnly && f.region.isNotEmpty) return false;
      if (_showReviewOnly && reviewIssues(f).isEmpty) return false;
      return true;
    }).toList();
  }

  /// 未识别城市的记录数
  int get _unmatchedCount => _results.where((f) => f.region.isEmpty).length;
  int get _reviewCount =>
      _results.where((f) => reviewIssues(f).isNotEmpty).length;

  // =========================================================
  // 导入图片（相册多选）
  // =========================================================
  Future<void> _pickImages() async {
    try {
      final files = await _picker.pickMultiImage(
        imageQuality: 100,
        maxWidth: 3000,
        maxHeight: 3000,
      );
      if (files.isEmpty) return;

      // 保存到临时目录
      final tempDir = await _saveToTemp(files);

      if (!mounted) return;
      // null 表示用户取消了范围选择；这时禁止自动城市匹配，避免
      // 同名店铺按全量城市命中到错误城市。选择“全量匹配”会返回空集，
      // 但仍视为用户明确确认，保留原有全量行为。
      final citySelectionConfirmed = await _showCitySelectDialog();
      setState(() {
        _results = [];
        _failedImagePaths.clear();
        _showUnmatchedOnly = false;
        _showReviewOnly = false;
      });
      await _runOcr(tempDir, autoDetectCities: citySelectionConfirmed);
    } catch (e) {
      _showSnack('导入图片失败: $e');
    }
  }

  Future<List<String>> _saveToTemp(List<XFile> files) async {
    final tempDir = Directory.systemTemp.createTempSync('ocr_imgs_');
    final paths = <String>[];
    for (var i = 0; i < files.length; i++) {
      final f = files[i];
      final ext = f.path.split('.').lastOrNull ?? 'jpg';
      final target = '${tempDir.path}${Platform.pathSeparator}img_$i.$ext';
      await f.saveTo(target);
      paths.add(target);
    }
    return paths;
  }

  // =========================================================
  // 省份+城市多选对话框
  // =========================================================
  Future<bool> _showCitySelectDialog() async {
    final selected = await showDialog<Set<String>>(
      context: context,
      builder: (ctx) => _CitySelectDialog(),
    );
    if (!mounted) return false;
    // 返回 null = 取消；返回空集 = 用户明确选择“全量匹配”。
    if (selected != null) {
      setState(() {
        _restrictCities = selected;
      });
    }
    // 如果之前已有范围，取消本次选择时可以继续沿用该范围；首次使用
    // 且没有范围时必须跳过自动匹配。
    return selected != null || _restrictCities.isNotEmpty;
  }

  // =========================================================
  // 城市识别（本地数据库 + 可选联网）
  // 独立于 OCR 流程，可对已有结果重新执行
  // =========================================================
  Future<void> _detectCities() async {
    if (_results.isEmpty) {
      _showSnack('没有可识别的数据');
      return;
    }
    final before = _unmatchedCount;
    final shopNames =
        _results.map((f) => f.shopName).where((s) => s.isNotEmpty).toSet();

    setState(() {
      _status = '识别城市中...${_useNetwork ? "（联网）" : ""}';
    });
    try {
      final cityMap = await CityDetector.detectCityBatch(
        shopNames.toList(),
        useNetwork: _useNetwork,
        restrictCities: _restrictCities.isEmpty ? null : _restrictCities,
        progressCallback: (current, total, shopName) {
          if (!mounted) return;
          setState(() {
            _status = '城市识别中: ${current + 1}/$total'
                '${_useNetwork ? "（联网）" : ""}';
          });
        },
      );
      var updated = 0;
      for (final f in _results) {
        final city = cityMap[f.shopName];
        if (city != null) {
          if (f.region != city) updated++;
          f.region = city;
        }
      }
      final after = _unmatchedCount;
      setState(() {
        _status = '城市识别完成：更新 $updated 条，'
            '未识别 ${_unmatchedCount} 条'
            '${before > after ? "（新增识别 ${before - after} 条）" : ""}';
      });
    } catch (e) {
      setState(() => _status = '城市识别失败: $e');
      _showSnack('城市识别失败: $e');
    }
  }

  // =========================================================
  // OCR 识别
  // =========================================================
  Future<void> _runOcr(
    List<String> imagePaths, {
    bool retry = false,
    bool autoDetectCities = true,
  }) async {
    setState(() {
      _isOcrRunning = true;
      _status = '开始识别...';
      _ocrCurrent = 0;
      _ocrTotal = imagePaths.length;
      _cancelOcrRequested = false;
    });

    try {
      final results = <FormFields>[];
      var nextIndex = 0;
      for (var i = 0; i < imagePaths.length; i++) {
        final path = imagePaths[i];
        if (_cancelOcrRequested) {
          nextIndex = i;
          break;
        }
        nextIndex = i + 1;
        if (mounted) {
          setState(() {
            _ocrCurrent = i;
            _status = '识别中: ${i + 1}/${imagePaths.length}';
          });
        }
        try {
          final ocrData = await OcrService.runOcr(path);
          final fields = FieldParser.parse(ocrData);
          fields.imagePath = path;
          results.add(fields);
          _failedImagePaths.remove(path);
        } catch (e) {
          final fields = FormFields(remark: 'OCR失败: $e', imagePath: path);
          results.add(fields);
          _failedImagePaths.add(path);
        }
      }
      if (_cancelOcrRequested) {
        for (var i = nextIndex; i < imagePaths.length; i++) {
          final path = imagePaths[i];
          results.add(FormFields(remark: 'OCR 未完成：已取消，可重试', imagePath: path));
          _failedImagePaths.add(path);
        }
      }

      if (!mounted) return;
      setState(() {
        if (retry && results.isNotEmpty) {
          final byPath = {for (final f in _results) f.imagePath: f};
          for (final f in results) {
            byPath[f.imagePath] = f;
          }
          _results = byPath.values.whereType<FormFields>().toList();
        } else if (results.isNotEmpty) {
          _results = results;
        }
        _ocrCurrent = imagePaths.length;
        final failedInRun =
            results.where((f) => reviewIssues(f).contains('OCR 识别失败')).length;
        _status = _cancelOcrRequested
            ? '识别已取消，可继续重试'
            : '识别完成：成功 ${results.length - failedInRun} 条，失败 $failedInRun 条';
      });
      if (!retry &&
          !_cancelOcrRequested &&
          results.isNotEmpty &&
          autoDetectCities) {
        await _detectCities();
      } else if (!retry &&
          !_cancelOcrRequested &&
          results.isNotEmpty &&
          mounted) {
        setState(() {
          _status = '识别完成：城市暂未匹配，可点击“重新识别城市”处理';
        });
      }
      if (mounted && _results.isEmpty) {
        setState(() => _status = '未识别到数据');
      }
    } catch (e) {
      if (mounted) {
        setState(() => _status = '识别失败，请重试');
        _showSnack('识别失败: $e');
      }
    } finally {
      if (mounted) {
        setState(() => _isOcrRunning = false);
      }
    }
  }

  void _cancelOcr() {
    if (!_isOcrRunning) return;
    setState(() {
      _cancelOcrRequested = true;
      _status = '正在取消识别...';
    });
  }

  Future<void> _retryFailedOcr() async {
    if (_failedImagePaths.isEmpty || _isOcrRunning) return;
    await _runOcr(_failedImagePaths.toList(), retry: true);
  }

  // =========================================================
  // 导出 Excel
  // =========================================================
  Future<void> _exportExcel() async {
    if (_results.isEmpty) {
      _showSnack('没有可导出的数据');
      return;
    }
    final review = [
      for (var i = 0; i < _results.length; i++)
        if (reviewIssues(_results[i]).isNotEmpty)
          (i, reviewIssues(_results[i])),
    ];
    if (review.isNotEmpty && mounted) {
      final proceed = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('导出前核对'),
          content: Text('当前有 ${review.length} 条记录需要人工核对。\n'
              '${review.take(3).map((e) => '第 ${e.$1 + 1} 条：${e.$2.join('；')}').join('\n')}\n\n'
              '建议先修正后再导出，是否仍然导出？'),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('返回核对')),
            FilledButton(
                onPressed: () => Navigator.pop(ctx, true),
                child: const Text('仍然导出')),
          ],
        ),
      );
      if (proceed != true) return;
    }
    try {
      setState(() => _status = '导出中...');
      final (path, _) = await ExcelExporter.exportInspectionSheet(_results);
      if (!mounted) return;
      setState(() => _status = '导出完成');
      _showSnack('已导出: $path');

      // 分享
      await Share.shareXFiles([XFile(path)], text: '价格巡查表');
    } catch (e) {
      if (mounted) setState(() => _status = '导出失败，请重试');
      _showSnack('导出失败: $e');
    }
  }

  Future<void> _checkDuplicates() async {
    final groups = findDuplicateGroups(_results);
    if (groups.isEmpty) {
      _showSnack('未发现重复商品');
      return;
    }
    final remove = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('查重核查'),
        content: Text(
            '发现 ${groups.length} 组重复，共 ${groups.fold<int>(0, (n, g) => n + g.length - 1)} 条冗余记录。\n'
            '口径：店铺名 + 平台 + 城市 + 理论成交价。\n\n确认删除每组中除第一条外的重复记录吗？'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('取消')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('删除重复项')),
        ],
      ),
    );
    if (remove != true) return;
    final deleteIndexes = groups.expand((g) => g.skip(1)).toSet().toList()
      ..sort((a, b) => b.compareTo(a));
    setState(() {
      for (final i in deleteIndexes) {
        _failedImagePaths.remove(_results[i].imagePath);
        _results.removeAt(i);
      }
      _status = '已清理 ${deleteIndexes.length} 条重复记录';
    });
  }

  void _showSnack(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('价格巡检移动端'),
        backgroundColor: Colors.blue.shade700,
        foregroundColor: Colors.white,
      ),
      body: Column(
        children: [
          // 操作区
          Padding(
            padding: const EdgeInsets.all(12),
            child: Column(
              children: [
                // 状态行
                if (_isOcrRunning)
                  LinearProgressIndicator(
                    value: _ocrTotal > 0 ? _ocrCurrent / _ocrTotal : null,
                  ),
                const SizedBox(height: 8),
                Text(_status.isEmpty ? '导入截图开始识别' : _status,
                    style: const TextStyle(fontSize: 13)),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: _isOcrRunning ? null : _pickImages,
                        icon: const Icon(Icons.photo_library),
                        label: const Text('导入截图并识别'),
                      ),
                    ),
                    const SizedBox(width: 8),
                    // 联网开关
                    FilterChip(
                      label: const Text('联网识别'),
                      selected: _useNetwork,
                      onSelected: (v) => setState(() => _useNetwork = v),
                    ),
                  ],
                ),
                if (_isOcrRunning) ...[
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: _cancelOcr,
                      icon: const Icon(Icons.stop_circle_outlined, size: 18),
                      label: const Text('取消识别'),
                    ),
                  ),
                ],
                if (_failedImagePaths.isNotEmpty && !_isOcrRunning) ...[
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: _retryFailedOcr,
                      icon: const Icon(Icons.refresh, size: 18),
                      label: Text('重试失败项（${_failedImagePaths.length}）'),
                    ),
                  ),
                ],
                // 结果操作行：重新识别城市 + 未识别筛选
                if (_results.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Row(
                    children: [
                      Expanded(
                        child: OutlinedButton.icon(
                          onPressed: _isOcrRunning ? null : _detectCities,
                          icon: const Icon(Icons.location_searching, size: 18),
                          label: const Text('重新识别城市',
                              style: TextStyle(fontSize: 13)),
                        ),
                      ),
                      const SizedBox(width: 8),
                      FilterChip(
                        label: Text(
                          '仅看未识别($_unmatchedCount)',
                          style: const TextStyle(fontSize: 12),
                        ),
                        selected: _showUnmatchedOnly,
                        onSelected: (v) =>
                            setState(() => _showUnmatchedOnly = v),
                      ),
                      const SizedBox(width: 6),
                      FilterChip(
                        label: Text('仅看待核对($_reviewCount)',
                            style: const TextStyle(fontSize: 12)),
                        selected: _showReviewOnly,
                        onSelected: (v) => setState(() => _showReviewOnly = v),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: _checkDuplicates,
                      icon: const Icon(Icons.content_copy, size: 18),
                      label: const Text('查重核查'),
                    ),
                  ),
                ],
                // 限定城市显示
                if (_restrictCities.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        '限定区域: ${_restrictCities.join("、")}',
                        style:
                            const TextStyle(fontSize: 12, color: Colors.grey),
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const Divider(height: 1),
          // 结果区
          Expanded(
            child: _results.isEmpty
                ? const Center(
                    child: Text('暂无数据\n点击"导入截图并识别"开始',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: Colors.grey)),
                  )
                : _visibleResults.isEmpty
                    ? Center(
                        child: Text(
                          _showUnmatchedOnly
                              ? '全部 $_unmatchedCount 条记录均已识别城市'
                              : _showReviewOnly
                                  ? '没有待核对记录'
                                  : '无记录',
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: Colors.grey),
                        ),
                      )
                    : ListView.builder(
                        itemCount: _visibleResults.length,
                        itemBuilder: (ctx, i) =>
                            _buildResultCard(_visibleResults[i], i),
                      ),
          ),
        ],
      ),
      bottomNavigationBar: _results.isEmpty
          ? null
          : SafeArea(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Row(
                  children: [
                    Expanded(
                      child: FilledButton.icon(
                        onPressed: _exportExcel,
                        icon: const Icon(Icons.table_chart),
                        label: const Text('导出Excel'),
                        style: FilledButton.styleFrom(
                          backgroundColor: Colors.green.shade700,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
    );
  }

  Widget _buildResultCard(FormFields f, int index) {
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      child: ListTile(
        leading: CircleAvatar(
          radius: 20,
          backgroundColor: Colors.blue.shade50,
          child: Text('${index + 1}',
              style: const TextStyle(fontWeight: FontWeight.bold)),
        ),
        title: Text(
          f.shopName.isEmpty ? '(未识别店铺)' : f.shopName,
          style: const TextStyle(fontWeight: FontWeight.w600),
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(f.productName.isEmpty ? '(未识别产品)' : f.productName,
                style: const TextStyle(fontSize: 12)),
            const SizedBox(height: 2),
            Text(
              '${f.region.isEmpty ? "城市未识别" : f.region}  |  '
              '${f.platform}  |  成交价¥${f.finalPrice.toStringAsFixed(2)}  '
              '配送费¥${f.deliveryFee.toStringAsFixed(2)}',
              style: const TextStyle(fontSize: 12, color: Colors.grey),
            ),
            if (reviewIssues(f).isNotEmpty)
              Text(
                '待核对：${reviewIssues(f).join('；')}',
                style: const TextStyle(fontSize: 11, color: Colors.orange),
              ),
          ],
        ),
        onTap: () => _editResult(f),
      ),
    );
  }

  Future<void> _editResult(FormFields f) async {
    final updated = await showDialog<FormFields>(
      context: context,
      builder: (ctx) => _EditResultDialog(fields: f),
    );
    if (updated != null) {
      setState(() {
        final idx = _results.indexOf(f);
        if (idx >= 0) _results[idx] = updated;
      });
      if (updated.shopName.isNotEmpty && updated.region.isNotEmpty) {
        // 人工确认后的店铺-城市关系进入本地学习库，后续批次可离线命中。
        await ShopCityDb.instance.save(
          updated.shopName,
          updated.region,
          source: 'manual',
        );
      }
    }
  }
}

/// 省份+城市多选对话框
class _CitySelectDialog extends StatefulWidget {
  @override
  State<_CitySelectDialog> createState() => _CitySelectDialogState();
}

class _CitySelectDialogState extends State<_CitySelectDialog> {
  final Map<String, bool> _provChecks = {};
  final Set<String> _citySelected = {};

  @override
  void initState() {
    super.initState();
    for (final p in AppConstants.getProvinces()) {
      _provChecks[p] = false;
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('选择省份和城市'),
      content: SizedBox(
        width: 400,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // 省份多选
            Wrap(
              spacing: 8,
              children: [
                for (final entry in _provChecks.entries)
                  FilterChip(
                    label: Text(entry.key),
                    selected: entry.value,
                    onSelected: (v) => setState(() {
                      _provChecks[entry.key] = v;
                      // 取消选中该省的城市
                      _citySelected.removeWhere(
                        (c) => AppConstants.getCities(entry.key)
                            .any((city) => '${city}市' == c),
                      );
                    }),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            // 城市多选
            Flexible(
              child: SingleChildScrollView(
                child: Wrap(
                  spacing: 8,
                  runSpacing: 4,
                  children: [
                    for (final entry in _provChecks.entries)
                      if (entry.value)
                        for (final city in AppConstants.getCities(entry.key))
                          FilterChip(
                            label: Text(city),
                            selected: _citySelected.contains('${city}市'),
                            onSelected: (v) => setState(() {
                              if (v) {
                                _citySelected.add('${city}市');
                              } else {
                                _citySelected.remove('${city}市');
                              }
                            }),
                          ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          // 返回空集表示全量匹配（清除之前的限定城市）
          onPressed: () => Navigator.pop(context, <String>{}),
          child: const Text('全量匹配'),
        ),
        FilledButton(
          onPressed: () {
            if (_citySelected.isEmpty) {
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('请至少选择一个城市')),
              );
              return;
            }
            Navigator.pop(context, _citySelected);
          },
          child: const Text('确定'),
        ),
      ],
    );
  }
}

/// 编辑单条结果对话框
class _EditResultDialog extends StatefulWidget {
  final FormFields fields;
  const _EditResultDialog({required this.fields});

  @override
  State<_EditResultDialog> createState() => _EditResultDialogState();
}

class _EditResultDialogState extends State<_EditResultDialog> {
  late final TextEditingController _shopCtrl;
  late final TextEditingController _productCtrl;
  late final TextEditingController _regionCtrl;
  late final TextEditingController _priceCtrl;
  late final TextEditingController _deliveryCtrl;

  @override
  void initState() {
    super.initState();
    _shopCtrl = TextEditingController(text: widget.fields.shopName);
    _productCtrl = TextEditingController(text: widget.fields.productName);
    _regionCtrl = TextEditingController(text: widget.fields.region);
    _priceCtrl =
        TextEditingController(text: widget.fields.finalPrice.toString());
    _deliveryCtrl =
        TextEditingController(text: widget.fields.deliveryFee.toString());
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('编辑识别结果'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
                controller: _shopCtrl,
                decoration: const InputDecoration(labelText: '店铺名称')),
            TextField(
                controller: _productCtrl,
                decoration: const InputDecoration(labelText: '产品名称')),
            TextField(
                controller: _regionCtrl,
                decoration: const InputDecoration(labelText: '所属区域')),
            TextField(
                controller: _priceCtrl,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: '成交价')),
            TextField(
                controller: _deliveryCtrl,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: '配送费')),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: () {
            final f = widget.fields;
            f.shopName = _shopCtrl.text.trim();
            f.productName = _productCtrl.text.trim();
            f.region = _regionCtrl.text.trim();
            f.finalPrice = double.tryParse(_priceCtrl.text) ?? 0;
            f.deliveryFee = double.tryParse(_deliveryCtrl.text) ?? 0;
            Navigator.pop(context, f);
          },
          child: const Text('保存'),
        ),
      ],
    );
  }
}
