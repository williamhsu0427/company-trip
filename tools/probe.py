"""Probe agency sites: save rendered HTML and the JSON/XHR requests each page makes."""
import json, os, re, sys, asyncio
from playwright.async_api import async_playwright

OUT = 'probe'
URLS = [l.strip() for l in open('tools/probe_urls.txt', encoding='utf-8') if l.strip() and not l.startswith('#')]

async def one(ctx, i, url):
    page = await ctx.new_page()
    reqs = []
    async def on_resp(r):
        try:
            ct = r.headers.get('content-type', '')
            if r.request.resource_type in ('xhr', 'fetch') or 'json' in ct:
                body = ''
                try:
                    body = (await r.text())[:3000]
                except Exception:
                    pass
                reqs.append({'url': r.url, 'method': r.request.method, 'status': r.status, 'ct': ct,
                             'post': (r.request.post_data or '')[:1500], 'body': body})
        except Exception:
            pass
    page.on('response', on_resp)
    info = {'url': url}
    try:
        resp = await page.goto(url, wait_until='domcontentloaded', timeout=70000)
        info['status'] = resp.status if resp else None
        try:
            await page.wait_for_load_state('networkidle', timeout=20000)
        except Exception:
            pass
        await page.mouse.wheel(0, 4000)
        await page.wait_for_timeout(3000)
        info['final'] = page.url
        info['title'] = await page.title()
        html = await page.content()
        open(f'{OUT}/{i:02d}.html', 'w', encoding='utf-8').write(html)
        links = await page.eval_on_selector_all('a[href]', 'els=>els.map(e=>[e.href,(e.innerText||"").trim().slice(0,40)])')
        info['links'] = links[:800]
    except Exception as e:
        info['error'] = repr(e)
    info['xhr'] = reqs
    json.dump(info, open(f'{OUT}/{i:02d}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(i, url, info.get('status'), info.get('title'), len(reqs), info.get('error', ''), flush=True)
    await page.close()

async def main():
    os.makedirs(OUT, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(locale='zh-TW', viewport={'width': 1366, 'height': 900},
                                  user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36')
        for i, u in enumerate(URLS):
            await one(ctx, i, u)
        await b.close()

asyncio.run(main())
