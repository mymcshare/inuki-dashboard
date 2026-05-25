#!/usr/bin/env python3
"""
飲食店ドットコム 居抜き物件スクレイパー
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

# ── 設定 ────────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.inshokuten.com/",
}

LIST_URLS = [
    "https://www.inshokuten.com/bukken/list/?type_cd[]=2&pref_cd[]=13&pref_cd[]=14&pref_cd[]=12&pref_cd[]=11&sort=new",
    "https://www.inshokuten.com/bukken/list/?type_cd[]=2&pref_cd[]=13&pref_cd[]=14&pref_cd[]=12&pref_cd[]=11&sort=new&page=2",
]

CRAWL_DELAY = 3
MAX_ITEMS   = 200
SOURCE_NAME = "飲食店ドットコム"

PREF_PATTERNS = [
    ("東京",   re.compile(r'東京都?')),
    ("神奈川", re.compile(r'神奈川県?')),
    ("千葉",   re.compile(r'千葉県?')),
    ("埼玉",   re.compile(r'埼玉県?')),
]

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
    text = (text or "").strip()
    m = re.search(r'([\d,]+(?:\.\d+)?)\s*万', text)
    if m:
        return float(m.group(1).replace(",", ""))
    return None

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

def parse_date(text: str) -> str:
    m = re.search(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return str(date.today())

# ── スコア・バッジ計算 ────────────────────────────────

def calc_score(item: dict) -> int:
    s = 0
    if item.get('juu'): s += 3
    else: s -= 2
    prev = item.get('prevTenant', '') or ''
    if any(k in prev for k in KITCHEN_GOOD): s += 2
    floor_ = item.get('floor', '') or ''
    if '地上1' in floor_: s += 2
    walk = item.get('walk')
    if walk is not None:
        if walk <= 5: s += 2
        elif walk <= 10: s += 1
    tsubo = item.get('tsubo')
    if tsubo is not None:
        if 10 <= tsubo <= 25: s += 2
        elif 8 <= tsubo <= 30: s += 1
    rent = item.get('rentMan')
    if rent is not None and rent <= 40: s += 1
    return s

def calc_badge(item: dict) -> str:
    s = calc_score(item)
    if not item.get('juu'): return "△要検討"
    if s >= 10: return "◎有力"
    if s >= 7: return "○候補"
    return "△要検討"

# ── スクレイピング ────────────────────────────────────

def fetch(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return BeautifulSoup(r.text, "lxml")

def parse_card(card) -> dict | None:
    url = card.get("href", "")
    if not url.startswith("http"):
        url = "https://www.inshokuten.com" + url
    id_m = re.search(r'/(\d+)/?$', url)
    item_id = id_m.group(1) if id_m else ""
    if not item_id:
        return None

    full_text = card.get_text(" ", strip=True)
    if not full_text.strip():
        return None

    pref = detect_pref(full_text)

    addr_m = re.search(r'(?:東京都?|神奈川県?|千葉県?|埼玉県?)([\w\d区市町村丁目番地\-－]+)', full_text)
    city = addr_m.group(1).strip()[:30] if addr_m else ""

    # 路線名・駅名・徒歩を個別に抽出
    stn_m = re.search(
        r'([^\s　]+(?:線|電鉄|鉄道|メトロ)[^\s　]*)\s+([^\s　]+(?:駅)?)\s+徒歩\s*(\d+)\s*分',
        full_text
    )
    if stn_m:
        line    = stn_m.group(1).strip()
        station = stn_m.group(2).strip()
        walk    = int(stn_m.group(3))
    else:
        stn_m2 = re.search(r'([^\s　]+(?:駅)?)\s+徒歩\s*(\d+)\s*分', full_text)
        if stn_m2:
            line    = ""
            station = stn_m2.group(1).strip()
            walk    = int(stn_m2.group(2))
        else:
            line = station = ""
            walk = None

    rent_m   = re.search(r'([\d,]+(?:\.\d+)?\s*万円)', full_text)
    rent_raw = rent_m.group(1).strip() if rent_m else "相談"
    rent_man = parse_rent(rent_raw)
    tsubo    = parse_tsubo(full_text)

    floor_m = re.search(r'((?:地[上下])?\d+(?:[～\-]\d+)?階)', full_text)
    floor   = floor_m.group(1) if floor_m else ""

    prev_m      = re.search(r'前業態[：:]\s*([^\s　]{1,20})', full_text)
    prev_tenant = prev_m.group(1) if prev_m else ""

    juu      = "重飲食" in full_text
    reg_date = parse_date(full_text) or str(date.today())

    item = {
        "source":        SOURCE_NAME,
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
        "addedDate":     str(date.today()),
    }
    item["score"]     = calc_score(item)
    item["badge"]     = calc_badge(item)
    item["prevReuse"] = any(k in (prev_tenant or '') for k in KITCHEN_GOOD)
    item["isNew"]     = True  # main() で再設定
    return item

def scrape_page(url: str) -> list[dict]:
    print(f"  取得: {url}")
    soup = fetch(url)
    cards = soup.find_all("a", href=re.compile(r'/bukken/bukkens/\d+'))
    print(f"  リンク検出: {len(cards)} 件")

    if not cards:
        print("  警告: 物件リンクが見つかりません。")
        title = soup.find("title")
        print(f"  ページタイトル: {title.text if title else '不明'}")
        return []

    results = []
    seen_ids: set[str] = set()
    for card in cards:
        try:
            item = parse_card(card)
            if item and item["id"] and item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                results.append(item)
        except Exception as e:
            print(f"  カード解析エラー: {e}")
    print(f"  -> {len(results)} 件取得（重複除外後）")
    return results

# ── 旧フォーマット変換 ────────────────────────────────

def migrate_item(item: dict, today_str: str) -> dict:
    """旧DATA形式 → 新ALL形式に変換"""
    if 'source' in item:
        return item  # 既に新フォーマット

    station_full = item.get('station', '') or ''
    line_m  = re.search(r'^([^\s　]+(?:線|電鉄|鉄道|メトロ)[^\s　]*)', station_full)
    line    = line_m.group(1).strip() if line_m else ''
    sname_m = re.search(r'(?:線|電鉄|鉄道|メトロ[^\s　]*)\s+([^\s　]+)', station_full)
    if not sname_m:
        sname_m = re.search(r'^([^\s　]+)', station_full)
    station_name = sname_m.group(1).strip() if sname_m else station_full

    add_date = item.get('regDate', today_str)
    new_item = {
        'source':        SOURCE_NAME,
        'id':            item['id'],
        'pref':          item.get('pref', '東京'),
        'city':          item.get('city', ''),
        'line':          line,
        'station':       station_name,
        'walk':          item.get('walk'),
        'rentMan':       item.get('rentMan'),
        'tsubo':         item.get('tsubo'),
        'floor':         item.get('floor', ''),
        'prevTenant':    item.get('prevTenant', ''),
        'juu':           item.get('juu', False),
        'regDate':       item.get('regDate', today_str),
        'dateConfirmed': False,
        'url':           item.get('url', ''),
        'addedDate':     add_date,
        'isNew':         False,
    }
    new_item['score']     = calc_score(new_item)
    new_item['badge']     = calc_badge(new_item)
    new_item['prevReuse'] = any(k in (new_item.get('prevTenant') or '') for k in KITCHEN_GOOD)
    return new_item

# ── HTML更新 ──────────────────────────────────────────

def load_existing_data() -> list[dict]:
    html = HTML_PATH.read_text(encoding="utf-8")
    m = re.search(r'const ALL = (\[[\s\S]*?\]);', html)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 旧フォーマット fallback
    m = re.search(r'const DATA = (\[[\s\S]*?\]);', html)
    return json.loads(m.group(1)) if m else []

def update_html(data: list[dict], today_date: str, today_datetime: str):
    html = HTML_PATH.read_text(encoding="utf-8")
    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    html = re.sub(r'const ALL = \[[\s\S]*?\];', f'const ALL = {json_str};', html)

    meta = {
        "generatedAt": today_datetime,
        "windowStart": (date.today() - timedelta(days=6)).strftime("%Y-%m-%d"),
        "windowEnd":   today_date,
        "areas":       ["東京", "神奈川", "千葉", "埼玉"],
        "sources":     [SOURCE_NAME],
        "count":       len(data),
    }
    meta_str = json.dumps(meta, ensure_ascii=False)
    html = re.sub(r'const META = \{[\s\S]*?\};', f'const META = {meta_str};', html)

    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"index.html updated: {len(data)} items, {today_datetime}")

# ── メイン ────────────────────────────────────────────

def main():
    jst = timezone(timedelta(hours=9))
    now            = datetime.now(jst)
    today_date     = now.strftime("%Y-%m-%d")
    today_datetime = now.strftime("%Y-%m-%d %H:%M")
    print(f"=== scraper {today_datetime} ===")

    existing = load_existing_data()
    existing = [migrate_item(d, today_date) for d in existing]
    existing = [d for d in existing if d.get('source') == SOURCE_NAME]
    existing_ids = {d["id"] for d in existing}
    print(f"既存件数: {len(existing)} 件")

    new_items: list[dict] = []
    for i, url in enumerate(LIST_URLS):
        try:
            items = scrape_page(url)
            print(f"  -> {len(items)} 件取得")
            new_items.extend(items)
        except Exception as e:
            print(f"  エラー: {e}")
        if i < len(LIST_URLS) - 1:
            time.sleep(CRAWL_DELAY)

    if not new_items:
        print("警告: 新着物件を取得できませんでした。")
        sys.exit(1)

    added = [d for d in new_items if d["id"] not in existing_ids]
    print(f"新規追加: {len(added)} 件 / スキップ（重複）: {len(new_items) - len(added)} 件")

    merged = added + existing
    merged.sort(key=lambda d: d.get("regDate") or "", reverse=True)
    merged = merged[:MAX_ITEMS]

    for item in merged:
        item["isNew"] = (item.get("addedDate", "") == today_date)

    update_html(merged, today_date, today_datetime)


if __name__ == "__main__":
    main()
