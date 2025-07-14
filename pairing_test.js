// pairing_test.js (KIRIRI01対応完全版)

// --- HTML Element References ---
const pairButton = document.getElementById('pairButton');
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

// --- Bluetooth関連変数 ---
let bleDevice = null;
let bleServer = null;
let angleCharacteristic = null;
let rxCharacteristic = null; // KIRIRI01用のRX特性
let currentSensorType = null;

// --- センサーとUUID ---
const KIRIRI_SERVICE_UUID = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
const RX_CHARACTERISTIC_UUID = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'; // Write用
const TX_CHARACTERISTIC_UUID = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'; // Notify用

// 対応センサー名のリスト (より具体的な名前を先に記述)
const SUPPORTED_DEVICE_NAMES = ['KIRIRI01', 'KIRI'];

// --- 状態管理用変数 ---
let referenceY = null;
let referenceX = null;
let currentY = 0.0;
let currentX = 0.0;
let isConnected = false;
let isFeedbackActive = false;
let isMeasuring = false;

// --- Chart Variables ---
let myPostureChart = null;
const maxDataPoints = 60;
let chartTimestamps = [];
let chartYData = [];
let chartXData = [];

// --- Helper Functions ---
function logStatus(message, type = 'info') {
    console.log(`[STATUS - ${type.toUpperCase()}] ${message}`);
    if (statusMessages) {
        statusMessages.textContent = message;
        statusMessages.className = type;
    }
}

function updateMessageDisplay(message, type = 'info') {
    if (messageDisplay) {
        messageDisplay.textContent = message;
        messageDisplay.className = type;
    }
}

function updateButtonStates() {
    const buttons = { pairButton, disconnectButton, calibrateButton, startMeasurementButton, endMeasurementButton, feedbackOnButton, feedbackOffButton };
    if (isConnected) {
        if (buttons.pairButton) buttons.pairButton.disabled = true;
        if (buttons.disconnectButton) buttons.disconnectButton.disabled = false;
        if (buttons.calibrateButton) buttons.calibrateButton.disabled = isMeasuring;
        if (buttons.startMeasurementButton) buttons.startMeasurementButton.disabled = isMeasuring || referenceY === null;
        if (buttons.endMeasurementButton) buttons.endMeasurementButton.disabled = !isMeasuring;
        if (buttons.feedbackOnButton) buttons.feedbackOnButton.disabled = !isMeasuring || isFeedbackActive;
        if (buttons.feedbackOffButton) buttons.feedbackOffButton.disabled = !isMeasuring || !isFeedbackActive;
    } else {
        if (buttons.pairButton) buttons.pairButton.disabled = false;
        if (buttons.disconnectButton) buttons.disconnectButton.disabled = true;
        if (buttons.calibrateButton) buttons.calibrateButton.disabled = true;
        if (buttons.startMeasurementButton) buttons.startMeasurementButton.disabled = true;
        if (buttons.endMeasurementButton) buttons.endMeasurementButton.disabled = true;
        if (buttons.feedbackOnButton) buttons.feedbackOnButton.disabled = true;
        if (buttons.feedbackOffButton) buttons.feedbackOffButton.disabled = true;
    }
}

// センサータイプを判定する関数
function detectSensorType(deviceName) {
    for (const sensorName of SUPPORTED_DEVICE_NAMES) {
        if (deviceName.startsWith(sensorName)) {
            return sensorName;
        }
    }
    return null;
}

// --- Chart Initialization ---
function initializeChart() {
    if (!postureChartCanvas) {
        console.warn("Canvas 'postureChart' not found, skipping chart initialization.");
        return;
    }
    const ctx = postureChartCanvas.getContext('2d');

    const gradientY = ctx.createLinearGradient(0, 0, 0, 300);
    gradientY.addColorStop(0, 'rgba(75, 192, 192, 0.5)');
    gradientY.addColorStop(1, 'rgba(75, 192, 192, 0)');

    const gradientX = ctx.createLinearGradient(0, 0, 0, 300);
    gradientX.addColorStop(0, 'rgba(255, 99, 132, 0.5)');
    gradientX.addColorStop(1, 'rgba(255, 99, 132, 0)');

    myPostureChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: chartTimestamps,
            datasets: [
                {
                    label: 'Y軸 (前後)',
                    data: chartYData,
                    borderColor: 'rgb(75, 192, 192)',
                    backgroundColor: gradientY,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: true
                },
                {
                    label: 'X軸 (左右)',
                    data: chartXData,
                    borderColor: 'rgb(255, 99, 132)',
                    backgroundColor: gradientX,
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: true
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 200
            },
            interaction: {
                mode: 'index',
                intersect: false,
                axis: 'x'
            },
            scales: {
                y: {
                    min: -45,
                    max: 45,
                    title: {
                        display: true,
                        text: '角度 (°)',
                        font: { size: 12 },
                        color: '#666'
                    },
                    grid: {
                        color: 'rgba(0, 0, 0, 0.07)',
                        borderDash: [2, 2]
                    },
                    ticks: {
                        color: '#555',
                        stepSize: 15
                    }
                },
                x: {
                    ticks: {
                        display: true,
                        maxTicksLimit: 6,
                        autoSkip: true,
                        color: '#555'
                    },
                    grid: {
                        display: false
                    }
                }
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#444',
                        font: { size: 12 },
                        padding: 15,
                        boxWidth: 15,
                        usePointStyle: true,
                    }
                },
                tooltip: {
                    enabled: true,
                    backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    titleColor: '#fff',
                    bodyColor: '#fff',
                    titleFont: { size: 13 },
                    bodyFont: { size: 12 },
                    padding: 10,
                    cornerRadius: 4,
                    displayColors: true,
                    callbacks: {
                        label: function(context) {
                            let label = context.dataset.label || '';
                            if (label) {
                                label += ': ';
                            }
                            if (context.parsed.y !== null) {
                                label += context.parsed.y.toFixed(2) + ' 度';
                            }
                            return label;
                        }
                    }
                }
            },
            elements: {
                line: {
                    borderWidth: 2,
                    tension: 0.3
                },
                point: {
                    radius: 0,
                    hoverRadius: 5,
                    hitRadius: 10
                }
            }
        }
    });
    console.log("Chart initialized.");
}

// --- BLE Event Handlers ---
function onDisconnected(event) {
    logStatus('センサーとの接続が切れました。再度ペアリングしてください。', 'warning');
    isConnected = false;
    isMeasuring = false;
    isFeedbackActive = false;
    currentSensorType = null;

    if (bleDevice && bleDevice.gatt) {
        bleDevice.removeEventListener('gattserverdisconnected', onDisconnected);
    }
    bleDevice = null;
    bleServer = null;
    angleCharacteristic = null;
    rxCharacteristic = null;

    if (sensorIdDisplay) sensorIdDisplay.textContent = '---';
    if (yAngleDisplay) yAngleDisplay.textContent = '---';
    if (xAngleDisplay) xAngleDisplay.textContent = '---';
    if (refYDisplay) refYDisplay.textContent = '---';
    if (refXDisplay) refXDisplay.textContent = '---';
    referenceY = null;
    referenceX = null;

    updateMessageDisplay("センサーとの接続が切れました。「ペアリング」からやり直してください。", "warning");
    
    chartTimestamps = [];
    chartYData = [];
    chartXData = [];
    if (myPostureChart) {
        myPostureChart.data.labels = chartTimestamps;
        myPostureChart.data.datasets[0].data = chartYData;
        myPostureChart.data.datasets[1].data = chartXData;
        myPostureChart.update('none');
    }

    updateButtonStates();
}

function handleAngleData(event) {
    const value = event.target.value;
    try {
        const textDecoder = new TextDecoder('utf-8');
        const receivedText = textDecoder.decode(value);

        if (receivedText.startsWith("N:")) {
            const partsStr = receivedText.substring(2);
            const parts = partsStr.split(':');
            
            if (parts.length === 2) {
                const yRaw = parseInt(parts[0].trim(), 10);
                const xRaw = parseInt(parts[1].trim(), 10);
                
                currentY = yRaw / 100.0;
                currentX = xRaw / 100.0;

                if (yAngleDisplay) yAngleDisplay.textContent = currentY.toFixed(2);
                if (xAngleDisplay) xAngleDisplay.textContent = currentX.toFixed(2);
                
                if (isMeasuring) {
                    if (myPostureChart) {
                        const now = new Date();
                        const timestamp = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

                        chartTimestamps.push(timestamp);
                        chartYData.push(currentY);
                        chartXData.push(currentX);

                        if (chartTimestamps.length > maxDataPoints) {
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
            } else {
                logStatus(`データ形式エラー(分割): ${receivedText}`, 'error');
            }
        }
    } catch (error) {
        logStatus(`角度データ処理エラー: ${error}`, 'error');
        console.error("Error processing angle data: ", error, value);
    }
}

// KIRIRI01用の初期化関数
async function initializeKIRIRI01() {
    try {
        if (!rxCharacteristic) {
            console.error("RX特性が取得できていません");
            return false;
        }

        logStatus('KIRIRI01の初期化を試みています...', 'info');
        
        // いくつかの初期化パターンを試す
        // パターン1: 改行付きSTARTコマンド
        try {
            const startCmd = new TextEncoder().encode('START\n');
            await rxCharacteristic.writeValue(startCmd);
            await new Promise(resolve => setTimeout(resolve, 100));
            console.log("STARTコマンド送信成功");
        } catch (e) {
            console.log("STARTコマンド送信失敗:", e.message);
        }

        // パターン2: 単純な初期化バイト
        try {
            const initCmd = new Uint8Array([0x01]);
            await rxCharacteristic.writeValue(initCmd);
            await new Promise(resolve => setTimeout(resolve, 100));
            console.log("初期化バイト送信成功");
        } catch (e) {
            console.log("初期化バイト送信失敗:", e.message);
        }

        return true;
    } catch (error) {
        console.error("KIRIRI01初期化エラー:", error);
        return false;
    }
}

// --- Button Click Handlers ---
if (pairButton) {
    pairButton.onclick = async () => {
        if (bleDevice && bleDevice.gatt.connected) {
            logStatus('既にセンサーに接続中です。一度切断してください。', 'warning');
            return;
        }
        
        onDisconnected();
        logStatus('Kiririセンサーを探しています...', 'info');
        if (pairButton) pairButton.disabled = true;
        if (disconnectButton) disconnectButton.disabled = true;
        updateMessageDisplay("センサー検索中...", "info");

        try {
            const supportedDevicesStr = SUPPORTED_DEVICE_NAMES.join('」または「');
            logStatus(`ブラウザのBluetoothデバイス選択ダイアログで「${supportedDevicesStr}」を選んでください...`, 'info');
            
            bleDevice = await navigator.bluetooth.requestDevice({
                filters: SUPPORTED_DEVICE_NAMES.map(name => ({ namePrefix: name })),
                optionalServices: [KIRIRI_SERVICE_UUID]
            });
            
            const deviceName = bleDevice.name || bleDevice.id;
            currentSensorType = detectSensorType(deviceName);
            
            logStatus(`デバイス選択: ${deviceName} (タイプ: ${currentSensorType})`, 'info');
            logStatus('センサーに接続しています...', 'info');

            bleDevice.addEventListener('gattserverdisconnected', onDisconnected);
            bleServer = await bleDevice.gatt.connect();
            logStatus('GATTサーバーに接続完了。', 'success');
            isConnected = true;
            if (sensorIdDisplay) sensorIdDisplay.textContent = deviceName;
            
            // サービス取得
            const service = await bleServer.getPrimaryService(KIRIRI_SERVICE_UUID);
            logStatus('サービスに接続しました。', 'info');
            
            // KIRIRI01の初期化処理を一旦コメントアウトし、通知開始を優先する
            // if (currentSensorType === 'KIRIRI01') {
            //     try {
            //         rxCharacteristic = await service.getCharacteristic(RX_CHARACTERISTIC_UUID);
            //         logStatus('RX特性を取得しました。', 'info');
                    
            //         // 初期化を試みる
            //         await initializeKIRIRI01();
            //     } catch (e) {
            //         console.log('RX特性の取得に失敗:', e.message);
            //     }
            // }
            
            // TX特性（通知用）を取得
            angleCharacteristic = await service.getCharacteristic(TX_CHARACTERISTIC_UUID);
            logStatus('TX特性を取得しました。', 'info');
            
            // イベントハンドラを先に設定
            angleCharacteristic.addEventListener('characteristicvaluechanged', handleAngleData);
            
            // KIRIRI01の場合はさらに待機 ← このブロックもコメントアウト
            // if (currentSensorType === 'KIRIRI01') {
            //     logStatus('通知開始前の待機中...', 'info');
            //     await new Promise(resolve => setTimeout(resolve, 1000));
            // }
            
            // 通知を開始
            logStatus('通知を開始しています...', 'info');
            await angleCharacteristic.startNotifications();
            
            logStatus('データ受信待機中...', 'info');
            updateMessageDisplay("接続成功！「A. 今の姿勢を覚える」で基準を設定してください。", "success");

        } catch (error) {
            logStatus(`ペアリング/接続エラー: ${error.message || error}`, 'error');
            console.error("Pairing/Connection Error: ", error);
            isConnected = false;
            if (bleDevice && bleDevice.gatt && bleDevice.gatt.connected) {
                try { await bleDevice.gatt.disconnect(); } catch (e) { /* ignore */ }
            }
            onDisconnected();
        }
        updateButtonStates();
    };
}

if (disconnectButton) {
    disconnectButton.onclick = async () => {
        if (!bleDevice || !bleDevice.gatt || !bleDevice.gatt.connected) {
            logStatus('センサーは既に切断されています。', 'warning');
            onDisconnected();
            return;
        }
        logStatus('センサーから切断しています...', 'info');
        try {
            if (angleCharacteristic && bleDevice.gatt.connected) {
                angleCharacteristic.removeEventListener('characteristicvaluechanged', handleAngleData);
                await angleCharacteristic.stopNotifications();
                logStatus('角度データの通知を停止しました。', 'info');
            }
            if (bleDevice.gatt.connected) {
                await bleDevice.gatt.disconnect();
            } else {
                onDisconnected();
            }
        } catch (error) {
            logStatus(`切断中にエラー: ${error}`, 'error');
            console.error("Disconnect error: ", error);
            onDisconnected();
        }
    };
}

if (calibrateButton) {
    calibrateButton.onclick = () => {
        if (!isConnected || isMeasuring) return;
        referenceY = currentY;
        referenceX = currentX;
        if (refYDisplay) refYDisplay.textContent = referenceY.toFixed(2);
        if (refXDisplay) refXDisplay.textContent = referenceX.toFixed(2);
        updateMessageDisplay("基準設定完了！「B. 計測開始」ボタンを押してください。", "success");
        console.log(`基準設定: Y=${referenceY}, X=${referenceX}`);
        isFeedbackActive = false;
        updateButtonStates();
    };
}

if (startMeasurementButton) {
    startMeasurementButton.onclick = () => {
        if (!isConnected || isMeasuring || referenceY === null) {
            if(referenceY === null) updateMessageDisplay("先に「A. 今の姿勢を覚える」で基準を設定してください。", "warning");
            return;
        }
        console.log("計測開始ボタンが押されました。");
        isMeasuring = true;
        isFeedbackActive = false;

        chartTimestamps = [];
        chartYData = [];
        chartXData = [];
        if (myPostureChart) {
            myPostureChart.data.labels = chartTimestamps;
            myPostureChart.data.datasets[0].data = chartYData;
            myPostureChart.data.datasets[1].data = chartXData;
            myPostureChart.update('none');
        }
        
        updateMessageDisplay("計測中... 必要なら「フィードバック ON」を押してください。", "info");
        updateButtonStates();
    };
}

if (feedbackOnButton) {
    feedbackOnButton.onclick = () => {
        if (!isConnected || !isMeasuring) return;
        console.log("フィードバックONボタンが押されました。");
        isFeedbackActive = true;
        checkPosture(currentY, currentX);
        updateButtonStates();
    };
}

if (feedbackOffButton) {
    feedbackOffButton.onclick = () => {
        if (!isConnected || !isMeasuring) return;
        console.log("フィードバックOFFボタンが押されました。");
        isFeedbackActive = false;
        updateMessageDisplay("フィードバック表示オフ中。「フィードバック ON」で再開できます。", "info");
        updateButtonStates();
    };
}

if (endMeasurementButton) {
    endMeasurementButton.onclick = () => {
        if (!isConnected || !isMeasuring) return;
        console.log("計測終了ボタンが押されました。");
        isMeasuring = false;
        isFeedbackActive = false;
        updateMessageDisplay("計測を終了しました。「B. 計測開始」で次の計測ができます。", "info");
        updateButtonStates();
    };
}

// --- Posture Check Function ---
const goodPostureMessages = [
    "素晴らしい！キリッとした良い姿勢が保てています！",
    "理想的な姿勢です！自信に満ち溢れていますね！",
    "ナイス姿勢！背筋がスッと伸びて美しいです！",
    "完璧です！その姿勢、ぜひ続けてください！"
];

const forwardSlightMessages = [
    "少し猫背気味かも。胸を軽く開いてみましょう。",
    "おっと、少しだけ頭が前に出ています。空から糸で引っ張られるイメージで。",
    "背中が少し丸まっています。リラックスして伸びをしてみましょう。"
];

const forwardNoticeableMessages = [
    "だいぶ猫背になっています！意識して背筋をスッと伸ばしましょう！",
    "PCやスマホの画面に近づきすぎていませんか？一度、顔を上げてみましょう。",
    "注意！背中が大きく丸まっています。腰への負担も大きいですよ。"
];

const backwardSlightMessages = [
    "少し後ろに反りすぎかも。リラックスしてお腹を意識してみて。",
    "胸を張りすぎているかもしれません。肩の力を抜いてみましょう。"
];

const backwardNoticeableMessages = [
    "大きく後ろに反っています。お腹に力を入れて、腰を守りましょう。",
    "反り腰に注意！腰に負担がかかっています。少しお腹をへこませるイメージで。"
];

const rightSlightMessages = [
    "少し右に傾いているようです。左の脇腹を意識してみて。",
    "体が少し右に流れています。中心に戻すイメージで。"
];
const rightNoticeableMessages = [
    "体が右に大きく傾いています！まっすぐを意識しましょう！",
    "右肩が下がっていませんか？両肩の高さを揃えてみましょう。"
];

const leftSlightMessages = [
    "少し左に傾いているようです。右の脇腹を意識してみて。",
    "体が少し左に流れています。中心に戻すイメージで。"
];

const leftNoticeableMessages = [
    "体が左に大きく傾いています！中心を意識しましょう！",
    "左肩が下がっていませんか？両肩の高さを揃えてみましょう。"
];

function getRandomMessage(messagesArray) {
    const randomIndex = Math.floor(Math.random() * messagesArray.length);
    return messagesArray[randomIndex];
}

function checkPosture(y, x) {
    if (referenceY === null || referenceX === null) {
        updateMessageDisplay("基準未設定。「A. 今の姿勢を覚える」で設定してください。", "warning");
        return;
    }
    const diffY = y - referenceY;
    const diffX = x - referenceX;

    const thresholdY_fwd_slight = 7.0;
    const thresholdY_fwd_noticeable = 15.0;
    const thresholdY_bwd_slight = -7.0;
    const thresholdY_bwd_noticeable = -15.0;
    const thresholdX_right_slight = 5.0;
    const thresholdX_right_noticeable = 10.0;
    const thresholdX_left_slight = -5.0;
    const thresholdX_left_noticeable = -10.0;

    let messages = [];
    let messageType = "success";

    if (diffY > thresholdY_fwd_noticeable) {
        messages.push(getRandomMessage(forwardNoticeableMessages));
        messageType = "error";
    } else if (diffY > thresholdY_fwd_slight) {
        messages.push(getRandomMessage(forwardSlightMessages));
        if (messageType !== "error") messageType = "warning";
    } else if (diffY < thresholdY_bwd_noticeable) {
        messages.push(getRandomMessage(backwardNoticeableMessages));
        messageType = "error";
    } else if (diffY < thresholdY_bwd_slight) {
        messages.push(getRandomMessage(backwardSlightMessages));
        if (messageType !== "error") messageType = "warning";
    }

    if (diffX > thresholdX_right_noticeable) {
        messages.push(getRandomMessage(rightNoticeableMessages));
        messageType = "error";
    } else if (diffX > thresholdX_right_slight) {
        messages.push(getRandomMessage(rightSlightMessages));
        if (messageType !== "error") messageType = "warning";
    } else if (diffX < thresholdX_left_noticeable) {
        messages.push(getRandomMessage(leftNoticeableMessages));
        messageType = "error";
    } else if (diffX < thresholdX_left_slight) {
        messages.push(getRandomMessage(leftSlightMessages));
        if (messageType !== "error") messageType = "warning";
    }

    if (messages.length > 0) {
        updateMessageDisplay(messages.join(" "), messageType);
    } else {
        updateMessageDisplay(getRandomMessage(goodPostureMessages), "success");
    }
}

// --- Initial Setup ---
function resetUIAndState() {
    isConnected = false;
    isMeasuring = false;
    isFeedbackActive = false;
    referenceY = null;
    referenceX = null;
    currentY = 0.0;
    currentX = 0.0;
    currentSensorType = null;

    logStatus('ページの準備ができました。「ペアリング」ボタンを押してください。', 'info');
    updateMessageDisplay('---', 'info');

    if (sensorIdDisplay) sensorIdDisplay.textContent = '---';
    if (yAngleDisplay) yAngleDisplay.textContent = '---';
    if (xAngleDisplay) xAngleDisplay.textContent = '---';
    if (refYDisplay) refYDisplay.textContent = '---';
    if (refXDisplay) refXDisplay.textContent = '---';

    updateButtonStates();
}

document.addEventListener('DOMContentLoaded', () => {
    const allRequiredElements = pairButton && disconnectButton && statusMessages &&
                                sensorIdDisplay && yAngleDisplay && xAngleDisplay &&
                                postureChartCanvas && calibrateButton && refYDisplay &&
                                refXDisplay && startMeasurementButton && endMeasurementButton &&
                                feedbackOnButton && feedbackOffButton && messageDisplay &&
                                scrollTopButton;

    if (!allRequiredElements) {
        console.error("ページ上の必須HTML要素のいずれかが見つかりませんでした。HTMLのIDを確認してください。");
        if (statusMessages) statusMessages.textContent = "ページ初期化エラー。HTMLのIDを確認してください。";
    }

    if (postureChartCanvas) {
        initializeChart();
    } else {
        console.warn("グラフ用のCanvas要素 'postureChart' がHTML内に見つかりませんでした（初期化スキップ）。");
    }
    resetUIAndState();

    if (scrollTopButton) {
        window.onscroll = function() {
            if (document.body.scrollTop > 100 || document.documentElement.scrollTop > 100) {
                scrollTopButton.style.display = "block";
            } else {
                scrollTopButton.style.display = "none";
            }
        };
        scrollTopButton.onclick = function() {
            window.scrollTo({top: 0, behavior: 'smooth'});
        };
    }
});