import os
from dotenv import load_dotenv

# Load all environment variables from the .env file
load_dotenv()

# We retrieve keys with os.getenv. 
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not YOUTUBE_API_KEY:
    print("Warning: YOUTUBE_API_KEY is not set in the .env file.")

if not GEMINI_API_KEY:
    print("Warning: GEMINI_API_KEY is not set in the .env file. Summaries will not be generated.")
