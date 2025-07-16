# -*- coding: utf-8 -*-
# Kiririセンサーブリッジプログラム (最終安定版)
# - KIRIRI01/02の開始コマンド送信に対応
# - 割り込み処理(handle_data)の堅牢性を最大限に高めたバージョン

import asyncio
import websockets
import json
from bleak import BleakScanner, BleakClient, BleakError
import logging
import sys
from typing import Set, Dict, Optional, Any, List

# --- 基本設定 ---
TARGET_DEVICE_NAMES: List[str] = ["KIRIRI02", "KIRIRI01", "KIRI"] # 02を優先的に探す
DATA_UUID: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"      # データ受信(Notify)用
RX_UUID: str = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"      # コマンド送信(Write)用
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

# データ解析で使用するバイト定数
START_MARKER = b'N:'
SEPARATOR = b':'
END_MARKER = b'\r'

def handle_data(sender: int, data: bytearray):
    """
    受信したBLEデータを解析する関数。
    データ形式が不正な場合は、例外を発生させずに安全に処理を抜ける。
    """
    global latest_angles, new_data_available

    try:
        start_index = data.find(START_MARKER)
        if start_index == -1: return

        separator_index = data.find(SEPARATOR, start_index + len(START_MARKER))
        if separator_index == -1: return

        end_index = data.find(END_MARKER, separator_index + 1)
        if end_index == -1: return
        
        y_bytes = data[start_index + len(START_MARKER) : separator_index]
        x_bytes = data[separator_index + 1 : end_index]
        
        y_val = int(y_bytes)
        x_val = int(x_bytes)

        latest_angles["y"] = y_val / 100.0
        latest_angles["x"] = x_val / 100.0
        new_data_available.set()

    except ValueError:
        return
    except Exception as e:
        logging.error(f"handle_dataで予期せぬ重大なエラー: {e}")
        return

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

                    # KIRIRI01/02の場合、開始コマンドを送信する
                    device_name = client.name if hasattr(client, 'name') else ""
                    if "KIRIRI01" in device_name or "KIRIRI02" in device_name:
                        logging.info(f"{device_name} のため、開始コマンドを送信します。")
                        try:
                            start_command = b'START\n'
                            await client.write_gatt_char(RX_UUID, start_command)
                            logging.info("開始コマンド送信成功。")
                            await asyncio.sleep(0.5)
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
