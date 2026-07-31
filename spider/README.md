# 百炼智能体爬虫

从百炼平台批量抓取智能体提示词的独立脚本（备用方案）。

## 使用方式

1. 启动 Edge 调试模式：双击 `start_edge.bat`
2. 在 Edge 中登录百炼控制台
3. 运行爬虫：`python bailian_spider.py`

## 依赖

```bash
pip install selenium>=4.0.0
```

## 输出

- `agents.json`: 所有抓取到的智能体数据
- `debug/`: 每个智能体的提示词快照
