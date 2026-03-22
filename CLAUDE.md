# ktomo-ws 共通ルール

## マシン
ktomo-ws: Xeon6 48C/96T, 512GB RAM, RTX 5090, Ubuntu 24.04, CUDA 13.0
管理者: 頓宮Josh(ytongu) / 利用者: 笠原Susie(ktomo)

## 絶対ルール
- 共通conda環境(bioinfo, umi_env, nanopore, rstudio, singlecell, boltz2, brpro, tage)を変更しない
- SSD容量を常に意識する。大容量データは作業中のみSSDに置く
- 長時間解析は必ずtmux内で実行する

## 出力ルール
- 全産物にタイムスタンプ: `TS=$(date +%Y%m%d_%H%M%S)`
- スクリプトと結果はペアで保存: `scripts/01_xxx.sh` → `results/01_xxx/`
- スクリプト冒頭に目的・入力・出力を記載、`set -euo pipefail`
- ログは `logs/` にタイムスタンプ付きで保存

## ディレクトリ構成
```
~/projects/YYYYMMDD_名前/
├── scripts/   results/   raw/   logs/
├── README.md   CLAUDE.md
```

## 容量管理・クリーンアップ
- 完了後: `archive-analysis .` でNAS保存＋SSD解放
- 確認: `df -h /home/ktomo` / `du -sh ~/projects/*`
- **中間産物は削除ではなくアーカイブ**: `archive/` ディレクトリに移動する（後でまとめて削除可能）
- **アーカイブ構成**: `archive/{解析名}_{YYYYMMDD_HHMMSS}/` に分けて格納
- **スクリプトは上書きしない**: 修正時は新しいタイムスタンプ付きファイルを作成し、旧版はarchiveに移動
- **図の古いバージョン**: 最終版確定時に古いタイムスタンプ版をarchiveに移動
- **自動タイミング**: セッション終了時（Stopフック）に古い中間産物をarchiveへ自動整理する

### アーカイブ構成例
```
archive/
├── 10_final_figures_20260318_180000/
│   ├── boddupalli_scatter_20260318_172607.pdf  (旧版)
│   └── shimizu_scatter_20260318_172803.pdf     (旧版)
├── scripts_20260318_180000/
│   └── 10_final_figures_v1.py                  (旧版スクリプト)
```

## conda
試したいツール → `conda create -n my_test python=3.11`（使用後は削除）

## セッション運用
- タスク間で `/clear` してコンテキストをリセットする
- 同じ修正を2回指摘したら `/clear` でやり直す
- **ラボノート自動生成**: セッション終了時（ユーザーが「終わり」「おわり」「done」等と言ったとき、または長い解析タスクの区切り）にClaude自身がラボノート要約を `logs/lab_notebook_YYYYMMDD_HHMMSS.md` に出力する。内容: 日付、目的、実行内容、結果、生成ファイル一覧、未解決事項

## 図作成ルール
- フォント: Arial or Helvetica、大きめ（軸ラベル12-14pt、タイトル14-16pt、tick 10-12pt）
- PDF: Illustrator編集可能（テキストをアウトライン化しない。matplotlib: `pdf.fonttype: 42`）
- PNG: 300dpi以上
- カラーブラインドフレンドリー
- 軸の範囲をデータに合わせて調整し、無駄な空白を排除する
- スクリプトと図を同じ結果ディレクトリにペアで保存する

## Agent活用ルール

### Agent Team（優先的に使う）
- 独立した複数タスクを並列処理するときはAgent Teamを使う
- 例: データDL担当 / 前処理担当 / 文献調査担当を同時に走らせる
- チームサイズ: 3-5人が最適。それ以上はトークンコスト対効果が悪化
- **ファイル競合を避ける**: 各teammateに異なるファイル/ディレクトリを担当させる
- teammateには十分なコンテキスト（目的・入出力・制約）を渡す（CLAUDE.mdは自動で読み込まれる）
- リード役は実装を始める前にteammateの完了を待つ
- 有効化: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`

### Sub-Agent（単発タスクに使う）
- 冗長な出力が出るタスク（テスト実行、ログ解析）をメインから隔離する
- バックグラウンド実行で長時間タスクをブロックしない
- 調査系にはRead/Grep/Globのみ、実装系にはEdit/Writeも許可
- Agentの入れ子は不可

### 使い分け
| 状況 | 手段 |
|------|------|
| 独立した3+タスクの並列実行 | Agent Team |
| 単発の調査/前処理を隔離 | Sub-Agent (background) |
| 反復修正、対話が必要な作業 | メイン会話で直接 |

### 参考
- https://code.claude.com/docs/en/agent-teams.md
- https://claude.com/blog/how-anthropic-teams-use-claude-code
- https://www.anthropic.com/engineering/building-c-compiler

## コンパクション時に保持
- 修正済みファイル一覧
- 実行コマンドとその結果
- 現在の解析プラン
- 未解決エラー
