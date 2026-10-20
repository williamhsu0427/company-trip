"""Collect overseas group tours from Taiwanese travel agencies into data/tours.json.

Each agency scraper is independent: if one breaks (site redesign, bot block),
the others still run and the agency is listed under "failed" so the page can
point people to the one-tap search instead.
"""
import asyncio, datetime as dt, json, os, re, sys, traceback
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from countries import guess_country

TODAY = dt.date.today()
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'
REGION_KEYWORDS = ['日本', '韓國', '泰國', '越南', '新加坡', '馬來西亞', '峇里島', '菲律賓', '港澳', '大陸',
                   '歐洲', '美國', '加拿大', '澳洲', '紐西蘭', '土耳其', '杜拜', '埃及', '印度', '北歐']


def num(s):
    s = re.sub(r'[^\d]', '', str(s or ''))
    return int(s) if s else None


def days_from(title):
    m = re.search(r'(\d{1,2})\s*[日天]', title or '')
    if m:
        return int(m.group(1))
    cn = '一二三四五六七八九十'
    m = re.search(r'([一二三四五六七八九十]{1,3})[日天]', title or '')
    if m:
        w = m.group(1)
        if w == '十':
            return 10
        if w.startswith('十'):
            return 10 + cn.index(w[1]) + 1
        if w.endswith('十'):
            return (cn.index(w[0]) + 1) * 10
        if len(w) == 1:
            return cn.index(w) + 1
    return None


def mmdd_to_date(s):
    """'04/15' -> nearest future ISO date."""
    m = re.match(r'(\d{1,2})/(\d{1,2})', s)
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    try:
        x = dt.date(TODAY.year, mo, d)
    except ValueError:
        return None
    if x < TODAY - dt.timedelta(days=7):
        x = x.replace(year=TODAY.year + 1)
    return x.isoformat()


def ymd(s):
    m = re.search(r'(\d{4})[/-]?(\d{2})[/-]?(\d{2})', s or '')
    return f'{m.group(1)}-{m.group(2)}-{m.group(3)}' if m else None


def tour(agency, title, url, price=None, days=None, deps=(), image=None, place=''):
    title = re.sub(r'\s+', ' ', title or '').strip()
    return {
        'agency': agency, 'title': title, 'url': url,
        'country': guess_country(place, title),
        'days': days or days_from(title), 'price': price,
        'departures': sorted({d for d in deps if d}), 'image': image,
    }


async def goto(page, url, wait=2500):
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
    except Exception as e:
        print('  slow page:', url, str(e)[:80], flush=True)
    try:
        await page.wait_for_load_state('networkidle', timeout=15000)
    except Exception:
        pass
    await page.wait_for_timeout(wait)


async def html_of(page):
    for _ in range(5):
        try:
            return await page.content()
        except Exception:
            await page.wait_for_timeout(2000)
    return ''


async def wait_for(page, check, seconds=20):
    """Poll until check(html) is truthy (e.g. a bot-check page has finished redirecting)."""
    for _ in range(seconds):
        h = await html_of(page)
        if check(h):
            return h
        await page.wait_for_timeout(1000)
    return await html_of(page)


# ---------------- 雄獅 ----------------
async def lion(ctx):
    page = await ctx.new_page()
    await goto(page, 'https://travel.liontravel.com/search?TravelType=2')
    out, pg = [], 1
    end = (TODAY + dt.timedelta(days=365)).isoformat()
    while pg <= 60:
        body = {"ArriveID": None, "GoDatestart": TODAY.isoformat(), "GroupID": None, "Keywords": None,
                "IsEnsureGroup": None, "IsSold": None, "ThemeID": None, "TravelPavilionGroupID": None,
                "KeywordsCity": None, "TravelType": 2, "BuIDs": "", "PreferAirlines": None, "GoDateEnd": end,
                "DepartureID": "", "WeekDay": "", "PriceList": None, "AirlineIDs": "", "TripTypes": "", "Tags": "",
                "SortType": 3, "Days": "", "MarketingProjects": "", "Page": pg, "PageSize": 50}
        r = await page.request.post('https://travel.liontravel.com/search/grouplistinfojson', data=body,
                                    headers={'content-type': 'application/json'})
        d = await r.json()
        items = d.get('NormGroupList') or []
        for it in items:
            if it.get('TravelType') not in (2, None):
                continue
            gl = it.get('GroupList') or []
            gid = gl[0]['GroupID'] if gl else ''
            url = (f"https://travel.liontravel.com/detail?NormGroupID={it['NormGroupID']}"
                   f"&GroupID={gid}&TourSource={it.get('TourSource') or 'Lion'}")
            price = num((it.get('DisplayPrice') or {}).get('Price')) or num(it.get('GroupStraightPrice'))
            out.append(tour('雄獅旅遊', it.get('TourName'), url, price, it.get('TourDays'),
                            [ymd(g.get('GoDate')) for g in gl if g.get('Status') != 'full'] or [ymd(g.get('GoDate')) for g in gl],
                            it.get('ImgM') or it.get('Img')))
        if pg >= (d.get('TotalPage') or 1) or not items:
            break
        pg += 1
    await page.close()
    return out


# ---------------- 可樂 ----------------
async def cola(ctx):
    page = await ctx.new_page()
    out = {}
    for kw in REGION_KEYWORDS:
        await goto(page, f'https://tour.colatour.com.tw/search?KeyWord={quote(kw)}', 3000)
        s = BeautifulSoup(await html_of(page), 'lxml')
        for a in s.select('a[href*="itinerary?PatternNo"]'):
            h3 = a.find(['h3', 'h2'])
            if not h3:
                continue
            href = a['href']
            box = a.parent
            text = box.get_text(' ', strip=True)
            deps = [mmdd_to_date(x) for x in re.findall(r'\b\d{2}/\d{2}\b', text)]
            img = box.find('img')
            out[href] = tour('可樂旅遊', h3.get_text(strip=True), href, num(re.search(r'\$([\d,]+)', text).group(1)) if '$' in text else None,
                             None, deps, img.get('src') if img else None, kw)
    await page.close()
    return [t for t in out.values() if t['country'] != '台灣']


# ---------------- 東南 ----------------
SETTOUR_PAGES = ['japan', 'korea', 'southeast_asia', 'china', 'europe', 'america', 'oceania', 'africa_middle_east']


async def settour(ctx):
    page = await ctx.new_page()
    out = {}
    urls = [f'https://tour.settour.com.tw/{p}.html' for p in SETTOUR_PAGES]
    urls += [f'https://tour.settour.com.tw/search?keyWords={quote(kw)}' for kw in REGION_KEYWORDS]
    for u in urls:
        await goto(page, u, 3000)
        s = BeautifulSoup(await html_of(page), 'lxml')
        for a in s.select('a[href*="/product/GFG"]'):
            m = re.search(r'GFG\d{10}', a['href'])
            h = a.find('h3')
            if not m or not h or m.group(0) in out:
                continue
            txt = a.get_text(' ', strip=True)
            img = a.find(attrs={'data-src': True})
            im = re.search(r'url\((https?://[^)]+)\)', img['data-src']) if img else None
            dd = re.search(r'(\d+)\s*天', txt)
            pr = re.search(r'\$([\d,]+)', txt)
            out[m.group(0)] = tour('東南旅遊', h.get_text(strip=True), f'https://tour.settour.com.tw/product/{m.group(0)}',
                                   num(pr.group(1)) if pr else None, int(dd.group(1)) if dd else None, [],
                                   im.group(1) if im else None)
    await page.close()
    return [t for t in out.values() if t['country'] != '台灣']


# ---------------- 易遊網 ----------------
async def eztravel(ctx):
    page = await ctx.new_page()
    out = {}
    for kw in REGION_KEYWORDS:
        for pg in range(1, 3):
            await goto(page, f'https://vacation.eztravel.com.tw/pkgfrn/keywords/?q={quote(kw)}&tt=1&avbl=1&page={pg}', 1500)
            h = await wait_for(page, lambda h: '__NEXT_DATA__' in h, 25)
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
            if not m:
                print('  ezTravel blocked at', kw, pg, flush=True)
                break
            st = json.loads(m.group(1))['props']['pageProps']['initialState']['search']
            res = st.get('searchResults') or []
            for r in res:
                key = r.get('pfGProdNo') or r.get('prodUrl')
                deps = [ymd(x.get('saleDt')) for x in (r.get('otherSaleDts') or [])] or [ymd(r.get('saleDt'))]
                out[key] = tour('易遊網', r.get('prodNm'), r.get('prodUrl'), r.get('minPrice1') or r.get('ezPrice1'),
                                r.get('travelDay'), deps, r.get('imgUrl'), ' '.join(r.get('tourCitysNm') or []) + ' ' + kw)
            if len(res) < 10:
                break
    await page.close()
    return [t for t in out.values() if t['country'] != '台灣']


# ---------------- 山富 ----------------
async def travel4u(ctx):
    page = await ctx.new_page()
    await goto(page, 'https://www.travel4u.com.tw/group/')
    cats = sorted(set(re.findall(r'/group/category/\d+/[\w-]+/', await html_of(page))))
    out = {}
    for c in cats:
        await goto(page, urljoin('https://www.travel4u.com.tw', c), 2000)
        s = BeautifulSoup(await html_of(page), 'lxml')
        for a in s.select('a.tourCard'):
            href = urljoin('https://www.travel4u.com.tw', a['href'])
            name = a.select_one('.name') or a.find(['h5', 'h6'])
            pr = a.select_one('.price')
            if not name or href in out or not pr or not num(pr.get_text()):
                continue
            txt = a.get_text(' ', strip=True)
            dd = re.search(r'(\d+)\s*天', txt)
            dep = a.select_one('.tourDepartureDate')
            img = a.find('img')
            out[href] = tour('山富旅遊', name.get_text(strip=True), href, num(pr.get_text()) if pr else None,
                             int(dd.group(1)) if dd else None,
                             [mmdd_to_date(x) for x in re.findall(r'\d{1,2}/\d{1,2}', dep.get_text() if dep else '')],
                             img.get('src') if img else None, c)
    await page.close()
    return [t for t in out.values() if t['country'] != '台灣']


# ---------------- 喜鴻 ----------------
async def besttour(ctx):
    page = await ctx.new_page()
    await goto(page, 'https://www.besttour.com.tw/search?type=2')
    groups = {}
    for pg in range(1, 80):
        r = await page.request.post(f'https://travelapi.besttour.com.tw/api/query_List_all.asp?pageid={pg}&pagesize=100')
        d = json.loads(await r.text())
        for it in d.get('data') or []:
            if (it.get('city') or '').startswith('台灣'):
                continue
            name = it.get('name') or ''
            g = groups.setdefault(name, {'it': it, 'deps': [], 'price': None})
            g['deps'].append(ymd(it.get('date')))
            p = num(it.get('price'))
            if p and (g['price'] is None or p < g['price']):
                g['price'] = p
                g['it'] = it
        if pg >= int(d.get('pagecount') or 1):
            break
    await page.close()
    out = []
    for name, g in groups.items():
        it = g['it']
        img = it.get('img') or ''
        out.append(tour('喜鴻假期', name, f"https://www.besttour.com.tw/itinerary/{it.get('id')}", g['price'],
                        num(it.get('day')), g['deps'], ('https:' + img) if img.startswith('//') else img or None,
                        it.get('city', '')))
    return out


# ---------------- 新魅力 ----------------
async def newamazing(ctx):
    page = await ctx.new_page()
    await goto(page, 'https://www.newamazing.com.tw/EW/GO/GroupList.asp')
    html = [await html_of(page)]
    end = (TODAY + dt.timedelta(days=365)).strftime('%Y/%m/%d')
    for pg in range(2, 31):
        r = await page.request.post('https://www.newamazing.com.tw/EW/GO/GroupList.asp?tabList=GO', form={
            'displayType': 'G', 'orderCd': '1', 'pageALL': str(pg), 'pageGO': str(pg), 'pagePGO': str(pg),
            'beginDt': TODAY.strftime('%Y/%m/%d'), 'endDt': end, 'allowJoin': '1', 'allowWait': '1'})
        h = await r.text()
        if 'product_name' not in h:
            break
        html.append(h)
    print('  newamazing pages:', len(html), flush=True)
    s = BeautifulSoup(''.join(html), 'lxml')
    await page.close()
    groups = {}
    for th in s.select('div.thumbnail'):
        a = th.select_one('.product_name a')
        if not a:
            continue
        code = th.select_one('.product_num')
        title = a.get_text(' ', strip=True)
        if code:
            title = title.replace(code.get_text(strip=True), '', 1).strip()
        g = groups.setdefault(title, {'deps': [], 'price': None, 'href': None, 'img': None, 'days': None})
        dep = th.select_one('.product_date')
        g['deps'].append(ymd(dep.get_text() if dep else ''))
        p = num(th.select_one('.product_price').get_text()) if th.select_one('.product_price') else None
        if p and (g['price'] is None or p < g['price']):
            g['price'] = p
            g['href'] = urljoin('https://www.newamazing.com.tw', a['href'])
        g['href'] = g['href'] or urljoin('https://www.newamazing.com.tw', a['href'])
        im = th.select_one('.product_img img')
        g['img'] = g['img'] or (urljoin('https://www.newamazing.com.tw', im['src']) if im else None)
        dd = th.select_one('.product_days')
        g['days'] = g['days'] or num(dd.get_text() if dd else '')
    out = [tour('新魅力旅遊', t, g['href'], g['price'], g['days'], g['deps'], g['img']) for t, g in groups.items()]
    return [t for t in out if t['country'] != '台灣']


# ---------------- 燦星 ----------------
async def startravel(ctx):
    page = await ctx.new_page()
    got = []

    async def on_resp(r):
        if 'GetAllProductList?' in r.url and r.status == 200:
            try:
                got.append(await r.json())
            except Exception:
                pass
    page.on('response', on_resp)
    await goto(page, 'https://tour.startravel.com.tw/list/ALL/0?avl=1', 3000)
    await wait_for(page, lambda h: '請稍候' not in h and 'Just a moment' not in h, 30)
    end = (TODAY + dt.timedelta(days=365)).isoformat()
    for kw in REGION_KEYWORDS:
        try:
            r = await page.request.get('https://tour.startravel.com.tw/ajax/GetAllProductList?DeptArea=ALL&TravelCode='
                                       f'&SDate={TODAY}&EDate={end}&Keyword={quote(kw)}&TripStatus=0&Avl=1&SortType=0')
            if r.status == 200:
                got.append(await r.json())
            else:
                print('  startravel api', kw, r.status, flush=True)
        except Exception as e:
            print('  startravel api', kw, str(e)[:80], flush=True)
    await page.close()
    out = {}
    for d in got:
        for it in d.get('ProductList') or []:
            code = it.get('MGroupCode')
            name = it.get('ProductName')
            if not code or not name or code in out:
                continue
            img = it.get('ImgUrl') or ''
            out[code] = tour('燦星旅遊', name, f'https://tour.startravel.com.tw/detail/00001-{code}',
                             it.get('MinPrice'), it.get('TripDays'), [it.get('NearDeptDate')],
                             urljoin('https://tour.startravel.com.tw', img) if img else None,
                             it.get('TripNation') or '')
    print('  startravel lists captured:', len(got), flush=True)
    return list(out.values())


NOT_GROUP = re.compile(r'自由行|機加酒|機\+酒|機票|訂房|票券')


def dedupe(tours):
    seen = {}
    for t in tours:
        k = (t['title'], t['price'])
        if k in seen:
            seen[k]['departures'] = sorted(set(seen[k]['departures']) | set(t['departures']))
        else:
            seen[k] = t
    return list(seen.values())


SCRAPERS = {'雄獅旅遊': lion, '可樂旅遊': cola, '東南旅遊': settour, '易遊網': eztravel,
            '山富旅遊': travel4u, '喜鴻假期': besttour, '新魅力旅遊': newamazing,
            '燦星旅遊': startravel}


async def main(only=None):
    tours, failed, stats = [], [], {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(locale='zh-TW', user_agent=UA, viewport={'width': 1366, 'height': 900})
        for name, fn in SCRAPERS.items():
            if only and name not in only:
                continue
            try:
                got = await asyncio.wait_for(fn(ctx), timeout=1500)
                got = dedupe(t for t in got if t['title'] and t['url'] and '找到了' not in t['title']
                             and t['country'] != '台灣' and not NOT_GROUP.search(t['title']))
                print(f'{name}: {len(got)}', flush=True)
                if not got:
                    failed.append(name)
                tours += got
                stats[name] = len(got)
            except Exception:
                print(f'{name}: FAILED', flush=True)
                traceback.print_exc()
                failed.append(name)
        await b.close()
    data = {'updated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            'stats': stats, 'failed': failed, 'tours': tours}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'data', 'tours.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print('total', len(tours), 'failed', failed)


if __name__ == '__main__':
    asyncio.run(main(set(sys.argv[1:]) or None))
