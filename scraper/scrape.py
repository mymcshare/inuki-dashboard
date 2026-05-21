#!/usr/bin/env python3
"""
飲食店ドットコム 居抜き物件スクレイパー
1都3県（東京・神奈川・千葉・埼玉）の新着居抜き物件を取得し
index.html の DATA 配列と CRAWL_DATE を自動更新する
"""

import re
import json
import time
import sys
import requests
from bs4 import BeautifulSoup
from datetime import date
from pathlib import Path

# ── 設定 ────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.inshokuten.com/",
}

# 居抜き物件一覧（1都3県）先頭2ページ
# ※ サイト改修時はここのURLを修正してください
LIST_URLS = [
    "https://www.inshokuten.com/bukken/list/?type_cd[]=2&pref_cd[]=13&pref_cd[]=14&pref_cd[]=12&pref_cd[]=11&sort=new",
    "https://www.inshokuten.com/bukken/list/?type_cd[]=2&pref_cd[]=13&pref_cd[]=14&pref_cd[]=12&pref_cd[]=11&sort=new&page=2",
]

CRAWL_DELAY = 3   # リクエスト間隔（秒）
MAX_ITEMS   = 200 # 保持する最大件数（古いものを切り捨て）

# 都県判定
PREF_PATTERNS = [
    ("東京",   re.compile(r'東京都?')),
    ("神奈川", re.compile(r'神奈川県?')),
    ("千葉",   re.compile(r'千葉県?')),
    ("埼玉",   re.compile(r'埼玉県?')),
]

# 厨房流用しやすい前業態キーワード（スコア計算用）
KITCHEN_GOOD = ["ラーメン","中華","中国料理","そば","うどん","居酒屋",
                "焼肉","定食","和食","鉄板","焼鳥","割烹","ダイニング"]

HTML_PATH = Path(__file__).parent.parent / "index.html"

# ── ユーティリティ ───────────────────────────────────

def detect_pref(text: str) -> str:
    for name, pat in PREF_PATTERNS:
        if pat.search(text):
            return name
    return "東京"

def parse_rent(text: str):
    """'77万円' → 77.0、'相談' → None"""
    text = (text or "").strip()
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*万', text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None

def parse_tsubo(text: str):
    """坪表記 or ㎡→坪換算"""
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*坪', text)
    if m:
        return round(float(m.group(1).replace(",", "")), 2)
    m2 = re.search(r'([\d,]+(?:\.\d+)?)\s*㎡', text)
    if m2:
        return round(float(m2.group(1).replace(",", "")) / 3.306, 2)
    return None

def parse_walk(text: str):
    """'徒歩5分' → 5"""
    m = re.search(r'徒歩\s*(\d+)\s*分', text)
    return int(m.group(1)) if m else None

def parse_date(text: str) -> str:
    m = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return str(date.today())

# ── スクレイピング ────────────────────────────────────

def fetch(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return BeautifulSoup(r.text, "lxml")

def parse_card(card) -> dict | None:
    """物件カード（<li>または<div>）からデータ抽出"""
    # ── URL / ID ──────────────────
    a_tag = card.find("a", href=re.compile(r'/bukken/'))
    if not a_tag:
        return None
    url = a_tag["href"]
    if not url.startswith("http"):
        url = "https://www.inshokuten.com" + url
    id_m = re.search(r'/(\d+)/?$', url)
    item_id = id_m.group(1) if id_m else ""

    full_text = card.get_text(" ", strip=True)

    # ── 都県 ──────────────────────
    pref = detect_pref(full_text)

    # ── 住所（都県名以降）──────────
    addr_m = re.search(r'(?:東京都?|神奈川県?|千葉県?|埼玉県?)([\w\s\d\-－区市町村丁目番地]+)', full_text)
    city = addr_m.group(1).strip()[:30] if addr_m else ""

    # ── 最寄り駅 ──────────────────
    stn_m = re.search(r'([^\s　]+(?:線|電鉄|鉄道)[^\s　]*\s+[^\s　]+駅?\s*徒歩\s*\d+\s*分)', full_text)
    if not stn_m:
        stn_m = re.search(r'([^\s　]+駅?\s+徒歩\s*\d+\s*分)', full_text)
    station = stn_m.group(1).strip() if stn_m else ""
    walk    = parse_walk(station)

    # ── 賃料 ──────────────────────
    rent_m = re.search(r'([\d,]+(?:\.\d+)?\s*万円|要相談|相談|価格相談)', full_text)
    rent_raw = rent_m.group(1).strip() if rent_m else "相談"
    rent_man = parse_rent(rent_raw)

    # ── 面積 ──────────────────────
    tsubo = parse_tsubo(full_text)

    # ── 階数 ──────────────────────
    floor_m = re.search(r'((?:地[上下])?\d+(?:[～\-]\d+)?階)', full_text)
    floor = floor_m.group(1) if floor_m else ""

    # ── 前業態 ────────────────────
    prev_m = re.search(r'前業態[：:]\s*([^\s　]{1,20})', full_text)
    prev_tenant = prev_m.group(1) if prev_m else ""

    # ── 重飲食可 ──────────────────
    juu = bool(re.search(r'重飲食可|重飲食[\s　]*可|ラーメン可|焼肉可', full_text))

    # ── 登録日 ────────────────────
    reg_date = parse_date(full_text)

    # ── コメント（タイトル/説明文）─
    title_el = card.find(re.compile(r'^h[1-6]$')) or \
               card.find(class_=re.compile(r'title|name|ttl', re.I))
    comment = title_el.get_text(strip=True)[:60] if title_el else full_text[:60]

    return {
        "id":         item_id,
        "pref":       pref,
        "city":       city,
        "station":    station,
        "walk":       walk,
        "rentMan":    rent_man,
        "rentRaw":    rent_raw,
        "tsubo":      tsubo,
        "floor":      floor,
        "prevTenant": prev_tenant,
        "juu":        juu,
        "comment":    comment,
        "regDate":    reg_date,
        "url":        url,
    }

def scrape_page(url: str) -> list[dict]:
    print(f"  取得: {url}")
    soup = fetch(url)

    # 物件カードのセレクタ（複数候補を試す）
    candidates = [
        soup.select("ul.bukken-list > li"),
        soup.select(".bukken-item"),
        soup.select(".property-list li"),
        soup.select(".result-list li"),
        soup.select("li.item"),
        # フォールバック：inshokuten.com 固有クラス
        soup.select("[class*='bukken']"),
    ]
    cards = next((c for c in candidates if c), [])

    if not cards:
        print("  ⚠ 物件カードが見つかりません。URLまたはセレクタを確認してください。")
        # デバッグ用：ページタイトルを出力
        title = soup.find("title")
        print(f"  ページタイトル: {title.text if title else '不明'}")
        return []

    results = []
    for card in cards:
        try:
            item = parse_card(card)
            if item and item["id"]:
                results.append(item)
        except Exception as e:
            print(f"  カード解析エラー: {e}")
    return results

# ── HTML更新 ──────────────────────────────────────────

def load_existing_data() -> list[dict]:
    html = HTML_PATH.read_text(encoding="utf-8")
    m = re.search(r'const DATA = (\[[\s\S]*?\]);', html)
    return json.loads(m.group(1)) if m else []

def update_html(data: list[dict], today: str):
    html = HTML_PATH.read_text(encoding="utf-8")
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    html = re.sub(r'const DATA = \[[\s\S]*?\];', f'const DATA = {json_str};', html)
    html = re.sub(r'const CRAWL_DATE = "[^"]*";', f'const CRAWL_DATE = "{today}";', html)
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"✓ index.html 更新完了（{len(data)} 件, 取得日: {today}）")

# ── メイン ────────────────────────────────────────────

def main():
    today = str(date.today())
    print(f"=== 居抜き物件スクレイパー {today} ===")

    existing = load_existing_data()
    existing_ids = {d["id"] for d in existing}
    print(f"既存件数: {len(existing)} 件")

    new_items: list[dict] = []
    for i, url in enumerate(LIST_URLS):
        try:
            items = scrape_page(url)
            print(f"  → {len(items)} 件取得")
            new_items.extend(items)
        except Exception as e:
            print(f"  エラー: {e}")
        if i < len(LIST_URLS) - 1:
            time.sleep(CRAWL_DELAY)

    if not new_items:
        print("⚠ 新着物件を取得できませんでした。スクレイパーのURLまたはセレクタ調整が必要な可能性があります。")
        sys.exit(1)

    added = [d for d in new_items if d["id"] not in existing_ids]
    print(f"新規追加: {len(added)} 件 / スキップ（重複）: {len(new_items) - len(added)} 件")

    # 新着を先頭、登録日降順でマージ
    merged = added + existing
    merged.sort(key=lambda d: d.get("regDate") or "", reverse=True)
    merged = merged[:MAX_ITEMS]

    update_html(merged, today)


if __name__ == "__main__":
    main()
