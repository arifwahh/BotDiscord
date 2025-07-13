import requests
from bs4 import BeautifulSoup
import sqlite3
from datetime import datetime
import os
from typing import List, Dict, Optional
import logging
import discord

class TalonTalesScraper:
    def __init__(self, db_path: str = "ro_bot.db"):
        self.session = requests.Session()
        self.base_url = "https://talontales.com/"
        self.login_url = f"{self.base_url}my-account/"
        self.vendor_url = f"{self.base_url}panel/?module=vending&swcfpc=1"
        self.db_path = db_path
        self._init_db()
        self.logger = self._setup_logger()
        

    def _setup_logger(self):
        logger = logging.getLogger('talon_scraper')
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        return logger

    def _init_db(self):
        """Initialize database with vendors table"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT,
                item_name TEXT,
                price INTEGER,
                amount INTEGER,
                vendor_name TEXT,
                vendor_title TEXT,
                location TEXT,
                icon_url TEXT,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')

    def login(self, username: str, password: str) -> bool:
        """Authenticate with Talon Tales with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Clear session cookies before each login attempt
                self.session.cookies.clear()
                
                # Get fresh login page
                response = self.session.get(self.login_url)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                login_form = soup.find('form', class_='woocommerce-form-login')
                
                if not login_form:
                    self.logger.warning(f"Login form not found (attempt {attempt + 1}/{max_retries})")
                    continue

                # Extract tokens
                nonce = login_form.find('input', {'name': 'woocommerce-login-nonce'})
                if not nonce:
                    self.logger.warning(f"Security nonce not found (attempt {attempt + 1}/{max_retries})")
                    continue
                    
                nonce = nonce.get('value', '')
                referer = login_form.find('input', {'name': '_wp_http_referer'}).get('value', '/my-account/')

                # Prepare payload
                payload = {
                    'username': username,
                    'password': password,
                    'woocommerce-login-nonce': nonce,
                    '_wp_http_referer': referer,
                    'login': 'Log in'
                }

                # Submit login
                response = self.session.post(self.login_url, data=payload)
                response.raise_for_status()

                # Verify success
                if 'logout' in response.text.lower():
                    self.logger.info("Login successful")
                    return True
                    
            except Exception as e:
                self.logger.error(f"Login attempt {attempt + 1} failed: {str(e)}")
                continue
                
        self.logger.error("All login attempts failed")
        return False

    def scrape_vendors(self, max_pages: int = 1) -> List[Dict]:
        """Scrape vendor data from multiple pages"""
        all_data = []
        page = 1
        entries_per_page = 25

        while page <= max_pages:
            url = f"{self.vendor_url}&start={((page-1)*entries_per_page)}"
            self.logger.info(f"Scraping page {page}/{max_pages}")

            page_data = self._scrape_page(url)
            if not page_data:
                break

            all_data.extend(page_data)
            page += 1

        return all_data

    def _scrape_page(self, url: str) -> Optional[List[Dict]]:
        """Scrape a single vendor page"""
        try:
            response = self.session.get(url)
            response.raise_for_status()

            if "my-account" in response.url.lower():
                self.logger.warning("Session expired, need to re-login")
                return None

            soup = BeautifulSoup(response.text, 'html.parser')
            vendor_table = soup.find('table', {'id': 'vendorlist_table'})
            if not vendor_table:
                self.logger.error("Vendor table not found")
                return None

            rows = vendor_table.find('tbody').find_all('tr')
            return [self._parse_row(row) for row in rows if len(row.find_all('td')) >= 8]

        except Exception as e:
            self.logger.error(f"Failed to scrape page: {str(e)}")
            return None

    def _parse_row(self, row) -> Dict:
        """Parse a single vendor row"""
        cols = row.find_all('td')
        img_tag = cols[0].find('img')
        
        return {
            'icon': img_tag.get('src') if img_tag else None,
            'item_id': cols[1].text.strip(),
            'item_name': cols[2].find('a').text.strip() if cols[2].find('a') else cols[2].text.strip(),
            'price': int(cols[3].text.strip().replace(',', '')) if cols[3].text.strip().replace(',', '').isdigit() else 0,
            'amount': int(cols[4].text.strip()) if cols[4].text.strip().isdigit() else 0,
            'vendor_name': cols[5].text.strip(),
            'vendor_title': cols[6].text.strip(),
            'location': cols[7].text.strip()
        }

    def save_to_db(self, data: List[Dict]) -> bool:
        """Save scraped data to database"""
        if not data:
            self.logger.warning("No data to save")
            return False

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # Clear old data
                cursor.execute("DELETE FROM vendors")
                # Insert new data
                cursor.executemany('''
                    INSERT INTO vendors (
                        item_id, item_name, price, amount, vendor_name,
                        vendor_title, location, icon_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', [(
                    item['item_id'],
                    item['item_name'],
                    item['price'],
                    item['amount'],
                    item['vendor_name'],
                    item['vendor_title'],
                    item['location'],
                    item['icon']
                ) for item in data])
                
                self.logger.info(f"Saved {len(data)} items to database")
                return True
        except Exception as e:
            self.logger.error(f"Database error: {str(e)}")
            return False
        
    def get_price_statistics(self) -> Dict[str, Dict]:
        """Calculate price statistics for all items"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 
                    item_name,
                    MIN(price) as min_price,
                    AVG(price) as avg_price,
                    COUNT(*) as count
                FROM vendors
                GROUP BY item_name
            ''')
            return {
                row[0]: {
                    'min': row[1],
                    'avg': int(row[2]),
                    'count': row[3]
                }
                for row in cursor.fetchall()
            }

    def format_as_discord_message(self) -> str:
        """Format vendor data like the reference image"""
        stats = self.get_price_statistics()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM vendors 
                ORDER BY price ASC
                LIMIT 20
            ''')
            vendors = cursor.fetchall()
        
        if not vendors:
            return "No vendor data available."
        
        # Header similar to reference image
        message = [
            "# shopee >",
            f"{len(vendors)} Online",
            "",
            "The BARGAIN item(s) in this list is currently available for purchase",
            "",
            "Talon Coin Rate: 989,896z | Vendor Slots: 579/862 | Server Time: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "---"
        ]
        
        for vendor in vendors:
            item_name = vendor[2]
            price = vendor[3]
            vendor_name = vendor[5]
            location = vendor[7]
            
            # Get stats for this item
            item_stats = stats.get(item_name, {})
            avg_price = item_stats.get('avg', price)
            discount = int((1 - (price / avg_price)) * 100) if avg_price else 0
            
            # Format like reference image
            message.extend([
                f"@{vendor_name} <= {discount}% discount 【{item_name}】",
                f"[1]:{hash(item_name) % 10000} | **{price:,} z** (min) | {avg_price:,} z (avg) |",
                f"1 pcs | {location} ► absolute garbage, pls take it",
                ""
            ])
        
        return "\n".join(message)

    def format_as_discord_embed(self) -> discord.Embed:
        """Create a Discord embed version of the data"""
        stats = self.get_price_statistics()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM vendors 
                ORDER BY price ASC
                LIMIT 10
            ''')
            vendors = cursor.fetchall()
        
        embed = discord.Embed(
            title="🛍️ Shopee > Vendor Listings",
            description=f"{len(vendors)} Online\n\nThe BARGAIN item(s) in this list is currently available for purchase",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        embed.set_footer(text="Talon Coin Rate: 989,896z | Vendor Slots: 579/862")
        
        for vendor in vendors:
            item_name = vendor[2]
            price = vendor[3]
            vendor_name = vendor[5]
            location = vendor[7]
            
            item_stats = stats.get(item_name, {})
            avg_price = item_stats.get('avg', price)
            discount = int((1 - (price / avg_price)) * 100) if avg_price else 0
            
            embed.add_field(
                name=f"@{vendor_name} <= {discount}% discount 【{item_name}】",
                value=(
                    f"`[1]:{hash(item_name) % 10000}` | **{price:,} z** (min) | {avg_price:,} z (avg) |\n"
                    f"1 pcs | `{location}` ► absolute garbage, pls take it"
                ),
                inline=False
            )
        
        return embed