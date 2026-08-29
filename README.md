# 入力漏れ確認ダッシュボード（デモ）

医療事務の請求業務が遅れる原因のひとつ「医師の電子カルテ入力漏れ」を、事務担当者が一覧で見える化するためのデモアプリです。

- 課題18（ヒアリング）→ 課題19（MVP企画）で決めた「未入力・未確認の状態が一覧で見えるダッシュボード」を実装したものです。
- 実際の患者データは使用していません。すべてサンプルデータです。

## ローカルでの動かし方

```bash
pip install -r requirements.txt
python server.py
```

`http://localhost:5000` を開くと確認できます（Chromeでの動作を確認済み。対応ブラウザ：Chrome / Edge）。

## できること

- 医師の電子カルテ入力が漏れている案件を一覧表示
- 手術日からの経過日数を自動計算し、3日以上放置されている案件を赤く強調
- 「確認済みにする」ボタンでステータスを更新
- デモ用に新しい案件を手動で追加可能

## 今後のアイデア（メモ）

- 案件が多くなったときの絞り込み・検索機能

## リリース手順

push→本番URLまでの手順は [リリース手順.md](./リリース手順.md) を参照。

## 障害復旧

落ちた時の「気づく→切り分け→ロールバック→復旧確認」の手順は [docs/障害復旧手順.md](./docs/障害復旧手順.md) を参照。

## 開発〜運用の型

ブランチ運用・CI/CD・環境分離・復旧手順をまとめた1枚資料は [開発運用の型.md](./開発運用の型.md) を参照。

## 認証基盤

サインアップ（メール確認）・ログイン／ログアウト・パスワード再発行・ロール（管理者／一般）の設計と動作確認の記録は [docs/auth-design.md](./docs/auth-design.md) ・ [docs/auth-record.md](./docs/auth-record.md) を参照。

## 課金（プラン・Stripe決済）

料金プラン（フリー／プロ）の設計と、Stripeテストモードでの決済動作確認の記録は [docs/billing-design.md](./docs/billing-design.md) ・ [docs/billing-record.md](./docs/billing-record.md) を参照。

## 継続課金（Webhook・解約の自動検知）

Stripe Webhookで解約を検知し、テナントのプランを自動で停止する仕組みの設計と動作確認の記録は [docs/billing-webhook-design.md](./docs/billing-webhook-design.md) ・ [docs/billing-webhook-record.md](./docs/billing-webhook-record.md) を参照。
