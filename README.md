# 企业微信 × 峰哥 Skill 桥接服务

企业微信消息 → OpenCode 峰哥亡命天涯 Skill → 自动回复

## 架构

```
┌──────────────┐    ┌─────────────────┐    ┌──────────────┐    ┌─────────────┐
│  企业微信 App  │───▶│  Cloudflare      │───▶│  Flask 服务    │───▶│  OpenCode    │
│  (用户发消息)  │    │  Tunnel + DNS    │    │  (加解密/路由)  │    │  + 峰哥 Skill │
└──────────────┘    └─────────────────┘    └──────────────┘    └─────────────┘
                           │                       │
                     fengge.yiyihmm.top        localhost:8080
```

## 前置条件

- Python 3.11+
- Node.js + OpenCode CLI（`npm i -g opencode-ai`）
- 峰哥 Skill 已安装到 `~/.config/opencode/skills/`
- 企业微信管理员权限
- Cloudflare 账号 + 自有域名

## 快速开始

### 1. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`：

```env
WECOM_TOKEN=你的Token
WECOM_ENCODING_AES_KEY=你的EncodingAESKey
WECOM_CORP_ID=你的企业ID
WECOM_SECRET=应用的Secret
WECOM_AGENT_ID=应用的AgentId
OPENCODE_MODEL=deepseek/deepseek-v4-pro
PORT=8080
```

企业微信后台 → 应用管理 → 自建应用 → 接收消息 获取以上信息。

### 2. 安装依赖

```bash
pip install flask python-dotenv pycryptodome requests
```

### 3. 启动服务

```bash
python server.py
```

### 4. 配置内网穿透（Cloudflare Tunnel）

```bash
# 安装 cloudflared
winget install Cloudflare.cloudflared

# 认证（打开浏览器授权）
cloudflared tunnel login

# 创建隧道
cloudflared tunnel create fengge

# 配置 DNS
cloudflared tunnel route dns fengge fengge.yiyihmm.top

# 启动隧道
cloudflared tunnel --config config.yml run fengge
```

`config.yml` 示例：

```yaml
tunnel: <tunnel-id>
credentials-file: C:\Users\<user>\.cloudflared\<tunnel-id>.json

ingress:
  - hostname: fengge.yiyihmm.top
    service: http://localhost:8080
  - service: http_status:404
```

### 5. 域名配置

1. Cloudflare 添加站点 → 连接域名
2. 域名注册商改 NS 为 Cloudflare 提供的 NS 地址
3. 等待 DNS 传播（几分钟到几小时）

### 6. 企业微信回调配置

1. 企业微信后台 → 应用管理 → 自建应用 → 接收消息
2. URL：`https://fengge.yiyihmm.top/callback`
3. Token：自定义填写
4. EncodingAESKey：点击随机生成
5. 企业可信 IP：添加服务器出口 IP
6. 保存验证

## 端点

| 路径 | 方法 | 说明 |
|------|------|------|
| `/ping` | GET | 服务健康检查 |
| `/callback` | GET/POST | 企微消息回调（URL 验证 + 接收消息） |
| `/test` | POST | 本地测试，`{"message": "你的问题"}` |

## 本地测试

```bash
curl -X POST http://127.0.0.1:8080/test \
  -H "Content-Type: application/json" \
  -d '{"message":"我女朋友要跟我分手怎么办"}'
```

## 服务管理

```powershell
# 查看进程
Get-Process python, cloudflared

# 停止服务
Get-Process python | Stop-Process -Force
Get-Process cloudflared | Stop-Process -Force

# 后台启动
Start-Process python -ArgumentList "server.py" -WorkingDirectory "D:\project\skill\wechat-bridge" -WindowStyle Hidden
Start-Process cloudflared -ArgumentList "tunnel --config config.yml run fengge" -WindowStyle Hidden

# 查看日志
Get-Content $env:TEMP\wecom-callback.log -Tail 20 -Wait
```

## 消息流程

```
1. 用户在企微发消息
2. 企微 POST 加密消息到 /callback
3. Flask 解密 → 提取文本
4. 调用 opencode run --model deepseek/deepseek-v4-pro "用峰哥视角回答：..."
5. 异步推送回复到企微
```

## 注意事项

- deepseek 模型回复需 10-30 秒，企微回调超时 5 秒，采用异步推送模式
- 企业可信 IP 白名单必须包含服务器出口 IP，否则主动推送失败
- 免费版 Cloudflare Tunnel 每次重启域名可能变化，建议使用命名隧道
