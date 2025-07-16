# -*- coding: utf-8 -*-
# Kiririセンサーブリッジプログラム (推奨版)
# websockets v10+ と bleak のモダンな書き方に対応し、データ解析の堅牢性を確保

import asyncio
import websockets
import json
from bleak import BleakScanner, BleakClient, BleakError
import logging
import sys
from typing import Set, Dict, Optional, Any, List, Tuple

# --- 基本設定 ---
# 接続したいセンサーのBluetoothデバイス名
TARGET_DEVICE_NAMES: List[str] = ["KIRIRI01", "KIRI"]

# 角度データが送られてくるキャラクタリスティックのUUID
DATA_UUID: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# WebSocketサーバーの設定
# PC内のみ接続許可する場合は "localhost"
# 同じネットワークのスマホ等から接続する場合は "0.0.0.0" に変更
WEBSOCKET_HOST: str = "localhost"
WEBSOCKET_PORT: int = 8765

# センサーとの接続が切れた際の再接続試行までの待機時間 (秒)
RECONNECT_DELAY: float = 5.0

# BLEスキャン時間 (秒)
SCAN_TIMEOUT: float = 10.0
# ----------------

# --- ログ設定 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] (%(funcName)s) %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# --- グローバル変数 ---
latest_angles: Dict[str, Optional[Any]] = {"id": None, "y": 0.0, "x": 0.0}
connected_clients: Set[websockets.WebSocketServerProtocol] = set()
new_data_available = asyncio.Event()

async def scan_and_select_sensor() -> Optional[str]:
    """近くにある対象のBLEデバイスをスキャンし、ユーザーに選択させる"""
    logging.info(f"'{', '.join(TARGET_DEVICE_NAMES)}' センサーを探しています (最大{SCAN_TIMEOUT}秒)...")
    
    try:
        # discoverで見つかったデバイスの中から対象のものを辞書に格納
        found_devices = {
            dev.address: dev.name
            for dev in await BleakScanner.discover(timeout=SCAN_TIMEOUT)
            if dev.name and any(target in dev.name for target in TARGET_DEVICE_NAMES)
        }
    except BleakError as e:
        logging.error(f"BLEスキャン中にエラーが発生しました: {e}")
        return None

    if not found_devices:
        logging.error("対象のセンサーが見つかりませんでした。センサーの電源やPCのBluetooth設定、距離を確認してください。")
        return None

    devices_list = list(found_devices.items())
    print("\n--- 見つかったKiririセンサー ---")
    for i, (address, name) in enumerate(devices_list):
        print(f"  [{i + 1}] {name} ({address})")
    print("----------------------------")

    while True:
        try:
            choice = input(f"接続するセンサーの番号を入力してください (1-{len(devices_list)}): ")
            choice_index = int(choice) - 1
            if 0 <= choice_index < len(devices_list):
                selected_address = devices_list[choice_index][0]
                logging.info(f"センサー {selected_address} が選択されました。")
                return selected_address
            else:
                print("リストにない番号です。")
        except (ValueError, IndexError):
            print("正しい番号を半角数字で入力してください。")
        except (EOFError, KeyboardInterrupt):
            logging.warning("センサー選択がキャンセルされました。")
            return None

def handle_data(sender: int, data: bytearray):
    """BLEデータ受信時のコールバック関数（データ解析部）"""
    global latest_angles, new_data_available
    try:
        text_data = data.decode("utf-8")
        if text_data.startswith("N:"):
            parts = text_data[2:].split(':')
            if len(parts) == 2:
                # .strip()で数値前後の空白や改行コードを安全に削除してから変換
                latest_angles["y"] = int(parts[0].strip()) / 100.0
                latest_angles["x"] = int(parts[1].strip()) / 100.0
                new_data_available.set() # 新しいデータがあることを他の処理に通知
    except Exception as e:
        logging.warning(f"データ処理エラー: {e} - 受信データ: {data}")

async def send_data_to_clients():
    """最新の角度データを接続中の全WebSocketクライアントに送信し続ける"""
    while True:
        await new_data_available.wait()
        new_data_available.clear()
        
        if connected_clients:
            json_data = json.dumps(latest_angles)
            # 接続が切れたクライアントがいてもエラーで止まらないように並列送信
            tasks = [client.send(json_data) for client in connected_clients]
            await asyncio.gather(*tasks, return_exceptions=True)

async def websocket_handler(websocket: websockets.WebSocketServerProtocol):
    """新しいWebSocketクライアント接続を管理する"""
    logging.info(f"Webページ接続: {websocket.remote_address}")
    connected_clients.add(websocket)
    try:
        # 接続が閉じるのを待つ (websockets v10+ のモダンな書き方)
        await websocket.wait_closed()
    finally:
        logging.info(f"Webページ切断: {websocket.remote_address}")
        connected_clients.remove(websocket)

async def ble_connect_and_notify(sensor_address: str):
    """指定されたBLEデバイスに接続し、データ通知を開始・維持する（自動再接続付き）"""
    global latest_angles
    latest_angles["id"] = sensor_address
    
    while True:
        try:
            async with BleakClient(sensor_address) as client:
                if client.is_connected:
                    logging.info(f"センサー ({client.address}) に接続成功！")
                    await client.start_notify(DATA_UUID, handle_data)
                    logging.info("データ受信待機中...")
                    # 接続が切れるのを待つ (bleakのモダンな書き方)
                    await client.disconnected_future
        except Exception as e:
            logging.error(f"BLE処理エラー: {e}")
        
        logging.warning(f"接続が切れました。{RECONNECT_DELAY}秒後に再接続します...")
        await asyncio.sleep(RECONNECT_DELAY)

async def main():
    """プログラム全体のエントリーポイント"""
    # Windowsで発生することがあるasyncioのエラーを回避
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    selected_address = await scan_and_select_sensor()
    if not selected_address:
        logging.critical("センサーが選択されなかったのでプログラムを終了します。")
        return

    # WebSocketサーバー、BLE接続、データ送信処理を並行して実行
    server_task = websockets.serve(websocket_handler, WEBSOCKET_HOST, WEBSOCKET_PORT)
    logging.info(f"WebSocketサーバーを ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT} で起動しました。")
    
    await asyncio.gather(
        server_task,
        ble_connect_and_notify(selected_address),
        send_data_to_clients()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("プログラムがユーザーによって中断されました (Ctrl+C)。")
