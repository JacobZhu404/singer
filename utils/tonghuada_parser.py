"""
通通信达 .day 文件解析器
支持多种可能的格式，自动检测正确的解析方式
"""

import struct
from pathlib import Path
import pandas as pd
from datetime import datetime
import numpy as np


def detect_tonghuada_format(file_path: str) -> str:
    """
    检测通通信达 .day 文件的格式
    
    尝试两种可能的格式：
    1. 标准格式：每32字节一条记录，价格需要/100
    2. 备选格式：每40字节一条记录（某些版本）
    """
    with open(file_path, 'rb') as fp:
        # 读取前32字节
        data = fp.read(32)
        
        if not data or len(data) < 32:
            return "unknown"
        
        # 尝试解析日期（前4字节）
        date_int = struct.unpack('<i', data[0:4])[0]
        date_str = str(date_int)
        
        # 检查是否是合理的日期格式（YYYYMMDD）
        if len(date_str) == 8:
            try:
                year = int(date_str[0:4])
                month = int(date_str[4:6])
                day = int(date_str[6:8])
                
                if 1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
                    return "standard"  # 标准格式
            except:
                pass
        
        # 如果不是标准格式，可能是其他格式
        return "unknown"


def parse_tonghuada_day_file(file_path: str, format_type: str = "auto") -> pd.DataFrame:
    """
    解析通通信达 .day 文件
    
    Args:
        file_path: .day 文件路径
        format_type: "auto" | "standard" | "alternative"
        
    Returns:
        DataFrame with columns: date, open, high, low, close, volume, amount
    """
    if format_type == "auto":
        format_type = detect_tonghuada_format(file_path)
    
    records = []
    
    with open(file_path, 'rb') as fp:
        while True:
            data = fp.read(32)
            if not data or len(data) < 32:
                break
            
            try:
                # 解析日期（前4字节，小端序int）
                date_int = struct.unpack('<i', data[0:4])[0]
                date_str = str(date_int)
                
                # 确保日期格式正确（YYYYMMDD）
                if len(date_str) != 8:
                    # 尝试其他格式
                    # 有些版本可能使用不同的日期编码
                    continue
                
                year = int(date_str[0:4])
                month = int(date_str[4:6])
                day = int(date_str[6:8])
                
                if not (1990 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31):
                    continue
                
                date = datetime(year, month, day)
                
                # 解析价格（4-20字节，小端序int，需要/100）
                open_price = struct.unpack('<i', data[4:8])[0] / 100.0
                high_price = struct.unpack('<i', data[8:12])[0] / 100.0
                low_price = struct.unpack('<i', data[12:16])[0] / 100.0
                close_price = struct.unpack('<i', data[16:20])[0] / 100.0
                
                # 解析成交量和成交额（20-28字节）
                volume = struct.unpack('<i', data[20:24])[0]  # 手
                amount = struct.unpack('<i', data[24:28])[0] / 100.0  # 元，需要/100
                
                records.append({
                    'date': date,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                    'volume': volume,
                    'amount': amount,
                })
                
            except Exception as e:
                print(f"解析记录失败: {e}")
                continue
    
    df = pd.DataFrame(records)
    if not df.empty:
        df = df.set_index('date')
        df = df.sort_index()  # 按日期排序（从旧到新）
    
    return df


def batch_parse_tonghuada(data_dir: str, output_dir: str = None, max_workers: int = 10):
    """
    批量解析通通信达 .day 文件
    
    Args:
        data_dir: 通通信达数据目录（包含 .day 文件）
        output_dir: 输出目录（如果为None，则不保存）
        max_workers: 最大并行数
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import os
    
    data_path = Path(data_dir)
    if not data_path.exists():
        print(f"目录不存在: {data_dir}")
        return
    
    # 查找所有 .day 文件
    day_files = list(data_path.glob("*.day"))
    print(f"找到 {len(day_files)} 个 .day 文件")
    
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
    
    results = []
    
    def parse_one(file_path: Path):
        try:
            df = parse_tonghuada_day_file(str(file_path))
            if not df.empty:
                if output_dir:
                    # 保存为 parquet 格式（高效压缩）
                    output_file = output_path / f"{file_path.stem}.parquet"
                    df.to_parquet(output_file)
                return (file_path.name, len(df), None)
            return (file_path.name, 0, "无数据")
        except Exception as e:
            return (file_path.name, 0, str(e))
    
    # 并行解析
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(parse_one, f): f for f in day_files}
        
        for future in as_completed(futures):
            file_name, record_count, error = future.result()
            results.append((file_name, record_count, error))
    
    # 打印结果
    success_count = sum(1 for _, _, error in results if error is None and _[1] > 0)
    print(f"\n解析完成: {success_count}/{len(day_files)} 成功")
    
    if any(error for _, _, error in results if error):
        print("\n失败的文件:")
        for file_name, _, error in results:
            if error:
                print(f"  {file_name}: {error}")
    
    return results


def integrate_with_stock_screener(tonghuada_dir: str, stock_screener_data_dir: str):
    """
    将通通信达数据集成到 stock_screener 系统中
    
    步骤：
    1. 解析所有 .day 文件
    2. 转换为 stock_screener 的格式
    3. 保存到 stock_screener 的数据目录
    4. 修改 data/fetcher.py，优先使用本地数据
    """
    print("步骤1: 解析通通信达数据...")
    parsed_dir = f"{stock_screener_data_dir}/tonghuada_parsed"
    batch_parse_tonghuada(tonghuada_dir, parsed_dir, max_workers=20)
    
    print("\n步骤2: 生成集成代码...")
    # 这里应该生成一个新的 fetcher 模块，优先从本地读取
    print(f"解析后的数据已保存到: {parsed_dir}")
    print("接下来需要修改 data/fetcher.py，添加本地数据源支持")
    
    return parsed_dir


if __name__ == "__main__":
    # 测试解析单个文件
    test_file = "/Users/jacob/Downloads/tonghuada/sh/lday/sh000001.day"
    
    print(f"测试解析: {test_file}")
    print(f"检测格式...")
    
    format_type = detect_tonghuada_format(test_file)
    print(f"检测到的格式: {format_type}")
    
    print(f"\n解析数据...")
    df = parse_tonghuada_day_file(test_file, format_type)
    
    if not df.empty:
        print(f"解析成功，共 {len(df)} 条记录")
        print(f"\n前5条记录:")
        print(df.head())
        print(f"\n最后5条记录:")
        print(df.tail())
        print(f"\n数据类型:")
        print(df.dtypes)
    else:
        print("解析失败或无数据")
