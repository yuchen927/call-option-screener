# 📈 Call Option Screener

自動化美股選擇權選股腳本，包含：
- 技術面指標（RSI、MACD、布林通道）
- 基本面成長（EPS / Revenue）
- IV Rank 過濾
- Delta / Theta 條件
- 自動上傳到 Google Sheets
- GitHub Actions 每日排程執行

## 🚀 如何使用

1. 建立 `GOOGLE_CREDENTIALS_JSON` secrets（貼入 credentials.json）
2. 修改 `call_option_screener.py` 為完整腳本
3. Push 到 GitHub，自動執行每日選股
