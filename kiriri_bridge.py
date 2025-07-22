"""
BLE センサー接続サービス - プロダクション版
すべての既知の問題に対応した堅牢な実装
"""

import asyncio
import logging
import sys
import json
import os
from datetime import datetime
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass
from enum import Enum
import time
import platform

from bleak import BleakScanner, BleakClient, BleakError
from bleak.exc import BleakDeviceNotFoundError

# ================== 設定 ==================
@dataclass
class BLEConfig:
    """BLE接続設定"""
    # デバイス設定
    target_device_names: list = None
    notify_characteristic_uuid: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
    write_characteristic_uuid: str = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
    
    # 接続設定
    connection_timeout: float = 30.0
    scan_timeout: float = 10.0
    connection_wait_time: float = 2.0
    service_discovery_wait: float = 1.0
    
    # 再接続設定
    max_reconnect_attempts: int = -1  # -1 = 無限
    reconnect_delay: float = 5.0
    reconnect_backoff_factor: float = 1.5
    max_reconnect_delay: float = 60.0
    
    # キープアライブ設定
    keepalive_enabled: bool = True
    keepalive_interval: float = 15.0
    keepalive_command: bytes = b'PING\n'
    
    # データ監視設定
    data_timeout_enabled: bool = True
    data_timeout: float = 60.0
    
    # ログ設定
    log_level: str = "INFO"
    log_file: Optional[str] = "ble_sensor_service.log"
    
    def __post_init__(self):
        if self.target_device_names is None:
            self.target_device_names = ["KIRIRI01", "KIRIRI02", "KIRIRI03"]

class ConnectionState(Enum):
    """接続状態"""
    DISCONNECTED = "切断"
    SCANNING = "スキャン中"
    CONNECTING = "接続中"
    DISCOVERING = "サービス探索中"
    CONNECTED = "接続済み"
    RECONNECTING = "再接続中"
    ERROR = "エラー"

# ================== ロギング設定 ==================
class BLELogger:
    """専用ロガー"""
    def __init__(self, config: BLEConfig):
        self.logger = logging.getLogger("BLESensorService")
        self.logger.setLevel(getattr(logging, config.log_level))
        
        # フォーマッター
        formatter = logging.Formatter(
            '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # コンソールハンドラー
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # ファイルハンドラー
        if config.log_file:
            file_handler = logging.FileHandler(config.log_file, encoding='utf-8')
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
    
    def __getattr__(self, name):
        return getattr(self.logger, name)

# ================== メインサービスクラス ==================
class BLESensorService:
    """BLEセンサー接続サービス"""
    
    def __init__(self, config: BLEConfig, data_callback: Optional[Callable] = None):
        self.config = config
        self.logger = BLELogger(config)
        self.data_callback = data_callback
        
        # 状態管理
        self.state = ConnectionState.DISCONNECTED
        self.client: Optional[BleakClient] = None
        self.device = None
        self.connected_device_name = None
        
        # 統計情報
        self.stats = {
            "start_time": None,
            "total_connections": 0,
            "total_disconnections": 0,
            "total_data_received": 0,
            "last_data_time": None,
            "current_session_start": None
        }
        
        # フラグ
        self.should_stop = False
        self.is_receiving = False
        
        # タスク管理
        self.tasks = []
        
    def _set_state(self, state: ConnectionState):
        """状態を変更"""
        if self.state != state:
            self.logger.info(f"状態変更: {self.state.value} → {state.value}")
            self.state = state
    
    def _clear_cache_if_needed(self):
        """必要に応じてBLEキャッシュをクリア（Windows）"""
        if platform.system() == "Windows":
            try:
                # Windowsの場合、レジストリからキャッシュを削除することも検討
                # ただし、これは管理者権限が必要なため、ここでは実装しない
                self.logger.debug("Windows環境を検出")
            except Exception as e:
                self.logger.warning(f"キャッシュクリア確認中にエラー: {e}")
    
    def handle_notification(self, sender, data: bytearray):
        """データ受信ハンドラー"""
        try:
            self.stats["last_data_time"] = time.time()
            self.stats["total_data_received"] += 1
            self.is_receiving = True
            
            decoded_data = data.decode(errors='ignore').strip()
            
            self.logger.debug(f"受信データ: {decoded_data}")
            
            # コールバック実行
            if self.data_callback:
                try:
                    self.data_callback(decoded_data, sender)
                except Exception as e:
                    self.logger.error(f"データコールバックエラー: {e}")
                    
        except Exception as e:
            self.logger.error(f"データ処理エラー: {e}")
    
    async def scan_for_device(self) -> Optional[Any]:
        """デバイスをスキャン"""
        self._set_state(ConnectionState.SCANNING)
        self.logger.info(f"対象デバイスをスキャン中: {', '.join(self.config.target_device_names)}")
        
        try:
            # 複数回スキャンを試みる
            for attempt in range(3):
                if attempt > 0:
                    self.logger.info(f"スキャン再試行 {attempt + 1}/3")
                
                device = await BleakScanner.find_device_by_filter(
                    lambda d, ad: d.name and any(
                        name in d.name for name in self.config.target_device_names
                    ),
                    timeout=self.config.scan_timeout
                )
                
                if device:
                    self.logger.info(f"デバイス発見: {device.name} ({device.address})")
                    return device
                
                await asyncio.sleep(2.0)
            
            self.logger.warning("対象デバイスが見つかりませんでした")
            return None
            
        except Exception as e:
            self.logger.error(f"スキャンエラー: {e}")
            return None
    
    async def establish_connection(self, device) -> bool:
        """デバイスに接続"""
        self._set_state(ConnectionState.CONNECTING)
        
        try:
            # 既存の接続をクリーンアップ
            if self.client:
                try:
                    if self.client.is_connected:
                        await self.client.disconnect()
                except:
                    pass
                self.client = None
            
            # BleakClientを作成
            self.client = BleakClient(
                device,
                timeout=self.config.connection_timeout,
                disconnected_callback=self._on_disconnect
            )
            
            # 接続
            self.logger.info("接続を開始します...")
            await self.client.connect()
            
            if not self.client.is_connected:
                self.logger.error("接続に失敗しました")
                return False
            
            self.logger.info("接続成功")
            
            # 接続を安定させる
            self.logger.debug(f"接続安定化のため {self.config.connection_wait_time} 秒待機")
            await asyncio.sleep(self.config.connection_wait_time)
            
            # ペアリング状態を確認（可能な場合）
            try:
                paired = await self.client.pair()
                self.logger.info(f"ペアリング状態: {'成功' if paired else '不要'}")
            except NotImplementedError:
                self.logger.debug("ペアリング確認はこのプラットフォームでサポートされていません")
            except Exception as e:
                self.logger.warning(f"ペアリング確認エラー: {e}")
            
            return True
            
        except asyncio.TimeoutError:
            self.logger.error("接続タイムアウト")
            return False
        except BleakError as e:
            self.logger.error(f"BLE接続エラー: {e}")
            return False
        except Exception as e:
            self.logger.error(f"予期しない接続エラー: {e}")
            return False
    
    async def discover_services(self) -> bool:
        """サービスディスカバリーを実行"""
        self._set_state(ConnectionState.DISCOVERING)
        
        try:
            # 必ずサービスディスカバリーを実行（キャッシュに依存しない）
            self.logger.info("サービスディスカバリーを開始")
            
            # 既存のサービス情報をクリア
            if hasattr(self.client, '_services'):
                self.client._services = None
            
            # サービスを取得
            services = await self.client.get_services()
            self.logger.info(f"{len(services)} 個のサービスを発見")
            
            # サービス詳細をログ
            for service in services:
                self.logger.debug(f"サービス: {service.uuid}")
                for char in service.characteristics:
                    props = ", ".join(char.properties)
                    self.logger.debug(f"  特性: {char.uuid} [{props}]")
            
            # 必要な特性を確認
            write_char = None
            notify_char = None
            
            for service in services:
                for char in service.characteristics:
                    char_uuid_lower = char.uuid.lower()
                    if char_uuid_lower == self.config.write_characteristic_uuid.lower():
                        write_char = char
                        self.logger.info(f"書き込み特性を発見: {char.uuid}")
                    elif char_uuid_lower == self.config.notify_characteristic_uuid.lower():
                        notify_char = char
                        self.logger.info(f"通知特性を発見: {char.uuid}")
            
            if not write_char:
                self.logger.error(f"書き込み特性が見つかりません: {self.config.write_characteristic_uuid}")
                return False
            
            if not notify_char:
                self.logger.error(f"通知特性が見つかりません: {self.config.notify_characteristic_uuid}")
                return False
            
            # プロパティを確認
            if "write" not in write_char.properties and "write-without-response" not in write_char.properties:
                self.logger.warning(f"書き込み特性に書き込み権限がありません: {write_char.properties}")
            
            if "notify" not in notify_char.properties:
                self.logger.warning(f"通知特性に通知権限がありません: {notify_char.properties}")
            
            await asyncio.sleep(self.config.service_discovery_wait)
            return True
            
        except Exception as e:
            self.logger.error(f"サービスディスカバリーエラー: {e}")
            return False
    
    async def setup_notifications(self) -> bool:
        """通知を設定"""
        try:
            self.logger.info("通知を設定中...")
            
            # 通知を開始
            await self.client.start_notify(
                self.config.notify_characteristic_uuid,
                self.handle_notification
            )
            
            self.logger.info("通知設定完了")
            await asyncio.sleep(0.5)
            
            # 開始コマンドを送信
            self.logger.info("開始コマンドを送信")
            await self.client.write_gatt_char(
                self.config.write_characteristic_uuid,
                b'START\n'
            )
            
            self.logger.info("開始コマンド送信完了")
            return True
            
        except Exception as e:
            self.logger.error(f"通知設定エラー: {e}")
            return False
    
    async def monitor_connection(self):
        """接続を監視"""
        last_report_time = time.time()
        
        while not self.should_stop and self.client and self.client.is_connected:
            try:
                await asyncio.sleep(5.0)
                
                if not self.client or not self.client.is_connected:
                    break
                
                current_time = time.time()
                
                # 定期状態レポート
                if current_time - last_report_time >= 30.0:
                    uptime = current_time - self.stats["current_session_start"]
                    self.logger.info(
                        f"接続状態: 正常 | "
                        f"稼働時間: {uptime:.0f}秒 | "
                        f"受信データ数: {self.stats['total_data_received']}"
                    )
                    last_report_time = current_time
                
                # データタイムアウトチェック
                if self.config.data_timeout_enabled and self.stats["last_data_time"]:
                    if current_time - self.stats["last_data_time"] > self.config.data_timeout:
                        self.logger.warning(
                            f"{self.config.data_timeout}秒以上データが受信されていません"
                        )
                
            except Exception as e:
                self.logger.error(f"監視エラー: {e}")
                break
    
    async def send_keepalive(self):
        """キープアライブを送信"""
        if not self.config.keepalive_enabled:
            return
        
        while not self.should_stop and self.client and self.client.is_connected:
            try:
                await asyncio.sleep(self.config.keepalive_interval)
                
                if self.client and self.client.is_connected:
                    self.logger.debug("キープアライブ送信")
                    await self.client.write_gatt_char(
                        self.config.write_characteristic_uuid,
                        self.config.keepalive_command
                    )
                    
            except Exception as e:
                self.logger.error(f"キープアライブエラー: {e}")
                break
    
    def _on_disconnect(self, client):
        """切断コールバック"""
        self.logger.warning("接続が切断されました")
        self._set_state(ConnectionState.DISCONNECTED)
        self.stats["total_disconnections"] += 1
    
    async def connect_and_run(self) -> bool:
        """接続してデータ受信を開始"""
        try:
            # デバイスをスキャン
            device = await self.scan_for_device()
            if not device:
                return False
            
            self.device = device
            self.connected_device_name = device.name
            
            # 接続
            if not await self.establish_connection(device):
                return False
            
            # サービスディスカバリー
            if not await self.discover_services():
                if self.client and self.client.is_connected:
                    await self.client.disconnect()
                return False
            
            # 通知設定
            if not await self.setup_notifications():
                if self.client and self.client.is_connected:
                    await self.client.disconnect()
                return False
            
            # 接続成功
            self._set_state(ConnectionState.CONNECTED)
            self.stats["total_connections"] += 1
            self.stats["current_session_start"] = time.time()
            
            self.logger.info("=" * 50)
            self.logger.info("データ受信を開始しました")
            self.logger.info("=" * 50)
            
            # 監視タスクを開始
            monitor_task = asyncio.create_task(self.monitor_connection())
            keepalive_task = asyncio.create_task(self.send_keepalive())
            
            self.tasks = [monitor_task, keepalive_task]
            
            # 切断まで待機
            try:
                await self.client.disconnected_future
            except:
                pass
            
            # タスクをキャンセル
            for task in self.tasks:
                task.cancel()
            
            return False
            
        except Exception as e:
            self.logger.error(f"実行エラー: {e}")
            return False
    
    async def run(self):
        """サービスを実行"""
        self.stats["start_time"] = time.time()
        self.logger.info("=" * 50)
        self.logger.info("BLEセンサーサービスを開始します")
        self.logger.info(f"対象デバイス: {', '.join(self.config.target_device_names)}")
        self.logger.info("=" * 50)
        
        reconnect_count = 0
        reconnect_delay = self.config.reconnect_delay
        
        while not self.should_stop:
            try:
                # 接続試行
                if reconnect_count > 0:
                    self._set_state(ConnectionState.RECONNECTING)
                    self.logger.info(
                        f"再接続試行 {reconnect_count}"
                        f"{f'/{self.config.max_reconnect_attempts}' if self.config.max_reconnect_attempts > 0 else ''}"
                    )
                
                success = await self.connect_and_run()
                
                if not success:
                    reconnect_count += 1
                    
                    # 再接続回数チェック
                    if (self.config.max_reconnect_attempts > 0 and 
                        reconnect_count >= self.config.max_reconnect_attempts):
                        self.logger.error("最大再接続回数に達しました")
                        break
                    
                    # 再接続待機
                    self.logger.info(f"{reconnect_delay:.0f}秒後に再接続します...")
                    await asyncio.sleep(reconnect_delay)
                    
                    # バックオフ
                    reconnect_delay = min(
                        reconnect_delay * self.config.reconnect_backoff_factor,
                        self.config.max_reconnect_delay
                    )
                
            except KeyboardInterrupt:
                self.logger.info("ユーザーによる中断")
                break
            except Exception as e:
                self.logger.error(f"予期しないエラー: {e}")
                await asyncio.sleep(5.0)
        
        # 統計情報を出力
        self._print_statistics()
    
    def _print_statistics(self):
        """統計情報を出力"""
        if self.stats["start_time"]:
            runtime = time.time() - self.stats["start_time"]
            self.logger.info("=" * 50)
            self.logger.info("サービス統計:")
            self.logger.info(f"  実行時間: {runtime:.0f}秒")
            self.logger.info(f"  総接続回数: {self.stats['total_connections']}")
            self.logger.info(f"  総切断回数: {self.stats['total_disconnections']}")
            self.logger.info(f"  総受信データ数: {self.stats['total_data_received']}")
            self.logger.info("=" * 50)
    
    async def stop(self):
        """サービスを停止"""
        self.logger.info("サービス停止を開始します...")
        self.should_stop = True
        
        # タスクをキャンセル
        for task in self.tasks:
            if not task.done():
                task.cancel()
        
        # 接続をクローズ
        if self.client and self.client.is_connected:
            try:
                await self.client.disconnect()
            except:
                pass
        
        self.logger.info("サービスを停止しました")

# ================== 使用例 ==================
def data_handler(data: str, sender):
    """データ処理例"""
    # ここでデータを処理（DB保存、解析、転送など）
    print(f"[データ] {data}")

async def main():
    """メイン関数"""
    # 設定を作成
    config = BLEConfig(
        target_device_names=["KIRIRI01", "KIRIRI02", "KIRIRI03"],
        max_reconnect_attempts=-1,  # 無限再接続
        log_level="INFO",
        keepalive_enabled=True,
        data_timeout_enabled=True
    )
    
    # サービスを作成
    service = BLESensorService(config, data_callback=data_handler)
    
    try:
        # サービスを実行
        await service.run()
    except KeyboardInterrupt:
        print("\n中断されました")
    finally:
        await service.stop()

if __name__ == "__main__":
    # イベントループポリシーを設定（Windows対応）
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    # 実行
    asyncio.run(main())
