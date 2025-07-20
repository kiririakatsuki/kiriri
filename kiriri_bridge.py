# -*- coding: utf-8 -*-

# Kiririセンサーブリッジプログラム (ハイブリッド版)
# - まずMACアドレス直結を試み、失敗したらスキャンに切り替えます。
# - ログ機能、自動再接続機能を搭載

import asyncio
import websockets
import json
from bleak import BleakClient, BleakScanner, BleakError
import logging
import sys
from typing import Set, Dict, Optional, Any, List
import os
from logging.handlers import TimedRotatingFileHandler

# --- 基本設定 ---
# 優先的に接続を試みるMACアドレス（不明な場合は空欄でもOK）
TARGET_MAC_ADDRESS: str = "EE:3C:71:89:4E:E5"  # <-- 接続したいセンサーのMACアドレス
# スキャン時に探すデバイス名
TARGET_DEVICE_NAMES: List[str] = ["KIRIRI02", "KIRIRI01", "KIRI"]

DATA_UUID: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
RX_UUID: str = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
WEBSOCKET_HOST: str = "localhost"
WEBSOCKET_PORT: int = 8765
RECONNECT_DELAY: float = 5.0
SCAN_TIMEOUT: float = 10.0
# ----------------

# --- ログ設定 ---
def setup_logging():
    """ログ設定を初期化する関数"""
    log_dir = "logs"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    log_file_path = os.path.join(log_dir, "sensor_bridge.log")
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] (%(funcName)s) %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        logger.addHandler(console_handler)
    # File handler
    file_handler = TimedRotatingFileHandler(
        log_file_path, when='D', interval=1, backupCount=30, encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    if not any(isinstance(h, TimedRotatingFileHandler) for h in logger.handlers):
        logger.addHandler(file_handler)

# --- グローバル変数 ---
latest_angles: Dict[str, Optional[Any]] = {"id": None, "y": 0.0, "x": 0.0}
connected_clients: Set[websockets.WebSocketServerProtocol] = set()
new_data_available = asyncio.Event()

# --- 関数定義 ---
async def scan_and_select_sensor() -> Optional[str]:
    """近くにある対象のBLEデバイスをスキャンし、ユーザーに選択させる"""
    logging.info(f"'{', '.join(TARGET_DEVICE_NAMES)}' センサーを探しています (最大{SCAN_TIMEOUT}秒)...")
    try:
        found_devices = {
            dev.address: dev.name
            for dev in await BleakScanner.discover(timeout=SCAN_TIMEOUT)
            if dev.name and any(target in dev.name for target in TARGET_DEVICE_NAMES)
        }
    except BleakError as e:
        logging.error(f"BLEスキャン中にエラーが発生しました: {e}")
        return None

    if not found_devices:
        logging.error("対象のセンサーが見つかりませんでした。")
        return None

    devices_list = list(found_devices.items())
    if len(devices_list) == 1:
        selected_address = devices_list[0][0]
        logging.info(f"センサーが1台見つかりました。自動的に接続します: {devices_list[0][1]}")
        return selected_address

    print("\n--- 見つかったKiririセンサー ---")
    for i, (address, name) in enumerate(devices_list):
        print(f"  [{i + 1}] {name} ({address})")
    print("----------------------------")

    while True:
        try:
            choice = input(f"接続するセンサーの番号を入力してください (1-{len(devices_list)}): ")
            choice_index = int(choice) - 1
            if 0 <= choice_index < len(devices_list):
                return devices_list[choice_index][0]
        except (ValueError, IndexError):
            print("正しい番号を半角数字で入力してください。")
        except (EOFError, KeyboardInterrupt):
            logging.warning("センサー
