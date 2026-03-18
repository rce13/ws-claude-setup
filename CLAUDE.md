# ktomo-ws 共通ルール

## マシン
ktomo-ws: Xeon6 48C/96T, 512GB RAM, RTX 5090, Ubuntu 24.04, CUDA 13.0
管理者: 東宮(ytongu) / 利用者: 丸山(ktomo)

## 絶対ルール
- 共通conda環境(bioinfo, umi_env, nanopore, rstudio, singlecell)を変更しない
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

## 容量管理
- 中間ファイル(未ソートBAM等)はこまめに削除
- 完了後: `archive-analysis .` でNAS保存＋SSD解放
- 確認: `df -h /home/ktomo` / `du -sh ~/projects/*`

## conda
試したいツール → `conda create -n my_test python=3.11`（使用後は削除）

## セッション運用
- タスク間で `/clear` してコンテキストをリセットする
- 同じ修正を2回指摘したら `/clear` でやり直す
- 終了時に `/lab-notebook` でラボノート生成

## コンパクション時に保持
- 修正済みファイル一覧
- 実行コマンドとその結果
- 現在の解析プラン
- 未解決エラー
