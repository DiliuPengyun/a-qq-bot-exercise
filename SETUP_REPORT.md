# NapCat + NcatBot + Agent 项目

## 环境配置

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## 配置

1. `agent/.env` 填入 DeepSeek API Key 和硅基流动 API Key
2. 复制 `config.example.yaml` 为本地 `config.yaml`，再填入 `ws_token`、`bot_uin` 和 `root` 等本机配置

## 启动

需要三个进程（NapCat 先启动）：

```bash
# 1. NapCat（双击 launcher.bat，扫码登录）

# 2. Agent
python agent/agent.py

# 3. Adapter
python adapter.py
```
