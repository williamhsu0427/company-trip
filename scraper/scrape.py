"""Collect overseas group tours from Taiwanese travel agencies into data/tours.json.

Each agency scraper is independent: if one breaks (site redesign, bot block),
the others still run and the agency is listed under "failed" so the page can
point people to the one-tap search instead.
"""
import asyncio, datetime as dt, json, os, re, sys, time, traceback
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from countries import guess_country
from curl_cffi import requests as curl


def http():
    """A Chrome-like HTTP client; plain clients get bot-checked on some sites."""
    return curl.Session(impersonate='chrome')

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


FIT = re.compile(r'自由行|旅行票|旅遊票|機加酒|機\+酒|半自助|自由配|\d天\d夜|酒店\d晚|含每日早|未稅|機票')


def tour(agency, title, url, price=None, days=None, deps=(), image=None, place=''):
    title = re.sub(r'\s+', ' ', title or '').strip()
    return {
        'agency': agency, 'title': title, 'url': url,
        'country': guess_country(place, title),
        'type': 'fit' if FIT.search(title) else 'group',
        'days': days or days_from(title), 'price': price,
        'departures': sorted({d for d in deps if d}), 'image': image,
    }


async def goto(page, url, wait=2500, idle=True):
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
    except Exception as e:
        print('  slow page:', url, str(e)[:80], flush=True)
    if idle:
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
# TravelType 1 = 團體旅遊, 2 = 自由行／小包團 (both include domestic trips, filtered out later).
def lion_sync():
    out = []
    end = (TODAY + dt.timedelta(days=365)).isoformat()
    for tt in (1, 2):
        pg = 1
        while True:
            body = {"ArriveID": None, "GoDatestart": TODAY.isoformat(), "GroupID": None, "Keywords": None,
                    "IsEnsureGroup": None, "IsSold": None, "ThemeID": None, "TravelPavilionGroupID": None,
                    "KeywordsCity": None, "TravelType": tt, "BuIDs": "", "PreferAirlines": None, "GoDateEnd": end,
                    "DepartureID": "", "WeekDay": "", "PriceList": None, "AirlineIDs": "", "TripTypes": "", "Tags": "",
                    "SortType": 3, "Days": "", "MarketingProjects": "", "Page": pg, "PageSize": 200}
            for attempt in range(4):
                try:
                    d = http().post('https://travel.liontravel.com/search/grouplistinfojson', json=body, timeout=60).json()
                    break
                except Exception as e:
                    print('  lion retry', tt, pg, type(e).__name__, flush=True)
                    time.sleep(5)
            else:
                raise RuntimeError('lion API unreachable')
            items = d.get('NormGroupList') or []
            for it in items:
                gl = it.get('GroupList') or []
                gid = gl[0]['GroupID'] if gl else ''
                url = (f"https://travel.liontravel.com/detail?NormGroupID={it['NormGroupID']}"
                       f"&GroupID={gid}&TourSource={it.get('TourSource') or 'Lion'}")
                price = num((it.get('DisplayPrice') or {}).get('Price')) or num(it.get('GroupStraightPrice'))
                t = tour('雄獅旅遊', it.get('TourName'), url, price, it.get('TourDays'),
                         [ymd(g.get('GoDate')) for g in gl], it.get('ImgM') or it.get('Img'))
                if tt == 2:
                    t['type'] = 'fit'
                out.append(t)
            if pg >= (d.get('TotalPage') or 1) or not items:
                break
            pg += 1
        print(f'  lion TravelType {tt}: {len(out)} so far', flush=True)
    return out


async def lion(ctx):
    return await asyncio.to_thread(lion_sync)


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
# The search page's GraphQL API lists one row per departure, 10 per page; a blank keyword returns everything.
SETTOUR_Q = """query q($begDt: String!, $endDt: String!, $keyWord: String, $pageNo: Int!) {
  getGfgSearchGrup(depCityCd: "", travelCd: "", begDt: $begDt, endDt: $endDt, keyWord: $keyWord, isOrder: true,
    isMiniTour: true, portTp: "B2C", pageNo: $pageNo, sort: "BA", companyNo: "", minPrice: -1, maxPrice: -1,
    isOffer: false, isMkt: false, isPakFly: false, isChaFly: false, isNoShopping: false, isNoPay: false,
    isGoEarly: false, isLateReturn: false, isOnlyMiniTour: false, week: "") {
    data { totalPage grup { prodNo grupNm begDt tourDays allowQtyRemark price { b2cPrice } } }
  }
}"""


def settour_page(pg):
    v = {'begDt': TODAY.strftime('%Y%m%d'), 'endDt': (TODAY + dt.timedelta(days=365)).strftime('%Y%m%d'),
         'keyWord': ' ', 'pageNo': pg}
    for attempt in range(3):
        try:
            d = http().post('https://gql.settour.com.tw/graphql', json={'query': SETTOUR_Q, 'variables': v},
                            headers={'origin': 'https://tour.settour.com.tw'}, timeout=60).json()
            return d['data']['getGfgSearchGrup']['data']
        except Exception as e:
            print('  settour retry', pg, type(e).__name__, flush=True)
    return {'totalPage': 0, 'grup': []}


def settour_sync():
    from concurrent.futures import ThreadPoolExecutor
    first = settour_page(1)
    with ThreadPoolExecutor(10) as ex:
        pages = [first] + list(ex.map(settour_page, range(2, (first['totalPage'] or 1) + 1)))
    out = {}
    for p in pages:
        for g in p.get('grup') or []:
            no = g['prodNo']
            price = num((g.get('price') or {}).get('b2cPrice'))
            t = out.get(no)
            if not t:
                img = f'https://photo.settour.com.tw/330x220/https://www.settour.com.tw/ss_img/GFG/{no[3:7]}/{no[7:11]}/{no[11:]}/{no}_m.jpg'
                t = out[no] = tour('東南旅遊', g['grupNm'], f'https://tour.settour.com.tw/product/{no}', price,
                                   g.get('tourDays'), [], img)
            if price and (not t['price'] or price < t['price']):
                t['price'] = price
            if g.get('allowQtyRemark') != '額滿':
                t['departures'] = sorted(set(t['departures']) | {ymd(g.get('begDt'))})
    print(f'  settour: {len(pages)} pages, {len(out)} products', flush=True)
    return [t for t in out.values() if t['country'] != '台灣']


async def settour(ctx):
    return await asyncio.to_thread(settour_sync)


# ---------------- 易遊網 ----------------
# ezTravel also resells other agencies' tours; the operator is kept in 'operator'.
# Searching for 日／天 matches nearly every title ("五日", "5天"), the region words catch the rest.
EZ_WORDS = ['日', '天', '郵輪'] + REGION_KEYWORDS


def ez_page(q, tt, pg):
    url = f'https://vacation.eztravel.com.tw/pkgfrn/keywords/?q={quote(q)}&tt={tt}&avbl=1&page={pg}&pageSize=100'
    for attempt in range(3):
        try:
            h = http().get(url, timeout=90).text
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S)
            st = json.loads(m.group(1))['props']['pageProps']['initialState']['search']
            return st['searchResults'] or [], int(st['searchInfo']['pageConfig']['total'] or 0)
        except Exception as e:
            print('  ezTravel retry', q, pg, type(e).__name__, flush=True)
    return [], 0


def eztravel_sync():
    from concurrent.futures import ThreadPoolExecutor
    out = {}

    def add(rows, q):
        for r in rows:
            key = r.get('pfGProdNo') or r.get('prodUrl')
            op = (r.get('travelAgency') or '').replace('出團', '')
            if '找到了' in op:
                continue
            deps = [ymd(x.get('saleDt')) for x in (r.get('otherSaleDts') or []) if x.get('fullStatus') != 'FULL'] or [ymd(r.get('saleDt'))]
            if key in out:
                out[key]['departures'] = sorted(set(out[key]['departures']) | {d for d in deps if d})
                continue
            t = tour('易遊網', r.get('prodNm'), r.get('prodUrl'), r.get('minPrice1') or r.get('ezPrice1'),
                     r.get('travelDay'), deps, r.get('imgUrl'), ' '.join(r.get('tourCitysNm') or []))
            t['operator'] = op
            out[key] = t

    jobs = []
    for q in EZ_WORDS:
        for tt in (1, 2):
            rows, total = ez_page(q, tt, 1)
            add(rows, q)
            jobs += [(q, tt, pg) for pg in range(2, (total + 99) // 100 + 1)]
    print(f'  ezTravel: {len(jobs)} more pages', flush=True)
    with ThreadPoolExecutor(6) as ex:
        for (q, tt, pg), (rows, _) in zip(jobs, ex.map(lambda j: ez_page(*j), jobs)):
            add(rows, q)
    return [t for t in out.values() if t['country'] != '台灣']


async def eztravel(ctx):
    return await asyncio.to_thread(eztravel_sync)


# ---------------- 山富 ----------------
# The keyword search lists every itinerary (10 per page); a blank keyword returns nothing, so search by region.
T4U_WORDS = REGION_KEYWORDS + ['中國', '香港', '澳門', '郵輪', '中東', '非洲', '中亞', '南亞', '島嶼']


def t4u_page(kw, pg):
    url = (f'https://www.travel4u.com.tw/group/search/?keyword={quote(kw)}&date_start={TODAY}'
           f'&date_end={TODAY + dt.timedelta(days=365)}&page={pg}')
    for attempt in range(3):
        try:
            return http().get(url, timeout=60).text
        except Exception as e:
            print('  travel4u retry', kw, pg, type(e).__name__, flush=True)
    return ''


def travel4u_sync():
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        firsts = list(ex.map(lambda k: t4u_page(k, 1), T4U_WORDS))
        jobs = []
        for kw, h in zip(T4U_WORDS, firsts):
            last = max([int(x) for x in re.findall(r'[?&]page=(\d+)', h)] or [1])
            jobs += [(kw, pg) for pg in range(2, last + 1)]
        print(f'  travel4u: {len(jobs) + len(firsts)} pages', flush=True)
        pages = firsts + list(ex.map(lambda j: t4u_page(*j), jobs))
    out = {}
    for h in pages:
        for box in BeautifulSoup(h, 'lxml').select('div.product'):
            name = box.select_one('h6.tourCard.name')
            a = name.find_parent('a') if name else None
            if not a:
                continue
            href = urljoin('https://www.travel4u.com.tw', a['href'])
            if href in out:
                continue
            txt = box.get_text(' ', strip=True)
            pr = re.search(r'\$([\d,]+)', txt)
            dd = re.search(r'(\d+)\s*天', txt)
            img = box.find('img')
            place = re.search(r'天\s*(.*?)\s*\S*出發', txt)
            out[href] = tour('山富旅遊', name.get_text(strip=True), href, num(pr.group(1)) if pr else None,
                             int(dd.group(1)) if dd else None,
                             [mmdd_to_date(x) for x in re.findall(r'\b\d{1,2}/\d{1,2}\b', txt)],
                             img.get('src') if img else None, place.group(1) if place else '')
    return [t for t in out.values() if t['country'] != '台灣']


async def travel4u(ctx):
    return await asyncio.to_thread(travel4u_sync)


# ---------------- 喜鴻 ----------------
# One row per departure, 100 per page.
def besttour_page(pg):
    for attempt in range(3):
        try:
            r = http().post(f'https://travelapi.besttour.com.tw/api/query_List_all.asp?pageid={pg}&pagesize=100', timeout=60)
            return json.loads(r.text)
        except Exception as e:
            print('  besttour retry', pg, type(e).__name__, flush=True)
    return {}


def besttour_sync():
    from concurrent.futures import ThreadPoolExecutor
    first = besttour_page(1)
    with ThreadPoolExecutor(8) as ex:
        pages = [first] + list(ex.map(besttour_page, range(2, int(first.get('pagecount') or 1) + 1)))
    print(f'  besttour: {len(pages)} pages', flush=True)
    groups = {}
    for d in pages:
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
    out = []
    for name, g in groups.items():
        it = g['it']
        img = it.get('img') or ''
        out.append(tour('喜鴻假期', name, f"https://www.besttour.com.tw/itinerary/{it.get('id')}", g['price'],
                        num(it.get('day')), g['deps'], ('https:' + img) if img.startswith('//') else img or None,
                        it.get('city', '')))
    return out


async def besttour(ctx):
    return await asyncio.to_thread(besttour_sync)


# ---------------- 新魅力 ----------------
def newamazing_page(pg):
    end = (TODAY + dt.timedelta(days=365)).strftime('%Y/%m/%d')
    for attempt in range(3):
        try:
            s = http()
            s.get('https://www.newamazing.com.tw/EW/GO/GroupList.asp', timeout=60)
            h = s.post('https://www.newamazing.com.tw/EW/GO/GroupList.asp?tabList=GO', data={
                'displayType': 'G', 'orderCd': '1', 'pageALL': str(pg), 'pageGO': str(pg), 'pagePGO': str(pg),
                'beginDt': TODAY.strftime('%Y/%m/%d'), 'endDt': end, 'allowJoin': '1', 'allowWait': '1'},
                timeout=60).text
            if '共' in h:
                return h
        except Exception as e:
            print('  newamazing retry', pg, type(e).__name__, flush=True)
    return ''


def newamazing_pages():
    # One row per departure, 20 per page, sorted by date; there is no page count, so fetch until pages come back empty.
    from concurrent.futures import ThreadPoolExecutor
    html, start = [], 1
    with ThreadPoolExecutor(8) as ex:
        while start < 1000:
            batch = list(ex.map(newamazing_page, range(start, start + 40)))
            html += [h for h in batch if 'product_num' in h]
            if not any('product_num' in h for h in batch[-5:]):
                break
            start += 40
    return html


async def newamazing(ctx):
    html = await asyncio.to_thread(newamazing_pages)
    print('  newamazing pages:', len(html), flush=True)
    s = BeautifulSoup(''.join(html), 'lxml')
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
# Cloudflare blocks headless Chrome here but lets a Chrome-like TLS client through.
def startravel_sync():
    s = http()
    areas = s.get('https://tour.startravel.com.tw/Ajax/GetDestinationList', timeout=60).json()
    codes = [b['TravelCodeLv2'] for a in areas if a['TravelCodeLv1'] != '800' for b in a.get('TravelLv2') or []]
    end = TODAY + dt.timedelta(days=365)
    out = {}
    for code in codes:
        try:
            d = s.get(f'https://tour.startravel.com.tw/ajax/GetAllProductList?DeptArea=&TravelCode={code}'
                      f'&SDate={TODAY}&EDate={end}&Keyword=&TripStatus=0&Avl=1&SortType=0', timeout=90).json()
        except Exception as e:
            print('  startravel failed', code, type(e).__name__, flush=True)
            continue
        for it in d.get('ProductList') or []:
            code_ = it.get('MGroupCode')
            name = it.get('ProductName')
            if not code_ or not name:
                continue
            dep = it.get('NearDeptDate')
            if code_ in out:
                if dep:
                    out[code_]['departures'] = sorted(set(out[code_]['departures']) | {dep})
                continue
            img = it.get('ImgUrl') or ''
            out[code_] = tour('燦星旅遊', name, f'https://tour.startravel.com.tw/detail/00001-{code_}',
                              it.get('MinPrice'), it.get('TripDays'), [dep],
                              urljoin('https://tour.startravel.com.tw', img) if img else None,
                              (it.get('TripNation') or '') + (it.get('TripCity') or ''))
    print('  startravel products:', len(out), flush=True)
    return list(out.values())


async def startravel(ctx):
    return await asyncio.to_thread(startravel_sync)


# Plain air tickets / hotel rooms / attraction tickets are not trips: drop them unless they have a day count.
NOT_GROUP = re.compile(r'^(?!.*\d\s*[日天]).*(機票|訂房|票券|門票)')


def dedupe(tours):
    seen = {}
    for t in tours:
        k = (t['title'], t['price'])
        if k in seen:
            seen[k]['departures'] = sorted(set(seen[k]['departures']) | set(t['departures']))
        else:
            seen[k] = t
    return list(seen.values())


def norm_title(s):
    return re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]', '', s or '')


def drop_resold_duplicates(tours):
    """ezTravel resells tours we also collect straight from the operator; keep the operator's copy."""
    direct = {(t['agency'], norm_title(t['title'])): t for t in tours if t['agency'] != '易遊網'}
    out = []
    for t in tours:
        d = direct.get((t.get('operator'), norm_title(t['title']))) if t['agency'] == '易遊網' else None
        if d:
            d['departures'] = sorted(set(d['departures']) | set(t['departures']))
        else:
            out.append(t)
    return out


SCRAPERS = {'雄獅旅遊': lion, '可樂旅遊': cola, '東南旅遊': settour, '易遊網': eztravel,
            '山富旅遊': travel4u, '喜鴻假期': besttour, '新魅力旅遊': newamazing,
            '燦星旅遊': startravel}


async def main(only=None):
    tours, failed, stats = [], [], {}
    async with async_playwright() as p:
        b = await p.chromium.launch(**({'proxy': {'server': os.environ['HTTPS_PROXY']}} if os.environ.get('HTTPS_PROXY') else {}))
        async def run(name, fn):
            # Each agency gets its own browser session so they can run side by side.
            c = await b.new_context(locale='zh-TW', user_agent=UA, viewport={'width': 1366, 'height': 900})
            try:
                got = await asyncio.wait_for(fn(c), timeout=1500)
                raw = len(got)
                got = dedupe(t for t in got if t['title'] and t['url'] and '找到了' not in t['title']
                             and t['country'] != '台灣' and not NOT_GROUP.search(t['title']))
                print(f'{name}: {len(got)} (raw {raw})', flush=True)
                return name, got
            except Exception:
                print(f'{name}: FAILED', flush=True)
                traceback.print_exc()
                return name, None
            finally:
                await c.close()

        jobs = [run(n, f) for n, f in SCRAPERS.items() if not only or n in only]
        for name, got in await asyncio.gather(*jobs):
            if not got:
                failed.append(name)
            tours += got or []
            stats[name] = len(got or [])
        await b.close()
    tours = drop_resold_duplicates(tours)
    stats['易遊網'] = sum(t['agency'] == '易遊網' for t in tours)
    # If an agency broke today (site change, bot check), keep yesterday's tours for it instead of dropping them.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'data', 'tours.json')
    try:
        prev = json.load(open(path, encoding='utf-8')).get('tours') or []
    except Exception:
        prev = []
    for name in SCRAPERS:
        old = [t for t in prev if t['agency'] == name]
        for t in old:
            c = guess_country('', t['title'])
            t['country'] = t['country'] if c == '其他' else c
            t['type'] = 'fit' if FIT.search(t['title']) else 'group'
        old = [t for t in old if t['country'] != '台灣' and not NOT_GROUP.search(t['title'])]
        if len(old) > stats.get(name, 0) * 3 and len(old) > 20:
            print(f'{name}: keeping {len(old)} tours from the previous run', flush=True)
            tours = [t for t in tours if t['agency'] != name] + old
    data = {'updated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
            'stats': stats, 'failed': failed, 'tours': tours}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print('total', len(tours), 'failed', failed)


if __name__ == '__main__':
    asyncio.run(main(set(sys.argv[1:]) or None))
