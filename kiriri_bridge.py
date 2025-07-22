import asyncio
from bleak import BleakScanner, BleakClient

# --- 設定 ---
# 探すデバイスの名前（KIRIRI01, 02, 03のいずれかを含む）
TARGET_DEVICE_NAMES = ["KIRIRI01", "KIRIRI02", "KIRIRI03"]
# データを受信するキャラクタリスティックのUUID
NOTIFY_CHARACTERISTIC_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
# 開始コマンドを送信するキャラクタリスティックのUUID
WRITE_CHARACTERISTIC_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
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
        # この 'async with' が接続とサービスディスカバリーを自動的に行う
        async with BleakClient(device) as client:
            if client.is_connected:
                print("--- 接続に成功しました！ ---")

                # 開始コマンドを送信
                try:
                    print("--- 開始コマンドを送信します ---")
                    await client.write_gatt_char(WRITE_CHARACTERISTIC_UUID, b'START\n')
                    print("--- コマンド送信成功 ---")
                except Exception as e:
                    print(f"エラー: コマンドの送信に失敗しました: {e}")

                # データの受信を開始
                print("--- データの受信待機中... (Ctrl+Cで終了) ---")
                await client.start_notify(NOTIFY_CHARACTERISTIC_UUID, handle_notification)
                
                # プログラムが終了しないように待機
                await client.disconnected_future
                
    except Exception as e:
        print(f"\nエラー: 接続中に問題が発生しました。")
        print(f"詳細: {e}")
    
    print("--- プログラムを終了します ---")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n--- ユーザーによってプログラムが中断されました ---")
