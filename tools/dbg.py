import asyncio, json, re, sys, datetime as dt
sys.path.insert(0, 'scraper')
from playwright.async_api import async_playwright
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36'
TODAY = dt.date.today()

def save(name, obj):
    open(f'dbg/{name}', 'w', encoding='utf-8').write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1))

async def capture(b, name, url, scroll=3, wait=4000):
    c = await b.new_context(locale='zh-TW', user_agent=UA, viewport={'width': 1366, 'height': 900})
    page = await c.new_page()
    reqs = []
    async def on_resp(r):
        try:
            if r.request.resource_type in ('xhr', 'fetch'):
                body = ''
                try: body = (await r.text())[:4000]
                except Exception: pass
                reqs.append({'url': r.url, 'm': r.request.method, 's': r.status, 'post': (r.request.post_data or '')[:3000], 'body': body})
        except Exception: pass
    page.on('response', on_resp)
    try:
        await page.goto(url, wait_until='domcontentloaded', timeout=60000)
        try: await page.wait_for_load_state('networkidle', timeout=20000)
        except Exception: pass
        for _ in range(scroll):
            await page.mouse.wheel(0, 5000); await page.wait_for_timeout(1500)
        await page.wait_for_timeout(wait)
        save(name + '.html', await page.content())
        print(name, 'title', await page.title(), 'reqs', len(reqs), flush=True)
    except Exception as e:
        print(name, 'ERR', e, flush=True)
    save(name + '.xhr.json', reqs)
    await c.close()

async def lion_api(b):
    c = await b.new_context(locale='zh-TW', user_agent=UA)
    page = await c.new_page()
    await page.goto('https://travel.liontravel.com/search?TravelType=2', wait_until='domcontentloaded', timeout=60000)
    end = (TODAY + dt.timedelta(days=365)).isoformat()
    for ps in (50, 20):
        body = {"ArriveID": None, "GoDatestart": TODAY.isoformat(), "GroupID": None, "Keywords": None,
                "IsEnsureGroup": None, "IsSold": None, "ThemeID": None, "TravelPavilionGroupID": None,
                "KeywordsCity": None, "TravelType": 2, "BuIDs": "", "PreferAirlines": None, "GoDateEnd": end,
                "DepartureID": "", "WeekDay": "", "PriceList": None, "AirlineIDs": "", "TripTypes": "", "Tags": "",
                "SortType": 3, "Days": "", "MarketingProjects": "", "Page": 1, "PageSize": ps}
        r = await page.request.post('https://travel.liontravel.com/search/grouplistinfojson', data=body, headers={'content-type': 'application/json'})
        d = await r.json()
        print('lion ps', ps, {k: v for k, v in d.items() if not isinstance(v, (list, dict))}, len(d.get('NormGroupList') or []), flush=True)
        save(f'lion_api_{ps}.json', d)
    await c.close()

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        await asyncio.gather(
            lion_api(b),
            capture(b, 'lion_search', 'https://travel.liontravel.com/search?TravelType=2', scroll=6),
            capture(b, 'star_list', 'https://tour.startravel.com.tw/list/ALL/0?avl=1', wait=15000),
            capture(b, 'ez_kw', 'https://vacation.eztravel.com.tw/pkgfrn/keywords/?q=%E6%97%A5%E6%9C%AC&tt=1&avbl=1', scroll=4),
        )
        await b.close()

asyncio.run(main())
