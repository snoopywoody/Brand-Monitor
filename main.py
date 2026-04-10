import feedparser
import requests
import json
import os
import sys
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from datetime import datetime
import schedule
import time

from config import YOUTUBE_API_KEY
from summarizer import summarize_text

SHEET_NAME = "Brand Monitor"

# Make sure your Google Sheet has the following headers on row 1:
# Timestamp | Brand | Source | Title | Link | Summary | Sentiment

BRANDS_FILE = "brands.json"
SEEN_FILE = "seen_links.json"  # Used for local runs only

def load_brands():
    """Load brands from brands.json. Edit that file to add/change brands without touching code."""
    try:
        with open(BRANDS_FILE, "r") as f:
            brands_list = json.load(f)
        return {b["name"]: b["keywords"] for b in brands_list}
    except Exception as e:
        print(f"Error loading {BRANDS_FILE}: {e}")
        return {}

def get_sheet():
    # Supports credentials.json file (local) OR GOOGLE_CREDENTIALS_JSON env var (Railway/cloud)
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        
        creds_json = os.getenv("GOOGLE_CREDENTIALS_JSON")
        if creds_json:
            import json as _json
            info = _json.loads(creds_json)
            creds = Credentials.from_service_account_info(info, scopes=scopes)
        else:
            creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
        
        client = gspread.authorize(creds)
        return client.open(SHEET_NAME).sheet1
    except Exception as e:
        print(f"Error accessing Google Sheet: {e}")
        return None

def load_seen_from_file():
    """Load seen links from local JSON file (for local runs)."""
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_to_file(seen):
    """Save seen links to local JSON file (for local runs)."""
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def load_seen_from_sheet(sheet):
    """Load already-processed links from the Google Sheet (for GitHub Actions)."""
    if not sheet:
        return set()
    try:
        # Column E (index 5) contains the Link
        links = sheet.col_values(5)
        # Skip the header row
        return set(links[1:]) if len(links) > 1 else set()
    except Exception as e:
        print(f"Error reading existing links from sheet: {e}")
        return set()

def fetch_reddit(query):
    # Sort by new will get the latest 100 posts matching the query
    url = f"https://www.reddit.com/search.rss?q={query}&sort=new&limit=100"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Accept": "application/rss+xml, application/xml, text/xml"
    }
    try:
        feed = feedparser.parse(requests.get(url, headers=headers, timeout=10).text)
        return feed.entries
    except Exception as e:
        print(f"Reddit fetch failed: {e}")
        return []

def fetch_google_news(query):
    url = f"https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
    }
    try:
        feed = feedparser.parse(requests.get(url, headers=headers, timeout=10).text)
        return feed.entries
    except Exception as e:
        print(f"Google News fetch failed: {e}")
        return []

def fetch_youtube(brand_name):
    results = []
    if not YOUTUBE_API_KEY:
        print("Missing YouTube API Key, skipping YouTube.")
        return results

    try:
        yt = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        search = yt.search().list(
            q=brand_name + " review",
            part="snippet",
            type="video",
            maxResults=10,
            order="date"
        ).execute()
        
        for item in search.get("items", []):
            video_id = item["id"]["videoId"]
            channel = item["snippet"]["channelTitle"]
            video_url = f"https://youtube.com/watch?v={video_id}"
            title = item["snippet"]["title"]
            description = item["snippet"]["description"]
            
            # Use Gemini to summarize the video description
            summary_prompt = f"Video by {channel}. Description: {description}"
            analysis = summarize_text(summary_prompt)
            results.append({
                "title": title,
                "link": video_url,
                "summary": analysis["summary"],
                "sentiment": analysis["sentiment"],
                "source": "YouTube"
            })
            
            # Fetch comments for the video
            try:
                comments = yt.commentThreads().list(
                    part="snippet",
                    videoId=video_id,
                    maxResults=10,
                    order="relevance"
                ).execute()
                for c in comments.get("items", []):
                    text = c["snippet"]["topLevelComment"]["snippet"]["textDisplay"]
                    if len(text) > 20:
                        analysis = summarize_text(f"Comment regarding {brand_name}: {text}")
                        results.append({
                            "title": text[:100],
                            "link": video_url,
                            "summary": analysis["summary"],
                            "sentiment": analysis["sentiment"],
                            "source": "YouTube Comment"
                        })
            except Exception:
                # Video might have comments disabled
                pass
    except Exception as e:
        print(f"YouTube failed: {e}")
    return results

def save_to_sheet(sheet, new_mentions):
    if not sheet:
        return
        
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    rows = []
    
    for mention in new_mentions:
        rows.append([
            timestamp,
            mention["brand"],
            mention["source"],
            mention["title"],
            mention["link"],
            mention["summary"],
            mention["sentiment"]
        ])
    
    if rows:
        try:
            # Append in bulk is more API efficient
            sheet.append_rows(rows)
            print(f"Added {len(rows)} rows to Google Sheet")
        except Exception as e:
            print(f"Failed to save to Google Sheets: {e}")

def run(use_sheet_for_seen=False):
    print(f"Running at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    brands = load_brands()
    if not brands:
        print("No brands configured. Add brands to brands.json.")
        return

    sheet = get_sheet()

    # In GitHub Actions, read seen links from the sheet (no local file persists).
    # Locally, use the JSON file for speed.
    if use_sheet_for_seen:
        print("Loading seen links from Google Sheet...")
        seen = load_seen_from_sheet(sheet)
    else:
        seen = load_seen_from_file()

    new_mentions = []

    for brand_name, query in brands.items():
        print(f"Checking mentions for: {brand_name}")
        rss_entries = fetch_reddit(query) + fetch_google_news(brand_name)
        yt_entries = fetch_youtube(brand_name)

        # Process RSS (Reddit + News)
        for entry in rss_entries:
            link = entry.get("link", "")
            if link and link not in seen:
                seen.add(link)
                source = "Google News" if "news.google.com" in link else "Reddit"
                raw_text = entry.get("summary", entry.get("title", ""))
                analysis = summarize_text(raw_text)
                
                new_mentions.append({
                    "brand": brand_name,
                    "source": source,
                    "title": entry.get("title", ""),
                    "link": link,
                    "summary": analysis["summary"],
                    "sentiment": analysis["sentiment"]
                })

        # Process YouTube (Videos and Comments)
        for entry in yt_entries:
            link = entry.get("link", "")
            if link and link not in seen:
                seen.add(link)
                new_mentions.append({
                    "brand": brand_name,
                    "source": entry["source"],
                    "title": entry["title"],
                    "link": link,
                    "summary": entry["summary"],
                    "sentiment": entry["sentiment"]
                })

    # Save locally only when running in local mode
    if not use_sheet_for_seen:
        save_seen_to_file(seen)

    if new_mentions:
        save_to_sheet(sheet, new_mentions)
    else:
        print("No new mentions found since last run.")

if __name__ == "__main__":
    print("--- Starting Brand Monitor ---")

    if "--once" in sys.argv:
        # GitHub Actions mode: run once using sheet for dedup, then exit
        run(use_sheet_for_seen=True)
        print("--- Done ---")
    else:
        # Local mode: run with schedule loop using local JSON for dedup
        run(use_sheet_for_seen=False)
        schedule.every(1).hours.do(lambda: run(use_sheet_for_seen=False))
        print("Monitor running. Checks every hour. Press Ctrl+C to stop.")
        while True:
            schedule.run_pending()
            time.sleep(60)
