import asyncio
from bleak import BleakScanner, BleakClient
import time
from datetime import datetime

# --- 設定 ---
# 探すデバイスの名前（KIRIRI01, 02, 03のいずれかを含む）
TARGET_DEVICE_NAMES = ["KIRIRI01", "KIRIRI02", "KIRIRI03"]

# データを受信するキャラクタリスティックのUUID
NOTIFY_CHARACTERISTIC_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# 開始コマンドを送信するキャラクタリスティックのUUID
WRITE_CHARACTERISTIC_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

# 接続の安定性のための待機時間（秒）
CONNECTION_WAIT_TIME = 2.0

# 再接続の設定
MAX_RECONNECT_ATTEMPTS = 5  # 最大再接続試行回数
RECONNECT_DELAY = 3.0  # 再接続までの待機時間（秒）

# キープアライブの設定
KEEPALIVE_INTERVAL = 10.0  # キープアライブの間隔（秒）
KEEPALIVE_COMMAND = b'PING\n'  # キープアライブコマンド

# データ受信タイムアウト
DATA_TIMEOUT = 30.0  # データが来ない場合のタイムアウト（秒）
# ----------------

# グローバル変数
last_data_time = None
is_receiving = False
client_global = None

def handle_notification(sender, data: bytearray):
    """
    センサーからデータが送られてきたときに呼び出される関数。
    """
    global last_data_time, is_receiving
    
    last_data_time = time.time()
    is_receiving = True
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    decoded_data = data.decode(errors='ignore').strip()
    
    print(f"[{timestamp}] 受信データ: {decoded_data}")

async def send_keepalive(client):
    """
    定期的にキープアライブコマンドを送信する
    """
    global last_data_time
    
    while client.is_connected:
        try:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            
            if client.is_connected:
                # データが長時間来ていない場合は警告
                if last_data_time and (time.time() - last_data_time > DATA_TIMEOUT):
                    print(f"警告: {DATA_TIMEOUT}秒以上データが受信されていません")
                
                # キープアライブコマンドを送信
                print(f"[{datetime.now().strftime('%H:%M:%S')}] キープアライブ送信")
                await client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, KEEPALIVE_COMMAND)
                
        except Exception as e:
            print(f"キープアライブエラー: {e}")
            break

async def monitor_connection(client):
    """
    接続状態を監視する
    """
    global is_receiving
    
    while True:
        try:
            await asyncio.sleep(5.0)  # 5秒ごとにチェック
            
            if not client.is_connected:
                print("警告: 接続が切断されました")
                break
                
            # 接続状態とデータ受信状態を表示
            status = "受信中" if is_receiving else "待機中"
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 接続状態: 正常 | データ: {status}")
            
            # データ受信フラグをリセット
            is_receiving = False
            
        except Exception as e:
            print(f"接続監視エラー: {e}")
            break

async def connect_and_receive(device, attempt=1):
    """
    デバイスに接続してデータを受信する
    """
    global last_data_time, client_global
    
    print(f"\n--- 接続試行 {attempt}/{MAX_RECONNECT_ATTEMPTS} ---")
    
    try:
        # BleakClientの接続パラメータを調整
        client = BleakClient(
            device, 
            timeout=20.0,  # 接続タイムアウトを長めに設定
            disconnected_callback=lambda client: print("切断コールバック: 接続が切断されました")
        )
        
        client_global = client
        
        await client.connect()
        
        if client.is_connected:
            print("--- 接続に成功しました！ ---")
            
            # 接続後、少し待機してBLEスタックを安定させる
            print(f"--- 接続を安定させるため {CONNECTION_WAIT_TIME} 秒待機中... ---")
            await asyncio.sleep(CONNECTION_WAIT_TIME)
            
            # MTUサイズの設定を試みる（対応している場合）
            try:
                mtu = await client.mtu_size
                print(f"--- 現在のMTUサイズ: {mtu} ---")
            except:
                print("--- MTUサイズの取得はサポートされていません ---")
            
            # 明示的にサービスディスカバリーを実行
            print("--- サービスディスカバリーを実行中... ---")
            services = await client.get_services()
            print(f"--- {len(services)} 個のサービスを発見しました ---")
            
            # 必要なキャラクタリスティックの確認
            write_char_found = False
            notify_char_found = False
            
            for service in services:
                for char in service.characteristics:
                    if char.uuid.lower() == WRITE_CHARACTERISTIC_UUID.lower():
                        write_char_found = True
                    if char.uuid.lower() == NOTIFY_CHARACTERISTIC_UUID.lower():
                        notify_char_found = True
            
            if not write_char_found or not notify_char_found:
                print("エラー: 必要なキャラクタリスティックが見つかりません")
                await client.disconnect()
                return False
            
            print("--- 必要なキャラクタリスティックを確認しました ---")
            
            # 通知を開始する前に少し待機
            await asyncio.sleep(0.5)
            
            # データの受信を開始
            print("--- 通知の受信を開始します ---")
            await client.start_notify(NOTIFY_CHARACTERISTIC_UUID, handle_notification)
            
            # 通知開始後も少し待機
            await asyncio.sleep(0.5)
            
            # 開始コマンドを送信
            print("--- 開始コマンドを送信します ---")
            await client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, b'START\n')
            print("--- コマンド送信成功 ---")
            
            # 初回データ受信時刻を記録
            last_data_time = time.time()
            
            print("\n--- データの受信待機中... (Ctrl+Cで終了) ---")
            print("--- 接続状態とデータ受信状態を監視しています ---\n")
            
            # キープアライブとモニタリングタスクを開始
            keepalive_task = asyncio.create_task(send_keepalive(client))
            monitor_task = asyncio.create_task(monitor_connection(client))
            
            # 接続が切断されるまで待機
            try:
                await client.disconnected_future
            except asyncio.CancelledError:
                pass
            
            # タスクをキャンセル
            keepalive_task.cancel()
            monitor_task.cancel()
            
            return False  # 切断された
            
        else:
            print("エラー: デバイスへの接続に失敗しました")
            return False
            
    except asyncio.TimeoutError:
        print("エラー: 接続タイムアウト")
        return False
    except Exception as e:
        print(f"エラー: 接続中に問題が発生しました: {e}")
        return False
    finally:
        # クリーンアップ
        try:
            if client and client.is_connected:
                await client.disconnect()
        except:
            pass

async def main():
    """
    メインの処理
    """
    print("=== BLEセンサー接続プログラム（安定化版） ===")
    print(f"対象デバイス: {', '.join(TARGET_DEVICE_NAMES)}")
    print(f"再接続試行回数: {MAX_RECONNECT_ATTEMPTS}")
    print(f"キープアライブ間隔: {KEEPALIVE_INTERVAL}秒")
    print(f"データタイムアウト: {DATA_TIMEOUT}秒")
    print("=" * 50)
    
    reconnect_count = 0
    
    while reconnect_count < MAX_RECONNECT_ATTEMPTS:
        print(f"\n--- センサーのスキャンを開始します ---")
        
        # デバイスをスキャン
        device = await BleakScanner.find_device_by_filter(
            lambda d, ad: d.name and any(name in d.name for name in TARGET_DEVICE_NAMES),
            timeout=10.0
        )
        
        if device is None:
            print("エラー: 対象のセンサーが見つかりませんでした。")
            print(f"{RECONNECT_DELAY}秒後に再スキャンします...")
            await asyncio.sleep(RECONNECT_DELAY)
            continue
        
        print(f"--- センサーが見つかりました: {device.name} ({device.address}) ---")
        
        # 接続を試行
        success = await connect_and_receive(device, reconnect_count + 1)
        
        if not success:
            reconnect_count += 1
            if reconnect_count < MAX_RECONNECT_ATTEMPTS:
                print(f"\n{RECONNECT_DELAY}秒後に再接続を試みます...")
                await asyncio.sleep(RECONNECT_DELAY)
            else:
                print("\n最大再接続回数に達しました。")
        
    print("\n--- プログラムを終了します ---")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n--- ユーザーによってプログラムが中断されました ---")
