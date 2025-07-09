import requests
from bs4 import BeautifulSoup
import sqlite3

# Database connection (adjust as needed)
conn = sqlite3.connect('ro_bot.db')
cursor = conn.cursor()

# Example table structure (adjust columns/types as needed)
cursor.execute('''
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    name TEXT,
    type TEXT,
    description TEXT,
    weight TEXT,
    level TEXT,
    jobs TEXT
)
''')
conn.commit()

def fetch_items(page=1):
    url = f"https://ratemyserver.net/index.php?page=re_item_db&itype=4&iclass=0&tabj=on&iju=-1&iname=&idesc=&iscript=&islot_sign=-1&islot=-1&icfix=&i_ele=-1&i_status=-1&i_race=-1&i_bonus=-1&sort_r=0&sort_o=0&isearch=Search"
    headers = {'User-Agent': 'Mozilla/5.0'}
    print(f"[DEBUG] Fetching URL: {url}")
    resp = requests.get(url, headers=headers)
    print(f"[DEBUG] HTTP Status: {resp.status_code}")
    resp.raise_for_status()
    return resp.text

def parse_items(html):
    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', {'class': 'itemdb'})
    if not table:
        print("[DEBUG] No table found with class 'itemdb'")
        return []
    rows = table.find_all('tr')[1:]  # skip header
    print(f"[DEBUG] Found {len(rows)} rows in table")
    items = []
    for i, row in enumerate(rows):
        cols = row.find_all('td')
        if len(cols) < 7:
            print(f"[DEBUG] Row {i} skipped, not enough columns ({len(cols)})")
            continue
        name = cols[1].get_text(strip=True)
        type_ = cols[2].get_text(strip=True)
        description = cols[3].get_text(strip=True)
        weight = cols[4].get_text(strip=True)
        level = cols[5].get_text(strip=True)
        jobs = cols[6].get_text(strip=True)
        print(f"[DEBUG] Parsed item: {name}, {type_}, {description}, {weight}, {level}, {jobs}")
        items.append((name, type_, description, weight, level, jobs))
    return items

def insert_items(items):
    for item in items:
        print(f"[DEBUG] Inserting item: {item}")
        cursor.execute('''
        INSERT OR IGNORE INTO items (name, type, description, weight, level, jobs)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', item)
    conn.commit()
    print(f"[DEBUG] Committed {len(items)} items to database")

def main():
    page = 1
    while True:
        print(f"[DEBUG] Processing page {page}")
        html = fetch_items(page)
        items = parse_items(html)
        if not items:
            print(f"[DEBUG] No items found on page {page}, stopping.")
            break
        insert_items(items)
        print(f"Inserted {len(items)} items from page {page}")
        page += 1

if __name__ == "__main__":
    main()
    conn.close()