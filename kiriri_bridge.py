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
            logging.warning("センサー選択がキャンセルされました。")
            return None

START_MARKER = b'N:'
SEPARATOR = ord(':')
END_MARKER = ord('\r')

def handle_data(sender: int, data: bytearray):
    """受信したBLEデータを解析する関数"""
    global latest_angles, new_data_available
    try:
        start_pos = data.index(START_MARKER)
        separator_pos = data.index(SEPARATOR, start_pos + 2)
        end_pos = data.index(END_MARKER, separator_pos + 1)
        y_bytes = data[start_pos + 2 : separator_pos]
        x_bytes = data[separator_pos + 1 : end_pos]
        y_val = int(y_bytes)
        x_val = int(x_bytes)
        latest_angles["y"] = y_val / 100.0
        latest_angles["x"] = x_val / 100.0
        new_data_available.set()
    except (ValueError, IndexError):
        return
    except Exception as e:
        logging.error(f"ハンドルデータで予期せぬエラー: {e}, データ: {data.hex()}")

async def send_data_to_clients():
    """最新の角度データを接続中の全WebSocketクライアントに送信し続ける"""
    while True:
        await new_data_available.wait()
        new_data_available.clear()
        if connected_clients:
            json_data = json.dumps(latest_angles)
            tasks = [client.send(json_data) for client in connected_clients]
            await asyncio.gather(*tasks, return_exceptions=True)

async def websocket_handler(websocket: websockets.WebSocketServerProtocol):
    """新しいWebSocketクライアント接続を管理する"""
    logging.info(f"Webページ接続: {websocket.remote_address}")
    connected_clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        logging.info(f"Webページ切断: {websocket.remote_address}")
        connected_clients.remove(websocket)

async def ble_connect_and_notify(sensor_address: str):
    """指定されたBLEデバイスに接続し、データ通知を開始・維持する"""
    global latest_angles
    latest_angles["id"] = sensor_address
    while True:
        try:
            async with BleakClient(sensor_address) as client:
                if client.is_connected:
                    logging.info(f"センサー ({client.address}) に接続成功！")
                    logging.info("コネクションを出しました。")
                    device_name = client.name if hasattr(client, 'name') else ""
                    if "KIRIRI01" in device_name or "KIRIRI02" in device_name:
                        logging.info(f"{device_name} のため、開始コマンドを送信します。")
                        try:
                            await client.write_gatt_char(RX_UUID, b'START\n')
                            logging.info("開始コマンド送信成功。")
                        except Exception as e:
                            logging.error(f"開始コマンドの送信に失敗しました: {e}")
                    await client.start_notify(DATA_UUID, handle_data)
                    logging.info("データ受信待機中...")
                    await client.disconnected_future
        except Exception as e:
            logging.error(f"BLE処理エラー: {e}")
        logging.warning(f"接続が切れました。{RECONNECT_DELAY}秒後に再接続します...")
        await asyncio.sleep(RECONNECT_DELAY)

async def main():
    """プログラム全体のエントリーポイント"""
    setup_logging()
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    selected_address = None
    # 1. まずMACアドレスで直接接続を試みる
    if TARGET_MAC_ADDRESS:
        logging.info(f"MACアドレス {TARGET_MAC_ADDRESS} に直接接続を試みます...")
        try:
            async with asyncio.wait_for(BleakClient(TARGET_MAC_ADDRESS), timeout=5.0) as client:
                if client.is_connected:
                    selected_address = TARGET_MAC_ADDRESS
                    logging.info("直接接続に成功しました。")
        except Exception:
            logging.warning("直接接続に失敗しました。スキャンモードに切り替えます。")

    # 2. 直接接続に失敗した場合、スキャンを行う
    if not selected_address:
        selected_address = await scan_and_select_sensor()

    if not selected_address:
        logging.critical("センサーが選択されなかったのでプログラムを終了します。")
        return

    # WebSocketサーバーを起動
    try:
        server = await websockets.serve(websocket_handler, WEBSOCKET_HOST, WEBSOCKET_PORT)
        logging.info(f"WebSocketサーバーを ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT} で起動しました。")
    except OSError as e:
        logging.critical(f"WebSocketサーバーの起動に失敗しました: {e}")
        return
        
    await asyncio.gather(
        ble_connect_and_notify(selected_address),
        send_data_to_clients()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("プログラムがユーザーによって中断されました (Ctrl+C)。")
