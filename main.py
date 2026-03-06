import os
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import sqlite3
import boto3
import time
import threading
from queue import Queue, Empty
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import json
import subprocess
import atexit

def backup_on_exit():
    print("🚀 Server stopping... uploading final database to iDrive e2")
    # Aapki purani upload function ka naam yahan likho
    upload_to_s3() 

atexit.register(backup_on_exit)
# --- 1. GLOBAL CONFIGURATION ---
DB_NAME = "streekx_index.db"
BUCKET_NAME = "streekxcrawler"
USER_AGENT = "StreekxBot/6.0 (+http://streekx.ai/bot)"
MAX_THREADS = 15
CRAWL_DELAY = 1
MAX_DEPTH = 5
SYNC_INTERVAL = 600

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
url_queue = Queue()
visited_urls = set()

# --- 2. CLOUD STORAGE SETUP ---
s3_client = boto3.client(
    's3',
    endpoint_url=os.environ.get('E2_ENDPOINT'),
    aws_access_key_id=os.environ.get('E2_ACCESS_KEY'),
    aws_secret_access_key=os.environ.get('E2_SECRET_KEY')
)

# --- 3. SEED URLS ---
SEED_URLS = [
    "https://www.google.com", "https://www.wikipedia.org", "https://www.github.com",
    "https://www.stackoverflow.com", "https://www.microsoft.com", "https://www.apple.com",
    "https://www.amazon.com", "https://www.reddit.com", "https://www.medium.com",
    "https://www.quora.com", "https://www.techcrunch.com", "https://www.theverge.com",
    "https://www.bbc.com", "https://www.reuters.com", "https://www.nytimes.com",
    "https://www.theguardian.com", "https://www.cnn.com", "https://www.forbes.com",
    "https://www.bloomberg.com", "https://www.wsj.com", "https://www.ndtv.com",
    "https://www.indianexpress.com", "https://www.thehindu.com", "https://www.indiatimes.com",
    "https://www.ebay.com", "https://www.walmart.com", "https://www.flipkart.com",
    "https://www.aliexpress.com", "https://www.target.com", "https://www.bestbuy.com",
    "https://www.nasa.gov", "https://www.nature.com", "https://www.sciencemag.org",
    "https://www.mit.edu", "https://www.stanford.edu", "https://www.harvard.edu",
    "https://www.youtube.com", "https://www.vimeo.com", "https://www.dailymotion.com",
    "https://www.twitch.tv", "https://www.ted.com", "https://www.netflix.com"
]

def kill_port(port):
    try:
        subprocess.run(["fuser", "-k", f"{port}/tcp"], check=False)
        print(f"Cleaned up port {port}")
    except Exception as e:
        print(f"Port cleanup error: {e}")

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS web_data 
                 (url TEXT PRIMARY KEY, 
                  title TEXT, 
                  description TEXT, 
                  content TEXT, 
                  content_type TEXT, 
                  media_url TEXT, 
                  price TEXT, 
                  links_found INTEGER, 
                  last_crawled DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def save_to_db(url, title, desc, content, c_type, m_url, price="N/A", links=0):
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("""INSERT OR REPLACE INTO web_data 
                     (url, title, description, content, content_type, media_url, price, links_found) 
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", 
                  (url, str(title), str(desc), str(content), c_type, m_url, price, links))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ DB Error: {e}")

def extract_deep_data(soup, url):
    title = soup.title.string if soup.title else "No Title"
    desc_tag = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', property='og:description')
    description = desc_tag['content'] if desc_tag else "No description."
    for script_or_style in soup(["script", "style", "nav", "footer"]):
        script_or_style.decompose()
    clean_text = re.sub(r'\s+', ' ', soup.get_text()).strip()[:5000]
    media = []
    for img in soup.find_all('img', src=True):
        media.append(('image', urljoin(url, img['src']), img.get('alt', 'Image')))
    for vid in soup.find_all(['video', 'iframe'], src=True):
        media.append(('video', urljoin(url, vid['src']), "Video Content"))
    price = "N/A"
    price_tag = soup.find(text=re.compile(r'[\$₹£]\d+'))
    if price_tag: price = price_tag.strip()
    return title, description, clean_text, media, price

def crawl_worker():
    while True:
        try:
            url, depth = url_queue.get(timeout=10)
            if url in visited_urls or depth > MAX_DEPTH:
                url_queue.task_done()
                continue
            visited_urls.add(url)
            headers = {'User-Agent': USER_AGENT}
            response = requests.get(url, timeout=10, headers=headers)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                title, desc, text, media, price = extract_deep_data(soup, url)
                links = soup.find_all('a', href=True)
                valid_links = []
                for link in links:
                    full_url = urljoin(url, link['href'])
                    if urlparse(full_url).scheme in ['http', 'https']:
                        valid_links.append(full_url)
                        if full_url not in visited_urls:
                            url_queue.put((full_url, depth + 1))
                category = 'news' if 'news' in url or 'blog' in url else 'web'
                if any(x in url for x in ['amazon', 'ebay', 'flipkart']): category = 'shopping'
                save_to_db(url, title, desc, text, category, None, price, len(valid_links))
                for m_type, m_url, m_title in media[:10]:
                    save_to_db(m_url, m_title, f"Media from {url}", "", m_type, m_url)
                print(f"🚀 [DEEP INDEX] {url} | Found: {len(valid_links)} links")
                time.sleep(CRAWL_DELAY)
            url_queue.task_done()
        except Empty: break
        except Exception as e:
            print(f"⚠️ Worker Error: {e}")
            url_queue.task_done()

@app.route('/')
def home():
    return "StreekX Online"

@app.route('/search')
def search():
    query = request.args.get('q', '')
    ctype = request.args.get('type', 'web')
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("""SELECT url, title, description, media_url, price FROM web_data 
                 WHERE (title LIKE ? OR content LIKE ? OR description LIKE ?) AND (content_type = ? OR ? = '')
                 ORDER BY links_found DESC LIMIT 50""", 
              ('%'+query+'%', '%'+query+'%', '%'+query+'%', ctype, query))
    results = [{"url": r[0], "title": r[1], "description": r[2], "media": r[3], "price": r[4]} for r in c.fetchall()]
    conn.close()
    return jsonify({"results": results, "total": len(results)})

def auto_sync():
    while True:
        time.sleep(SYNC_INTERVAL)
        try:
            if os.path.exists(DB_NAME):
                s3_client.upload_file(DB_NAME, BUCKET_NAME, DB_NAME)
                print("📡 [CLOUD] Sync Finished Success")
        except: pass

if __name__ == "__main__":
    init_db()
    kill_port(8080)
    port = int(os.environ.get("PORT", 8080))
    print(f"🚀 STREEKX LIVE ON PORT: {port}")
    
    for seed in SEED_URLS:
        url_queue.put((seed, 1))
        
    for _ in range(MAX_THREADS):
        threading.Thread(target=crawl_worker, daemon=True).start()
        
    threading.Thread(target=auto_sync, daemon=True).start()
    
    print("🔥 STREEKX ENGINE V6.0 ONLINE")
    app.run(host='0.0.0.0', port=port, debug=False)
