# -*- coding: utf-8 -*-
# Kiririセンサーブリッジプログラム (更新版)
# 割り込み処理(handle_data)の堅牢性を最大限に高めたバージョン

import asyncio
import websockets
import json
from bleak import BleakScanner, BleakClient, BleakError
import logging
import sys
from typing import Set, Dict, Optional, Any, List

# --- 基本設定 ---
TARGET_DEVICE_NAMES: List[str] = ["KIRIRI01", "KIRI"]
DATA_UUID: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
WEBSOCKET_HOST: str = "localhost"
WEBSOCKET_PORT: int = 8765
RECONNECT_DELAY: float = 5.0
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
                return selected_address
        except (ValueError, IndexError):
            print("正しい番号を半角数字で入力してください。")
        except (EOFError, KeyboardInterrupt):
            logging.warning("センサー選択がキャンセルされました。")
            return None

# ▼▼▼ ここから下がご指摘のあった、堅牢なデータ処理部分です ▼▼▼

# データ解析で使用するバイト定数
START_MARKER = b'N:'
SEPARATOR = b':'
END_MARKER = b'\r'

def handle_data(sender: int, data: bytearray):
    """
    BLEデータ受信時のコールバック関数（割り込みスレッドでの動作を想定）
    例外を極力発生させず、不正なデータは単に無視する堅牢な実装。
    """
    global latest_angles, new_data_available

    try:
        # 1. 開始マーカー 'N:' の位置を探す (データずれに対応)
        start_index = data.find(START_MARKER)
        if start_index == -1:
            return  # マーカーが見つからなければ、処理せず抜ける

        # 2. Y軸とX軸を区切る2番目の ':' の位置を探す
        separator_index = data.find(SEPARATOR, start_index + len(START_MARKER))
        if separator_index == -1:
            return  # 2番目の区切り文字がなければ不正な形式。処理せず抜ける

        # 3. 終端マーカー '\r' の位置を探す
        end_index = data.find(END_MARKER, separator_index + 1)
        if end_index == -1:
            return  # 終端マーカーがなければ不正な形式。処理せず抜ける

        # 4. バイト配列からY軸とX軸のデータをスライスで切り出す
        y_bytes = data[start_index + len(START_MARKER) : separator_index]
        x_bytes = data[separator_index + 1 : end_index]
        
        # 5. バイト配列を直接整数に変換。
        y_val = int(y_bytes)
        x_val = int(x_bytes)

        # 6. 正常に変換できた場合のみ、グローバル変数を更新
        latest_angles["y"] = y_val / 100.0
        latest_angles["x"] = x_val / 100.0
        new_data_available.set()

    except ValueError:
        # int()変換に失敗した場合。不正なデータとして静かに処理を終了。
        return
    except Exception as e:
        # 想定外の重大なエラーが発生した場合のみログに残す
        logging.error(f"handle_dataで予期せぬ重大なエラー: {e} - データ: {data}")
        return

# ▲▲▲ ここまでが堅牢なデータ処理部分です ▲▲▲

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
                    await client.start_notify(DATA_UUID, handle_data)
                    logging.info("データ受信待機中...")
                    await client.disconnected_future
        except Exception as e:
            logging.error(f"BLE処理エラー: {e}")
        
        logging.warning(f"接続が切れました。{RECONNECT_DELAY}秒後に再接続します...")
        await asyncio.sleep(RECONNECT_DELAY)

async def main():
    """プログラム全体のエントリーポイント"""
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    selected_address = await scan_and_select_sensor()
    if not selected_address:
        logging.critical("センサーが選択されなかったのでプログラムを終了します。")
        return

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
