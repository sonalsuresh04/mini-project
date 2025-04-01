from flask import Flask, render_template, request, redirect, url_for, flash
import requests
from bs4 import BeautifulSoup
from datetime import datetime
from pony import orm
import re
import time
import random
import os
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Set up Flask app
app = Flask(__name__)
app.secret_key = 'book_bargain_secret_key'

# Create templates directory if it doesn't exist
os.makedirs('templates', exist_ok=True)

# Set up the Database
db = orm.Database()
db.bind(provider='sqlite', filename='book_database.sqlite', create_db=True)

class BookPrice(db.Entity):
    book_name = orm.Required(str)
    isbn = orm.Optional(str, nullable=True)
    author = orm.Optional(str, nullable=True)
    image_url = orm.Optional(str, nullable=True)
    website = orm.Required(str)
    price = orm.Required(float)
    rating = orm.Optional(float, nullable=True)
    description = orm.Optional(str, nullable=True)
    date_created = orm.Required(datetime)
    genre = orm.Optional(str, nullable=True)
    binding = orm.Optional(str, nullable=True)
    language = orm.Optional(str, nullable=True)

db.generate_mapping(create_tables=True)

# Headers for web requests - updated with more browser-like headers
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Referer': 'https://www.google.com/',
    'DNT': '1',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Cache-Control': 'max-age=0',
}

def extract_price(text):
    """Extract price from text using regex."""
    if not text:
        return 0.0
    
    # Clean the text
    price_text = text.strip()
    logger.info(f"Extracting price from: {price_text}")
    
    # Try different price patterns
    patterns = [
        r'₹\s*([\d,]+\.?\d{0,2})',  # ₹ symbol with number
        r'Rs\.?\s*([\d,]+\.?\d{0,2})',  # Rs. with number
        r'([\d,]+\.?\d{0,2})'  # Just the number with optional decimal
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, price_text)
        if matches:
            try:
                # Handle tuple results from regex groups
                if isinstance(matches[0], tuple):
                    price_str = matches[0][0]
                else:
                    price_str = matches[0]
                
                price = float(price_str.replace(',', ''))
                logger.info(f"Extracted price: {price} using pattern {pattern}")
                return price
            except (ValueError, IndexError) as e:
                logger.warning(f"Error parsing price: {e}")
                continue
    
    logger.warning(f"Could not extract price from: {price_text}")
    return 0.0

def determine_genre(book_name, description):
    """Helper function to determine genre based on book name and description"""
    book_name = book_name.lower()
    description = description.lower() if description else ""
    
    if any(keyword in book_name + description for keyword in ['fiction', 'novel', 'story', 'fantasy']):
        return "Fiction"
    elif any(keyword in book_name + description for keyword in ['science fiction', 'sci-fi', 'space']):
        return "Science Fiction"
    elif any(keyword in book_name + description for keyword in ['mystery', 'thriller', 'crime', 'detective']):
        return "Mystery"
    elif any(keyword in book_name + description for keyword in ['romance', 'love', 'romantic']):
        return "Romance"
    elif any(keyword in book_name + description for keyword in ['biography', 'autobiography', 'memoir']):
        return "Biography"
    elif any(keyword in book_name + description for keyword in ['history', 'historical']):
        return "History"
    elif any(keyword in book_name + description for keyword in ['self-help', 'personal', 'motivation']):
        return "Self-Help"
    elif any(keyword in book_name + description for keyword in ['children', 'kids', 'juvenile', 'harry potter']):
        return "Children"
    return "Unknown"

def amazon(session, headers, book_name):
    search_url = f"https://www.amazon.in/s?k={'+'.join(book_name.split())}&i=stripbooks"
    try:
        logger.info(f"Searching Amazon for: {book_name}")
        resp = session.get(search_url, headers=headers, timeout=10)
        
        # Debug the response
        logger.info(f"Amazon response status: {resp.status_code}")
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Updated selector for Amazon's search results
        book_link = soup.select_one("div.s-result-item h2 a")
        if not book_link:
            # Try alternative selectors
            book_link = soup.select_one(".s-title-instructions-style a")
            
        if not book_link:
            logger.warning("No book link found on Amazon")
            # Save the HTML for debugging
            with open("amazon_debug.html", "w", encoding="utf-8") as f:
                f.write(resp.text)
            logger.info("Saved Amazon HTML for debugging")
            return book_name, "Unknown", "Unknown", "https://source.unsplash.com/random/300x400/?book", "amazon", 0.0, 0.0, "No description available", "Unknown", "Unknown", "Unknown"

        # Get the book page URL
        book_url = book_link['href']
        if not book_url.startswith("https://"):
            book_url = "https://www.amazon.in" + book_url
        
        # Fix for javascript:void(0) links
        if "javascript:void" in book_url:
            logger.warning("Found javascript:void link, trying alternative selector")
            # Try alternative selectors
            book_link = soup.select_one(".a-link-normal.s-underline-text.s-underline-link-text.s-link-style.a-text-normal")
            if book_link:
                book_url = book_link['href']
                if not book_url.startswith("https://"):
                    book_url = "https://www.amazon.in" + book_url
            else:
                logger.warning("No valid book link found on Amazon")
                return book_name, "Unknown", "Unknown", "https://source.unsplash.com/random/300x400/?book", "amazon", 0.0, 0.0, "No description available", "Unknown", "Unknown", "Unknown"
        
        logger.info(f"Found book on Amazon: {book_url}")
        
        # Add a delay to avoid being blocked
        time.sleep(2)
        
        # Get the book page
        resp = session.get(book_url, headers=headers, timeout=10)
        logger.info(f"Amazon book page status: {resp.status_code}")
        
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract book title
        title_element = soup.select_one("#productTitle")
        book_name = title_element.text.strip() if title_element else book_name
        logger.info(f"Extracted title: {book_name}")

        # Try multiple price selectors
        price = 0.0
        price_selectors = [
            ".a-price .a-offscreen",
            ".a-price-whole", 
            "span.a-price span.a-offscreen",
            "#price span.a-color-price",
            "#price",
            ".kindle-price .a-color-price"
        ]
        
        for selector in price_selectors:
            price_elements = soup.select(selector)
            for price_element in price_elements:
                if price_element:
                    price_text = price_element.text.strip()
                    price = extract_price(price_text)
                    if price > 0:
                        logger.info(f"Extracted price: {price} using selector {selector}")
                        break
            if price > 0:
                break
        
        if price == 0.0:
            logger.warning("Could not extract price from Amazon")

        # Extract author
        author_element = soup.select_one("span.author a, a.contributorNameID")
        author = author_element.text.strip() if author_element else "Unknown"
        logger.info(f"Extracted author: {author}")

        # Extract ISBN
        isbn = "Unknown"
        details = soup.select_one("#detailBullets_feature_div, .detail-bullet-list")
        if details:
            isbn_match = re.search(r'ISBN-13\D*(\d{3}-?\d{10}|\d{10})', details.text)
            if not isbn_match:
                isbn_match = re.search(r'ISBN\D*(\d{3}-?\d{10}|\d{10})', details.text)
            isbn = isbn_match.group(1).replace("-", "") if isbn_match else "Unknown"
        logger.info(f"Extracted ISBN: {isbn}")

        # Extract rating
        rating_element = soup.select_one("span[data-hook='rating-out-of-text'], #acrPopover")
        rating = 0.0
        if rating_element:
            rating_text = rating_element.text.strip()
            rating_match = re.search(r'(\d+(\.\d+)?)', rating_text)
            rating = float(rating_match.group(1)) if rating_match else 0.0
        logger.info(f"Extracted rating: {rating}")

        # Extract description
        description_element = soup.select_one("#bookDescription_feature_div, #productDescription, #feature-bullets")
        description = description_element.text.strip() if description_element else "No description available"
        logger.info(f"Extracted description: {description[:50]}...")

        # Extract image URL
        image_element = soup.select_one("#imgBlkFront, #landingImage, #ebooksImgBlkFront")
        image_url = image_element['src'] if image_element and 'src' in image_element.attrs else "https://source.unsplash.com/random/300x400/?book"
        if not image_url or image_url == "":
            image_url = image_element['data-a-dynamic-image'] if image_element and 'data-a-dynamic-image' in image_element.attrs else "https://source.unsplash.com/random/300x400/?book"
            if image_url and "{" in image_url:
                # Extract the first URL from the JSON string
                try:
                    import json
                    image_dict = json.loads(image_url)
                    image_url = list(image_dict.keys())[0] if image_dict else "https://source.unsplash.com/random/300x400/?book"
                except:
                    image_url = "https://source.unsplash.com/random/300x400/?book"
        logger.info(f"Extracted image URL: {image_url}")

        # Extract genre and binding from description or categories
        genre = "Unknown"
        binding = "Unknown"
        language = "Unknown"
        
        # Try to find genre from breadcrumbs or categories
        breadcrumbs = soup.select("#wayfinding-breadcrumbs_feature_div a, #nav-subnav a")
        for crumb in breadcrumbs:
            crumb_text = crumb.text.strip()
            if "Books" in crumb_text and crumb_text != "Books":
                genre = crumb_text
                logger.info(f"Found genre from breadcrumbs: {genre}")
                break
        
        # Try to find binding and language from product details
        detail_items = soup.select("#detailBullets_feature_div li, .detail-bullet-list li, .a-expander-content li")
        for item in detail_items:
            item_text = item.text.strip()
            if "Binding" in item_text or "Format" in item_text:
                binding_match = re.search(r'(Binding|Format):\s*([^:]+)', item_text)
                binding = binding_match.group(2).strip() if binding_match else "Unknown"
                logger.info(f"Found binding: {binding}")
            elif "Language" in item_text:
                language_match = re.search(r'Language:\s*([^:]+)', item_text)
                language = language_match.group(1).strip() if language_match else "Unknown"
                logger.info(f"Found language: {language}")

        # If genre is still unknown, try to determine from book name and description
        if genre == "Unknown":
            genre = determine_genre(book_name, description)
            logger.info(f"Determined genre from content: {genre}")

        # Log the final data being returned
        logger.info(f"Amazon scraping complete. Returning data for {book_name}")
        return book_name, isbn, author, image_url, "amazon", price, rating, description, genre, binding, language

    except Exception as e:
        logger.error(f"Error scraping Amazon: {e}")
        return book_name, "Unknown", "Unknown", "https://source.unsplash.com/random/300x400/?book", "amazon", 0.0, 0.0, "No description available", "Unknown", "Unknown", "Unknown"

def bookswagon(session, headers, book_name):
    search_url = "https://www.bookswagon.com/search-books/" + "-".join(book_name.split())
    try:
        logger.info(f"Searching Bookswagon for: {book_name}")
        resp = session.get(search_url, headers=headers, timeout=10)
        logger.info(f"Bookswagon response status: {resp.status_code}")
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Find the first book link
        book_link = soup.select_one("div.title a")
        if not book_link:
            # Try alternative selector
            book_link = soup.select_one(".product-title a")
            
        if not book_link:
            logger.warning("No book link found on Bookswagon")
            # Save the HTML for debugging
            with open("bookswagon_debug.html", "w", encoding="utf-8") as f:
                f.write(resp.text)
            logger.info("Saved Bookswagon HTML for debugging")
            return book_name, "Unknown", "Unknown", "https://source.unsplash.com/random/300x400/?book", "bookswagon", 0.0, 0.0, "No description available", "Unknown", "Unknown", "Unknown"

        # Get the book page URL
        book_url = book_link['href']
        if not book_url.startswith("https://"):
            book_url = "https://www.bookswagon.com" + book_url
        logger.info(f"Found book on Bookswagon: {book_url}")
        
        # Add a delay to avoid being blocked
        time.sleep(2)
        
        # Get the book page
        resp = session.get(book_url, headers=headers, timeout=10)
        logger.info(f"Bookswagon book page status: {resp.status_code}")
        
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract book title - FIX: Clean up the title by removing newlines and extra text
        title_element = soup.select_one("h1")
        if title_element:
            book_name = title_element.text.strip()
            # Clean up the title - remove release date and format info
            book_name = re.sub(r'\s*\|.*$', '', book_name)
            book_name = re.sub(r'\s*$$.*?$$\s*$', '', book_name)
            book_name = book_name.strip()
        logger.info(f"Extracted title: {book_name}")

        # Try multiple price selectors
        price = 0.0
        price_selectors = [
            "div.price > div.sell",
            "span#ctl00_phBody_ProductDetail_lblourPrice",
            "label#ctl00_phBody_ProductDetail_lblourPrice",  # Added from app3.py
            "label#ctl00_phBody_ProductDetail_lblDiscountPrice",
            ".product-price",
            ".our-price",
            ".sell",
            ".price-text",
            "#site-wrapper .price"
        ]
        
        for selector in price_selectors:
            price_elements = soup.select(selector)
            for price_element in price_elements:
                if price_element:
                    price_text = price_element.text.strip()
                    logger.info(f"Found price element with text: {price_text}")
                    price = extract_price(price_text)
                    if price > 0:
                        logger.info(f"Extracted price: {price} using selector {selector}")
                        break
            if price > 0:
                break
        
        # Try direct text search for price if selectors fail
        if price == 0.0:
            logger.info("Trying direct text search for price")
            price_patterns = [
                r'₹\s*([\d,]+\.?\d{0,2})',
                r'Rs\.?\s*([\d,]+\.?\d{0,2})'
            ]
            
            for pattern in price_patterns:
                matches = re.findall(pattern, soup.text)
                if matches:
                    try:
                        price_str = matches[0]
                        if isinstance(price_str, tuple):
                            price_str = price_str[0]
                        price = float(price_str.replace(',', ''))
                        logger.info(f"Found price {price} using direct text search")
                        break
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Error parsing price from direct text: {e}")
                        continue

        # Extract author - improved from app3.py
        author = "Unknown"
        author_selectors = [
            "#ctl00_phBody_ProductDetail_AuthorLink", 
            ".author-name a",
            ".author a",
            "label#ctl00_phBody_ProductDetail_lblAuthor1 a",  # Added from app3.py
            "span.a-list-item a.contributorNameID"
        ]
        
        for selector in author_selectors:
            author_element = soup.select_one(selector)
            if author_element:
                author = author_element.text.strip()
                logger.info(f"Extracted author: {author}")
                break

        # Extract ISBN - improved from app3.py
        isbn = "Unknown"
        # First try to find ISBN in the product details section
        details = soup.select_one("#ctl00_phBody_ProductDetail_lblProductDetail, .product-details")
        if details:
            isbn_match = re.search(r'ISBN-13\D*(\d{3}-?\d{10}|\d{10})', details.text)
            if not isbn_match:
                isbn_match = re.search(r'ISBN\D*(\d{3}-?\d{10}|\d{10})', details.text)
            isbn = isbn_match.group(1).replace("-", "") if isbn_match else "Unknown"
        
        # If ISBN is still unknown, try to find it in list items
        if isbn == "Unknown":
            for li in soup.select("ul.list-unstyled.detailfont14.border-right li, ul.list-unstyled li"):
                text = li.text.strip()
                if "ISBN-13:" in text:
                    isbn = text.split("ISBN-13:")[-1].strip()
                    break
                elif "ISBN:" in text:
                    isbn = text.split("ISBN:")[-1].strip()
                    break
        
        # Clean up the ISBN
        if isbn != "Unknown":
            # Remove any non-digit characters
            isbn = re.sub(r'[^0-9]', '', isbn)
            # Ensure it's a valid ISBN length (10 or 13 digits)
            if len(isbn) not in [10, 13]:
                isbn = "Unknown"
        
        logger.info(f"Extracted ISBN: {isbn}")

        # Extract rating - improved from app3.py
        rating_element = soup.select_one("div.starRating, .rating, span#ctl00_phBody_ProductDetail_StarRating_LblAvgRate")
        rating = 0.0
        if rating_element:
            if rating_element.get("title"):
                rating_text = rating_element["title"]
                rating_match = re.search(r'(\d+(\.\d+)?)', rating_text)
                rating = float(rating_match.group(1)) if rating_match else 0.0
            else:
                rating_text = rating_element.text.strip()
                if rating_text and rating_text != "Not Rated":
                    rating_match = re.search(r'(\d+(\.\d+)?)', rating_text)
                    rating = float(rating_match.group(1)) if rating_match else 0.0
        logger.info(f"Extracted rating: {rating}")

        # Extract description - improved from app3.py
        description_element = soup.select_one("#ctl00_phBody_ProductDetail_lblProductDesc, .desc")
        if not description_element:
            description_elements = soup.select("div.col-sm-12 p")
            description = " ".join([p.text.strip() for p in description_elements]) if description_elements else "No description available"
        else:
            description = description_element.text.strip()
        logger.info(f"Extracted description: {description[:50]}...")

        # Extract image URL
        image_element = soup.select_one("#ctl00_phBody_ProductDetail_imgProduct, .product-image img")
        image_url = image_element['src'] if image_element and 'src' in image_element.attrs else "https://source.unsplash.com/random/300x400/?book"
        logger.info(f"Extracted image URL: {image_url}")

        # Extract genre from themecolor links - from app3.py
        genre = "Unknown"
        for a_tag in soup.select("a.themecolor, .category-links a"):
            if "-books" in a_tag.get("href", ""):  # Check if it's a category page
                genre = a_tag.text.strip()
                logger.info(f"Found genre: {genre}")
                break  # Stop at the first valid genre

        # Extract binding and language - from app3.py
        binding = "Unknown"
        language = "Unknown"
        for li in soup.select("ul.list-unstyled.detailfont14 li, ul.list-unstyled.detailfont14.border-right li, .product-specs li"):
            text = li.text.strip()
            if "Language:" in text:
                language = text.split("Language:")[-1].strip()
                logger.info(f"Found language: {language}")
            elif "Binding:" in text:
                binding = text.split("Binding:")[-1].strip()
                logger.info(f"Found binding: {binding}")

        # If genre is still unknown, try to determine from book name and description
        if genre == "Unknown":
            genre = determine_genre(book_name, description)
            logger.info(f"Determined genre from content: {genre}")

        # Log the final data being returned
        logger.info(f"Bookswagon scraping complete. Returning data for {book_name}")
        return book_name, isbn, author, image_url, "bookswagon", price, rating, description, genre, binding, language

    except Exception as e:
        logger.error(f"Error scraping Bookswagon: {e}")
        return book_name, "Unknown", "Unknown", "https://source.unsplash.com/random/300x400/?book", "bookswagon", 0.0, 0.0, "No description available", "Unknown", "Unknown", "Unknown"

def kitabay(session, headers, book_name):
    # FIX: Improved Kitabay search to better match book titles
    search_url = "https://kitabay.com/search?q=" + "+".join(book_name.split())
    try:
        logger.info(f"Searching Kitabay for: {book_name}")
        resp = session.get(search_url, headers=headers, timeout=10)
        logger.info(f"Kitabay response status: {resp.status_code}")
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # FIX: Improved book link detection to find relevant books
        book_links = []
        
        # Find all potential book links
        for link in soup.select("a"):
            href = link.get('href', '')
            if '/products/' in href:
                # Check if the link text or parent text contains parts of the book name
                link_text = link.text.strip().lower()
                parent_text = link.parent.text.strip().lower() if link.parent else ""
                book_name_parts = [part.lower() for part in book_name.split() if len(part) > 2]
                
                # Check if any significant part of the book name is in the link text
                matches = sum(1 for part in book_name_parts if part in link_text or part in parent_text)
                if matches > 0:
                    book_links.append((link, matches))
        
        # Sort by number of matches (highest first)
        book_links.sort(key=lambda x: x[1], reverse=True)
        
        # Use the link with the most matches
        book_link = book_links[0][0] if book_links else None
            
        if not book_link:
            logger.warning("No relevant book link found on Kitabay")
            # Save the HTML for debugging
            with open("kitabay_debug.html", "w", encoding="utf-8") as f:
                f.write(resp.text)
            logger.info("Saved Kitabay HTML for debugging")
            return book_name, "Unknown", "Unknown", "https://source.unsplash.com/random/300x400/?book", "kitabay", 0.0, 0.0, "No description available", "Unknown", "Unknown", "Unknown"

        # Get the book page URL
        book_url = book_link['href']
        if not book_url.startswith("https://"):
            book_url = "https://kitabay.com" + book_url
        logger.info(f"Found book on Kitabay: {book_url}")
        
        # Add a delay to avoid being blocked
        time.sleep(2)
        
        # Get the book page
        resp = session.get(book_url, headers=headers, timeout=10)
        logger.info(f"Kitabay book page status: {resp.status_code}")
        
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract book title
        title_element = soup.select_one("h1")
        book_name = title_element.text.strip() if title_element else book_name
        logger.info(f"Extracted title: {book_name}")

        # Try multiple price selectors
        price = 0.0
        price_selectors = [
            "p.product__inline__price > span.price.on-sale",
            "p.product__inline__price > span.price",
            "div.product__price span.price",
            ".product-price",
            ".price-item",
            ".price--highlight",  # Add more selectors
            ".price-item--regular",
            "[data-price]"
        ]
        
        for selector in price_selectors:
            price_elements = soup.select(selector)
            for price_element in price_elements:
                if price_element:
                    # Try to get price from data attribute first
                    if price_element.has_attr('data-price'):
                        try:
                            price = float(price_element['data-price']) / 100  # Convert cents to rupees
                            logger.info(f"Extracted price: {price} from data-price attribute")
                            break
                        except (ValueError, TypeError):
                            pass
                    
                    # Try to extract from text
                    price_text = price_element.text.strip()
                    price = extract_price(price_text)
                    if price > 0:
                        logger.info(f"Extracted price: {price} using selector {selector}")
                        break
            if price > 0:
                break
        
        # Try direct text search for price if selectors fail
        if price == 0.0:
            logger.info("Trying direct text search for price")
            price_patterns = [
                r'₹\s*([\d,]+\.?\d{0,2})',
                r'Rs\.?\s*([\d,]+\.?\d{0,2})'
            ]
            
            for pattern in price_patterns:
                matches = re.findall(pattern, soup.text)
                if matches:
                    try:
                        price_str = matches[0]
                        if isinstance(price_str, tuple):
                            price_str = price_str[0]
                        price = float(price_str.replace(',', ''))
                        logger.info(f"Found price {price} using direct text search")
                        break
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Error parsing price from direct text: {e}")
                        continue

        # Extract author - improved author extraction
        author_element = soup.select_one("div.product__inline__author, .author-name, .product-meta__vendor")
        author = "Unknown"
        if author_element:
            author_text = author_element.text.strip()
            # Clean up author text
            if "by" in author_text.lower():
                author = author_text.split("by", 1)[1].strip()
            else:
                author = author_text
            logger.info(f"Extracted author: {author}")

        # Extract ISBN - IMPROVED FROM webscrape2.py
        isbn = "Unknown"
        # First try to find a paragraph that specifically contains ISBN
        isbn_element = soup.find("p", string=re.compile("ISBN:"))
        if isbn_element:
            isbn_text = isbn_element.text.strip()
            isbn_match = re.search(r'ISBN:\s*(\d{3}-?\d{10}|\d{10})', isbn_text)
            if isbn_match:
                isbn = isbn_match.group(1).replace("-", "")
                logger.info(f"Found ISBN in dedicated paragraph: {isbn}")
        
        # If ISBN is still unknown, try to find it in the product description
        if isbn == "Unknown":
            details = soup.select_one("div.product__description, .product-details")
            if details:
                isbn_match = re.search(r'ISBN-13\D*(\d{3}-?\d{10}|\d{10})', details.text)
                if not isbn_match:
                    isbn_match = re.search(r'ISBN\D*(\d{3}-?\d{10}|\d{10})', details.text)
                isbn = isbn_match.group(1).replace("-", "") if isbn_match else "Unknown"
                logger.info(f"Found ISBN in product description: {isbn}")
        
        # If ISBN is still unknown, try to find it in any text on the page
        if isbn == "Unknown":
            isbn_match = re.search(r'ISBN-13\D*(\d{3}-?\d{10}|\d{10})', soup.text)
            if not isbn_match:
                isbn_match = re.search(r'ISBN\D*(\d{3}-?\d{10}|\d{10})', soup.text)
            isbn = isbn_match.group(1).replace("-", "") if isbn_match else "Unknown"
            if isbn != "Unknown":
                logger.info(f"Found ISBN in page text: {isbn}")
        
        # Clean up the ISBN
        if isbn != "Unknown":
            # Remove any non-digit characters
            isbn = re.sub(r'[^0-9]', '', isbn)
            # Ensure it's a valid ISBN length (10 or 13 digits)
            if len(isbn) not in [10, 13]:
                isbn = "Unknown"
        
        logger.info(f"Extracted ISBN: {isbn}")

        # Kitabay doesn't consistently provide ratings
        rating = 0.0

        # Extract description
        description_element = soup.select_one("div.product__description, .product-description")
        description = description_element.text.strip() if description_element else "No description available"
        logger.info(f"Extracted description: {description[:50]}...")

        # Extract image URL
        image_element = soup.select_one("div.product__image img, .product-featured-img, .product-single__media img")
        image_url = "https://source.unsplash.com/random/300x400/?book"
        if image_element:
            if 'src' in image_element.attrs:
                image_url = image_element['src']
            elif 'data-src' in image_element.attrs:
                image_url = image_element['data-src']
                
        if image_url and not image_url.startswith("http"):
            image_url = "https:" + image_url
        logger.info(f"Extracted image URL: {image_url}")

        # Try to extract genre, binding, and language from description
        genre = "Unknown"
        binding = "Unknown"
        language = "Unknown"
        
        if description_element:
            desc_text = description_element.text.lower()
            
            # Extract genre 
            desc_text = description_element.text.lower()
            
            # Extract genre
            genre_keywords = {
                "fiction": "Fiction",
                "non-fiction": "Non-Fiction",
                "biography": "Biography",
                "autobiography": "Autobiography",
                "mystery": "Mystery",
                "thriller": "Thriller",
                "romance": "Romance",
                "science fiction": "Science Fiction",
                "fantasy": "Fantasy",
                "horror": "Horror",
                "children": "Children's Books",
                "young adult": "Young Adult",
                "self-help": "Self-Help",
                "business": "Business",
                "history": "History"
            }
            
            for keyword, genre_name in genre_keywords.items():
                if keyword in desc_text:
                    genre = genre_name
                    logger.info(f"Found genre from description: {genre}")
                    break
            
            # Extract binding and language if mentioned in description
            if "hardcover" in desc_text:
                binding = "Hardcover"
                logger.info(f"Found binding from description: {binding}")
            elif "paperback" in desc_text:
                binding = "Paperback"
                logger.info(f"Found binding from description: {binding}")
            elif "ebook" in desc_text or "e-book" in desc_text:
                binding = "E-Book"
                logger.info(f"Found binding from description: {binding}")
            
            language_patterns = [
                r'language:\s*(\w+)',
                r'in\s+(\w+)\s+language',
                r'written in\s+(\w+)'
            ]
            
            for pattern in language_patterns:
                language_match = re.search(pattern, desc_text)
                if language_match:
                    language = language_match.group(1).capitalize()
                    logger.info(f"Found language from description: {language}")
                    break

        # If genre is still unknown, try to determine from book name and description
        if genre == "Unknown":
            genre = determine_genre(book_name, description)
            logger.info(f"Determined genre from content: {genre}")

        # Log the final data being returned
        logger.info(f"Kitabay scraping complete. Returning data for {book_name}")
        return book_name, isbn, author, image_url, "kitabay", price, rating, description, genre, binding, language

    except Exception as e:
        logger.error(f"Error scraping Kitabay: {e}")
        return book_name, "Unknown", "Unknown", "https://source.unsplash.com/random/300x400/?book", "kitabay", 0.0, 0.0, "No description available", "Unknown", "Unknown", "Unknown"

def scrape_book(book_name):
    session = requests.Session()
    data = []
    
    try:
        # Scrape from all sources
        amazon_data = amazon(session, headers, book_name)
        logger.info(f"Amazon data: {amazon_data[0]}, price: {amazon_data[5]}")
        
        # Add a delay between requests to avoid being blocked
        time.sleep(2)
        
        bookswagon_data = bookswagon(session, headers, book_name)
        logger.info(f"Bookswagon data: {bookswagon_data[0]}, price: {bookswagon_data[5]}")
        
        time.sleep(2)
        
        kitabay_data = kitabay(session, headers, book_name)
        logger.info(f"Kitabay data: {kitabay_data[0]}, price: {kitabay_data[5]}")
        
        # Only add sources that returned valid data
        if amazon_data[5] > 0:
            data.append(amazon_data)
        if bookswagon_data[5] > 0:
            data.append(bookswagon_data)
        if kitabay_data[5] > 0:
            data.append(kitabay_data)
        
        # If we have no price data but have book details, add the sources with price 0
        if not data:
            if amazon_data[0] != "Unknown" and amazon_data[0] != book_name:
                data.append(amazon_data)
            if bookswagon_data[0] != "Unknown" and bookswagon_data[0] != book_name:
                data.append(bookswagon_data)
            if kitabay_data[0] != "Unknown" and kitabay_data[0] != book_name:
                data.append(kitabay_data)
            
            # If still no data, add a placeholder
            if not data:
                data.append((
                    book_name, "Unknown", "Unknown", 
                    "https://source.unsplash.com/random/300x400/?book",
                    "amazon", 0.0, 0.0, 
                    "No description available", "Unknown",
                    "Unknown", "Unknown"
                ))
    except Exception as e:
        logger.error(f"Error in scrape_book: {e}")
        # Add a placeholder if there's an error
        data.append((
            book_name, "Unknown", "Unknown", 
            "https://source.unsplash.com/random/300x400/?book",
            "amazon", 0.0, 0.0, 
            "No description available", "Unknown",
            "Unknown", "Unknown"
        ))
    
    # Print summary of scraping results
    logger.info("\nScraping Summary:")
    logger.info(f"Amazon: {'Success' if amazon_data[5] > 0 else 'No price found'}")
    logger.info(f"Bookswagon: {'Success' if bookswagon_data[5] > 0 else 'No price found'}")
    logger.info(f"Kitabay: {'Success' if kitabay_data[5] > 0 else 'No price found'}")
    
    return data


def get_random_book():
    popular_books = [
        "Harry Potter and the Philosopher's Stone",
        "To Kill a Mockingbird",
        "The Great Gatsby",
        "Pride and Prejudice",
        "The Alchemist",
        "1984",
        "The Lord of the Rings",
        "The Hobbit",
        "The Catcher in the Rye",
        "The Da Vinci Code",
        "Atomic Habits",
        "Rich Dad Poor Dad",
        "Ikigai",
        "The Psychology of Money",
        "Think and Grow Rich"
    ]
    return random.choice(popular_books)

def get_all_genres():
    with orm.db_session:
        genres = list(orm.select(b.genre for b in BookPrice if b.genre is not None).distinct())
        # Filter out None or empty genres
        genres = [genre for genre in genres if genre]
        # Add default genres if database is empty
        if not genres:
            genres = ["Fiction", "Non-Fiction", "Mystery", "Romance", "Science Fiction", 
                     "Fantasy", "Biography", "History", "Self-Help", "Children"]
    return genres

def get_all_authors():
    with orm.db_session:
        authors = list(orm.select(b.author for b in BookPrice if b.author is not None and b.author != "Unknown").distinct())
        # Add default authors if database is empty
        if not authors:
            authors = ["J.K. Rowling", "George Orwell", "Jane Austen", "Agatha Christie", 
                      "Chetan Bhagat", "Sudha Murty", "Paulo Coelho"]
    return authors

# Check database connectivity
def check_database():
    try:
        with orm.db_session:
            count = orm.count(b for b in BookPrice)
            logger.info(f"Database connection successful. Found {count} books in database.")
            return True
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        return False

# Routes
@app.route('/')
def home():
    with orm.db_session:
        recent_books = list(orm.select(b for b in BookPrice).order_by(orm.desc(BookPrice.date_created)).limit(6))
        unique_books = {}
        for book in recent_books:
            if book.book_name not in unique_books:
                unique_books[book.book_name] = {
                    'name': book.book_name,
                    'author': book.author,
                    'image': book.image_url,
                    'genre': book.genre or "Unknown",
                    'stores': []
                }
            if book.website not in unique_books[book.book_name]['stores']:
                unique_books[book.book_name]['stores'].append(book.website)
    
    genres = get_all_genres()
    authors = get_all_authors()
    
    return render_template('index.html', books=list(unique_books.values())[:3], genres=genres, authors=authors)

@app.route('/search')
def search():
    query = request.args.get('query', '')
    min_price = request.args.get('min', None)
    max_price = request.args.get('max', None)
    
    if not query:
        return redirect(url_for('home'))
    
    with orm.db_session:
        # Search in book name, author, and genre
        existing_books = list(orm.select(b for b in BookPrice if 
                                        query.lower() in b.book_name.lower() or 
                                        (b.author and query.lower() in b.author.lower()) or
                                        (b.genre and query.lower() in b.genre.lower())))
        
        # If no books found or data is old, scrape new data
        if not existing_books or (datetime.now() - existing_books[0].date_created).days >= 1:
            logger.info(f"Scraping new data for '{query}'")
            book_data = scrape_book(query)
            
            # Debug the scraped data
            logger.info(f"Scraped data: {book_data}")
            
            with orm.db_session:
                for item in book_data:
                    # Debug each item being saved
                    logger.info(f"Saving book: {item[0]}, price: {item[5]}, website: {item[4]}")
                    
                    BookPrice(
                        book_name=item[0], isbn=item[1], author=item[2], image_url=item[3],
                        website=item[4], price=item[5], rating=item[6], description=item[7],
                        date_created=datetime.now(), genre=item[8], binding=item[9], language=item[10]
                    )
            
            # Fetch the newly added books
            existing_books = list(orm.select(b for b in BookPrice if 
                                        query.lower() in b.book_name.lower() or 
                                        (b.author and query.lower() in b.author.lower()) or
                                        (b.genre and query.lower() in b.genre.lower())))
        
        logger.info(f"Using existing data from database for '{query}'")
        
        # Group books by name to create unique entries for search results
        unique_books = {}
        for book in existing_books:
            if book.book_name not in unique_books:
                unique_books[book.book_name] = {
                    'author': book.author or "Unknown",
                    'isbn': book.isbn or "Unknown",
                    'image_url': book.image_url or "https://source.unsplash.com/random/300x400/?book",
                    'genre': book.genre or "Unknown",
                    'rating': book.rating or 0.0,
                    'min_price': float('inf'),
                    'sources': []
                }
            
            # Add source if not already in the list
            if book.website not in unique_books[book.book_name]['sources']:
                unique_books[book.book_name]['sources'].append(book.website)
            
            # Update minimum price
            if book.price > 0 and book.price < unique_books[book.book_name]['min_price']:
                unique_books[book.book_name]['min_price'] = book.price
        
        # Set min_price to 0 if it's still infinity
        for book_info in unique_books.values():
            if book_info['min_price'] == float('inf'):
                book_info['min_price'] = 0
    
    # Apply price filters if provided
    if min_price or max_price:
        filtered_books = {}
        for book_title, book_info in unique_books.items():
            price = book_info['min_price']
            if min_price and price < float(min_price):
                continue
            if max_price and price > float(max_price) and price > 0:
                continue
            filtered_books[book_title] = book_info
        unique_books = filtered_books
    
    genres = get_all_genres()
    authors = get_all_authors()
    
    # Debug the final data being sent to template
    logger.info(f"Sending {len(unique_books)} unique books to search template")
    
    return render_template('search.html', query=query, unique_books=unique_books, genres=genres, authors=authors)

# This is a snippet of the relevant functions that handle price data

@app.route('/book/<isbn>')
def book_detail(isbn):
    # Handle invalid ISBN values
    if isbn in ['True', 'False']:
        flash("Invalid book identifier", "error")
        return redirect(url_for('home'))
        
    with orm.db_session:
        logging.info(f"Looking up book with ISBN: {isbn}")
        
        # Handle "Unknown" ISBN
        if isbn == "Unknown":
            flash("Book not found", "error")
            return redirect(url_for('home'))
            
        # First try to find by exact ISBN match
        books_by_isbn = list(orm.select(b for b in BookPrice if b.isbn == isbn))
        
        if books_by_isbn:
            # Collect all entries for this ISBN
            book_data = list(orm.select((
                b.book_name, b.isbn, b.author, 
                b.image_url, b.website, b.price, b.rating, 
                b.description, b.genre, b.binding, b.language
            ) for b in BookPrice if b.isbn == isbn))
            
            # Replace None values with "Unknown" to avoid template errors
            book_data = [(
                item[0], 
                item[1] if item[1] else "Unknown", 
                item[2] if item[2] else "Unknown",
                item[3] if item[3] else "https://source.unsplash.com/random/300x400/?book",
                item[4], 
                item[5], 
                item[6] if item[6] is not None else 0.0,
                item[7] if item[7] else "No description available",
                item[8] if item[8] else "Unknown",
                item[9] if item[9] else "Unknown",
                item[10] if item[10] else "Unknown"
            ) for item in book_data]
            
            # Create a dictionary to store unique entries by website
            # This prevents duplicates in the price comparison table
            websites = {}
            for item in book_data:
                website = item[4]
                if website not in websites or (websites[website][5] == 0 and item[5] > 0) or (item[5] > 0 and item[5] < websites[website][5]):
                    websites[website] = item
            
            # Convert back to list, ensuring no duplicates
            book_data = list(websites.values())
            
            # Ensure all three websites are present
            main_websites = ["amazon", "bookswagon", "kitabay"]
            for website in main_websites:
                if website not in [item[4] for item in book_data]:
                    # Add a placeholder entry for the missing website
                    book_data.append((
                        book_data[0][0],  # Use the same book name
                        book_data[0][1],  # Use the same ISBN
                        book_data[0][2],  # Use the same author
                        book_data[0][3],  # Use the same image
                        website,          # The missing website
                        0.0,              # Price not available
                        0.0,              # No rating
                        "No description available",
                        book_data[0][8],  # Use the same genre
                        book_data[0][9],  # Use the same binding
                        book_data[0][10]  # Use the same language
                    ))
            
            # Sort the book data to show websites with prices first
            book_data = sorted(book_data, key=lambda x: (0 if x[5] > 0 else 1, x[5] if x[5] > 0 else float('inf')))
            
        else:
            # If no books found by ISBN, try to find by partial ISBN match
            if len(isbn) >= 10:
                isbn_clean = isbn.replace("-", "").strip()
                books_by_partial = list(orm.select(b for b in BookPrice if b.isbn and isbn_clean in b.isbn.replace("-", "")))
                
                if books_by_partial:
                    book_data = list(orm.select((
                        b.book_name, b.isbn, b.author, 
                        b.image_url, b.website, b.price, b.rating, 
                        b.description, b.genre, b.binding, b.language
                    ) for b in books_by_partial))
                    
                    # Replace None values with "Unknown"
                    book_data = [(
                        item[0], 
                        item[1] if item[1] else "Unknown", 
                        item[2] if item[2] else "Unknown",
                        item[3] if item[3] else "https://source.unsplash.com/random/300x400/?book",
                        item[4], 
                        item[5], 
                        item[6] if item[6] is not None else 0.0,
                        item[7] if item[7] else "No description available",
                        item[8] if item[8] else "Unknown",
                        item[9] if item[9] else "Unknown",
                        item[10] if item[10] else "Unknown"
                    ) for item in book_data]
                    
                    # Create a dictionary to store unique entries by website
                    websites = {}
                    for item in book_data:
                        website = item[4]
                        if website not in websites or (websites[website][5] == 0 and item[5] > 0) or (item[5] > 0 and item[5] < websites[website][5]):
                            websites[website] = item
                    
                    # Convert back to list, ensuring no duplicates
                    book_data = list(websites.values())
                    
                    # Ensure all three websites are present
                    main_websites = ["amazon", "bookswagon", "kitabay"]
                    for website in main_websites:
                        if website not in [item[4] for item in book_data]:
                            # Add a placeholder entry for the missing website
                            book_data.append((
                                book_data[0][0],  # Use the same book name
                                book_data[0][1],  # Use the same ISBN
                                book_data[0][2],  # Use the same author
                                book_data[0][3],  # Use the same image
                                website,          # The missing website
                                0.0,              # Price not available
                                0.0,              # No rating
                                "No description available",
                                book_data[0][8],  # Use the same genre
                                book_data[0][9],  # Use the same binding
                                book_data[0][10]  # Use the same language
                            ))
                    
                    # Sort the book data to show websites with prices first
                    book_data = sorted(book_data, key=lambda x: (0 if x[5] > 0 else 1, x[5] if x[5] > 0 else float('inf')))
                    
                else:
                    flash("Book not found with that ISBN, showing similar books", "warning")
                    return redirect(url_for('search', query=isbn))
            else:
                flash("Invalid ISBN format, showing search results", "warning")
                return redirect(url_for('search', query=isbn))
        
        if not book_data:
            logging.warning(f"No book found with ISBN: {isbn}")
            flash("Book not found", "error")
            return redirect(url_for('home'))
    
    genres = get_all_genres()
    authors = get_all_authors()
    
    logging.info(f"Sending book data to product template: {book_data}")
    
    return render_template('product.html', book_data=book_data, genres=genres, authors=authors)

@app.route('/refresh/<isbn>')
def refresh_prices(isbn):
    # Handle the "True" boolean string issue
    if isbn in ['True', 'False']:
        flash("Invalid book identifier", "error")
        return redirect(url_for('home'))
        
    with orm.db_session:
        book = orm.select(b for b in BookPrice if b.isbn == isbn).first()
        if not book:
            flash("Book not found", "error")
            return redirect(url_for('home'))
        
        book_name = book.book_name
        logging.info(f"Refreshing prices for '{book_name}' (ISBN: {isbn})")
        
        # Delete existing records for this book
        orm.delete(b for b in BookPrice if b.isbn == isbn)
        
        # Scrape fresh data
        book_data = scrape_book(book_name)
        for item in book_data:
            BookPrice(
                book_name=item[0], isbn=item[1], author=item[2], image_url=item[3],
                website=item[4], price=item[5], rating=item[6], description=item[7],
                date_created=datetime.now(), genre=item[8], binding=item[9], language=item[10]
            )
        
        flash("Prices refreshed successfully", "success")
    
    return redirect(url_for('book_detail', isbn=isbn))

@app.route('/book_by_name/<book_name>')
def book_by_name(book_name):
    with orm.db_session:
        logger.info(f"Looking up book with name: {book_name}")
        
        # Find books by name (case-insensitive)
        books_by_name = list(orm.select(b for b in BookPrice if book_name.lower() in b.book_name.lower()))
        
        if not books_by_name:
            logger.warning(f"No book found with name: {book_name}")
            flash("Book not found", "error")
            return redirect(url_for('home'))
        
        # Collect all entries for this book name
        book_data = list(orm.select((
            b.book_name, b.isbn, b.author, 
            b.image_url, b.website, b.price, b.rating, 
            b.description, b.genre, b.binding, b.language
        ) for b in BookPrice if book_name.lower() in b.book_name.lower()))
        
        # Replace None values with "Unknown" to avoid template errors
        book_data = [(
            item[0], 
            item[1] if item[1] else "Unknown", 
            item[2] if item[2] else "Unknown",
            item[3] if item[3] else "https://source.unsplash.com/random/300x400/?book",
            item[4], 
            item[5], 
            item[6] if item[6] is not None else 0.0,
            item[7] if item[7] else "No description available",
            item[8] if item[8] else "Unknown",
            item[9] if item[9] else "Unknown",
            item[10] if item[10] else "Unknown"
        ) for item in book_data]
        
        # Select the "best" entry for displaying book details
        # Criteria: Prefer the entry with a non-empty description, then highest rating
        best_entry = max(book_data, key=lambda x: (
            len(x[7]) if x[7] != "No description available" else 0,  # Prioritize non-empty description
            x[6]  # Then highest rating
        ))
        
        # FIX: Create a dictionary to store unique entries by website
        websites = {}
        for item in book_data:
            website = item[4]
            if website not in websites or item[5] < websites[website][5]:  # Keep the cheapest price per website
                websites[website] = item
        
        # Convert back to list, ensuring no duplicates
        book_data = list(websites.values())
        
        # Ensure the best entry is used for the main details
        if book_data and best_entry:
            book_data[0] = best_entry  # Replace the first entry with the best one for display
    
    genres = get_all_genres()
    authors = get_all_authors()
    
    logger.info(f"Sending book data to product template: {book_data}")
    
    return render_template('product.html', book_data=book_data, genres=genres, authors=authors)

@app.route('/random')
def random_book():
    book_name = get_random_book()
    return redirect(url_for('search', query=book_name))

@app.route('/category/<category>')
def category_page(category):
    with orm.db_session:
        # Get filter parameters
        min_price = request.args.get('min', type=float)
        max_price = request.args.get('max', type=float)
        store = request.args.get('store')
        sort_by = request.args.get('sort')
        page = request.args.get('page', 1, type=int)
        per_page = 12  # Books per page
        
        # Base query - filter by genre (case insensitive)
        query = orm.select(b for b in BookPrice if b.genre and b.genre.lower() == category.lower())
        
        # Apply price filters if provided
        if min_price is not None:
            query = query.filter(lambda b: b.price >= min_price)
        if max_price is not None:
            query = query.filter(lambda b: b.price <= max_price and b.price > 0)
        
        # Apply store filter if provided
        if store and store.lower() != 'all':
            query = query.filter(lambda b: b.website.lower() == store.lower())
        
        # Apply sorting
        if sort_by == 'price_asc':
            query = query.filter(lambda b: b.price > 0).order_by(BookPrice.price)
        elif sort_by == 'price_desc':
            query = query.order_by(orm.desc(BookPrice.price))
        elif sort_by == 'rating':
            query = query.order_by(orm.desc(BookPrice.rating))
        elif sort_by == 'newest':
            query = query.order_by(orm.desc(BookPrice.date_created))
        else:
            # Default sorting by relevance
            query = query.order_by(orm.desc(BookPrice.rating))
        
        # Get total count for pagination
        total_books = query.count()
        
        # Apply pagination
        books = query.page(page, per_page) if total_books > 0 else []
        
        # Group books by ISBN to avoid duplicates
        unique_books = {}
        for book in books:
            # FIX: Use book name as key if ISBN is Unknown
            key = book.isbn if book.isbn != "Unknown" else book.book_name
            if key not in unique_books or book.price < unique_books[key].price:
                unique_books[key] = book
        
        # Convert to list for template
        books_list = list(unique_books.values())
    
    genres = get_all_genres()
    authors = get_all_authors()
    
    return render_template('category.html', 
                          category=category,
                          genres=genres,
                          authors=authors,
                          books=books_list,
                          total=total_books,
                          page=page,
                          pages=(total_books + per_page - 1) // per_page if total_books > 0 else 1)

@app.route('/categories')
def categories():
    genres = get_all_genres()
    authors = get_all_authors()
    
    with orm.db_session:
        # Get a sample book for each genre
        genre_books = {}
        for genre in genres:
            if genre:  # Skip None or empty genres
                book = orm.select(b for b in BookPrice if b.genre == genre).first()
                if book:
                    genre_books[genre] = book
    
    return render_template('categories.html', genres=genres, authors=authors, genre_books=genre_books)

@app.route('/author/<author_name>')
def author_page(author_name):
    with orm.db_session:
        # Get filter parameters
        min_price = request.args.get('min', type=float)
        max_price = request.args.get('max', type=float)
        store = request.args.get('store')
        sort_by = request.args.get('sort')
        page = request.args.get('page', 1, type=int)
        per_page = 12  # Books per page
        
        # Base query - filter by author
        query = orm.select(b for b in BookPrice if b.author and author_name.lower() in b.author.lower())
        
        # Apply price filters if provided
        if min_price is not None:
            query = query.filter(lambda b: b.price >= min_price)
        if max_price is not None:
            query = query.filter(lambda b: b.price <= max_price and b.price > 0)
        
        # Apply store filter if provided
        if store and store.lower() != 'all':
            query = query.filter(lambda b: b.website.lower() == store.lower())
        
        # Apply sorting
        if sort_by == 'price_asc':
            query = query.filter(lambda b: b.price > 0).order_by(BookPrice.price)
        elif sort_by == 'price_desc':
            query = query.order_by(orm.desc(BookPrice.price))
        elif sort_by == 'rating':
            query = query.order_by(orm.desc(BookPrice.rating))
        elif sort_by == 'newest':
            query = query.order_by(orm.desc(BookPrice.date_created))
        else:
            # Default sorting by relevance
            query = query.order_by(orm.desc(BookPrice.rating))
        
        # Get total count for pagination
        total_books = query.count()
        
        # Apply pagination
        books = query.page(page, per_page) if total_books > 0 else []
        
        # Group books by ISBN to avoid duplicates
        unique_books = {}
        for book in books:
            # FIX: Use book name as key if ISBN is Unknown
            key = book.isbn if book.isbn != "Unknown" else book.book_name
            if key not in unique_books or book.price < unique_books[key].price:
                unique_books[key] = book
        
        # Convert to list for template
        books_list = list(unique_books.values())
    
    genres = get_all_genres()
    authors = get_all_authors()
    
    return render_template('author.html', 
                          author=author_name,
                          genres=genres,
                          authors=authors,
                          books=books_list,
                          total=total_books,
                          page=page,
                          pages=(total_books + per_page - 1) // per_page if total_books > 0 else 1)

@app.route('/authors')
def authors_list():
    with orm.db_session:
        # Get search and filter parameters
        search = request.args.get('search', '')
        letter = request.args.get('letter', '')
        
        # Base query
        query = orm.select((b.author, orm.count(b)) for b in BookPrice if b.author != "Unknown" and b.author is not None)
        
        # Apply search filter if provided
        if search:
            query = orm.select(
            (b.author, orm.count(b)) for b in BookPrice 
            if b.author != "Unknown" and b.author is not None and search.lower() in b.author.lower()
            )
        
        # Apply letter filter if provided
        if letter:
            query = orm.select((b.author, orm.count(b)) for b in BookPrice 
                              if b.author != "Unknown" and b.author is not None and b.author.startswith(letter))
        
        # Group by author and count books
        authors_data = list(query)
        
        # Sort by author name
        authors_data.sort(key=lambda x: x[0])
    
    genres = get_all_genres()
    authors = get_all_authors()
    
    return render_template('authors.html', authors_data=authors_data, genres=genres, authors=authors)

@app.route('/best-deals')
def best_deals():
    with orm.db_session:
        # Get books with highest ratings
        top_rated_books = list(orm.select(b for b in BookPrice if b.rating > 0).order_by(orm.desc(BookPrice.rating)).limit(20))
        
        # Group by book name to avoid duplicates
        unique_books = {}
        for book in top_rated_books:
            # FIX: Use book name as key if ISBN is Unknown
            key = book.isbn if book.isbn != "Unknown" else book.book_name
            if key not in unique_books:
                unique_books[key] = book
        
        # Convert to list for template
        books_list = list(unique_books.values())
    
    genres = get_all_genres()
    authors = get_all_authors()
    
    return render_template('best_deals.html', books=books_list, genres=genres, authors=authors)

@app.route('/services')
def services():
    genres = get_all_genres()
    authors = get_all_authors()
    return render_template('services.html', genres=genres, authors=authors)

@app.route('/more')
def more():
    genres = get_all_genres()
    authors = get_all_authors()
    return render_template('more.html', genres=genres, authors=authors)

@app.route('/debug-templates')
def debug_templates():
    """Route to check template rendering and variables"""
    with orm.db_session:
        # Get a sample book
        book = orm.select(b for b in BookPrice).first()
        if not book:
            return "No books in database"
        
        # Create sample data in the format expected by templates
        book_data = [(
            book.book_name, 
            book.isbn or "Unknown", 
            book.author or "Unknown",
            book.image_url or "https://source.unsplash.com/random/300x400/?book",
            book.website, 
            book.price, 
            book.rating or 0.0,
            book.description or "No description available",
            book.genre or "Unknown",
            book.binding or "Unknown",
            book.language or "Unknown"
        )]
        
        # Log the data structure
        logger.info(f"Debug template data: {book_data}")
        
        # Create a debug response with template variables
        response = "<h1>Template Debug Info</h1>"
        response += "<h2>Book Data Structure:</h2>"
        response += "<pre>" + str(book_data) + "</pre>"
        
        # Check product.html template for issues
        try:
            rendered = render_template('product.html', 
                                      book_data=book_data, 
                                      genres=get_all_genres(), 
                                      authors=get_all_authors())
            response += "<h2>Template Renders Successfully</h2>"
        except Exception as e:
            response += f"<h2>Template Error:</h2><pre>{str(e)}</pre>"
            
        return response

if __name__ == '__main__':
    # Check database connectivity before starting
    check_database()
    app.run(debug=True)
