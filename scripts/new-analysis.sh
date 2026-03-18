#!/bin/bash
set -euo pipefail
if [ $# -eq 0 ]; then
  echo "使い方: new-analysis <プロジェクト名>"
  exit 1
fi
PROJECT_NAME="$1"
DATE=$(date +%Y%m%d)
DIR="$HOME/projects/${DATE}_${PROJECT_NAME}"
if [ -d "$DIR" ]; then
  echo "エラー: $DIR は既に存在します"
  exit 1
fi
mkdir -p "$DIR"/{raw,scripts,results,logs}
cat << EOF > "$DIR/README.md"
# ${PROJECT_NAME}
作成日: ${DATE}
作成者: $(whoami)
## 目的

## データソース

## 解析手順

## 結果の要約

EOF
cat << EOF > "$DIR/CLAUDE.md"
# ${PROJECT_NAME}
## 概要

## conda環境
\`\`\`bash
conda activate bioinfo
\`\`\`
## 解析ステップ

## 命名規則
- スクリプト: NN_ステップ名.sh
- 結果: results/NN_ステップ名/YYYYMMDD_HHMMSS_ファイル名
- ログ: logs/NN_ステップ名_YYYYMMDD_HHMMSS.log
EOF
echo "✅ $DIR"
echo "  cd $DIR && tmux new -s $PROJECT_NAME && claude"
