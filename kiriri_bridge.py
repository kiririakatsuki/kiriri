# -*- coding: utf-8 -*-
# Kiririセンサーブリッジプログラム (websockets v10+対応版)
import asyncio
import websockets
import json
from bleak import BleakScanner, BleakClient
import logging
import sys
from typing import Set, Dict, Optional, Any, List, Tuple

# --- 設定 ---
TARGET_DEVICE_NAMES: List[str] = ["KIRIRI01", "KIRI"]
DATA_UUID: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
WEBSOCKET_HOST: str = "localhost"
WEBSOCKET_PORT: int = 8765
RECONNECT_DELAY: float = 5.0

# --- ログ設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] (%(funcName)s) %(message)s')

# --- グローバル変数 ---
latest_angles: Dict[str, Optional[Any]] = {"id": None, "y": 0.0, "x": 0.0}
# ★★★ 修正点1 ★★★
connected_clients: Set[websockets.WebSocketServerProtocol] = set()
new_data_available = asyncio.Event()

async def scan_and_select_sensor() -> Optional[str]:
    logging.info(f"'{', '.join(TARGET_DEVICE_NAMES)}' センサーを探しています...")
    found_devices = {
        dev.address: dev.name
        for dev in await BleakScanner.discover(timeout=10.0)
        if dev.name and any(target in dev.name for target in TARGET_DEVICE_NAMES)
    }

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
            if 0 <= int(choice) - 1 < len(devices_list):
                return devices_list[int(choice) - 1][0]
        except (ValueError, IndexError):
            print("正しい番号を入力してください。")
        except (EOFError, KeyboardInterrupt):
            return None

def handle_data(sender: int, data: bytearray):
    global latest_angles, new_data_available
    try:
        text_data = data.decode("utf-8")
        if text_data.startswith("N:"):
            parts = text_data[2:].split(':')
            if len(parts) == 2:
                latest_angles["y"] = int(parts[0]) / 100.0
                latest_angles["x"] = int(parts[1]) / 100.0
                new_data_available.set()
    except Exception as e:
        logging.warning(f"データ処理エラー: {e}")

async def send_data_to_clients():
    while True:
        await new_data_available.wait()
        new_data_available.clear()
        if connected_clients:
            json_data = json.dumps(latest_angles)
            # 接続が切れたクライアントを安全に処理
            tasks = [client.send(json_data) for client in connected_clients]
            await asyncio.gather(*tasks, return_exceptions=True)

# ★★★ 修正点2 ★★★
async def websocket_handler(websocket: websockets.WebSocketServerProtocol):
    logging.info(f"Webページ接続: {websocket.remote_address}")
    connected_clients.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        logging.info(f"Webページ切断: {websocket.remote_address}")
        connected_clients.remove(websocket)

async def ble_connect_and_notify(sensor_address: str):
    global latest_angles
    latest_angles["id"] = sensor_address
    while True:
        try:
            async with BleakClient(sensor_address) as client:
                logging.info(f"センサー ({client.address}) に接続成功！")
                await client.start_notify(DATA_UUID, handle_data)
                logging.info("データ受信待機中...")
                await client.disconnected_future
        except Exception as e:
            logging.error(f"BLEエラー: {e}")
        logging.warning(f"接続が切れました。{RECONNECT_DELAY}秒後に再接続します...")
        await asyncio.sleep(RECONNECT_DELAY)

async def main():
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    selected_address = await scan_and_select_sensor()
    if not selected_address:
        return

    await asyncio.gather(
        websockets.serve(websocket_handler, WEBSOCKET_HOST, WEBSOCKET_PORT),
        ble_connect_and_notify(selected_address),
        send_data_to_clients()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("プログラムが中断されました。")
