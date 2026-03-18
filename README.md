# ws-claude-setup

バイオインフォマティクス解析WS用 Claude Code 環境セットアップ。
トランスクリプトーム(RNA-seq, scRNA-seq)、ATAC-seq、GWAS解析と論文執筆のための環境。

## セットアップ

    git clone https://github.com/rce13/ws-claude-setup.git ~/ws-claude-setup
    cd ~/ws-claude-setup
    bash setup.sh

## 更新

    cd ~/ws-claude-setup && git pull && bash setup.sh

## プラグイン一覧

### 自動インストール (setup.sh)
| プラグイン | 用途 |
|-----------|------|
| [claude-ntfy](https://github.com/rce13/claude-ntfy-plugin) | 5分放置でスマホ通知 |
| [claude-lab-notebook](https://github.com/rce13/claude-lab-notebook) | ラボノート自動生成 |

### 公式マーケットプレイス
| プラグイン | 用途 |
|-----------|------|
| commit-commands | /commit, /commit-push-pr でgit管理 |
| claude-md-management | CLAUDE.md品質監査・自動更新 |

### Life Sciences マーケットプレイス
| プラグイン | 用途 |
|-----------|------|
| pubmed | PubMed文献検索 |
| single-cell-rna-qc | scRNA-seq QC (scverse準拠) |
| scvi-tools | 深層学習シングルセル解析 (scVI/scANVI/PeakVI) |
| nextflow-development | nf-core (RNA-seq, ATAC-seq, variant calling) |

### Scientific Skills (K-Dense)
170+ スキル: GWAS Catalog, gnomAD, GTEx, GEO, scanpy, BioPython等

### matsengrp (ローカル手動配置)
| エージェント | 用途 |
|-------------|------|
| scientific-tex-editor | LaTeX論文の科学的編集 |
| tex-grammar-checker | LaTeX文法チェック |
| tex-verb-tense-checker | 動詞時制一貫性チェック |
| topic-sentence-stickler | トピックセンテンス改善 |
| snakemake-pipeline-expert | Snakemakeパイプライン支援 |
| journal-submission-checker | 投稿前チェック |
| pdf-proof-reader | PDF校正 |
| clean-code-reviewer | コードレビュー |

## 設定ファイル
| ファイル | 役割 |
|---------|------|
| CLAUDE.md | グローバルルール（タイムスタンプ、容量管理等） |
| .claudeignore | FASTQ/BAM等の大容量ファイル除外 |
| hooks/settings.json | セッション開始時ディスク表示、終了時リマインダー |
| scripts/new-analysis.sh | プロジェクト雛形作成 |
| scripts/archive-analysis.sh | NASアーカイブ+SSD解放 |

## マーケットプレイス追加手順 (Claude Code内)

    /plugin marketplace add https://github.com/anthropics/life-sciences.git
    /plugin marketplace add matsengrp/plugins
    /plugin marketplace add K-Dense-AI/claude-scientific-skills

## Auto Memory

Claude Code v2.1.59+ では MEMORY.md がセッション間の学習を自動保存。
CLAUDE.md にはルールだけ書き、デバッグパターン等は Auto Memory に任せる。
