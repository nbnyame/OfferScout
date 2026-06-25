from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests as http_requests
from bs4 import BeautifulSoup
import re
from urllib.parse import quote_plus
import statistics
import time
import threading
import os
import sys
import json
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

# ──────────────────────────────────────────────
# Abacus.AI workflow configuration (read from environment, never hardcoded)
# ──────────────────────────────────────────────
# The prediction endpoint is the org-specific public host (NOT api.abacus.ai,
# which redirects to an internal cluster). Overridable via ABACUS_ENDPOINT.
ABACUS_ENDPOINT = os.environ.get(
    "ABACUS_ENDPOINT",
    "https://winmarkcorporation.abacus.ai/api/v0/executeAgent",
)
ABACUS_DEPLOYMENT_ID = os.environ.get("ABACUS_DEPLOYMENT_ID")
ABACUS_DEPLOYMENT_TOKEN = os.environ.get("ABACUS_DEPLOYMENT_TOKEN")

# Management API host + key used to auto-start a stopped deployment.
ABACUS_API_HOST = os.environ.get("ABACUS_API_HOST", "https://api.abacus.ai")
ABACUS_API_KEY = os.environ.get("ABACUS_API_KEY")

# Resolve paths for both script and PyInstaller exe
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'))
CORS(app)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
}

# ──────────────────────────────────────────────
# Heartbeat auto-shutdown: server exits when browser tab closes
# ──────────────────────────────────────────────
_last_heartbeat = time.time()
_HEARTBEAT_TIMEOUT = 10  # seconds without heartbeat before shutdown


# ──────────────────────────────────────────────
# Relevance filter
# ──────────────────────────────────────────────

# Common filler words to ignore when checking relevance
_STOP_WORDS = {'the','a','an','and','or','for','of','in','on','to','with','is','by','at','from','as','it','be','this','that','are','was','were','been','has','have','had','do','does','did','but','not','so','if','no','all','my','your','our','its','his','her','new','up','out','one','two','set','get','can','will','just','into'}

def is_relevant(title, query):
    """Check if a product title is relevant to the search query.
    For short queries (1-3 keywords), ALL keywords must appear.
    For longer queries (4+), at least 60% must appear.
    Includes stem matching so 'crib' matches 'cribs', 'club' matches 'clubs'.
    """
    if not title or not query:
        return False
    title_lower = title.lower()
    title_words = set(re.split(r'\W+', title_lower))
    # Extract meaningful keywords from the query (skip stop words and short words)
    keywords = [w for w in re.split(r'\W+', query.lower()) if w and len(w) > 2 and w not in _STOP_WORDS]
    if not keywords:
        keywords = [w for w in re.split(r'\W+', query.lower()) if w and len(w) > 1]
    if not keywords:
        return True

    def keyword_matches(kw):
        # Direct substring match
        if kw in title_lower:
            return True
        # Stem match: 'crib' matches 'cribs', 'club' matches 'clubs', etc.
        for tw in title_words:
            if len(tw) >= 3 and len(kw) >= 3:
                if tw.startswith(kw[:min(len(kw), len(tw))]) or kw.startswith(tw[:min(len(kw), len(tw))]):
                    # Ensure stems share at least 3 chars and are close in length
                    shared = min(len(kw), len(tw))
                    if kw[:shared] == tw[:shared] and abs(len(kw) - len(tw)) <= 3:
                        return True
        return False

    matches = sum(1 for kw in keywords if keyword_matches(kw))
    # Short queries: require ALL keywords; longer queries: require 60%+
    if len(keywords) <= 3:
        return matches == len(keywords)
    return matches >= max(2, int(len(keywords) * 0.6))


# ──────────────────────────────────────────────
# Individual retailer scrapers
# ──────────────────────────────────────────────

def scrape_amazon(query):
    """Amazon – works with plain requests."""
    try:
        url = f"https://www.amazon.com/s?k={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=12)
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
                print(f"  Amazon: ${price:.2f} – {title[:50]}")
                return {'title': title, 'price': price, 'url': link, 'source': 'Amazon'}
    except Exception as e:
        print(f"  Amazon error: {e}")
    return None


def scrape_newegg(query):
    """Newegg – works with plain requests."""
    try:
        url = f"https://www.newegg.com/p/pl?d={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=12)
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
                    print(f"  Newegg: ${price:.2f} – {title[:50]}")
                    return {'title': title, 'price': price, 'url': link, 'source': 'Newegg'}
    except Exception as e:
        print(f"  Newegg error: {e}")
    return None



def scrape_ebay(query):
    """eBay – try requests first (works, just needs longer timeout)."""
    try:
        url = f"https://www.ebay.com/sch/i.html?_nkw={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=20)
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
            # Get link from parent wrapper
            parent = item.find_parent('div', class_='s-item__wrapper') or item.find_parent('li')
            link_el = parent.find('a', href=True) if parent else None
            link = link_el['href'] if link_el else url
            # Clean tracking params from eBay links
            if '?' in link:
                link = link.split('?')[0]
            print(f"  eBay: ${price:.2f} – {title[:50]}")
            return {'title': title, 'price': price, 'url': link, 'source': 'eBay'}
    except Exception as e:
        print(f"  eBay error: {e}")
    return None


def scrape_walmart(query):
    """Walmart – requests with embedded JSON extraction."""
    try:
        url = f"https://www.walmart.com/search?q={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        content = r.text
        # Extract from embedded JSON data
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
                print(f"  Walmart: ${price:.2f} – {title[:50]}")
                return {'title': title, 'price': price, 'url': link, 'source': 'Walmart'}
    except Exception as e:
        print(f"  Walmart error: {e}")
    return None


def scrape_officedepot(query):
    """Office Depot – works with plain requests, 24 product cards per page."""
    try:
        url = f"https://www.officedepot.com/catalog/search.do?Ntt={quote_plus(query)}"
        r = http_requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.content, 'html.parser')
        for card in soup.find_all('div', class_='od-product-card'):
            # Find the link that has actual title text (skip image-only links)
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
                print(f"  Office Depot: ${price:.2f} – {title[:50]}")
                return {'title': title, 'price': price, 'url': full_url, 'source': 'Office Depot'}
    except Exception as e:
        print(f"  Office Depot error: {e}")
    return None


# ──────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────

SCRAPERS = [scrape_amazon, scrape_newegg, scrape_ebay, scrape_officedepot, scrape_walmart]

# Fallback search-link retailers if we still need more results
FALLBACK_RETAILERS = [
    ('Target',    'https://www.target.com/s?searchTerm='),
    ('Best Buy',  'https://www.bestbuy.com/site/searchpage.jsp?st='),
    ('Costco',    'https://www.costco.com/CatalogSearch?dept=All&keyword='),
    ('Home Depot','https://www.homedepot.com/s/'),
    ('eBay',      'https://www.ebay.com/sch/i.html?_nkw='),
    ('Walmart',   'https://www.walmart.com/search?q='),
]


def search_all(query):
    """Search all retailers in parallel and return up to 10 unique results."""
    results = []
    seen = set()
    print(f"\n{'='*60}")
    print(f"OfferScout search: '{query}'")
    print(f"{'='*60}")

    # All scrapers in parallel (no Playwright, all use requests)
    print("\n[Searching retailers]")
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fn, query): fn.__name__ for fn in SCRAPERS}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                if result and result['source'] not in seen:
                    seen.add(result['source'])
                    results.append(result)
            except Exception as e:
                print(f"  {name} failed: {e}")

    # Fallback search links for remaining slots
    if len(results) < 6:
        print("\n[Fallback search links]")
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
            print(f"  + {retailer} search link added")

    print(f"\nTotal results: {len(results)}")
    print('='*60)
    return results


# ──────────────────────────────────────────────
# Recall checker (CPSC SaferProducts.gov API)
# ──────────────────────────────────────────────

def check_recalls(query):
    """Check the CPSC SaferProducts.gov API for product recalls matching the query."""
    try:
        url = f"https://www.saferproducts.gov/RestWebServices/Recall?format=json&RecallTitle={quote_plus(query)}"
        r = http_requests.get(url, timeout=8)
        if r.status_code != 200:
            return []
        data = r.json()
        recalls = []
        for item in data[:5]:  # Limit to 5 most relevant
            recall = {
                'title': item.get('Title', 'Unknown Recall'),
                'date': item.get('RecallDate', ''),
                'description': '',
                'url': item.get('URL', ''),
                'hazard': '',
            }
            # Extract hazard description
            hazards = item.get('Hazards', [])
            if hazards and isinstance(hazards, list):
                recall['hazard'] = hazards[0].get('Name', '')
            # Extract product description
            products = item.get('Products', [])
            if products and isinstance(products, list):
                recall['description'] = products[0].get('Description', '')
            recalls.append(recall)
        print(f"  Recall check: {len(recalls)} recall(s) found for '{query}'")
        return recalls
    except Exception as e:
        print(f"  Recall check error: {e}")
        return []


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('static', 'index.html')


@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    """Frontend pings this every few seconds. If it stops, the server shuts down."""
    global _last_heartbeat
    _last_heartbeat = time.time()
    return jsonify({'status': 'ok'})


def _to_number(value):
    """Best-effort parse of a price-like value into a float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r'[\d]+(\.\d+)?', str(value).replace(',', ''))
    return float(m.group(0)) if m else None


def _parse_maybe_json(value):
    """Some Abacus responses embed the agent JSON as a string; parse it if so."""
    if isinstance(value, str):
        t = value.strip()
        if t.startswith('{') or t.startswith('['):
            try:
                return json.loads(t)
            except ValueError:
                pass
    return value


def _find_data_object(obj, depth=0):
    """Recursively locate the object holding the comparison data (the one with a
    `retailers` array or the summary fields)."""
    obj = _parse_maybe_json(obj)
    if obj is None or depth > 8:
        return None
    if isinstance(obj, list):
        for el in obj:
            found = _find_data_object(el, depth + 1)
            if found is not None:
                return found
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get('retailers'), list) or 'average_price' in obj or 'product_identified' in obj:
            return obj
        for value in obj.values():
            found = _find_data_object(value, depth + 1)
            if found is not None:
                return found
    return None


def _pick(d, keys):
    for k in keys:
        if d.get(k) not in (None, ''):
            return d[k]
    return None


def normalize_abacus(payload):
    """Normalise an Abacus payload into (results, stats, product)."""
    data = _find_data_object(payload) or {}
    product = _pick(data, ['product_identified', 'product', 'product_name']) or ''
    arr = data.get('retailers') if isinstance(data.get('retailers'), list) else \
        (data.get('results') if isinstance(data.get('results'), list) else [])

    results = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        source = _pick(item, ['retailer_name', 'retailer', 'source', 'store', 'seller', 'site'])
        price = _to_number(_pick(item, ['price', 'current_price', 'amount', 'value', 'cost']))
        if price == 0:
            price = None  # 0 means "price unavailable"
        url = _pick(item, ['product_url', 'url', 'link', 'href'])
        results.append({
            'title': source or product or 'Retailer',
            'price': price,
            'url': url or '#',
            'source': product or 'Offer',
        })

    prices = [r['price'] for r in results if r['price'] is not None]
    stats = {}
    if prices:
        lowest_obj = data.get('lowest_price') or {}
        highest_obj = data.get('highest_price') or {}
        lowest = _to_number(lowest_obj.get('value')) if isinstance(lowest_obj, dict) else None
        highest = _to_number(highest_obj.get('value')) if isinstance(highest_obj, dict) else None
        average = _to_number(data.get('average_price'))
        count = _to_number(data.get('total_retailers_found'))
        stats = {
            'average': round(average if average is not None else statistics.mean(prices), 2),
            'lowest': round(lowest if lowest is not None else min(prices), 2),
            'highest': round(highest if highest is not None else max(prices), 2),
            'median': round(statistics.median(prices), 2),
            'count': int(count) if count is not None else len(prices),
        }
    return results, stats, product


def _is_deployment_stopped(resp):
    """Abacus returns HTTP 503 "DNS resolution failure" when the deployment is
    STOPPED (auto-stopped after inactivity)."""
    return resp.status_code == 503 and 'DNS resolution failure' in (resp.text or '')


def _start_deployment_and_wait():
    """Start the stopped deployment and poll until it reports ACTIVE.
    Returns True if it became active within the timeout, else False."""
    if not ABACUS_API_KEY:
        return False

    print("  Abacus deployment is stopped — sending startDeployment...")
    try:
        http_requests.post(
            f"{ABACUS_API_HOST}/api/v0/startDeployment",
            headers={'Content-Type': 'application/json', 'apiKey': ABACUS_API_KEY},
            json={'deploymentId': ABACUS_DEPLOYMENT_ID},
            timeout=30,
        )
    except Exception as e:
        print(f"  startDeployment failed: {e}")
        return False

    # Poll up to ~3 minutes for the deployment to spin up.
    for _ in range(30):
        time.sleep(6)
        try:
            r = http_requests.get(
                f"{ABACUS_API_HOST}/api/v0/describeDeployment",
                headers={'apiKey': ABACUS_API_KEY},
                params={'deploymentId': ABACUS_DEPLOYMENT_ID},
                timeout=30,
            )
            status = (r.json().get('result') or {}).get('status')
            if status == 'ACTIVE':
                print("  Deployment is now ACTIVE.")
                return True
        except Exception:
            continue
    return False


def abacus_search(query=None, image=None, image_url=None):
    """Call the Abacus.AI workflow and return the raw JSON payload.

    If the deployment is stopped, auto-start it and retry once."""
    if not (ABACUS_DEPLOYMENT_ID and ABACUS_DEPLOYMENT_TOKEN):
        raise RuntimeError('Missing Abacus.AI environment variables')

    payload = {
        'deploymentId': ABACUS_DEPLOYMENT_ID,
        'deploymentToken': ABACUS_DEPLOYMENT_TOKEN,
    }
    if image:
        # The workflow's price_comparison(product_description, product_image)
        # accepts product_image as a plain base64 string. The server marks that
        # field as a blob, so passing it in keywordArguments is rejected with
        # "Invalid blob input data". Passing it POSITIONALLY bypasses that
        # validation and routes straight into the function's base64 branch.
        payload['arguments'] = [None, image]
    else:
        payload['keywordArguments'] = {'product_description': query}

    def _post():
        return http_requests.post(
            ABACUS_ENDPOINT,
            headers={'Content-Type': 'application/json'},
            json=payload,
            timeout=120,
        )

    resp = _post()
    if _is_deployment_stopped(resp):
        if _start_deployment_and_wait():
            resp = _post()
        else:
            raise RuntimeError(
                'The AI service was asleep and could not be woken in time. '
                'Please try again in a minute.'
            )
    if resp.status_code != 200:
        raise RuntimeError(f"Abacus {resp.status_code}: {resp.text[:500]}")
    return resp.json()


@app.route('/api/search', methods=['POST'])
def search():
    data = request.get_json() or {}
    query = (data.get('query') or '').strip()
    image = data.get('image')
    image_url = data.get('image_url')

    if not query and not image and not image_url:
        return jsonify({'error': 'Please enter a search term or upload an image'}), 400

    if not (ABACUS_DEPLOYMENT_ID and ABACUS_DEPLOYMENT_TOKEN):
        return jsonify({'error': 'Server is not configured. Missing Abacus.AI environment variables.'}), 500

    try:
        raw = abacus_search(query=query, image=image, image_url=image_url)
    except Exception as e:
        print(f"  Abacus.AI error: {e}")
        return jsonify({'error': 'AI workflow request failed', 'detail': str(e)}), 502

    results, stats, product = normalize_abacus(raw)

    return jsonify({
        'query': query or product or 'your product',
        'results': results,
        'stats': stats,
        'recalls': [],
    })


# ──────────────────────────────────────────────
# Heartbeat watchdog thread
# ──────────────────────────────────────────────

def _heartbeat_watchdog():
    """Background thread that monitors heartbeat and shuts down when browser closes."""
    # Give the browser time to open and send first heartbeat
    time.sleep(_HEARTBEAT_TIMEOUT + 5)
    while True:
        time.sleep(3)
        elapsed = time.time() - _last_heartbeat
        if elapsed > _HEARTBEAT_TIMEOUT:
            print("\n  Browser closed – shutting down OfferScout. Goodbye!")
            os._exit(0)


def _find_free_port(start=5050):
    """Find a free port starting from `start`, skipping occupied ones."""
    import socket
    for port in range(start, start + 20):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('127.0.0.1', port))
                return port
        except OSError:
            continue
    return start  # fallback


if __name__ == '__main__':
    port = _find_free_port(5050)

    print(f"\n  OfferScout is running on port {port}!")
    print(f"  Opening http://localhost:{port} in your browser")
    print("  (Server will auto-stop when you close the browser tab)\n")

    # Start heartbeat watchdog
    watchdog = threading.Thread(target=_heartbeat_watchdog, daemon=True)
    watchdog.start()

    # Auto-open browser
    threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()

    app.run(debug=False, port=port)
