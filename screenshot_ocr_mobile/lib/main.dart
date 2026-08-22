import 'package:flutter/material.dart';

import 'pages/home_page.dart';
import 'services/shop_city_db.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // 初始化数据库（异步，不阻塞启动）
  ShopCityDb.instance.init();
  runApp(const PriceCheckApp());
}

class PriceCheckApp extends StatelessWidget {
  const PriceCheckApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '美团价格核查',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
        useMaterial3: true,
      ),
      home: const HomePage(),
    );
  }
}
