"""
BROKEN: LLM-based PowerPoint editing agent.

Known issues (do not fix here — this is the "before" state):
  1. Sequential calls: processes one slide at a time — 10 slides × ~2s = >20s total.
  2. No prompt caching: the same 1500-token system prompt is re-sent on every call,
     billed at full price every time.
  3. No fallback model: if Claude Sonnet is slow or rate-limited, the whole request fails.
  4. Hardcoded API key: leaked if repo goes public.
  5. No timeout: a single slow response blocks the entire request indefinitely.
  6. No logging: impossible to diagnose why a request took 25 seconds.
  7. No concurrency control: under load, may exhaust API rate limits.
"""

import json
import time
import anthropic


# ❌ Hardcoded API key — should be in environment variables
ANTHROPIC_API_KEY = "sk-ant-api03-REPLACE-WITH-REAL-KEY"

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# A long system prompt (~1500 tokens) re-sent on every single call.
# With prompt caching this would be billed at 10% after the first call.
# Without caching, you pay full price each time.
SYSTEM_PROMPT = """You are a professional PowerPoint slide editor for Dinosaur Whisperer,
a data platform company. Your job is to take a slide's text content and an editing
instruction, then return a concise JSON response with your suggested edits.

## Editing Principles

1. CONCISENESS: Remove filler words, redundant phrases, and unnecessary qualifiers.
   Bad:  "We are very excited to announce that we have successfully completed..."
   Good: "We completed..."

2. CLARITY: Replace jargon with plain language unless the audience is technical.
   Bad:  "Leverage synergies to optimise cross-functional alignment."
   Good: "Help teams work together more effectively."

3. STRUCTURE: Use parallel construction for bullet points.
   Bad:  "• Increased revenue  • We reduced costs  • Customer satisfaction improved"
   Good: "• Increased revenue  • Reduced costs  • Improved customer satisfaction"

4. DATA: Always keep numbers, metrics, and data points. Never remove quantitative claims.

5. TONE: Match the original tone (formal/casual). Don't inject humour unless asked.

6. VISUAL HIERARCHY: Suggest splitting dense text into bullets or sub-headings where
   a slide has more than 40 words of body text.

## Response Format

Return valid JSON only (no markdown fences):
{
  "slide_number": <int>,
  "original_word_count": <int>,
  "suggested_word_count": <int>,
  "reduction_percent": <float>,
  "suggestions": [
    {
      "type": "rewrite" | "delete" | "split" | "restructure",
      "original": "<original text>",
      "suggested": "<your suggestion>",
      "reason": "<brief reason>"
    }
  ],
  "overall_note": "<one-sentence summary of what you changed and why>"
}
"""


def edit_presentation(slides: list[dict], instruction: str) -> dict:
    """
    Process a PowerPoint presentation slide by slide.

    Args:
        slides: list of {"number": int, "content": str}
        instruction: e.g. "Make these slides more concise"

    Returns:
        dict with results, total_time, and per-slide timings
    """
    results = []
    start = time.time()

    # ❌ Sequential: each call blocks until the previous one finishes
    for slide in slides:
        slide_start = time.time()

        # ❌ system= as a plain string — no cache_control, no caching
        # ❌ no timeout — if the API hangs, we hang
        response = client.messages.create(
            model="claude-sonnet-4-6-20250929",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Slide {slide['number']}:\n"
                        f"{slide['content']}\n\n"
                        f"Instruction: {instruction}"
                    ),
                }
            ],
        )

        slide_time = time.time() - slide_start

        results.append({
            "slide_number": slide["number"],
            "suggestion": response.content[0].text,
            "time_seconds": round(slide_time, 2),
        })

    total_time = time.time() - start

    return {
        "results": results,
        "total_time_seconds": round(total_time, 2),
        "slide_count": len(slides),
    }


# ❌ No fallback — if you want to run this, you need the exact model available
# ❌ No error handling for rate limits, API errors, or malformed responses
# ❌ No way to configure models, timeouts, or concurrency at deploy time

if __name__ == "__main__":
    sample_slides = [
        {"number": 1, "content": "We are very excited to announce that our team has "
         "successfully completed the migration of all legacy systems to the new cloud "
         "infrastructure platform, which will enable us to better serve our customers."},
        {"number": 2, "content": "Key Metrics\n• Revenue increased by 25%\n"
         "• We managed to reduce our costs by 15%\n"
         "• Customer satisfaction scores have improved significantly\n"
         "• The team successfully onboarded 50 new enterprise clients"},
        {"number": 3, "content": "Next Steps\nWe plan to leverage our existing synergies "
         "to optimise cross-functional alignment and drive strategic initiatives forward "
         "in Q3, while also exploring new market opportunities."},
    ]

    result = edit_presentation(sample_slides, "Make these slides more concise")
    print(json.dumps(result, indent=2))
    print(f"\nTotal time: {result['total_time_seconds']}s")
