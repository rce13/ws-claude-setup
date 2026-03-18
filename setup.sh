#!/bin/bash
# ktomo-ws Claude Code 一括セットアップ
# WSのターミナルにこのスクリプト全体をコピペして実行してください
set -euo pipefail

echo "=== ktomo-ws Claude Code セットアップ開始 ==="

# 1. ディレクトリ作成
mkdir -p ~/projects ~/lab-notebooks ~/.claude/plugins
echo "✅ ディレクトリ作成"

# 2. グローバル CLAUDE.md
cat << 'CLAUDEMD' > ~/.claude/CLAUDE.md
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
CLAUDEMD
echo "✅ ~/.claude/CLAUDE.md"

# 3. .claudeignore
cat << 'EIGNORE' > ~/.claudeignore
# 大容量バイナリ
*.fastq
*.fastq.gz
*.fq
*.fq.gz
*.bam
*.bai
*.sam
*.cram
*.sra
*.bcf
*.vcf.gz

# 参照ゲノム・インデックス
*.fa
*.fasta
*.fai
*.bt2
*.nhr
*.nin
*.nsq
*.pdb

# 中間・キャッシュ
*.tmp
*.temp
*.pyc
__pycache__/

# conda/仮想環境
.conda/
.venv/
envs/

# NAS（巨大）
/mnt/nas/

# システム
.git/
node_modules/
EIGNORE
echo "✅ ~/.claudeignore"

# 4. Hooks (settings.json にマージ)
# 既存のsettings.jsonがある場合はhooksだけ追加
if [ -f ~/.claude/settings.json ]; then
  # 既存ファイルにhooksがなければ追加
  if ! grep -q '"hooks"' ~/.claude/settings.json; then
    python3 << 'PYEOF'
import json
with open("/home/ytongu/.claude/settings.json" if __import__("os").path.exists("/home/ytongu/.claude/settings.json") else "/home/ktomo/.claude/settings.json", "r") as f:
    config = json.load(f)

config["hooks"] = {
    "SessionStart": [{
        "hooks": [{
            "type": "command",
            "command": "echo \"=== ktomo-ws ===\"; echo \"SSD: $(df -h /home 2>/dev/null | tail -1 | awk '{print $4}')\"; echo \"NAS: $(df -h /mnt/nas 2>/dev/null | tail -1 | awk '{print $4}' || echo 'not mounted')\""
        }]
    }],
    "Stop": [{
        "hooks": [{
            "type": "command",
            "command": "echo '💡 /lab-notebook を実行しましたか？'"
        }]
    }]
}

path = "/home/ytongu/.claude/settings.json" if __import__("os").path.exists("/home/ytongu/.claude/settings.json") else "/home/ktomo/.claude/settings.json"
with open(path, "w") as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
print("✅ hooks を既存settings.jsonに追加")
PYEOF
  else
    echo "⚠️  settings.jsonに既にhooksあり、スキップ"
  fi
else
  cat << 'SETTINGS' > ~/.claude/settings.json
{
  "hooks": {
    "SessionStart": [{
      "hooks": [{
        "type": "command",
        "command": "echo \"=== ktomo-ws ===\"; echo \"SSD: $(df -h /home 2>/dev/null | tail -1 | awk '{print $4}')\"; echo \"NAS: $(df -h /mnt/nas 2>/dev/null | tail -1 | awk '{print $4}' || echo 'not mounted')\""
      }]
    }],
    "Stop": [{
      "hooks": [{
        "type": "command",
        "command": "echo '💡 /lab-notebook を実行しましたか？'"
      }]
    }]
  }
}
SETTINGS
  echo "✅ ~/.claude/settings.json (hooks)"
fi

# 5. new-analysis コマンド
sudo tee /usr/local/bin/new-analysis > /dev/null << 'NEWANALYSIS'
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
NEWANALYSIS
sudo chmod +x /usr/local/bin/new-analysis
echo "✅ new-analysis"

# 6. archive-analysis コマンド
sudo tee /usr/local/bin/archive-analysis > /dev/null << 'ARCHIVE'
#!/bin/bash
set -euo pipefail
NAS_ARCHIVE="/mnt/nas/archive/$(whoami)"
if [ $# -eq 0 ]; then
  echo "使い方: archive-analysis <プロジェクトディレクトリ>"
  exit 1
fi
PROJECT_DIR="$(realpath "$1")"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
if [ ! -d "$PROJECT_DIR" ]; then
  echo "エラー: $PROJECT_DIR が見つかりません"
  exit 1
fi
du -sh "$PROJECT_DIR"/* 2>/dev/null || true
mkdir -p "$NAS_ARCHIVE"
ARCHIVE_FILE="$NAS_ARCHIVE/${PROJECT_NAME}.tar.gz"
echo "📦 アーカイブ中（raw/を除く）..."
tar -czf "$ARCHIVE_FILE" \
  -C "$(dirname "$PROJECT_DIR")" \
  --exclude="*/raw" \
  --exclude="*.bam" --exclude="*.fastq" --exclude="*.fastq.gz" \
  --exclude="*.fq" --exclude="*.fq.gz" --exclude="*.sam" \
  "$PROJECT_NAME"
echo "✅ 保存: $ARCHIVE_FILE ($(du -sh "$ARCHIVE_FILE" | cut -f1))"
echo ""
read -p "raw/と大容量中間ファイルを削除しますか？ (y/N): " confirm
if [ "$confirm" = "y" ]; then
  rm -rf "$PROJECT_DIR/raw"
  find "$PROJECT_DIR" \( -name "*.bam" -o -name "*.sam" -o -name "*.fastq" -o -name "*.fastq.gz" -o -name "*.fq" -o -name "*.fq.gz" \) -delete
  echo "✅ クリーンアップ完了"
fi
ARCHIVE
sudo chmod +x /usr/local/bin/archive-analysis
echo "✅ archive-analysis"

# 7. プラグイン: claude-ntfy
if [ ! -d ~/.claude/plugins/claude-ntfy ]; then
  git clone --depth 1 https://github.com/rce13/claude-ntfy-plugin.git ~/.claude/plugins/claude-ntfy
  echo "✅ claude-ntfy（Claude Code内で /setup-ntfy を実行してください）"
else
  echo "✅ claude-ntfy（インストール済み）"
fi

# 8. プラグイン: claude-lab-notebook
if [ ! -d ~/.claude/plugins/claude-lab-notebook ]; then
  git clone --depth 1 https://github.com/rce13/claude-lab-notebook.git ~/.claude/plugins/claude-lab-notebook
  echo "✅ claude-lab-notebook"
else
  echo "✅ claude-lab-notebook（インストール済み）"
fi

# 9. 完了
echo ""
echo "=== セットアップ完了 ==="
echo ""
echo "設置済み:"
echo "  ~/.claude/CLAUDE.md         グローバルルール"
echo "  ~/.claude/settings.json     hooks（ディスク状況表示 + ラボノートリマインダー）"
echo "  ~/.claudeignore             大容量ファイル除外"
echo "  ~/.claude/plugins/          ntfy + lab-notebook"
echo "  /usr/local/bin/             new-analysis + archive-analysis"
echo ""
echo "Claude Codeでの追加手順:"
echo "  /setup-ntfy                               # 通知設定"
echo "  /plugin marketplace add https://github.com/anthropics/life-sciences.git"
echo "  /plugin install single-cell-rna-qc@life-sciences"
echo "  /plugin install scvi-tools@life-sciences"
echo "  /plugin install nextflow-development@life-sciences"
