#!/usr/bin/env python3
"""
基于EBOLABOY原始策略的简单网格回测
回测过去6个月，本金10000U
支持数据库缓存功能
"""

import asyncio
import ccxt.async_support as ccxt
import numpy as np
from datetime import datetime, timedelta
import time
import json
import sqlite3
import os

class SimpleGridBacktest:
    def __init__(self, initial_capital=10000):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.base_amount = 0  # BNB数量
        self.symbol = "BNB/USDT"
        
        # 策略参数 - 复制EBOLABOY的逻辑
        self.base_price = 0
        self.grid_size = 2.0  # 初始网格2%
        self.is_monitoring_buy = False
        self.is_monitoring_sell = False
        self.highest = None
        self.lowest = None
        
        # 交易记录
        self.trades = []
        self.equity_curve = []
        
        # 波动率相关
        self.price_history = []
        self.ewma_volatility = None
        self.last_price = None
        
        # 数据库缓存
        self.db_path = 'backtest_cache.db'
        self._init_database()
        
        print(f"🚀 初始化网格回测系统")
        print(f"💰 初始资金: ${initial_capital:,}")
        print(f"📊 交易对: {self.symbol}")
        print(f"💾 数据库缓存: {self.db_path}")
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS kline_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                open_price REAL NOT NULL,
                high_price REAL NOT NULL,
                low_price REAL NOT NULL,
                close_price REAL NOT NULL,
                volume REAL NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(symbol, timeframe, timestamp)
            )
        ''')
        
        # 创建索引提高查询效率
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_symbol_timeframe_timestamp 
            ON kline_data(symbol, timeframe, timestamp)
        ''')
        
        conn.commit()
        conn.close()
    
    def FLIP_THRESHOLD(self, grid_size):
        """复制原始的FLIP_THRESHOLD逻辑"""
        return grid_size / 100 / 5  # 网格大小的1/5
    
    def _reset_extremes(self):
        """重置极值 - 复制原始策略逻辑"""
        self.highest = None
        self.lowest = None
    
    async def fetch_historical_data(self, days=180, use_cache=True):
        """获取指定天数的历史数据（支持智能缓存补全）"""
        print("📈 获取历史数据...")
        
        # 计算时间范围
        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)
        start_timestamp = int(start_time.timestamp() * 1000)
        end_timestamp = int(end_time.timestamp() * 1000)
        
        print(f"📅 回测期间: {start_time.strftime('%Y-%m-%d')} 到 {end_time.strftime('%Y-%m-%d')}")
        
        timeframe = '1h'
        
        # 检查缓存数据覆盖情况
        if use_cache:
            cache_info = self._check_cache_coverage(self.symbol, timeframe, start_timestamp, end_timestamp)
            
            if cache_info['full_coverage']:
                # 完全覆盖，直接从缓存加载
                cached_data = self._load_from_database(self.symbol, timeframe, start_timestamp, end_timestamp)
                print(f"💾 从缓存加载 {len(cached_data)} 条数据")
                return cached_data
            elif cache_info['partial_coverage']:
                # 部分覆盖，需要补全数据
                print(f"📦 缓存部分覆盖: {cache_info['cached_start']} 到 {cache_info['cached_end']}")
                return await self._fetch_and_merge_data(start_timestamp, end_timestamp, timeframe, cache_info)
        
        # 完全从交易所获取数据
        return await self._fetch_from_exchange(start_timestamp, end_timestamp, timeframe)
    
    def _check_cache_coverage(self, symbol, timeframe, start_timestamp, end_timestamp):
        """检查缓存数据的覆盖情况"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts, COUNT(*) as count
                FROM kline_data 
                WHERE symbol = ? AND timeframe = ?
            ''', (symbol, timeframe))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row or row[2] == 0:
                return {'full_coverage': False, 'partial_coverage': False}
            
            cached_start, cached_end, count = row
            
            # 检查是否完全覆盖所需范围
            if cached_start <= start_timestamp and cached_end >= end_timestamp:
                # 还需要检查数据密度是否足够
                expected_hours = (end_timestamp - start_timestamp) // (3600 * 1000)
                actual_data = self._count_data_in_range(symbol, timeframe, start_timestamp, end_timestamp)
                
                if actual_data >= expected_hours * 0.9:  # 90%覆盖率认为是完整的
                    return {'full_coverage': True, 'partial_coverage': False}
            
            # 检查是否有部分覆盖
            if (cached_start < end_timestamp and cached_end > start_timestamp):
                return {
                    'full_coverage': False, 
                    'partial_coverage': True,
                    'cached_start': cached_start,
                    'cached_end': cached_end
                }
            
            return {'full_coverage': False, 'partial_coverage': False}
            
        except Exception as e:
            print(f"❌ 检查缓存覆盖失败: {e}")
            return {'full_coverage': False, 'partial_coverage': False}
    
    def _count_data_in_range(self, symbol, timeframe, start_timestamp, end_timestamp):
        """统计指定范围内的数据条数"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT COUNT(*) FROM kline_data 
                WHERE symbol = ? AND timeframe = ? 
                AND timestamp >= ? AND timestamp <= ?
            ''', (symbol, timeframe, start_timestamp, end_timestamp))
            
            count = cursor.fetchone()[0]
            conn.close()
            return count
            
        except Exception as e:
            print(f"❌ 统计数据失败: {e}")
            return 0
    
    async def _fetch_and_merge_data(self, start_timestamp, end_timestamp, timeframe, cache_info):
        """获取并合并缺失的数据"""
        print("🔄 补全缺失的数据...")
        
        # 确定需要获取的时间段
        fetch_ranges = []
        
        if start_timestamp < cache_info['cached_start']:
            # 需要获取更早的数据
            fetch_ranges.append((start_timestamp, cache_info['cached_start'] - 3600000))
            print(f"📥 需要补全早期数据: {datetime.fromtimestamp(start_timestamp/1000).strftime('%Y-%m-%d')} 到 {datetime.fromtimestamp(cache_info['cached_start']/1000).strftime('%Y-%m-%d')}")
        
        if end_timestamp > cache_info['cached_end']:
            # 需要获取更新的数据
            fetch_ranges.append((cache_info['cached_end'] + 3600000, end_timestamp))
            print(f"📥 需要补全最新数据: {datetime.fromtimestamp(cache_info['cached_end']/1000).strftime('%Y-%m-%d')} 到 {datetime.fromtimestamp(end_timestamp/1000).strftime('%Y-%m-%d')}")
        
        # 获取缺失的数据
        new_data = []
        for fetch_start, fetch_end in fetch_ranges:
            range_data = await self._fetch_from_exchange(fetch_start, fetch_end, timeframe)
            new_data.extend(range_data)
        
        # 从缓存加载现有数据
        cached_data = self._load_from_database(self.symbol, timeframe, start_timestamp, end_timestamp)
        
        # 合并并排序所有数据
        all_data = new_data + (cached_data or [])
        all_data.sort(key=lambda x: x[0])  # 按时间戳排序
        
        # 去重（以防有重叠）
        seen_timestamps = set()
        unique_data = []
        for candle in all_data:
            if candle[0] not in seen_timestamps:
                seen_timestamps.add(candle[0])
                unique_data.append(candle)
        
        print(f"✅ 合并后共有 {len(unique_data)} 条数据")
        return unique_data
    
    async def _fetch_from_exchange(self, start_timestamp, end_timestamp, timeframe):
        """从交易所获取指定时间范围的数据"""
        print("🌐 从交易所获取数据...")
        
        exchange = ccxt.binance({
            'sandbox': False,
            'rateLimit': 1200,
            'enableRateLimit': True,
        })
        
        try:
            all_data = []
            current_since = start_timestamp
            
            while current_since < end_timestamp:
                ohlcv = await exchange.fetch_ohlcv(
                    self.symbol, 
                    timeframe, 
                    since=current_since, 
                    limit=1000
                )
                
                if not ohlcv:
                    break
                
                # 过滤出指定范围内的数据
                filtered_data = [candle for candle in ohlcv if start_timestamp <= candle[0] <= end_timestamp]
                all_data.extend(filtered_data)
                
                current_since = ohlcv[-1][0] + 3600000  # 下一个小时
                
                # 显示进度
                if len(all_data) % 1000 == 0:
                    print(f"📊 已获取 {len(all_data)} 条数据...")
                
                # 避免请求过快
                await asyncio.sleep(0.1)
            
            await exchange.close()
            
            print(f"✅ 获取到 {len(all_data)} 条历史数据")
            
            # 保存到数据库
            if all_data:
                self._save_to_database(self.symbol, timeframe, all_data)
            
            return all_data
            
        except Exception as e:
            print(f"❌ 获取历史数据失败: {e}")
            await exchange.close()
            return []
    
    def _load_from_database(self, symbol, timeframe, start_timestamp, end_timestamp):
        """从数据库加载数据"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT timestamp, open_price, high_price, low_price, close_price, volume
                FROM kline_data 
                WHERE symbol = ? AND timeframe = ? 
                AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp
            ''', (symbol, timeframe, start_timestamp, end_timestamp))
            
            rows = cursor.fetchall()
            conn.close()
            
            if not rows:
                return None
            
            # 转换为ccxt格式 [timestamp, open, high, low, close, volume]
            return [[row[0], row[1], row[2], row[3], row[4], row[5]] for row in rows]
            
        except Exception as e:
            print(f"❌ 从数据库加载数据失败: {e}")
            return None
    
    def _save_to_database(self, symbol, timeframe, data):
        """保存数据到数据库"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            created_at = int(time.time())
            saved_count = 0
            
            for candle in data:
                try:
                    cursor.execute('''
                        INSERT OR REPLACE INTO kline_data 
                        (symbol, timeframe, timestamp, open_price, high_price, low_price, close_price, volume, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        symbol, timeframe, candle[0], candle[1], candle[2], 
                        candle[3], candle[4], candle[5], created_at
                    ))
                    saved_count += 1
                except sqlite3.IntegrityError:
                    # 数据已存在，跳过
                    pass
            
            conn.commit()
            conn.close()
            
            print(f"💾 数据已保存到数据库: {saved_count} 条记录")
            
        except Exception as e:
            print(f"❌ 保存数据到数据库失败: {e}")
    
    def calculate_volatility(self):
        """计算EWMA波动率 - 复制原始逻辑"""
        if len(self.price_history) < 2:
            return 0.02  # 默认波动率
        
        # 计算收益率
        returns = []
        for i in range(1, len(self.price_history)):
            ret = (self.price_history[i] - self.price_history[i-1]) / self.price_history[i-1]
            returns.append(ret)
        
        if not returns:
            return 0.02
        
        # EWMA计算
        lambda_param = 0.94  # 原始代码中的参数
        if self.ewma_volatility is None:
            self.ewma_volatility = np.std(returns)
        else:
            latest_return = returns[-1]
            self.ewma_volatility = lambda_param * self.ewma_volatility + (1 - lambda_param) * (latest_return ** 2)
        
        return np.sqrt(self.ewma_volatility) if self.ewma_volatility > 0 else 0.02
    
    def adjust_grid_size(self):
        """动态调整网格大小 - 复制原始逻辑"""
        volatility = self.calculate_volatility()
        
        # 原始逻辑：高波动率 -> 大网格，低波动率 -> 小网格
        volatility_factor = min(max(volatility / 0.02, 0.5), 3.0)
        new_grid_size = 2.0 * volatility_factor  # 初始网格2%
        
        # 限制范围
        new_grid_size = max(0.5, min(10.0, new_grid_size))
        
        if abs(new_grid_size - self.grid_size) / self.grid_size > 0.3:  # 30%变化阈值
            old_grid = self.grid_size
            self.grid_size = new_grid_size
            print(f"🔧 网格调整: {old_grid:.2f}% -> {new_grid_size:.2f}% (波动率: {volatility:.4f})")
    
    def check_buy_signal(self, price):
        """检查买入信号 - 复制原始逻辑"""
        if self.base_price <= 0:
            return False
        
        lower_band = self.base_price * (1 - self.grid_size / 100)
        
        if price <= lower_band:
            self.is_monitoring_buy = True
            self.lowest = price if self.lowest is None else min(self.lowest, price)
            
            # 检查反弹
            threshold = self.FLIP_THRESHOLD(self.grid_size)
            if self.lowest and price >= self.lowest * (1 + threshold):
                self.is_monitoring_buy = False
                return True
        else:
            if self.is_monitoring_buy:
                self.is_monitoring_buy = False
                self.highest = None
                self.lowest = None
        
        return False
    
    def check_sell_signal(self, price):
        """检查卖出信号 - 复制原始逻辑"""
        if self.base_price <= 0:
            return False
        
        upper_band = self.base_price * (1 + self.grid_size / 100)
        
        if price >= upper_band:
            self.is_monitoring_sell = True
            self.highest = price if self.highest is None else max(self.highest, price)
            
            # 检查回撤
            threshold = self.FLIP_THRESHOLD(self.grid_size)
            if self.highest and price <= self.highest * (1 - threshold):
                self.is_monitoring_sell = False
                return True
        else:
            if self.is_monitoring_sell:
                self.is_monitoring_sell = False
                self.highest = None
                self.lowest = None
        
        return False
    
    def execute_buy(self, price, timestamp):
        """执行买入 - 总资产的10%"""
        total_value = self.cash + self.base_amount * price
        trade_amount = total_value * 0.1  # 10%
        
        if trade_amount > self.cash:
            trade_amount = self.cash * 0.95  # 留5%余量
        
        if trade_amount < 20:  # 最小交易额
            return False
        
        buy_quantity = trade_amount / price
        self.cash -= trade_amount
        self.base_amount += buy_quantity
        
        # 【关键】更新基准价格 - 复制原始策略逻辑
        old_base_price = self.base_price
        self.base_price = price
        
        # 重置极值（但不重置监测状态）- 完全复制原始逻辑
        self._reset_extremes()
        
        trade = {
            'timestamp': timestamp,
            'type': 'buy',
            'price': price,
            'quantity': buy_quantity,
            'amount': trade_amount,
            'total_value': self.cash + self.base_amount * price,
            'old_base_price': old_base_price,
            'new_base_price': self.base_price
        }
        self.trades.append(trade)
        
        print(f"🟢 买入: ${trade_amount:.2f} @ ${price:.2f} | 持仓: {self.base_amount:.4f} | 基准价: ${old_base_price:.2f} -> ${self.base_price:.2f} | {timestamp.strftime('%Y-%m-%d %H:%M')}")
        return True
    
    def execute_sell(self, price, timestamp):
        """执行卖出 - 总资产的10%"""
        if self.base_amount <= 0:
            return False
        
        total_value = self.cash + self.base_amount * price
        target_amount = total_value * 0.1  # 10%
        sell_quantity = min(target_amount / price, self.base_amount * 0.95)  # 最多卖95%
        
        if sell_quantity * price < 20:  # 最小交易额
            return False
        
        sell_amount = sell_quantity * price
        self.cash += sell_amount
        self.base_amount -= sell_quantity
        
        # 【关键】更新基准价格 - 复制原始策略逻辑
        old_base_price = self.base_price
        self.base_price = price
        
        # 重置极值（但不重置监测状态）- 完全复制原始逻辑
        self._reset_extremes()
        
        trade = {
            'timestamp': timestamp,
            'type': 'sell',
            'price': price,
            'quantity': sell_quantity,
            'amount': sell_amount,
            'total_value': self.cash + self.base_amount * price,
            'old_base_price': old_base_price,
            'new_base_price': self.base_price
        }
        self.trades.append(trade)
        
        print(f"🔴 卖出: ${sell_amount:.2f} @ ${price:.2f} | 持仓: {self.base_amount:.4f} | 基准价: ${old_base_price:.2f} -> ${self.base_price:.2f} | {timestamp.strftime('%Y-%m-%d %H:%M')}")
        return True
    
    async def run_backtest(self, days=180, use_cache=True):
        """运行回测"""
        print("🧪 开始回测...")
        
        # 获取历史数据
        data = await self.fetch_historical_data(days=days, use_cache=use_cache)
        if not data:
            print("❌ 无法获取历史数据")
            return
        
        # 设置初始基准价格
        self.base_price = data[0][4]  # 第一个收盘价
        print(f"📍 初始基准价格: ${self.base_price:.2f}")
        
        # 初始化价格历史
        for i in range(min(100, len(data))):
            self.price_history.append(data[i][4])
        
        last_adjust_time = 0
        
        # 遍历历史数据
        for i, candle in enumerate(data):
            timestamp = datetime.fromtimestamp(candle[0] / 1000)
            price = candle[4]  # 收盘价
            
            # 更新价格历史
            self.price_history.append(price)
            if len(self.price_history) > 168:  # 保持7天数据
                self.price_history.pop(0)
            
            # 每24小时调整一次网格
            if candle[0] - last_adjust_time > 24 * 3600 * 1000:
                self.adjust_grid_size()
                last_adjust_time = candle[0]
            
            # 检查交易信号
            if self.check_sell_signal(price):
                self.execute_sell(price, timestamp)
            elif self.check_buy_signal(price):
                self.execute_buy(price, timestamp)
            
            # 记录资产曲线
            total_value = self.cash + self.base_amount * price
            self.equity_curve.append({
                'timestamp': timestamp,
                'price': price,
                'total_value': total_value,
                'cash': self.cash,
                'base_amount': self.base_amount
            })
            
            # 进度显示
            if i % 500 == 0:
                progress = i / len(data) * 100
                print(f"📊 进度: {progress:.1f}% | 价格: ${price:.2f} | 总价值: ${total_value:.2f}")
        
        # 计算最终结果
        final_price = data[-1][4]
        final_value = self.cash + self.base_amount * final_price
        total_return = (final_value - self.initial_capital) / self.initial_capital * 100
        
        print("\n" + "="*60)
        print("📈 回测结果")
        print("="*60)
        print(f"初始资金: ${self.initial_capital:,}")
        print(f"最终价值: ${final_value:,.2f}")
        print(f"总收益率: {total_return:+.2f}%")
        print(f"交易次数: {len(self.trades)}")
        print(f"最终持仓: {self.base_amount:.4f} BNB (${self.base_amount * final_price:.2f})")
        print(f"剩余现金: ${self.cash:.2f}")
        
        # 计算胜率
        profitable_trades = 0
        for i in range(1, len(self.trades)):
            if self.trades[i]['type'] == 'sell':
                # 找到对应的买入
                for j in range(i-1, -1, -1):
                    if self.trades[j]['type'] == 'buy':
                        if self.trades[i]['price'] > self.trades[j]['price']:
                            profitable_trades += 1
                        break
        
        sell_trades = len([t for t in self.trades if t['type'] == 'sell'])
        win_rate = (profitable_trades / sell_trades * 100) if sell_trades > 0 else 0
        print(f"胜率: {win_rate:.1f}% ({profitable_trades}/{sell_trades})")
        
        # 计算最大回撤
        peak_value = self.initial_capital
        max_drawdown = 0
        for point in self.equity_curve:
            if point['total_value'] > peak_value:
                peak_value = point['total_value']
            drawdown = (peak_value - point['total_value']) / peak_value * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown
        
        print(f"最大回撤: {max_drawdown:.2f}%")
        
        # 保存详细结果
        self.save_results()
        
        print("="*60)
        return {
            'initial_capital': self.initial_capital,
            'final_value': final_value,
            'total_return': total_return,
            'total_trades': len(self.trades),
            'win_rate': win_rate,
            'max_drawdown': max_drawdown
        }
    
    def save_results(self):
        """保存回测结果"""
        results = {
            'summary': {
                'initial_capital': self.initial_capital,
                'final_value': self.cash + self.base_amount * self.equity_curve[-1]['price'],
                'total_return': ((self.cash + self.base_amount * self.equity_curve[-1]['price']) - self.initial_capital) / self.initial_capital * 100,
                'total_trades': len(self.trades),
                'backtest_period': f"{self.equity_curve[0]['timestamp']} to {self.equity_curve[-1]['timestamp']}"
            },
            'trades': self.trades,
            'equity_curve': [
                {
                    'timestamp': point['timestamp'].isoformat(),
                    'price': point['price'],
                    'total_value': point['total_value'],
                    'cash': point['cash'],
                    'base_amount': point['base_amount']
                } for point in self.equity_curve
            ]
        }
        
        with open('grid_backtest_results.json', 'w') as f:
            json.dump(results, f, indent=2, default=str)
        
        print("💾 详细结果已保存到 grid_backtest_results.json")
    
    def clear_cache(self):
        """清空数据库缓存"""
        try:
            if os.path.exists(self.db_path):
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('DELETE FROM kline_data')
                conn.commit()
                conn.close()
                print("🗑️  数据库缓存已清空")
            else:
                print("📁 数据库文件不存在")
        except Exception as e:
            print(f"❌ 清空缓存失败: {e}")
    
    def show_cache_info(self):
        """显示数据库缓存信息"""
        try:
            if not os.path.exists(self.db_path):
                print("📁 数据库文件不存在")
                return
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 获取各交易对的数据统计
            cursor.execute('''
                SELECT symbol, timeframe, COUNT(*) as count, 
                       MIN(timestamp) as min_ts, MAX(timestamp) as max_ts,
                       MAX(created_at) as last_update
                FROM kline_data 
                GROUP BY symbol, timeframe
                ORDER BY symbol, timeframe
            ''')
            
            rows = cursor.fetchall()
            
            if not rows:
                print("📦 数据库缓存为空")
                conn.close()
                return
            
            # 获取数据库文件大小
            db_size = os.path.getsize(self.db_path)
            
            print(f"📦 数据库缓存信息:")
            print("-" * 80)
            print(f"{'交易对':<12} {'周期':<6} {'记录数':<8} {'开始时间':<12} {'结束时间':<12} {'最后更新':<12}")
            print("-" * 80)
            
            for row in rows:
                symbol, timeframe, count, min_ts, max_ts, last_update = row
                
                start_date = datetime.fromtimestamp(min_ts/1000).strftime('%Y-%m-%d')
                end_date = datetime.fromtimestamp(max_ts/1000).strftime('%Y-%m-%d')
                
                update_age = time.time() - last_update
                if update_age < 3600:
                    update_desc = f"{update_age/60:.0f}分钟前"
                elif update_age < 86400:
                    update_desc = f"{update_age/3600:.1f}小时前"
                else:
                    update_desc = f"{update_age/86400:.1f}天前"
                
                print(f"{symbol:<12} {timeframe:<6} {count:<8} {start_date:<12} {end_date:<12} {update_desc:<12}")
            
            print("-" * 80)
            print(f"数据库大小: {db_size/1024:.1f}KB")
            
            conn.close()
            
        except Exception as e:
            print(f"❌ 获取缓存信息失败: {e}")

async def main():
    """主函数"""
    import sys
    
    # 简单的命令行参数处理
    use_cache = True
    initial_capital = 10000
    days = 180  # 默认6个月
    
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg == '--no-cache':
                use_cache = False
                print("🚫 禁用缓存模式")
            elif arg == '--clear-cache':
                backtest = SimpleGridBacktest()
                backtest.clear_cache()
                return
            elif arg == '--cache-info':
                backtest = SimpleGridBacktest()
                backtest.show_cache_info()
                return
            elif arg.startswith('--capital='):
                initial_capital = float(arg.split('=')[1])
            elif arg.startswith('--days='):
                days = int(arg.split('=')[1])
            elif arg == '--help':
                print("""
🎯 简单网格回测工具

用法:
  python simple_grid_backtest.py [选项]

选项:
  --capital=10000     设置初始资金 (默认: 10000)
  --days=180          设置回测天数 (默认: 180天/6个月)
  --no-cache          不使用缓存，强制从交易所获取数据
  --clear-cache       清空所有缓存数据
  --cache-info        显示缓存信息
  --help              显示此帮助信息

示例:
  python simple_grid_backtest.py --capital=5000 --days=365
  python simple_grid_backtest.py --days=90 --no-cache
  python simple_grid_backtest.py --cache-info
                """)
                return
    
    backtest = SimpleGridBacktest(initial_capital=initial_capital)
    
    # 显示回测参数
    period_desc = f"{days}天"
    if days >= 365:
        years = days / 365
        period_desc += f" ({years:.1f}年)"
    elif days >= 30:
        months = days / 30
        period_desc += f" ({months:.1f}个月)"
    
    print(f"🎯 回测参数: {period_desc}, 初始资金: ${initial_capital:,}")
    
    # 如果指定了不使用缓存，先显示提示
    if not use_cache:
        print("⚠️  将从交易所重新获取所有数据，这可能需要较长时间...")
    
    await backtest.run_backtest(days=days, use_cache=use_cache)

if __name__ == "__main__":
    asyncio.run(main())