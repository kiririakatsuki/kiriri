import asyncio
from bleak import BleakScanner, BleakClient

# --- 設定 ---
# 探すデバイスの名前（KIRIRI01, 02, 03のいずれかを含む）
TARGET_DEVICE_NAMES = ["KIRIRI01", "KIRIRI02", "KIRIRI03"]

# データを受信するキャラクタリスティックのUUID
NOTIFY_CHARACTERISTIC_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

# 開始コマンドを送信するキャラクタリスティックのUUID
WRITE_CHARACTERISTIC_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

# 接続の安定性のための待機時間（秒）
CONNECTION_WAIT_TIME = 1.0
# ----------------

def handle_notification(sender, data: bytearray):
    """
    センサーからデータが送られてきたときに呼び出される関数。
    データを文字列に変換して画面に表示するだけ。
    """
    print(f"受信データ: {data.decode(errors='ignore')}")

async def main():
    """
    スキャン、接続、データ受信を行うメインの処理。
    """
    print("--- センサーのスキャンを開始します ---")
    
    # 名前に基づいてデバイスを1台探す
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: d.name and any(name in d.name for name in TARGET_DEVICE_NAMES)
    )
    
    if device is None:
        print(f"エラー: 対象のセンサーが見つかりませんでした。")
        return
    
    print(f"--- センサーが見つかりました: {device.name} ({device.address}) ---")
    
    try:
        # 見つけたデバイスに接続する
        async with BleakClient(device) as client:
            if client.is_connected:
                print("--- 接続に成功しました！ ---")
                
                # 接続後、少し待機してBLEスタックを安定させる
                print(f"--- 接続を安定させるため {CONNECTION_WAIT_TIME} 秒待機中... ---")
                await asyncio.sleep(CONNECTION_WAIT_TIME)
                
                # 明示的にサービスディスカバリーを実行
                print("--- サービスディスカバリーを実行中... ---")
                try:
                    services = await client.get_services()
                    print(f"--- {len(services)} 個のサービスを発見しました ---")
                    
                    # デバッグ用：発見したサービスとキャラクタリスティックを表示
                    print("\n--- 発見したサービスの詳細 ---")
                    for service in services:
                        print(f"サービス UUID: {service.uuid}")
                        for char in service.characteristics:
                            print(f"  └ キャラクタリスティック UUID: {char.uuid}")
                            print(f"     プロパティ: {char.properties}")
                    print("--- サービス詳細の表示終了 ---\n")
                    
                except Exception as e:
                    print(f"警告: サービスディスカバリー中にエラーが発生しました: {e}")
                    print("続行を試みます...")
                
                # 必要なキャラクタリスティックが存在するか確認
                try:
                    # 書き込み用キャラクタリスティックの確認
                    write_char = None
                    for service in client.services:
                        for char in service.characteristics:
                            if char.uuid.lower() == WRITE_CHARACTERISTIC_UUID.lower():
                                write_char = char
                                break
                        if write_char:
                            break
                    
                    if write_char is None:
                        print(f"エラー: 書き込み用キャラクタリスティック {WRITE_CHARACTERISTIC_UUID} が見つかりません")
                        return
                    
                    # 通知用キャラクタリスティックの確認
                    notify_char = None
                    for service in client.services:
                        for char in service.characteristics:
                            if char.uuid.lower() == NOTIFY_CHARACTERISTIC_UUID.lower():
                                notify_char = char
                                break
                        if notify_char:
                            break
                    
                    if notify_char is None:
                        print(f"エラー: 通知用キャラクタリスティック {NOTIFY_CHARACTERISTIC_UUID} が見つかりません")
                        return
                    
                    print("--- 必要なキャラクタリスティックを確認しました ---")
                    
                except Exception as e:
                    print(f"エラー: キャラクタリスティックの確認中に問題が発生しました: {e}")
                    return
                
                # 開始コマンドを送信
                try:
                    print("--- 開始コマンドを送信します ---")
                    await client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, b'START\n')
                    print("--- コマンド送信成功 ---")
                except Exception as e:
                    print(f"エラー: コマンドの送信に失敗しました: {e}")
                    return
                
                # データの受信を開始
                try:
                    print("--- データの受信待機中... (Ctrl+Cで終了) ---")
                    await client.start_notify(NOTIFY_CHARACTERISTIC_UUID, handle_notification)
                    
                    # プログラムが終了しないように待機
                    await client.disconnected_future
                    
                except Exception as e:
                    print(f"エラー: 通知の開始に失敗しました: {e}")
                    return
                
            else:
                print("エラー: デバイスへの接続に失敗しました")
                
    except Exception as e:
        print(f"\nエラー: 接続中に問題が発生しました。")
        print(f"詳細: {e}")
    
    print("--- プログラムを終了します ---")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n--- ユーザーによってプログラムが中断されました ---")
