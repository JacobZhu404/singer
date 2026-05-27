"""
通信达 .day 文件解析器 - 完整版本
支持上海、深圳市场的日线数据
"""

import struct
import os
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List
import logging

logger = logging.getLogger(__name__)


def parse_tonghuada_day_file(file_path: str) -> Optional[pd.DataFrame]:
    """
    解析通信达 .day 文件
    
    文件格式（每32字节一条记录，小端序）：
    - 0-3: 日期 (int, 格式 YYYYMMDD)
    - 4-7: 开盘价 (int, 需要除以100)
    - 8-11: 最高价 (int, 需要除以100)
    - 12-15: 最低价 (int, 需要除以100)
    - 16-19: 收盘价 (int, 需要除以100)
    - 20-23: 成交量 (int, 单位：手)
    - 24-27: 成交额 (int, 需要除以100，单位：元)
    - 28-31: reserved (保留字段)
    
    Args:
        file_path: .day 文件路径
        
    Returns:
        DataFrame with columns: ['open', 'high', 'low', 'close', 'volume', 'amount']
        index: DatetimeIndex (date)
    """
    records = []
    
    try:
        with open(file_path, 'rb') as fp:
            while True:
                data = fp.read(32)
                if not data or len(data) < 32:
                    break
                
                # 解析日期（前4字节，小端序int）
                date_int = struct.unpack('<i', data[0:4])[0]
                date_str = str(date_int)
                
                # 验证日期格式（YYYYMMDD）
                if len(date_str) != 8:
                    logger.debug(f"跳过无效日期: {date_int} in {file_path}")
                    continue
                    
                try:
                    year = int(date_str[0:4])
                    month = int(date_str[4:6])
                    day = int(date_str[6:8])
                    
                    # 验证日期有效性
                    if not (1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31):
                        logger.debug(f"跳过无效日期: {date_str} in {file_path}")
                        continue
                        
                    date = datetime(year, month, day)
                    
                except (ValueError, TypeError) as e:
                    logger.debug(f"日期解析失败: {date_str} in {file_path}: {e}")
                    continue
                
                # 解析价格（4-19字节，小端序int，需要除以100）
                try:
                    open_price = struct.unpack('<i', data[4:8])[0] / 100.0
                    high_price = struct.unpack('<i', data[8:12])[0] / 100.0
                    low_price = struct.unpack('<i', data[12:16])[0] / 100.0
                    close_price = struct.unpack('<i', data[16:20])[0] / 100.0
                    
                    # 验证价格合理性
                    if not (0 < open_price < 10000 and 0 < high_price < 10000 and 
                            0 < low_price < 10000 and 0 < close_price < 10000):
                        logger.debug(f"价格异常: {open_price}, {high_price}, {low_price}, {close_price} in {file_path}")
                        continue
                        
                except (struct.error, ZeroDivisionError) as e:
                    logger.debug(f"价格解析失败 in {file_path}: {e}")
                    continue
                
                # 解析成交量和成交额（20-27字节）
                try:
                    volume = struct.unpack('<i', data[20:24])[0]  # 成交量（手）
                    amount = struct.unpack('<i', data[24:28])[0] / 100.0  # 成交额（元，除以100）
                    
                except struct.error as e:
                    logger.debug(f"成交量解析失败 in {file_path}: {e}")
                    volume = 0
                    amount = 0.0
                
                records.append({
                    'date': date,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                    'volume': volume,
                    'amount': amount,
                })
                
    except FileNotFoundError:
        logger.error(f"文件不存在: {file_path}")
        return None
    except PermissionError:
        logger.error(f"无权限读取文件: {file_path}")
        return None
    except Exception as e:
        logger.error(f"解析文件失败 {file_path}: {e}")
        return None
    
    if not records:
        logger.warning(f"文件无有效数据: {file_path}")
        return None
    
    # 创建DataFrame
    df = pd.DataFrame(records)
    df = df.set_index('date')
    df = df.sort_index()  # 按日期排序（从旧到新）
    
    return df


def batch_parse_tonghuada(data_dir: str, 
                           output_dir: str = None,
                           max_workers: int = 20) -> Dict[str, int]:
    """
    批量解析通信达 .day 文件
    
    Args:
        data_dir: 通信达数据目录（包含 .day 文件）
        output_dir: 输出目录（如果为None，则不保存）
        max_workers: 最大并行数
        
    Returns:
        Dict: {ts_code: record_count}
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    data_path = Path(data_dir)
    if not data_path.exists():
        logger.error(f"目录不存在: {data_dir}")
        return {}
    
    # 查找所有 .day 文件
    day_files = list(data_path.glob("*.day"))
    logger.info(f"找到 {len(day_files)} 个 .day 文件")
    
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
    
    results = {}
    
    def parse_one(file_path: Path):
        try:
            df = parse_tonghuada_day_file(str(file_path))
            if df is not None and not df.empty:
                # 从文件名提取股票代码（如 sh000001.day -> 000001.SH）
                file_name = file_path.stem  # sh000001
                if file_name.startswith('sh'):
                    ts_code = file_name[2:] + '.SH'
                elif file_name.startswith('sz'):
                    ts_code = file_name[2:] + '.SZ'
                else:
                    ts_code = file_name
                
                if output_dir:
                    # 保存为 parquet 格式（高效压缩）
                    output_file = output_path / f"{ts_code}.parquet"
                    df.to_parquet(output_file)
                
                return (ts_code, len(df), None)
            return (file_name, 0, "无数据")
            
        except Exception as e:
            logger.error(f"解析失败 {file_path}: {e}")
            return (file_name, 0, str(e))
    
    # 并行解析
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(parse_one, f): f for f in day_files}
        
        for future in as_completed(futures):
            ts_code, record_count, error = future.result()
            results[ts_code] = record_count
            
            if error:
                logger.error(f"失败: {ts_code}: {error}")
            else:
                logger.info(f"成功: {ts_code}: {record_count} 条记录")
    
    success_count = sum(1 for _, count, error in results.items() if error is None and count > 0)
    logger.info(f"解析完成: {success_count}/{len(day_files)} 成功")
    
    return results


def integrate_tonghuada_with_stock_screener(tonghuada_dir: str, 
                                            stock_screener_data_dir: str):
    """
    将通信达数据集成到 stock_screener 系统中
    
    步骤：
    1. 解析所有 .day 文件
    2. 保存到 stock_screener 的数据目录
    3. 返回解析后的数据目录路径
    """
    logger.info("步骤1: 解析通信达数据...")
    parsed_dir = os.path.join(stock_screener_data_dir, "tonghuada_parsed")
    batch_parse_tonghuada(tonghuada_dir, parsed_dir, max_workers=20)
    
    logger.info(f"步骤2: 解析后的数据已保存到: {parsed_dir}")
    logger.info("步骤3: 接下来需要修改 data/fetcher.py，添加本地数据源支持")
    
    return parsed_dir


if __name__ == "__main__":
    # 测试解析单个文件
    test_file = "/Users/jacob/Downloads/tonghuada/sh/lday/sh000001.day"
    
    print(f"测试解析: {test_file}")
    
    df = parse_tonghuada_day_file(test_file)
    
    if df is not None and not df.empty:
        print(f"解析成功，共 {len(df)} 条记录")
        print(f"\n前5条记录:")
        print(df.head())
        print(f"\n最后5条记录:")
        print(df.tail())
        print(f"\n数据类型:")
        print(df.dtypes)
    else:
        print("解析失败或无数据")
