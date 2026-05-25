#!/usr/bin/env python3
"""
居抜き物件スクレイパー（8サイト対応）
1都3県（東京・神奈川・千葉・埼玉）の新着居抜き物件を取得し
index.html の ALL 配列と META を自動更新する
"""

import re
import json
import time
import sys
import requests
from bs4 import BeautifulSoup
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

# ── 設定 ────────────────────────────────────────────
CRAWL_DELAY = 2   # リクエスト間隔（秒）
MAX_ITEMS   = 400 # 保持する最大件数

KITCHEN_GOOD = ["ラーメン","中華","中国料理","そば","うどん","居酒屋",
                "焼肉","定食","和食","鉄板","焼鳥","割烹","ダイニング"]

HTML_PATH = Path(__file__).parent.parent / "index.html"

PREF_PATTERNS = [
    ("東京",   re.compile(r'東京都?')),
    ("神奈川", re.compile(r'神奈川県?')),
    ("千葉",   re.compile(r'千葉県?')),
    ("埼玉",   re.compile(r'埼玉県?')),
]

# ── 8サイト定義 ──────────────────────────────────────
# (source_name, base_url, list_urls, link_href_pattern)
SITES = [
    (
        "飲食店ドットコム",
        "https://www.inshokuten.com",
        [
            "https://www.inshokuten.com/bukken/list/?type_cd[]=2&pref_cd[]=13&pref_cd[]=14&pref_cd[]=12&pref_cd[]=11&sort=new",
            "https://www.inshokuten.com/bukken/list/?type_cd[]=2&pref_cd[]=13&pref_cd[]=14&pref_cd[]=12&pref_cd[]=11&sort=new&page=2",
        ],
        re.compile(r'/bukken/bukkens/\d+'),
    ),
    (
        "居抜き市場",
        "https://inuki-ichiba.jp",
        [
            "https://inuki-ichiba.jp/rent/?pref[]=13&pref[]=14&pref[]=11&pref[]=12&sort=new",
            "https://inuki-ichiba.jp/rent/?pref[]=13&pref[]=14&pref[]=11&pref[]=12&sort=new&page=2",
        ],
        re.compile(r'/rent/\d+'),
    ),
    (
        "居抜き本舗",
        "https://www.inuki-honpo.jp",
        [
            "https://www.inuki-honpo.jp/rent/?pref[]=13&pref[]=14&pref[]=11&pref[]=12",
            "https://www.inuki-honpo.jp/rent/?pref[]=13&pref[]=14&pref[]=11&pref[]=12&p=2",
        ],
        re.compile(r'/rent/\d+/?$'),
    ),
    (
        "居抜きの神様",
        "https://godproperty.jp",
        [
            "https://godproperty.jp/property?prefecture[]=13&prefecture[]=14&prefecture[]=11&prefecture[]=12",
        ],
        re.compile(r'/property/\d+'),
    ),
    (
        "居抜き店舗ABC",
        "https://www.abc-tenpo.com",
        [
            "https://www.abc-tenpo.com/feature/new_arrival",
        ],
        re.compile(r'/property/view/\d+'),
    ),
    (
        "テンポスマート",
        "https://www.temposmart.jp",
        [
            "https://www.temposmart.jp/estates",  # 全国新着（都県はテキストで判定）
        ],
        re.compile(r'/estates/\d+'),
    ),
    (
        "ぶけなび",
        "https://bukenavi.jp",
        [
            "https://bukenavi.jp/kanto/pref/%E6%9D%B1%E4%BA%AC%E9%83%BD",   # 東京
            "https://bukenavi.jp/kanto/pref/%E7%A5%9E%E5%A5%88%E5%B7%9D%E7%9C%8C",  # 神奈川
            "https://bukenavi.jp/kanto/pref/%E5%8D%83%E8%91%89%E7%9C%8C",   # 千葉
            "https://bukenavi.jp/kanto/pref/%E5%9F%BC%E7%8E%89%E7%9C%8C",   # 埼玉
        ],
        re.compile(r'/kanto/station/[^/?]+/\d+'),
    ),
    (
        "居抜き店舗.com",
        "https://www.i-tenpo.com",
        [
            "https://www.i-tenpo.com/search/result?schArPrefId%5B%5D=1",   # 東京23区
            "https://www.i-tenpo.com/search/result?schArPrefId%5B%5D=2",   # 東京近郊
            "https://www.i-tenpo.com/search/result?schArPrefId%5B%5D=3",   # 神奈川
            "https://www.i-tenpo.com/search/result?schArPrefId%5B%5D=4",   # 埼玉・千葉
        ],
        re.compile(r'/t\d+'),
    ),
]

# ── ユーティリティ ───────────────────────────────────

def detect_pref(text: str) -> str:
    for name, pat in PREF_PATTERNS:
        if pat.search(text):
            return name
    return "東京"

def parse_tsubo(text: str):
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*坪', text)
    if m:
        return round(float(m.group(1).replace(",", "")), 2)
    m2 = re.search(r'([\d,]+(?:\.\d+)?)\s*㎡', text)
    if m2:
        return round(float(m2.group(1).replace(",", "")) / 3.306, 2)
    return None

def parse_walk(text: str):
    m = re.search(r'徒歩\s*(\d+)\s*分', text)
    return int(m.group(1)) if m else None

def parse_date_str(text: str) -> str:
    m = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return str(date.today())

def parse_rent_man(text: str):
    """賃料を万円で返す"""
    # "77万円" or "39.6万円(税込)"
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*万円', text)
    if m:
        return round(float(m.group(1).replace(",", "")), 2)
    # "385,000円" or "626,560円(税込)" → 万円換算
    m2 = re.search(r'賃料[^\d]*([\d,]+)\s*円', text)
    if m2:
        yen = float(m2.group(1).replace(",", ""))
        if yen > 9999:
            return round(yen / 10000, 2)
    return None

# ── スコア・バッジ計算 ────────────────────────────────

def calc_score(item: dict) -> int:
    s = 0
    if item.get('juu'): s += 3
    else: s -= 2
    prev = item.get('prevTenant', '') or ''
    if any(k in prev for k in KITCHEN_GOOD): s += 2
    if '地上1' in (item.get('floor', '') or ''): s += 2
    walk = item.get('walk')
    if walk is not None:
        s += 2 if walk <= 5 else (1 if walk <= 10 else 0)
    tsubo = item.get('tsubo')
    if tsubo is not None:
        s += 2 if 10 <= tsubo <= 25 else (1 if 8 <= tsubo <= 30 else 0)
    rent = item.get('rentMan')
    if rent is not None and rent <= 40: s += 1
    return s

def calc_badge(item: dict) -> str:
    s = calc_score(item)
    if not item.get('juu'): return "△要検討"
    if s >= 10: return "◎有力"
    if s >= 7:  return "○候補"
    return "△要検討"

# ── 汎用テキストパーサー ──────────────────────────────

def parse_common(text: str, url: str, source: str, today_str: str) -> dict | None:
    """テキストからプロパティデータを抽出する汎用パーサー"""
    # IDを URLから抽出
    id_m = re.search(r'/(\d{4,})/?(?:\?|$)', url) or re.search(r'/t(\d+)', url)
    if not id_m:
        id_m = re.search(r'(\d{4,})', url)
    item_id = id_m.group(1) if id_m else ""
    if not item_id:
        return None

    pref = detect_pref(text)

    addr_m = re.search(r'(?:東京都?|神奈川県?|千葉県?|埼玉県?)([\w\d区市町村丁目番地\-－]+)', text)
    city = addr_m.group(1).strip()[:30] if addr_m else ""

    # 路線名・駅名・徒歩
    stn_m = re.search(
        r'([^\s　]+(?:線|電鉄|鉄道|メトロ)[^\s　]*)\s+([^\s　]+(?:駅)?)\s+徒歩\s*(\d+)\s*分', text
    )
    if stn_m:
        line    = stn_m.group(1).strip()
        station = stn_m.group(2).strip()
        walk    = int(stn_m.group(3))
    else:
        stn_m2 = re.search(r'([^\s　]+(?:駅)?)\s+徒歩\s*(\d+)\s*分', text)
        if stn_m2:
            line    = ""
            station = stn_m2.group(1).strip()
            walk    = int(stn_m2.group(2))
        else:
            line = station = ""
            walk = None

    rent_man = parse_rent_man(text)
    tsubo    = parse_tsubo(text)

    floor_m = re.search(r'((?:地[上下])?\d+(?:[～\-]\d+)?階)', text)
    floor   = floor_m.group(1) if floor_m else ""

    prev_m = (re.search(r'前業態[：:]\s*([^\s　]{1,20})', text) or
              re.search(r'現業態[：:]\s*([^\s　]{1,20})', text) or
              re.search(r'前業種[：:]\s*([^\s　]{1,20})', text))
    prev_tenant = prev_m.group(1) if prev_m else ""

    juu = any(kw in text for kw in ["重飲食可", "重飲食OK", "重飲食〇", "重飲食○", "重飲食相談", "重飲食ご相談"])
    if not juu:
        juu = bool(re.search(r'重飲食', text))

    reg_date = parse_date_str(text)

    item = {
        "source":        source,
        "id":            item_id,
        "pref":          pref,
        "city":          city,
        "line":          line,
        "station":       station,
        "walk":          walk,
        "rentMan":       rent_man,
        "tsubo":         tsubo,
        "floor":         floor,
        "prevTenant":    prev_tenant,
        "juu":           juu,
        "regDate":       reg_date,
        "dateConfirmed": False,
        "url":           url,
        "addedDate":     today_str,
    }
    item["score"]     = calc_score(item)
    item["badge"]     = calc_badge(item)
    item["prevReuse"] = any(k in (prev_tenant or '') for k in KITCHEN_GOOD)
    item["isNew"]     = True
    return item

# ── HTTPフェッチ ─────────────────────────────────────

HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}

def fetch(url: str, referer: str = "") -> BeautifulSoup:
    h = dict(HEADERS_BASE)
    if referer:
        h["Referer"] = referer
    r = requests.get(url, headers=h, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return BeautifulSoup(r.text, "lxml")

# ── 汎用サイトスクレイパー ────────────────────────────

def get_card_container(link_tag, min_len: int = 80):
    """リンクから物件情報を含むコンテナを取得（最大6階層上へ）"""
    text = link_tag.get_text(" ", strip=True)
    if len(text) >= min_len:
        return link_tag, text
    el = link_tag.parent
    for _ in range(6):
        if el is None:
            break
        t = el.get_text(" ", strip=True)
        if len(t) >= min_len:
            return el, t
        el = el.parent
    return link_tag, link_tag.get_text(" ", strip=True)

def scrape_site(source: str, base_url: str, list_urls: list, link_pattern) -> list[dict]:
    today_str = str(date.today())
    results   = []
    seen_ids  = set()
    seen_hrefs = set()

    for url in list_urls:
        try:
            print(f"  [{source}] 取得: {url}")
            soup = fetch(url, base_url + "/")
            cards = soup.find_all("a", href=link_pattern)
            print(f"  [{source}] リンク検出: {len(cards)}")

            for card in cards:
                href = card.get("href", "")
                if not href.startswith("http"):
                    href = base_url + href
                if href in seen_hrefs:
                    continue
                seen_hrefs.add(href)

                _, text = get_card_container(card)
                try:
                    item = parse_common(text, href, source, today_str)
                    if item and item["id"] and item["id"] not in seen_ids:
                        seen_ids.add(item["id"])
                        results.append(item)
                except Exception as e:
                    pass  # サイレントスキップ

            time.sleep(CRAWL_DELAY)

        except Exception as e:
            print(f"  [{source}] エラー ({url}): {e}")

    print(f"  [{source}] 完了: {len(results)} 件")
    return results

# ── HTML更新 ──────────────────────────────────────────

def load_existing_data() -> list[dict]:
    html = HTML_PATH.read_text(encoding="utf-8")
    m = re.search(r'const ALL = (\[[\s\S]*?\]);', html)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    m = re.search(r'const DATA = (\[[\s\S]*?\]);', html)
    return json.loads(m.group(1)) if m else []

def update_html(data: list[dict], today_date: str, today_datetime: str, sources: list):
    html     = HTML_PATH.read_text(encoding="utf-8")
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    html = re.sub(r'const ALL = \[[\s\S]*?\];', f'const ALL = {json_str};', html)

    meta = {
        "generatedAt": today_datetime,
        "windowStart": (date.today() - timedelta(days=6)).strftime("%Y-%m-%d"),
        "windowEnd":   today_date,
        "areas":       ["東京", "神奈川", "千葉", "埼玉"],
        "sources":     sources,
        "count":       len(data),
    }
    meta_str = json.dumps(meta, ensure_ascii=False)
    html = re.sub(r'const META = \{[\s\S]*?\};', f'const META = {meta_str};', html)

    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"index.html updated: {len(data)} items, {today_datetime}")

# ── メイン ────────────────────────────────────────────

def main():
    jst            = timezone(timedelta(hours=9))
    now            = datetime.now(jst)
    today_date     = now.strftime("%Y-%m-%d")
    today_datetime = now.strftime("%Y-%m-%d %H:%M")
    print(f"=== 居抜き物件スクレイパー（8サイト） {today_datetime} ===")

    # 既存データ読み込み
    existing     = load_existing_data()
    existing_ids = {(d.get("source",""), d.get("id","")) for d in existing}
    print(f"既存件数: {len(existing)} 件")

    # 全サイトからスクレイピング
    all_new   = []
    succeeded = []

    for (source, base_url, list_urls, link_pattern) in SITES:
        try:
            items = scrape_site(source, base_url, list_urls, link_pattern)
            if items:
                all_new.extend(items)
                succeeded.append(source)
        except Exception as e:
            print(f"[{source}] 致命的エラー: {e}")

    if not all_new:
        print("警告: 全サイトから物件を取得できませんでした。")
        sys.exit(1)

    # 新規のみ抽出（source + id の組み合わせで重複チェック）
    added = [d for d in all_new if (d.get("source",""), d.get("id","")) not in existing_ids]
    print(f"\n新規追加: {len(added)} 件 / スキップ（重複）: {len(all_new) - len(added)} 件")

    # マージ・ソート・切り捨て
    merged = added + existing
    merged.sort(key=lambda d: d.get("regDate") or "", reverse=True)
    merged = merged[:MAX_ITEMS]

    # isNew フラグ更新
    for item in merged:
        item["isNew"] = (item.get("addedDate", "") == today_date)

    # ソース別集計
    from collections import Counter
    cnt = Counter(d["source"] for d in merged)
    print("\n[ソース別件数]")
    for s, n in cnt.most_common():
        print(f"  {s}: {n} 件")

    update_html(merged, today_date, today_datetime, succeeded)


if __name__ == "__main__":
    main()
