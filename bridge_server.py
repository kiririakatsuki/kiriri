# -*- coding: utf-8 -*-
# Kiririセンサーブリッジプログラム
# BLEでKiririセンサーに接続し、角度データをWebSocketでWebページに送信します。

import asyncio
import websockets
import json
from bleak import BleakScanner, BleakClient, BleakError
import logging
import sys
from typing import Set, Dict, Optional, Any, List, Tuple

# --- 基本設定 (★★★ 必ず確認・必要なら変更してください ★★★) ---

# 1. KiririセンサーのBluetoothデバイス名リスト
#    ★★★ KIRIとKIRIRI01の両方を探すようにリスト形式に変更 ★★★
TARGET_DEVICE_NAMES: List[str] = ["KIRIRI01", "KIRI"]

# 2. Kiririセンサーが角度データを送ってくるキャラクタリスティックのUUID (128ビット形式)
#    (以前の調査で、このUUIDが正しいことは確認済みです)
DATA_UUID: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# 3. WebSocketサーバーの設定
WEBSOCKET_HOST: str = "localhost" # このPC内からの接続のみ許可 (安全な初期設定)
WEBSOCKET_PORT: int = 8765      # Webページが接続しにくるポート番号

# 4. BLEスキャン時間 (秒)
SCAN_TIMEOUT: float = 10.0

# 5. BLE接続試行タイムアウト (秒)
CONNECT_TIMEOUT: float = 20.0

# 6. 再接続試行までの待機時間 (秒)
RECONNECT_DELAY: float = 5.0
# ---------------------------------------------------------------

# --- ログ設定 ---
LOG_LEVEL = logging.INFO
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s [%(levelname)s] (%(funcName)s) %(message)s')

# --- グローバル変数 ---
latest_angles: Dict[str, Optional[Any]] = {"id": None, "y": 0.0, "x": 0.0}
connected_clients: Set['websockets.server.WebSocketServerProtocol'] = set()
new_data_available = asyncio.Event()

# --- 関数定義 ---

async def scan_and_select_sensor() -> Optional[str]:
    """
    近くにあるTARGET_DEVICE_NAMESのいずれかを含むBLEデバイスをスキャンし、
    ユーザーにコンソールで選択させ、選択されたデバイスのBLEアドレスを返す。
    """
    search_names = ', '.join(TARGET_DEVICE_NAMES)
    logging.info(f"'{search_names}' センサーを探しています (最大{SCAN_TIMEOUT}秒)...")
    found_devices_dict: Dict[str, str] = {} # {アドレス: 名前}

    try:
        devices = await BleakScanner.discover(timeout=SCAN_TIMEOUT)
        for device in devices:
            rssi_val = 'N/A'
            if hasattr(device, 'advertisement_data'):
                 rssi_val = device.advertisement_data.rssi
            
            # ★★★ デバイス名がリスト内のいずれかの名前を含むかチェック ★★★
            if device.name and any(target in device.name for target in TARGET_DEVICE_NAMES):
                if device.address not in found_devices_dict:
                    logging.info(f"  発見: {device.name} ({device.address}) RSSI={rssi_val}")
                    found_devices_dict[device.address] = device.name

    except BleakError as e:
        logging.error(f"BLEスキャン中にエラーが発生しました: {e}")
        return None
    except Exception as e:
        logging.error(f"スキャン中に予期せぬエラー: {e}")
        return None

    kiriri_devices: List[Tuple[str, str]] = list(found_devices_dict.items())

    if not kiriri_devices:
        logging.error(f"'{search_names}' が見つかりませんでした。センサー電源、PCのBluetooth、距離を確認してください。")
        return None

    print("\n--- 見つかったKiririセンサー ---")
    for i, (address, name) in enumerate(kiriri_devices):
        print(f"  [{i + 1}] {name} ({address})")
    print("----------------------------")

    while True:
        try:
            choice = input(f"接続するセンサーの番号を入力してください (1-{len(kiriri_devices)}): ")
            choice_index = int(choice) - 1
            if 0 <= choice_index < len(kiriri_devices):
                selected_address = kiriri_devices[choice_index][0]
                logging.info(f"センサー {selected_address} が選択されました。")
                return selected_address
            else:
                print("リストにない番号です。もう一度入力してください。")
        except ValueError:
            print("番号を半角数字で入力してください。")
        except (EOFError, KeyboardInterrupt):
            logging.warning("センサー選択がキャンセルされました。")
            return None

def handle_data(sender: int, data: bytearray):
    """ BLEデータ受信時のコールバック関数。データを解析し、グローバル変数を更新する """
    global latest_angles, new_data_available
    try:
        text_data = data.decode("utf-8")
        
        if text_data.startswith("N:"):
            parts_str = text_data[2:]
            separator = ':'
            parts = parts_str.split(separator)

            if len(parts) == 2:
                y_raw = int(parts[0].strip())
                x_raw = int(parts[1].strip())
                latest_angles["y"] = y_raw / 100.0
                latest_angles["x"] = x_raw / 100.0
                new_data_available.set()
            else:
                logging.warning(f"データ形式エラー: 区切り文字'{separator}'での分割失敗 - {text_data}")

    except (UnicodeDecodeError, ValueError) as e:
        logging.warning(f"データ形式エラー: {e} - {data}")
    except Exception as e:
        logging.error(f"handle_data関数内で予期せぬエラー: {e}", exc_info=True)

async def send_data_to_clients():
    """ 最新の角度データを接続中の全WebSocketクライアントに送信し続ける """
    while True:
        try:
            await new_data_available.wait()
            new_data_available.clear()

            if not connected_clients: continue

            json_data = json.dumps(latest_angles)
            
            disconnected_clients = set()
            for client in connected_clients:
                try:
                    await client.send(json_data)
                except websockets.exceptions.ConnectionClosed:
                    disconnected_clients.add(client)
            
            if disconnected_clients:
                connected_clients.difference_update(disconnected_clients)

        except Exception as e:
            logging.error(f"send_data_to_clientsループで予期せぬエラー: {e}", exc_info=True)
            await asyncio.sleep(1)

async def websocket_handler(websocket: 'websockets.server.WebSocketServerProtocol'):
    """ 新しいWebSocketクライアント接続を処理する """
    client_addr = websocket.remote_address
    logging.info(f"Webページ接続: {client_addr}")
    connected_clients.add(websocket)
    try:
        async for _ in websocket:
            pass
    except websockets.exceptions.ConnectionClosedError as e:
        logging.warning(f"Webページがエラーで切断: {client_addr} - {e}")
    finally:
        logging.info(f"Webページ切断処理: {client_addr}")
        connected_clients.discard(websocket)

async def ble_connect_and_notify(sensor_address: str):
    """ 指定されたBLEデバイスに接続し、データ通知を開始・維持する (自動再接続付き) """
    global latest_angles
    latest_angles["id"] = sensor_address
    
    while True:
        try:
            logging.info(f"センサー ({sensor_address}) に接続試行中...")
            async with BleakClient(sensor_address, timeout=CONNECT_TIMEOUT) as client:
                logging.info(f"センサー ({client.address}) に接続成功！")
                logging.info(f"データ通知 (Notify) を開始します (UUID: {DATA_UUID})...")
                await client.start_notify(DATA_UUID, handle_data)
                logging.info("データ受信待機中... (Ctrl+Cでプログラム終了)")
                while client.is_connected:
                    await asyncio.sleep(1.0)
                logging.warning("BLE接続維持ループが終了しました。")
        except Exception as e:
            logging.error(f"BLE処理中にエラーが発生: {e}")
        
        logging.warning(f"BLE接続が切断されました。{RECONNECT_DELAY}秒後に再接続を試みます...")
        await asyncio.sleep(RECONNECT_DELAY)

async def main():
    """ プログラム全体の非同期処理を管理する """
    if sys.platform == "win32" and sys.version_info >= (3, 8):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    selected_address = await scan_and_select_sensor()
    if not selected_address:
        logging.critical("センサーが選択されませんでした。プログラムを終了します。")
        return

    server_task = websockets.serve(websocket_handler, WEBSOCKET_HOST, WEBSOCKET_PORT)
    
    await asyncio.gather(
        server_task,
        ble_connect_and_notify(selected_address),
        send_data_to_clients(),
    )

if __name__ == "__main__":
    try:
        logging.info("センサーブリッジプログラムを開始します。")
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("プログラムがユーザーによって中断されました (Ctrl+C)。")