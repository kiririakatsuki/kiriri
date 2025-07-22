"""
BLE センサー接続プログラム - GATT Server切断エラー対応版
すべての既知のエラーを解決した安定版
"""

import asyncio
import logging
import sys
import time
import platform
from datetime import datetime
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum

from bleak import BleakScanner, BleakClient, BleakError
from bleak.backends.scanner import AdvertisementData

# ================== 設定 ==================
@dataclass
class BLEConfig:
    """BLE接続設定"""
    # デバイス設定
    target_device_names: list = None
    notify_characteristic_uuid: str = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
    write_characteristic_uuid: str = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
    
    # 接続設定（GATTエラー対策で長めに設定）
    connection_timeout: float = 30.0
    scan_timeout: float = 15.0
    initial_connection_wait: float = 5.0  # 初回接続時の待機時間を長く
    service_discovery_wait: float = 3.0   # サービスディスカバリー前の待機時間
    post_discovery_wait: float = 2.0      # サービスディスカバリー後の待機時間
    
    # 再接続設定
    max_reconnect_attempts: int = -1  # -1 = 無限
    reconnect_delay: float = 10.0     # GATTエラー対策で長めに
    max_scan_retry: int = 3           # スキャン再試行回数
    
    # キープアライブ設定
    keepalive_enabled: bool = True
    keepalive_interval: float = 20.0  # 間隔を長めに
    keepalive_command: bytes = b'PING\n'
    
    # エラーリトライ設定
    gatt_error_retry_delay: float = 15.0  # GATTエラー時の待機時間
    service_discovery_retry: int = 3       # サービスディスカバリーの再試行回数
    
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
    CONNECTED = "接続済み"
    ERROR = "エラー"

# ================== ロギング設定 ==================
def setup_logger(config: BLEConfig) -> logging.Logger:
    """ロガーのセットアップ"""
    logger = logging.getLogger("BLESensor")
    logger.setLevel(getattr(logging, config.log_level))
    
    # フォーマッター
    formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # コンソールハンドラー
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # ファイルハンドラー
    if config.log_file:
        file_handler = logging.FileHandler(config.log_file, encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger

# ================== メインクラス ==================
class BLESensorConnection:
    """BLEセンサー接続管理"""
    
    def __init__(self, config: BLEConfig, data_callback: Optional[Callable] = None):
        self.config = config
        self.logger = setup_logger(config)
        self.data_callback = data_callback
        
        # 状態管理
        self.state = ConnectionState.DISCONNECTED
        self.client: Optional[BleakClient] = None
        self.device = None
        
        # 統計
        self.stats = {
            "connections": 0,
            "disconnections": 0,
            "data_received": 0,
            "last_data_time": None,
            "gatt_errors": 0
        }
        
        # フラグ
        self.should_stop = False
        self.last_gatt_error_time = 0
        
    def handle_notification(self, sender, data: bytearray):
        """データ受信ハンドラー"""
        try:
            self.stats["last_data_time"] = time.time()
            self.stats["data_received"] += 1
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            decoded_data = data.decode(errors='ignore').strip()
            
            self.logger.info(f"受信データ [{timestamp}]: {decoded_data}")
            
            if self.data_callback:
                self.data_callback(decoded_data, sender)
                
        except Exception as e:
            self.logger.error(f"データ処理エラー: {e}")
    
    async def find_device(self) -> Optional[any]:
        """デバイスをスキャン（複数回試行）"""
        for attempt in range(self.config.max_scan_retry):
            if attempt > 0:
                self.logger.info(f"スキャン再試行 {attempt + 1}/{self.config.max_scan_retry}")
            
            self.state = ConnectionState.SCANNING
            self.logger.info(f"デバイスをスキャン中: {', '.join(self.config.target_device_names)}")
            
            try:
                # コールバック形式でスキャン（より安定）
                devices = []
                
                def detection_callback(device, advertisement_data: AdvertisementData):
                    if device.name and any(name in device.name for name in self.config.target_device_names):
                        devices.append(device)
                
                scanner = BleakScanner(detection_callback)
                await scanner.start()
                await asyncio.sleep(self.config.scan_timeout)
                await scanner.stop()
                
                if devices:
                    device = devices[0]
                    self.logger.info(f"デバイス発見: {device.name} ({device.address})")
                    return device
                
            except Exception as e:
                self.logger.error(f"スキャンエラー: {e}")
            
            if attempt < self.config.max_scan_retry - 1:
                await asyncio.sleep(5.0)
        
        self.logger.warning("デバイスが見つかりませんでした")
        return None
    
    async def connect_with_retry(self, device) -> bool:
        """接続を確立（GATTエラー対策込み）"""
        self.state = ConnectionState.CONNECTING
        
        try:
            # 既存接続をクリーンアップ
            if self.client:
                try:
                    if self.client.is_connected:
                        await self.client.disconnect()
                        await asyncio.sleep(2.0)  # 切断後の待機
                except:
                    pass
                self.client = None
            
            # GATTエラーから十分時間が経過しているか確認
            time_since_gatt_error = time.time() - self.last_gatt_error_time
            if time_since_gatt_error < self.config.gatt_error_retry_delay:
                wait_time = self.config.gatt_error_retry_delay - time_since_gatt_error
                self.logger.info(f"GATTエラー回復待機中... {wait_time:.0f}秒")
                await asyncio.sleep(wait_time)
            
            # 新しいクライアントを作成
            self.logger.info("接続を開始します...")
            self.client = BleakClient(
                device,
                timeout=self.config.connection_timeout,
                disconnected_callback=self._on_disconnect
            )
            
            # 接続試行
            await self.client.connect()
            
            if not self.client.is_connected:
                self.logger.error("接続に失敗しました")
                return False
            
            self.logger.info("接続成功！")
            self.stats["connections"] += 1
            
            # 接続を安定させるための長めの待機
            self.logger.info(f"接続安定化のため {self.config.initial_connection_wait} 秒待機中...")
            await asyncio.sleep(self.config.initial_connection_wait)
            
            return True
            
        except BleakError as e:
            if "GATT" in str(e):
                self.logger.error(f"GATTエラー: {e}")
                self.stats["gatt_errors"] += 1
                self.last_gatt_error_time = time.time()
            else:
                self.logger.error(f"BLE接続エラー: {e}")
            return False
        except Exception as e:
            self.logger.error(f"接続エラー: {e}")
            return False
    
    async def discover_services_with_retry(self) -> bool:
        """サービスディスカバリー（リトライ付き）"""
        for attempt in range(self.config.service_discovery_retry):
            try:
                if attempt > 0:
                    self.logger.info(f"サービスディスカバリー再試行 {attempt + 1}/{self.config.service_discovery_retry}")
                
                # ディスカバリー前の待機
                self.logger.info(f"サービスディスカバリー前に {self.config.service_discovery_wait} 秒待機...")
                await asyncio.sleep(self.config.service_discovery_wait)
                
                # サービスディスカバリー実行
                self.logger.info("サービスディスカバリーを実行中...")
                
                # キャッシュをクリア
                if hasattr(self.client, '_services'):
                    self.client._services = None
                
                services = await self.client.get_services()
                self.logger.info(f"{len(services)} 個のサービスを発見")
                
                # 必要な特性を確認
                write_found = False
                notify_found = False
                
                for service in services:
                    self.logger.debug(f"サービス: {service.uuid}")
                    for char in service.characteristics:
                        self.logger.debug(f"  特性: {char.uuid} - {char.properties}")
                        
                        if char.uuid.lower() == self.config.write_characteristic_uuid.lower():
                            write_found = True
                        elif char.uuid.lower() == self.config.notify_characteristic_uuid.lower():
                            notify_found = True
                
                if write_found and notify_found:
                    self.logger.info("必要な特性を確認しました")
                    
                    # ディスカバリー後の待機
                    await asyncio.sleep(self.config.post_discovery_wait)
                    return True
                else:
                    self.logger.warning("必要な特性が見つかりません")
                    
            except BleakError as e:
                if "GATT" in str(e):
                    self.logger.error(f"サービスディスカバリー中のGATTエラー: {e}")
                    self.last_gatt_error_time = time.time()
                    await asyncio.sleep(5.0)
                else:
                    self.logger.error(f"サービスディスカバリーエラー: {e}")
            except Exception as e:
                self.logger.error(f"サービスディスカバリーエラー: {e}")
            
            if attempt < self.config.service_discovery_retry - 1:
                await asyncio.sleep(3.0)
        
        return False
    
    async def setup_notifications(self) -> bool:
        """通知設定とコマンド送信"""
        try:
            # 通知開始前の待機
            await asyncio.sleep(1.0)
            
            # 通知を開始
            self.logger.info("通知を設定中...")
            await self.client.start_notify(
                self.config.notify_characteristic_uuid,
                self.handle_notification
            )
            
            # 通知開始後の待機
            await asyncio.sleep(1.0)
            
            # 開始コマンドを送信
            self.logger.info("開始コマンドを送信...")
            await self.client.write_gatt_char(
                self.config.write_characteristic_uuid,
                b'START\n'
            )
            
            self.logger.info("セットアップ完了")
            return True
            
        except BleakError as e:
            if "GATT" in str(e):
                self.logger.error(f"通知設定中のGATTエラー: {e}")
                self.last_gatt_error_time = time.time()
            else:
                self.logger.error(f"通知設定エラー: {e}")
            return False
        except Exception as e:
            self.logger.error(f"通知設定エラー: {e}")
            return False
    
    async def maintain_connection(self):
        """接続を維持（キープアライブ）"""
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
                    
            except BleakError as e:
                if "GATT" in str(e):
                    self.logger.error(f"キープアライブ中のGATTエラー: {e}")
                    self.last_gatt_error_time = time.time()
                break
            except Exception as e:
                self.logger.error(f"キープアライブエラー: {e}")
                break
    
    def _on_disconnect(self, client):
        """切断コールバック"""
        self.logger.warning("接続が切断されました")
        self.state = ConnectionState.DISCONNECTED
        self.stats["disconnections"] += 1
    
    async def connect_and_run(self) -> bool:
        """メイン接続処理"""
        try:
            # デバイスを探す
            device = await self.find_device()
            if not device:
                return False
            
            self.device = device
            
            # 接続
            if not await self.connect_with_retry(device):
                return False
            
            # サービスディスカバリー
            if not await self.discover_services_with_retry():
                if self.client and self.client.is_connected:
                    await self.client.disconnect()
                return False
            
            # 通知設定
            if not await self.setup_notifications():
                if self.client and self.client.is_connected:
                    await self.client.disconnect()
                return False
            
            # 接続成功
            self.state = ConnectionState.CONNECTED
            self.logger.info("="*50)
            self.logger.info("データ受信を開始しました")
            self.logger.info("="*50)
            
            # 接続維持タスク
            maintain_task = asyncio.create_task(self.maintain_connection())
            
            # 切断まで待機
            try:
                await self.client.disconnected_future
            except:
                pass
            
            maintain_task.cancel()
            return False
            
        except Exception as e:
            self.logger.error(f"実行エラー: {e}")
            return False
    
    async def run(self):
        """サービスメインループ"""
        self.logger.info("="*50)
        self.logger.info("BLEセンサー接続サービス開始")
        self.logger.info(f"対象: {', '.join(self.config.target_device_names)}")
        self.logger.info("="*50)
        
        reconnect_count = 0
        
        while not self.should_stop:
            try:
                if reconnect_count > 0:
                    self.logger.info(f"再接続試行 {reconnect_count}")
                    
                    # GATTエラーの場合は長めに待機
                    if self.stats["gatt_errors"] > 0:
                        self.logger.info(f"GATTエラー回復のため {self.config.gatt_error_retry_delay} 秒待機...")
                        await asyncio.sleep(self.config.gatt_error_retry_delay)
                    else:
                        await asyncio.sleep(self.config.reconnect_delay)
                
                # 接続実行
                await self.connect_and_run()
                
                reconnect_count += 1
                
                # 最大再接続回数チェック
                if (self.config.max_reconnect_attempts > 0 and 
                    reconnect_count >= self.config.max_reconnect_attempts):
                    self.logger.error("最大再接続回数に達しました")
                    break
                    
            except KeyboardInterrupt:
                self.logger.info("ユーザーによる中断")
                break
            except Exception as e:
                self.logger.error(f"予期しないエラー: {e}")
                await asyncio.sleep(10.0)
        
        # 統計表示
        self.logger.info("="*50)
        self.logger.info(f"総接続回数: {self.stats['connections']}")
        self.logger.info(f"総切断回数: {self.stats['disconnections']}")
        self.logger.info(f"受信データ数: {self.stats['data_received']}")
        self.logger.info(f"GATTエラー数: {self.stats['gatt_errors']}")
        self.logger.info("="*50)
    
    async def stop(self):
        """サービス停止"""
        self.should_stop = True
        if self.client and self.client.is_connected:
            try:
                await self.client.disconnect()
            except:
                pass

# ================== メイン関数 ==================
def data_handler(data: str, sender):
    """データ処理コールバック例"""
    # ここでデータを処理
    pass

async def main():
    """メイン処理"""
    # 設定
    config = BLEConfig(
        target_device_names=["KIRIRI01", "KIRIRI02", "KIRIRI03"],
        max_reconnect_attempts=-1,  # 無限再接続
        log_level="INFO"
    )
    
    # サービス作成
    service = BLESensorConnection(config, data_callback=data_handler)
    
    try:
        await service.run()
    except KeyboardInterrupt:
        print("\n中断されました")
    finally:
        await service.stop()

if __name__ == "__main__":
    # Windows対応
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    asyncio.run(main())
