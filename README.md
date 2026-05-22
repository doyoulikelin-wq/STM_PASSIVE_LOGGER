# STM Experimenter Agent — Passive Logger (Phase 0)

只读、不写硬件的数据采集层。负责：

1. 通过 Nanonis TCP Programming Interface 读取 Bias / Current / Z / Scan 状态。
2. 监听 Nanonis 保存目录中的 `.sxm` 文件，解析头部参数和通道。
3. 周期性轮询信号，写入 SQLite + JSONL（双写、可审计）。
4. 生成 preview PNG，供后续标注 UI 使用。
5. 全程不发送任何 `*_Set`、`Pulse`、`Approach`、`Motor`、`TipShaper` 等写命令。

## 快速开始

```powershell
# 在 d:\STM 下
python -m pip install -e .\stm_experimenter_agent
python -m stm_experimenter_agent.cli start `
    --sample sampleA --operator linye `
    --watch-dir "C:\Nanonis Data" `
    --poll-hz 1.0
```

按 `Ctrl+C` 退出会优雅关闭采集线程并 flush 全部缓存。

## 目录布局

```
stm_experimenter_agent/
  config/                YAML 配置（端口、限值、标签 schema、动作空间）
  nanonis_driver/        raw TCP + 只读 client wrapper
  data_collection/       session/signal/scan logger + sxm parser + writer
  cli.py                 进程入口
tests/                   离线单元测试，不依赖 Nanonis 在线
data/                    运行时生成（sessions/, scans/, previews/）
```

## 安全约束

- `nanonis_driver.client.NanonisReadOnlyClient` 仅暴露 `*_Get` 方法。
- 任何尝试调用写方法的代码路径会抛 `WriteOperationNotAllowed`。
- raw 协议 fallback 仅用于版本/状态探测，不发送 `Set` 命令体。
