# ws-claude-setup

ktomo-ws (研究用ワークステーション) での Claude Code 設定ファイル。

## ファイル
- `CLAUDE.md` — グローバルルール（出力規則、図作成ルール、Agent活用、容量管理等）
- `settings.json` — hooks、プラグイン、環境変数の設定

## 主な設定内容
- セッション終了時のラボノート自動生成（Stop hook + CLAUDE.md指示）
- 中間産物のアーカイブ自動整理
- Agent Team優先利用の方針
- 図作成ルール（Arial/Helvetica、Illustrator編集可能PDF、軸範囲最適化）
- conda環境保護、SSD容量管理
