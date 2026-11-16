-- T6/D12：用户配置迁入 data/settings.json，本表只承载运行时台账，
-- 改名为 runtime_state 使表名与内容一致。
-- 旧配置行由 app/settings/document.py 的启动导入读取并删除（纯 SQL 无法写文件）。
ALTER TABLE app_setting RENAME TO runtime_state;
