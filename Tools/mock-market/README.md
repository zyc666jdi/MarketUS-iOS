# Mock Market Server

本地模拟行情服务：REST + WebSocket，**无 API key、无外网、结果确定**。
契约与场景设计见 [`docs/data-sources.md`](../../docs/data-sources.md)。

## 启动

```bash
# Docker（推荐）
cd Tools/mock-market
docker compose up --build

# 或本地 Python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

健康检查：

```bash
curl -s localhost:8080/healthz
# {"status":"ok","scenario":"default","seed":42}
```

## 端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/healthz` | 健康检查 |
| GET | `/v1/symbols` | 标的列表（16 个美股/ETF） |
| GET | `/v1/quotes?symbols=AAPL,MSFT` | 最新报价 |
| GET | `/v1/candles?symbol=AAPL&interval=1m&limit=30` | K 线 |
| GET | `/v1/news?symbol=AAPL` | 模拟新闻 |
| POST | `/v1/_scenario` | 切换场景（仅本地/测试） |
| WS | `/v1/stream` | 实时 tick |

```bash
curl -s "localhost:8080/v1/quotes?symbols=AAPL,MSFT" | jq

# WebSocket（需要 websocat 或 wscat）
websocat ws://localhost:8080/v1/stream
{"type":"subscribe","symbols":["AAPL","MSFT"]}
```

## 场景

```bash
curl -s -X POST localhost:8080/v1/_scenario \
  -H 'content-type: application/json' \
  -d '{"scenario":"bull"}'
```

| 场景 | 行为 |
| --- | --- |
| `default` | 确定性随机游走 |
| `bull` / `bear` | 强制正/负涨跌 |
| `volatile` | 涨跌放大 3 倍 + 高频心跳 |
| `stale` | 只发心跳，不发 tick（客户端应显示「延迟」） |
| `disconnect` | WebSocket 连接 2 秒后关闭（测试重连） |
| `slow` | 每个 HTTP 请求延迟 2 秒 |
| `error500` | HTTP 500 |
| `rate429` | HTTP 429 + `Retry-After: 1` |
| `malformed` | 返回非法 JSON（测试解码健壮性） |
| `empty` | 空列表（测试空态） |

## 注意

- 仅供开发与演示，数据为**模拟**，不得用于任何投资决策。
- 场景状态保存在进程内存，重启即恢复默认。
- 该服务不参与 App 的发布构建，也不需要密钥。
