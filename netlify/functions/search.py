import json
import re
import statistics
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests as http_requests
from bs4 import BeautifulSoup

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}

# Shorter timeouts for serverless (Netlify has a 10s function limit)
_TIMEOUT = 7

# ──────────────────────────────────────────────
# Relevance filter
# ──────────────────────────────────────────────

_STOP_WORDS = {'the','a','an','and','or','for','of','in','on','to','with','is','by','at','from','as','it','be','this','that','are','was','were','been','has','have','had','do','does','did','but','not','so','if','no','all','my','your','our','its','his','her','new','up','out','one','two','set','get','can','will','just','into'}

def is_relevant(title, query):
    if not title or not query:
        return False
    title_lower = title.lower()
    title_words = set(re.split(r'\W+', title_lower))
    keywords = [w for w in re.split(r'\W+', query.lower()) if w and len(w) > 2 and w not in _STOP_WORDS]
    if not keywords:
        keywords = [w for w in re.split(r'\W+', query.lower()) if w and len(w) > 1]
    if not keywords:
        return True

    def keyword_matches(kw):
        if kw in title_lower:
            return True
        for tw in title_words:
            if len(tw) >= 3 and len(kw) >= 3:
                if tw.startswith(kw[:min(len(kw), len(tw))]) or kw.startswith(tw[:min(len(kw), len(tw))]):
                    shared = min(len(kw), len(tw))
                    if kw[:shared] == tw[:shared] and abs(len(kw) - len(tw)) <= 3:
                        return True
        return False

    matches = sum(1 for kw in keywords if keyword_matches(kw))
    if len(keywords) <= 3:
        return matches == len(keywords)
    return matches >= max(2, int(len(keywords) * 0.6))


# ──────────────────────────────────────────────
# Scrapers
# ──────────────────────────────────────────────

def scrape_amazon(query):
    try:
        url = f"https://www.amazon.com/s?k={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.content, 'html.parser')
        for item in soup.find_all('div', {'data-component-type': 's-search-result'}):
            title_el = item.find('h2')
            price_el = item.find('span', class_='a-price-whole')
            frac_el = item.find('span', class_='a-price-fraction')
            link_el = item.find('a', class_='a-link-normal', href=True)
            if title_el and price_el and link_el:
                title = title_el.get_text(strip=True)
                if not is_relevant(title, query):
                    continue
                whole = price_el.get_text(strip=True).replace(',', '').rstrip('.')
                frac = frac_el.get_text(strip=True) if frac_el else '00'
                price = float(f"{whole}.{frac}")
                href = link_el['href']
                link = href if href.startswith('http') else f"https://www.amazon.com{href}"
                return {'title': title, 'price': price, 'url': link, 'source': 'Amazon'}
    except Exception:
        pass
    return None


def scrape_newegg(query):
    try:
        url = f"https://www.newegg.com/p/pl?d={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.content, 'html.parser')
        for item in soup.find_all('div', class_='item-cell'):
            title_el = item.find('a', class_='item-title')
            price_el = item.find('li', class_='price-current')
            if title_el and price_el:
                title = title_el.get_text(strip=True)
                if not is_relevant(title, query):
                    continue
                price_text = price_el.get_text(strip=True)
                m = re.search(r'([\d,]+\.\d{2})', price_text)
                if m:
                    price = float(m.group(1).replace(',', ''))
                    href = title_el.get('href', '')
                    link = href if href.startswith('http') else f"https://www.newegg.com{href}"
                    return {'title': title, 'price': price, 'url': link, 'source': 'Newegg'}
    except Exception:
        pass
    return None


def scrape_ebay(query):
    try:
        url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.content, 'html.parser')
        for item in soup.find_all('div', class_='s-item__info'):
            title_el = item.find('div', class_='s-item__title') or item.find('span', role='heading')
            price_el = item.find('span', class_='s-item__price')
            if not title_el or not price_el:
                continue
            title = title_el.get_text(strip=True)
            if not title or title == 'Shop on eBay':
                continue
            if not is_relevant(title, query):
                continue
            price_text = price_el.get_text(strip=True)
            m = re.search(r'\$([\d,]+\.?\d*)', price_text)
            if not m:
                continue
            price = float(m.group(1).replace(',', ''))
            parent = item.find_parent('div', class_='s-item__wrapper') or item.find_parent('li')
            link_el = parent.find('a', href=True) if parent else None
            link = link_el['href'] if link_el else url
            if '?' in link:
                link = link.split('?')[0]
            return {'title': title, 'price': price, 'url': link, 'source': 'eBay'}
    except Exception:
        pass
    return None


def scrape_walmart(query):
    try:
        url = f"https://www.walmart.com/search?q={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        content = r.text
        pm = re.search(r'"priceInfo":\{"currentPrice":\{"price":([\d.]+)', content)
        if not pm:
            pm = re.search(r'"currentPrice":\{"price":([\d.]+)', content)
        tm = re.search(r'"name":"([^"]{10,150})"', content)
        im = re.search(r'"usItemId":"(\d+)"', content)
        if pm and tm:
            price = float(pm.group(1))
            title = tm.group(1)
            if is_relevant(title, query):
                item_id = im.group(1) if im else ''
                link = f"https://www.walmart.com/ip/{item_id}" if item_id else url
                return {'title': title, 'price': price, 'url': link, 'source': 'Walmart'}
    except Exception:
        pass
    return None


def scrape_officedepot(query):
    try:
        url = f"https://www.officedepot.com/catalog/search.do?Ntt={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.content, 'html.parser')
        for card in soup.find_all('div', class_='od-product-card'):
            title = None
            href = None
            for a in card.find_all('a', href=True):
                t = a.get_text(strip=True)
                if t and len(t) >= 5:
                    title = t
                    href = a['href']
                    break
            if not title or not href:
                continue
            if not is_relevant(title, query):
                continue
            full_url = href if href.startswith('http') else f"https://www.officedepot.com{href}"
            text = card.get_text()
            pm = re.search(r'\$([\d,]+\.\d{2})', text)
            if pm:
                price = float(pm.group(1).replace(',', ''))
                return {'title': title, 'price': price, 'url': full_url, 'source': 'Office Depot'}
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────

SCRAPERS = [scrape_amazon, scrape_newegg, scrape_ebay, scrape_officedepot, scrape_walmart]

FALLBACK_RETAILERS = [
    ('Target',    'https://www.target.com/s?searchTerm='),
    ('Best Buy',  'https://www.bestbuy.com/site/searchpage.jsp?st='),
    ('Costco',    'https://www.costco.com/CatalogSearch?dept=All&keyword='),
    ('Home Depot','https://www.homedepot.com/s/'),
    ('eBay',      'https://www.ebay.com/sch/i.html?_nkw='),
    ('Walmart',   'https://www.walmart.com/search?q='),
]


def search_all(query):
    results = []
    seen = set()

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn, query): fn.__name__ for fn in SCRAPERS}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result and result['source'] not in seen:
                    seen.add(result['source'])
                    results.append(result)
            except Exception:
                pass

    if len(results) < 6:
        for retailer, url_base in FALLBACK_RETAILERS:
            if len(results) >= 10 or retailer in seen:
                continue
            seen.add(retailer)
            results.append({
                'title': f'Search {retailer} for "{query}"',
                'price': None,
                'url': f'{url_base}{quote_plus(query)}',
                'source': f'{retailer} (search link)',
            })

    return results


# ──────────────────────────────────────────────
# Netlify Function handler
# ──────────────────────────────────────────────

def handler(event, context):
    # CORS headers
    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
    }

    # Handle preflight
    if event.get('httpMethod') == 'OPTIONS':
        return {'statusCode': 204, 'headers': headers, 'body': ''}

    try:
        body = json.loads(event.get('body', '{}'))
        query = body.get('query', '').strip()
    except Exception:
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({'error': 'Invalid request body'}),
        }

    if not query:
        return {
            'statusCode': 400,
            'headers': headers,
            'body': json.dumps({'error': 'Please enter a search term'}),
        }

    results = search_all(query)

    prices = [r['price'] for r in results if r.get('price') is not None]
    stats = {}
    if prices:
        stats = {
            'average': round(statistics.mean(prices), 2),
            'lowest': round(min(prices), 2),
            'highest': round(max(prices), 2),
            'median': round(statistics.median(prices), 2),
            'count': len(prices),
        }

    return {
        'statusCode': 200,
        'headers': headers,
        'body': json.dumps({
            'query': query,
            'results': results,
            'stats': stats,
        }),
    }
