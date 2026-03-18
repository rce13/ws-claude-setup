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
