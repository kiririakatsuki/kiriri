// pairing_test.js (多機能・高安定版)
// - Web Bluetoothによる直接接続と、Pythonブリッジ経由のWebSocket接続に対応
// - 計測データのエクスポート機能を追加
// - BLEデータ処理の堅牢性を向上

// --- グローバル定数 ---
const KIRIRI_SERVICE_UUID = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
const RX_CHARACTERISTIC_UUID = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'; // Write用
const TX_CHARACTERISTIC_UUID = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'; // Notify用
const SUPPORTED_DEVICE_NAMES = ['KIRIRI01', 'KIRI', 'KIRIRI02']; // KIRIRI02を追加
const MAX_CHART_POINTS = 120; // グラフに表示する最大データ点数 (2分)

// --- HTML要素の参照 ---
const connectModeSelect = document.getElementById('connectMode');
const bleOptions = document.getElementById('bleOptions');
const wsOptions = document.getElementById('wsOptions');
const wsUrlInput = document.getElementById('wsUrl');
const connectButton = document.getElementById('connectButton');
const disconnectButton = document.getElementById('disconnectButton');
const statusMessages = document.getElementById('statusMessages');
const sensorIdDisplay = document.getElementById('sensorIdDisplay');
const yAngleDisplay = document.getElementById('yAngleDisplay');
const xAngleDisplay = document.getElementById('xAngleDisplay');
const postureChartCanvas = document.getElementById('postureChart');
const calibrateButton = document.getElementById('calibrateButton');
const refYDisplay = document.getElementById('refYDisplay');
const refXDisplay = document.getElementById('refXDisplay');
const startMeasurementButton = document.getElementById('startMeasurementButton');
const endMeasurementButton = document.getElementById('endMeasurementButton');
const feedbackOnButton = document.getElementById('feedbackOnButton');
const feedbackOffButton = document.getElementById('feedbackOffButton');
const messageDisplay = document.getElementById('messageDisplay');
const scrollTopButton = document.getElementById('scrollTopButton');

// --- 状態管理用変数 ---
let bleDevice = null;
let bleServer = null;
let angleCharacteristic = null;
let webSocket = null;
let referenceY = null;
let referenceX = null;
let currentY = 0.0;
let currentX = 0.0;
let isConnected = false;
let isFeedbackActive = false;
let isMeasuring = false;
let connectionMode = 'ble'; // 'ble' または 'ws'

// --- グラフ関連変数 ---
let myPostureChart = null;
let chartTimestamps = [];
let chartYData = [];
let chartXData = [];


/**
 * ログをコンソールと画面上のステータス欄に出力します。
 * @param {string} message - 表示するメッセージ
 * @param {'info'|'success'|'warning'|'error'} type - メッセージの種類
 */
function logStatus(message, type = 'info') {
    console.log(`[STATUS - ${type.toUpperCase()}] ${message}`);
    if (statusMessages) {
        statusMessages.textContent = message;
        statusMessages.className = type;
    }
}

/**
 * 画面上部のアドバイスメッセージを更新します。
 * @param {string} message - 表示するメッセージ
 * @param {'info'|'success'|'warning'|'error'} type - メッセージの種類
 */
function updateMessageDisplay(message, type = 'info') {
    if (messageDisplay) {
        messageDisplay.textContent = message;
        messageDisplay.className = type;
    }
}


/**
 * 現在の状態に応じて、各ボタンの有効/無効状態を切り替えます。
 */
function updateButtonStates() {
    const isModeSelected = !!connectionMode;
    connectButton.disabled = !isModeSelected || isConnected;
    disconnectButton.disabled = !isModeSelected || !isConnected;

    calibrateButton.disabled = !isConnected || isMeasuring;
    startMeasurementButton.disabled = !isConnected || isMeasuring || referenceY === null;
    endMeasurementButton.disabled = !isConnected || !isMeasuring;
    feedbackOnButton.disabled = !isConnected || !isMeasuring || isFeedbackActive;
    feedbackOffButton.disabled = !isConnected || !isMeasuring || !isFeedbackActive;
}

/**
 * グラフを初期化します。
 */
function initializeChart() {
    if (!postureChartCanvas) {
        console.warn("Canvas 'postureChart' not found, skipping chart initialization.");
        return;
    }
    const ctx = postureChartCanvas.getContext('2d');
    myPostureChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: [],
            datasets: [{
                label: 'Y軸 (前後)',
                data: [],
                borderColor: 'rgb(75, 192, 192)',
                tension: 0.2,
                borderWidth: 2,
                pointRadius: 0
            }, {
                label: 'X軸 (左右)',
                data: [],
                borderColor: 'rgb(255, 99, 132)',
                tension: 0.2,
                borderWidth: 2,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: { min: -45, max: 45, title: { display: true, text: '角度 (°)' } },
                x: { ticks: { display: false } }
            },
            animation: { duration: 150 },
        }
    });
}

/**
 * センサーから受信した角度データでUIとグラフを更新します。
 * @param {number} y - Y軸の角度
 * @param {number} x - X軸の角度
 */
function updateAngleValues(y, x) {
    currentY = y;
    currentX = x;

    if (yAngleDisplay) yAngleDisplay.textContent = currentY.toFixed(2);
    if (xAngleDisplay) xAngleDisplay.textContent = currentX.toFixed(2);

    if (isMeasuring && myPostureChart) {
        const now = new Date();
        const timestamp = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

        chartTimestamps.push(timestamp);
        chartYData.push(currentY);
        chartXData.push(currentX);

        if (chartTimestamps.length > MAX_CHART_POINTS) {
            chartTimestamps.shift();
            chartYData.shift();
            chartXData.shift();
        }

        myPostureChart.data.labels = chartTimestamps;
        myPostureChart.data.datasets[0].data = chartYData;
        myPostureChart.data.datasets[1].data = chartXData;
        myPostureChart.update('none');
    }

    if (isFeedbackActive) {
        checkPosture(currentY, currentX);
    }
}


// --- WebSocket関連の関数 ---

/**
 * WebSocketサーバーに接続します。
 */
async function connectWebSocket() {
    const url = wsUrlInput.value;
    if (!url) {
        logStatus("WebSocketのURLが入力されていません。", 'error');
        return;
    }
    logStatus(`WebSocketサーバー (${url}) に接続しています...`, 'info');

    try {
        webSocket = new WebSocket(url);

        webSocket.onopen = (event) => {
            isConnected = true;
            logStatus("WebSocketサーバーに接続しました。", 'success');
            updateMessageDisplay("接続成功！「A. 今の姿勢を覚える」で基準を設定してください。", "success");
            updateButtonStates();
        };

        webSocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (typeof data.y === 'number' && typeof data.x === 'number') {
                    if (sensorIdDisplay && sensorIdDisplay.textContent !== data.id) {
                        sensorIdDisplay.textContent = data.id || 'Python Bridge';
                    }
                    updateAngleValues(data.y, data.x);
                }
            } catch (e) {
                logStatus(`WebSocketデータ処理エラー: ${e.message}`, 'error');
            }
        };

        webSocket.onclose = (event) => {
            logStatus("WebSocket接続が切れました。", 'warning');
            handleDisconnect();
        };

        webSocket.onerror = (error) => {
            logStatus(`WebSocketエラー: 接続に失敗しました。URLやPythonブリッジの状態を確認してください。`, 'error');
            handleDisconnect();
        };

    } catch (e) {
        logStatus(`WebSocket接続の開始に失敗しました: ${e.message}`, 'error');
        updateButtonStates();
    }
}


// --- Web Bluetooth (BLE) 関連の関数 ---

/**
 * BLEデバイスに接続し、サービスの検索、通知のセットアップを行います。
 */
async function connectBLE() {
    logStatus('Kiririセンサーを探しています...', 'info');

    try {
        logStatus(`ブラウザのダイアログで「${SUPPORTED_DEVICE_NAMES.join(' or ')}」を選んでください...`, 'info');
        bleDevice = await navigator.bluetooth.requestDevice({
            filters: SUPPORTED_DEVICE_NAMES.map(name => ({ namePrefix: name })),
            optionalServices: [KIRIRI_SERVICE_UUID]
        });

        const deviceName = bleDevice.name || bleDevice.id;
        logStatus(`デバイス選択: ${deviceName}`, 'info');
        
        bleDevice.addEventListener('gattserverdisconnected', handleDisconnect);

        logStatus('GATTサーバーに接続しています...', 'info');
        bleServer = await bleDevice.gatt.connect();
        
        logStatus('サービスを検索しています...', 'info');
        const service = await bleServer.getPrimaryService(KIRIRI_SERVICE_UUID);
        
        logStatus('キャラクタリスティックを取得しています...', 'info');
        angleCharacteristic = await service.getCharacteristic(TX_CHARACTERISTIC_UUID);
        angleCharacteristic.addEventListener('characteristicvaluechanged', handleBleData);

        logStatus('通知を開始しています...', 'info');
        await angleCharacteristic.startNotifications();
        
        isConnected = true;
        if (sensorIdDisplay) sensorIdDisplay.textContent = deviceName;
        logStatus('接続完了！データ受信待機中...', 'success');
        updateMessageDisplay("接続成功！「A. 今の姿勢を覚える」で基準を設定してください。", "success");

    } catch (error) {
        logStatus(`BLE接続エラー: ${error.message}`, 'error');
        console.error("BLE Connection Error:", error);
        handleDisconnect();
    } finally {
        updateButtonStates();
    }
}

/**
 * BLEデバイスからのデータ通知を堅牢に処理します。
 * @param {Event} event - characteristicvaluechangedイベント
 */
function handleBleData(event) {
    const value = event.target.value; // DataViewオブジェクト
    if (!value) return;

    try {
        const textDecoder = new TextDecoder('utf-8');
        const receivedText = textDecoder.decode(value);

        const startIndex = receivedText.indexOf('N:');
        if (startIndex === -1) {
            return;
        }

        const dataPart = receivedText.substring(startIndex + 2);

        const separatorIndex = dataPart.indexOf(':');
        if (separatorIndex === -1) {
            return;
        }

        const yStr = dataPart.substring(0, separatorIndex);
        const xStr = dataPart.substring(separatorIndex + 1);

        const yRaw = parseInt(yStr, 10);
        const xRaw = parseInt(xStr, 10);

        if (isNaN(yRaw) || isNaN(xRaw)) {
            return;
        }

        updateAngleValues(yRaw / 100.0, xRaw / 100.0);

    } catch (error) {
        logStatus(`BLEデータ処理の例外: ${error.message}`, 'error');
    }
}


// --- 接続・切断の共通処理 ---

/**
 * 接続ボタンが押された時の処理。選択中のモードに応じて処理を振り分けます。
 */
async function handleConnect() {
    connectButton.disabled = true;
    if (connectionMode === 'ble') {
        await connectBLE();
    } else if (connectionMode === 'ws') {
        await connectWebSocket();
    }
}

/**
 * 切断処理。BLEとWebSocketの両方に対応します。
 */
async function handleDisconnect() {
    logStatus('切断処理を実行しています...', 'info');

    if (webSocket) {
        webSocket.onclose = null;
        webSocket.close();
        webSocket = null;
    }

    if (bleDevice) {
        bleDevice.removeEventListener('gattserverdisconnected', handleDisconnect);
        if (bleDevice.gatt && bleDevice.gatt.connected) {
            try {
                await bleDevice.gatt.disconnect();
                logStatus('BLEデバイスから切断しました。', 'info');
            } catch (error) {
                logStatus(`BLE切断エラー: ${error.message}`, 'error');
            }
        }
        bleDevice = null;
    }

    isConnected = false;
    isMeasuring = false;
    isFeedbackActive = false;
    referenceY = null;
    referenceX = null;
    sensorIdDisplay.textContent = '---';
    yAngleDisplay.textContent = '---';
    xAngleDisplay.textContent = '---';
    refYDisplay.textContent = '---';
    refXDisplay.textContent = '---';

    chartTimestamps = [];
    chartYData = [];
    chartXData = [];
    if (myPostureChart) {
        myPostureChart.data.labels = chartTimestamps;
        myPostureChart.data.datasets[0].data = chartYData;
        myPostureChart.data.datasets[1].data = chartXData;
        myPostureChart.update('none');
    }
    
    updateMessageDisplay("切断されました。再度接続してください。", "warning");
    logStatus("ページの準備ができました。接続モードを選んでください。", 'info');
    updateButtonStates();
}


// --- 計測とフィードバック ---

/**
 * 現在の姿勢を基準として記憶します。
 */
function calibratePosture() {
    if (!isConnected || isMeasuring) return;
    referenceY = currentY;
    referenceX = currentX;
    if (refYDisplay) refYDisplay.textContent = referenceY.toFixed(2);
    if (refXDisplay) refXDisplay.textContent = referenceX.toFixed(2);
    updateMessageDisplay("基準設定完了！「B. 計測開始」ボタンを押してください。", "success");
    isFeedbackActive = false;
    updateButtonStates();
}

/**
 * 姿勢の計測を開始します。
 */
function startMeasurement() {
    if (!isConnected || isMeasuring || referenceY === null) {
        if (referenceY === null) {
            updateMessageDisplay("先に「A. 今の姿勢を覚える」で基準を設定してください。", "warning");
        }
        return;
    }
    isMeasuring = true;
    isFeedbackActive = false;
    
    chartTimestamps = [];
    chartYData = [];
    chartXData = [];
    if (myPostureChart) {
        myPostureChart.data.labels = chartTimestamps;
        myPostureChart.data.datasets[0].data = chartYData;
        myPostureChart.data.datasets[1].data = chartXData;
    }
    
    updateMessageDisplay("計測中... 必要なら「フィードバック ON」を押してください。", "info");
    updateButtonStates();
}

/**
 * 計測を終了し、結果をエクスポートします。
 */
function endMeasurement() {
    if (!isConnected || !isMeasuring) return;
    isMeasuring = false;
    isFeedbackActive = false;
    updateMessageDisplay("計測を終了しました。「B. 計測開始」で次の計測ができます。", "info");
    updateButtonStates();
    exportDataAsCsv();
}

/**
 * 計測データをCSV形式でコンソールに出力します。
 */
function exportDataAsCsv() {
    if (chartTimestamps.length === 0) {
        console.log("エクスポートするデータがありません。");
        return;
    }

    let csvContent = "Timestamp,Y_Angle,X_Angle\n";
    for (let i = 0; i < chartTimestamps.length; i++) {
        csvContent += `${chartTimestamps[i]},${chartYData[i].toFixed(4)},${chartXData[i].toFixed(4)}\n`;
    }

    console
