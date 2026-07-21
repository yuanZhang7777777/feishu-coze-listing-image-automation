# Feishu Coze Listing Image Automation

Shopee Listing 自动出图系统：飞书按钮快速提交任务，中转服务异步执行 Coze 工作流、轮询结果、归档 OSS，并把标准商品图和 Listing 图片回写飞书。

## 目录

- `bridge/`：Flask/Gunicorn 中转服务、systemd 单元和测试
- `coze/`：已清除插件 Key 的 Coze 工作流导出包
- `docs/HANDOFF.md`：当前生产状态、接口契约和运维交接
- `docs/specs/`：系统与工作流设计
- `docs/plans/`：Coze 节点实施和演示指南

## 本地验证

```powershell
cd bridge
python -m unittest discover -s tests -p test_app.py -v
```

真实凭证只放在被忽略的 `config/*.local.env` 或服务器 `/etc/listing-bridge.env` 中，禁止提交 Token、Secret、Bridge Key 或云访问密钥。
