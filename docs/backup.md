# 本地备份与恢复

MediX 有两类备份入口：

1. **导出备份**：在历史抽屉点“导出备份”，把完整 JSON 下载到浏览器；适合迁移到另一台机器或外部保存。
2. **本地备份**：由服务端写入 `data/backups/`，可通过“立即备份”主动创建，也可按 `MEDIX_AUTO_BACKUP_HOURS` 周期自动创建；恢复时不需要重新上传文件。

本地备份包含会话、消息、患者档案、健康指标、指标目标、随访提醒、症状、用药与打卡、操作审计，属于敏感健康数据。

## 配置

在项目根目录 `.env` 中配置（留空或 0 表示关闭自动备份）：

```dotenv
# 自动备份间隔小时；0 或留空关闭
MEDIX_AUTO_BACKUP_HOURS=0
# 保留最近几份（默认 7，范围 1–100）
MEDIX_BACKUP_KEEP=7
# 自定义目录（默认 data/backups）
# MEDIX_BACKUP_DIR=data/backups
```

修改后重启服务生效。开启自动备份时，服务启动后会先执行一次，之后按间隔运行。

## 立即备份

Web 端：打开“会诊记录”抽屉，点击“立即备份”。成功后按钮显示“已备份”，首页“数据备份”卡片会更新上次备份时间。

API：

```bash
curl -X POST http://127.0.0.1:8000/api/backups \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MEDIX_ACCESS_TOKEN" \
  -d '{"confirm": true}'
```

`confirm` 必填为 `true`；接口沿用 `MEDIX_BACKUP_KEEP` 保留策略，超过 128MB 的数据会拒绝写入，避免生成无法通过 Web 恢复的文件。成功创建会写入 `session.export` 审计，失败时返回明确错误。

## 恢复

Web 端：在历史抽屉点击“恢复备份”。默认恢复最新一份；存在多份时会列出最近 10 份，按序号选择。恢复前会先调用预检接口校验文件并展示会话、消息、档案、指标、化验与用药数量；校验失败会停止恢复。恢复只做合并：已存在的会话、档案和记录会跳过，不会删除现有数据。

预检 API：

```bash
curl http://127.0.0.1:8000/api/backups/<name>/inspect \
  -H "Authorization: Bearer $MEDIX_ACCESS_TOKEN"
```

返回 `checksum_ok`（无校验和时为 `null`）、`counts`、`total_records` 与 `warnings`；校验失败或备份为空会在 `warnings` 中说明。

API：

```bash
# 列出本地备份（最多 50 份）
curl http://127.0.0.1:8000/api/backups \
  -H "Authorization: Bearer $MEDIX_ACCESS_TOKEN"

# 恢复指定备份（文件名必须是接口返回的名称）
curl -X POST http://127.0.0.1:8000/api/backups/<name>/restore \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MEDIX_ACCESS_TOKEN" \
  -d '{"confirm": true}'
```

恢复流程会校验：

- 文件名必须匹配 `medix-backup-YYYYMMDD-HHMMSS[-n].json`，并解析到备份目录内，拒绝路径穿越。
- 文件不超过 128MB。
- 备份自带 SHA-256 校验和必须匹配，篡改或损坏会返回 400。
- 单条消息不超过 1,000,000 字符，整次导入消息总量不超过 30,000,000 字符。

恢复成功写入 `session.import` 审计，详情包含 `source=backup:<name>`。

## 删除备份

Web 端：在历史抽屉点击“管理备份”，选择最近 10 份中的一份确认删除。删除后备份文件从服务器磁盘移除，无法再恢复。

API：

```bash
curl -X DELETE http://127.0.0.1:8000/api/backups/<name> \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $MEDIX_ACCESS_TOKEN" \
  -d '{"confirm": true}'
```

文件名同样经过路径穿越校验；成功写入 `backup.delete` 审计，文件不存在返回 404。

> **注意**：维护清理（`POST /api/maintenance/purge`）只删除数据库与会诊总结中的旧会诊，**不会**删除历史本地备份。如果清理是为了隐私（例如转手设备），请同时用“管理备份”删除仍含旧数据的备份。

## 安全注意事项

- `data/` 已在 `.gitignore` 中；不要把备份 JSON 提交到 Git 或上传到不可信位置。
- 备份文件写入后会尽量设置为仅当前用户可读（POSIX `0600`）；Windows 的权限行为取决于文件系统。
- 如果设置了 `MEDIX_ACCESS_TOKEN`，备份列表、创建和恢复接口都需要令牌；建议在非本机访问时启用。
- 创建与恢复接口要求 JSON `confirm=true`，避免无请求体的跨站触发。
- 备份不会加密；如设备可能被他人使用，请自行使用磁盘加密或加密归档。
- 恢复是合并操作，不会回滚恢复点之后的记录；需要完整回滚时请先导出当前数据并人工核对。

## 故障排查

| 现象 | 可能原因 | 处理 |
| --- | --- | --- |
| 列表为空 | 自动备份未开启，且从未点“立即备份” | 设置 `MEDIX_AUTO_BACKUP_HOURS` 或点“立即备份” |
| 创建返回 400 | 数据超过 128MB | 清理旧会话/审计后重试，或用“导出备份”保存 |
| 恢复返回 400 校验失败 | 文件被修改或损坏 | 换一份备份；不要手工编辑 JSON |
| 恢复返回 404 | 文件已被保留策略清理 | 用列表接口确认现存文件名 |
| 恢复后记录数没变 | 备份中的记录已存在，按设计跳过 | 查看返回的 `skipped` 字段 |
| 无权限写入 | 备份目录不可写 | 检查 `MEDIX_BACKUP_DIR` 权限或改用默认目录 |

## 相关测试

```bash
python -m pytest tests/test_auto_backup.py tests/test_api_backup_restore.py tests/test_backup_consistency.py tests/test_lifecycle_erasure.py -q
```
