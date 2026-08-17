---
name: rich-link
description: Convert long or complex answers into a mobile-friendly rich HTML page with a shareable link. Use whenever the response is long, structured, or would render better as a formatted page (especially for chat/Telegram users).
---

# Rich Link

Long answers are painful on phones. Render them as a self-contained HTML
page, store it via the `shortlinks` mini-app, and reply with a short
summary plus the link.

## When to use

- The answer is long (more than a few paragraphs), heavily structured
  (tables, steps, code), or contains formatted content that chat mangles.
- The user is on a chat channel (Telegram, etc.) or asked for something
  they will read later.

Do NOT use for: short confirmations, quick facts, brief status replies.

## Procedure

1. **Compose the page** as one self-contained HTML string:
   - inline all CSS in a `<style>` block; no external scripts/fonts/images
     (remote resources break and leak the reader's IP);
   - `<meta name="viewport" content="width=device-width, initial-scale=1">`;
   - readable on mobile: max-width container, system font stack, dark-on-light
     or light-on-dark with good contrast;
   - include a title, generation date, and clear section headings.

2. **Store it**:

   ```bash
   curl -s -X POST http://endpoint-platform:8000/api/shortlinks/links \
     -H 'Content-Type: application/json' \
     -d "$(python3 - <<'EOF'
   import json
   html = open('/tmp/page.html').read()   # or build inline
   print(json.dumps({"title": "My report", "html": html, "expire_hours": 168}))
   EOF
   )"
   ```

   Response: `{"slug": "...", "path": "/api/shortlinks/p/<slug>", ...}`

3. **Reply** with a 1–3 line summary of the content + the link:
   - use `PUBLIC_BASE_URL` + path when set (externally reachable),
     otherwise `LOCAL_BASE_URL` + path.

## Notes

- Pages expire (default 7 days); say so if the content matters long-term.
- Never put secrets into pages — links are unauthenticated. Anyone with
  the URL can read the page.
- Keep the in-chat summary genuinely useful; the link is for depth, not a
  replacement for an answer.
