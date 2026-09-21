# Intentionally Retrofitted BannerAgency

OpenAIのLLMを搭載した複数の専門エージェントを指揮者（Orchestrator）がツールとして順次呼び出し、協調して高品質な広告バナー（**HTML / SVG / 高解像度PNG**）を自動生成するマルチエージェント制作システムです。

* 本システムは、BannerAgencyを実装しつつも、意図的に画像生成AI（T2I）の利用を回避し、既存アセットのメタデータ検索および洗練されたCSS/SVGレイアウトを活用した**ベクター＆HTMLファースト**のバナー生成パイプラインを実装しています。

---

## 1. システム概要とアーキテクチャ

本システムは、論文 **"BannerAgency: Advertising Banner Design with Multimodal LLM Agents"** の設計思想に基づきつつも、**画像生成AI（T2I）の利用を回避するという特定の環境下**でバナー制作を行うためのマルチエージェントシステムです。
既存アセットのメタデータ検索および洗練されたCSS/SVGレイアウトを活用した**ベクター＆HTMLファースト**のバナー生成パイプラインを実装しています。
Human-in-the-loopとして、人がAIエージェントの動作を監視するだけでなく、自らバナーを修正・制作する機能を設けています。

```mermaid
flowchart TD
    User([ユーザー / 入力]) --> Orchestrator[指揮者: Banner Orchestrator]

    subgraph LLM_Tools [指揮者がツールとして扱う専門LLMエージェント群]
        Strategist[1. 企画立案者: Strategist<br/>目的・ターゲット・トーン・コピー要件策定]
        BgDesigner[2. 背景選択: Background Designer<br/>メタデータ検索 / CSSフォールバック]
        FrontDesigner[3. コピー & 素材選定: Front Designer<br/>キャッチコピー作成・ロゴ/装飾選定・初期レイアウト]
        FrontRefiner[4. 配置調整: Front Refiner<br/>要素重なり解消・バウンディングボックス最適化]
        Developer[5. 開発 & レンダラー: Developer<br/>HTML/SVG構築 & Playwright PNGレンダリング]
    end

    Orchestrator -->|create_banner_strategy| Strategist
    Strategist -->|BannerRequirements| Orchestrator

    Orchestrator -->|select_banner_background| BgDesigner
    BgDesigner -->|BannerBackgroundSelection| Orchestrator

    Orchestrator -->|plan_banner_front| FrontDesigner
    FrontDesigner -->|BannerForegroundPlan (Draft)| Orchestrator

    Orchestrator -->|refine_banner_front| FrontRefiner
    FrontRefiner -->|BannerForegroundPlan (Refined)| Orchestrator

    Orchestrator -->|develop_banner| Developer
    Developer -->|BannerDeveloperOutput| Orchestrator

    Orchestrator --> FinalOutput([最終出力: HTML + SVG + PNG + レビュー管理])
```

### 各エージェントの役割
1. **指揮者 (`orchestrator`)**: ユーザーからのバナー作成依頼を受け取り、専門エージェントをツールとして順次呼び出し、各イテレーションの生成結果を統合。
2. **企画立案者 (`strategist`)**: 広告の目的、ターゲット層、トーン＆マナー、必須コピー要件を定義。
3. **背景画像選択 (`background_designer`)**: `assets/backgrounds` 内のメタデータJSONを検索して最適な背景画像を選定（画像がない場合は洗練されたCSSグラデーションを設計）。
4. **キャッチコピー＆素材選定 (`front_designer`)**: メインコピー（キャッチコピー）、サブコピー、CTAボタン、ロゴ選定、装飾バッジ選定、初期レイアウトを策定。
5. **素材配置調整 (`front_refiner`)**: 各要素の座標・サイズ・重なり（overlap）を検知・解消し、バウンディングボックス内に最適配置。
6. **開発・レンダラー (`developer`)**: エディタブルなHTML/CSSバナーおよびSVGバナーを出力し、Playwrightによる高解像度PNG画像化を実行。

---

## 2. 主な特徴

- **マルチフォーマット出力 (HTML / SVG / PNG)**:
  Web表示用のHTMLだけでなく、ベクター編集可能なSVG、プレビュー・配信用の高解像度PNGを同時に自動出力します。
- **Base64 Data URI による完全なポータビリティ**:
  ローカル画像・ロゴ・装飾アセットはすべて Base64 Data URI として HTML / SVG にインライン埋め込みされるため、CORS制限や相対パス崩れを起こさずスタンドアロンで閲覧可能です。
- **2パス衝突解決ガードレール**:
  ロゴと装飾バッジの重なり防止、縦方向に並ぶテキスト要素（メインコピー、サブコピー、CTA）の物理的な文字被りをレンダラー内部で自動検知・解消。
- **直近編集ファイルの自動検出 & スマートな `Render`**:
  出力された HTML や SVG をエディタで直接手動編集した後、対話メニューで `Render` を実行するだけで、**直近で更新されたファイルを自動検出**して即座に PNG プレビューを再生成します。ただし、**デフォルトではrequirements.txt内でplaywrightが有効になっていない**ため、PNGのレンダリングを行いたい場合は有効化して下さい。
- **本Githubに用意したサンプルアセット**:
  セール、テック、オーガニック、ラグジュアリーの用途に対応した背景（5種）、ロゴ（4種）、装飾バッジ（4種）を格納しています。

---

## 3. セットアップ手順

### 1. 仮想環境の作成とライブラリインストール
```bash
# 仮想環境の作成
python3 -m venv .venv

# 仮想環境の有効化
source .venv/bin/activate  # macOS / Linux
# Windowsの場合: .venv\Scripts\activate

# 依存ライブラリのインストール
pip install -r requirements.txt
```

### 2. 環境変数（OpenAI APIキー）の設定
`.env.example` をコピーして `.env` を作成し、OpenAI APIキーを記載します。
```bash
cp .env.example .env
```

`.env` の内容：
```env
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxx
# オプション: モデル指定 (デフォルトは gpt-4o)
# OPENAI_MODEL=gpt-4o
# オプション: PlaywrightによるPNG画像化のデフォルトON/OFF (true/false)
# ENABLE_PLAYWRIGHT_RENDER=false
```

### 3. （推奨）Playwright ブラウザのインストール
PNGプレビュー画像の自動レンダリングを行うためにインストールします（HTML/SVGファイルのみ利用する場合はスキップ可能です）。
```bash
playwright install chromium
```

---

## 4. 実行方法

### 対話型モードでの実行
```bash
python src/main.py
```
実行すると、バナーの目的、サイズ、PlaywrightによるPNG画像生成の有無を入力後、マルチエージェントによる自動生成が開始されます。

#### レビュー対話アクション
各イテレーション完了後、以下のレビュー操作が可能です：

| アクション | 説明 |
| :--- | :--- |
| **`OK` / `approve`** | 現在のバナーを承認し、制作を完了します。 |
| **`Comment` / `c`** | 修正指示（例：「コピー同士のマージンを広げて」「CTAボタンを黄色に」など）を入力し、エージェントによる次イテレーション（再設計）を実行します。 |
| **`Render` / `r`** | **直近で編集された HTML または SVG ファイルを自動検出**し、LLMを介さずに PNG プレビューを即座に再生成します。（`render banner_iter2.svg` のようにファイル名を直接指定することも可能） |
| **`Exit` / `quit`** | バナー制作を中断・終了します。 |

### コマンドライン引数での直接実行
```bash
# 基本実行 (対話型レビューモード)
python src/main.py --objective "春の新生活応援キャンペーン セールバナー" --width 300 --height 250

# PlaywrightによるPNGプレビューも有効化
python src/main.py --objective "セキュリティソフト セールバナー" --width 300 --height 250 --render-png

# 非対話型（1イテレーションで自動終了）
python src/main.py --objective "AI SaaS ツールの新規ユーザー獲得" --non-interactive
```

---

## 5. 出力ファイル構造

生成された成果物は `artifacts/output/` に保存されます。

```text
artifacts/
├── output/
│   ├── banners/
│   │   └── <banner_id>/              # セッション固有の出力フォルダ（例: 20260921_122325）
│   │       ├── banner.html           # 最新イテレーションのHTMLバナー（ブラウザで再読み込み可能）
│   │       ├── banner.svg            # 最新イテレーションのSVGバナー
│   │       ├── banner.png            # 最新イテレーションの高解像度PNGプレビュー
│   │       ├── banner_iter1.html     # イテレーション1の履歴
│   │       ├── banner_iter1.svg
│   │       ├── banner_iter1.png
│   │       ├── banner_iter2.html     # イテレーション2の履歴
│   │       └── ...
│   └── memory/
│       └── <banner_id>.jsonl         # 各エージェントの思考ログ・レビューフィードバック履歴
└── graph/
    └── banner_graph_<banner_id>.md   # Mermaid形式のエージェント実行フロー可視化グラフ
```

---

## 6. アセットの構成と追加方法

`assets/` ディレクトリ配下に、画像ファイル（SVG / PNG / JPG）と同名の `.json` メタデータファイルを配置することで、エージェントが自動的に検索・選定できるようになります。

```text
assets/
├── backgrounds/         # 背景素材（spring_sale, tech_blue, warm_sunset, natural_mint, luxury_dark）
├── logos/               # ロゴ素材（circle, shield, leaf, hex）
└── decorations/         # 装飾バッジ（ribbon, discount_burst, new_tag, special_crown）
```

**メタデータ JSON の例 (`assets/backgrounds/warm_sunset_bg.json`):**
```json
{
  "id": "warm_sunset_bg",
  "name": "Warm Sunset Energy Gradient",
  "category": "background",
  "theme": "sale, campaign, vibrant, energetic, warm, orange, red, sunset",
  "description": "A dynamic and vibrant warm sunset gradient with energetic glowing accents, perfect for urgent promotions.",
  "recommended_mood": "energetic, passionate, urgent, warm, vibrant",
  "dimensions": {
    "width": 1200,
    "height": 1000
  }
}
```

---

## 7. テストの実行

```bash
# 仮想環境内でテストを実行
.venv/bin/pytest
# または
pytest
```
アセット読み込み、HTML/SVG生成、Base64 Data URI埋め込み、衝突解消ガードレール、直近編集ファイルの自動検出レンダリングなど全テストのパスを確認できます。

---

## 8. 引用・参考文献

本プロジェクトは以下の論文およびリポジトリの設計思想を参考にしています：
- **Paper**: [arXiv:2503.11060](https://arxiv.org/abs/2503.11060)
- **Official Repository**: [sony/BannerAgency](https://github.com/sony/BannerAgency)
- **Project Page**: [https://banneragency.github.io/](https://banneragency.github.io/)

```bibtex
@inproceedings{wang2025banneragency,
  title     = {BannerAgency: Advertising Banner Design with Multimodal LLM Agents},
  author    = {Wang, Heng and Shimose, Yotaro and Takamatsu, Shingo},
  booktitle = {Proceedings of the 2025 Conference on Empirical Methods in Natural Language Processing (EMNLP)},
  year      = {2025}
}
```

---

## 9. ライセンス

本プロジェクトは [MIT License](LICENSE) のもとで公開されています。
