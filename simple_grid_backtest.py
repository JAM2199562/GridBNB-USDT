#!/usr/bin/env python3
"""
基于EBOLABOY原始策略的简单网格回测
回测过去6个月，本金10000U
"""

import asyncio
import ccxt.async_support as ccxt
import numpy as np
from datetime import datetime, timedelta
import time
import json

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
        
        print(f"🚀 初始化网格回测系统")
        print(f"💰 初始资金: ${initial_capital:,}")
        print(f"📊 交易对: {self.symbol}")
    
    def FLIP_THRESHOLD(self, grid_size):
        """复制原始的FLIP_THRESHOLD逻辑"""
        return grid_size / 100 / 5  # 网格大小的1/5
    
    async def fetch_historical_data(self):
        """获取过去6个月的历史数据"""
        print("📈 获取历史数据...")
        
        exchange = ccxt.binance({
            'sandbox': False,
            'rateLimit': 1200,
            'enableRateLimit': True,
        })
        
        try:
            # 计算6个月前的时间
            end_time = datetime.now()
            start_time = end_time - timedelta(days=180)  # 6个月
            
            print(f"📅 回测期间: {start_time.strftime('%Y-%m-%d')} 到 {end_time.strftime('%Y-%m-%d')}")
            
            # 获取1小时K线数据
            since = int(start_time.timestamp() * 1000)
            
            all_data = []
            current_since = since
            
            while current_since < int(end_time.timestamp() * 1000):
                ohlcv = await exchange.fetch_ohlcv(
                    self.symbol, 
                    '1h', 
                    since=current_since, 
                    limit=1000
                )
                
                if not ohlcv:
                    break
                
                all_data.extend(ohlcv)
                current_since = ohlcv[-1][0] + 3600000  # 下一个小时
                
                # 避免请求过快
                await asyncio.sleep(0.1)
            
            await exchange.close()
            
            print(f"✅ 获取到 {len(all_data)} 条历史数据")
            return all_data
            
        except Exception as e:
            print(f"❌ 获取历史数据失败: {e}")
            await exchange.close()
            return []
    
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
        
        trade = {
            'timestamp': timestamp,
            'type': 'buy',
            'price': price,
            'quantity': buy_quantity,
            'amount': trade_amount,
            'total_value': self.cash + self.base_amount * price
        }
        self.trades.append(trade)
        
        print(f"🟢 买入: ${trade_amount:.2f} @ ${price:.2f} | 持仓: {self.base_amount:.4f} BNB")
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
        
        trade = {
            'timestamp': timestamp,
            'type': 'sell',
            'price': price,
            'quantity': sell_quantity,
            'amount': sell_amount,
            'total_value': self.cash + self.base_amount * price
        }
        self.trades.append(trade)
        
        print(f"🔴 卖出: ${sell_amount:.2f} @ ${price:.2f} | 持仓: {self.base_amount:.4f} BNB")
        return True
    
    async def run_backtest(self):
        """运行回测"""
        print("🧪 开始回测...")
        
        # 获取历史数据
        data = await self.fetch_historical_data()
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

async def main():
    """主函数"""
    backtest = SimpleGridBacktest(initial_capital=10000)
    await backtest.run_backtest()

if __name__ == "__main__":
    asyncio.run(main())