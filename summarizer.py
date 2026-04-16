import json
from google import genai
from config import GEMINI_API_KEY

def summarize_text(text: str) -> dict:
    """
    Sends text to the Gemini API and returns a dict with:
      - 'summary': a 1-sentence summary of the mention
      - 'sentiment': one of 'Positive', 'Negative', or 'Neutral'
    """
    fallback = {
        "summary": text.strip()[:200] if text else "",
        "sentiment": "Neutral"
    }

    if not text or len(text.strip()) < 50:
        fallback["summary"] = text.strip()
        return fallback

    if not GEMINI_API_KEY:
        if len(text) > 200:
            fallback["summary"] = text[:200] + "..."
        return fallback

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        prompt = f"""You are an expert brand mention analyst who understands internet culture, slang, and sarcasm.
Read the following mention and respond with ONLY valid JSON (no markdown, no code fences) in this exact format:
{{"summary": "one concise sentence summarizing what the person is saying", "sentiment": "Positive or Negative or Neutral"}}

Rules:
- The summary should NOT start with phrases like "The speaker says" or "This post".
- The sentiment must be exactly one of: Positive, Negative, Neutral.
- Understand internet slang: "sick", "fire", "hits different", "goated" = Positive. "mid", "trash", "L" = Negative.
- Detect sarcasm from context and emoji cues (e.g. "Yeah right 🙄" = Negative, not Positive).
- If genuinely ambiguous, use Neutral.

Mention:
{text[:4000]}"""
        
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
        )
        
        result = json.loads(response.text.strip())
        # Validate expected keys exist
        if "summary" in result and "sentiment" in result:
            # Ensure sentiment is one of the expected values
            if result["sentiment"] not in ("Positive", "Negative", "Neutral"):
                result["sentiment"] = "Neutral"
            return result
        return fallback
    except Exception as e:
        print(f"Error calling Gemini: {e}")
        return fallback
