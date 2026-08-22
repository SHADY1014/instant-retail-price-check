import 'package:flutter_test/flutter_test.dart';
import 'package:price_check_ocr/services/city_detector.dart';

void main() {
  group('CityDetector.filterCitiesWithinScope', () {
    test('drops cached cities outside the selected area', () {
      final filtered = CityDetector.filterCitiesWithinScope(
        {'东莞店': '东莞市', '佛山店': '佛山市'},
        {'东莞市'},
      );

      expect(filtered, {'东莞店': '东莞市'});
    });

    test('keeps all cached cities when no area is selected', () {
      final cached = {'东莞店': '东莞市', '佛山店': '佛山市'};

      expect(CityDetector.filterCitiesWithinScope(cached, null), cached);
    });
  });
}
