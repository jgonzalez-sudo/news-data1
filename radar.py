"""News radar: what competitor and reference outlets are covering, by group.

For each active group in sources.json it:
  1. pulls RSS items from outlets that have feeds (last 48h),
  2. asks Claude, with web search restricted to the group's outlet domains,
     what those outlets are leading with (filling gaps for outlets without feeds),
  3. drops any story whose link can't be traced to a real feed item or search
     result, and
  4. publishes everything to radar.json in the repo.

Usage:
  python radar.py                    # all active groups
  python radar.py Business Africa    # only these groups (names from sources.json)
  python radar.py --dry-run          # write radar.json locally, don't upload
Groups can also be passed comma-separated in the RADAR_GROUPS env var.
"""
import base64
import json
import os
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests

MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"
WEB_SEARCH_TOOL = "web_search_20250305"
MAX_SEARCHES_PER_GROUP = 4
MAX_TOKENS = 10000
FEED_WINDOW_HOURS = 48
MAX_FEED_ITEMS_PER_OUTLET = 8

GITHUB_USERNAME = "jgonzalez-sudo"
GITHUB_REPO = "news-data1"
RADAR_FILE = "radar.json"
RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_USERNAME}/{GITHUB_REPO}/main/{RADAR_FILE}"

SYSTEM_PROMPT = """You are a research assistant for newsroom editors at Semafor. Report what the listed outlets are covering right now, so journalists can decide what to consider for coverage.

Rules:
- Use web search restricted to the allowed outlets, plus the feed items provided. Look for what each outlet is leading with in the last 24-48 hours. Skip anything older.
- Some outlets block web search. For those, report only what appears in the feed items provided, and never claim to have checked them any other way.
- Stay on the group's topic. Feed items from general outlets include unrelated stories; ignore those.
- Outlets marked state-run or company announcements publish official positions. When you cite one, say so in the summary (for example 'state-run Global Times says...').
- Use at most 4 searches. Write no commentary before the answer: your final message must be only the JSON object.
- Rank by newsworthiness for a global news audience: human stakes, major economic or policy moves, cross-border significance, and stories several outlets are converging on. Skip routine items, opinion columns, listicles and lifestyle.
- Many outlets are paywalled, so you will usually see only headlines and snippets. Write each summary in your own words (one sentence, two at most), based only on what you actually saw. If you only saw a headline, keep the summary modest. Never quote more than a few words.
- Every url must be copied exactly from a search result or feed item. Never invent or guess URLs, headlines or details.
- If several outlets cover the same story, list it once and name the others in also_covered_by (only outlets where you actually saw it).
- Give 5-8 stories, most important first.

Respond with ONLY valid JSON, no preamble and no markdown:
{"overview": "2-3 sentences on what these outlets are collectively focused on", "stories": [{"headline": "...", "outlet": "...", "url": "...", "summary": "...", "also_covered_by": ["..."]}], "gaps": "outlets you could find nothing recent from, or an empty string"}"""


# ── registry ────────────────────────────────────────────────────────────────
def load_registry(path="sources.json"):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_outlets(registry, ids):
    return [dict(registry["outlets"][i], id=i) for i in ids]


# ── feeds ───────────────────────────────────────────────────────────────────
def entries_from_parsed(parsed, outlet_name, hours=FEED_WINDOW_HOURS, limit=MAX_FEED_ITEMS_PER_OUTLET):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    items = []
    for e in parsed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        if t and datetime(*t[:6], tzinfo=timezone.utc) < cutoff:
            continue
        link = e.get("link")
        title = e.get("title")
        if not link or not title:
            continue
        summary = re.sub(r"<[^<]+?>", "", e.get("summary", "") or "")
        items.append({"outlet": outlet_name, "title": title.strip(), "url": link,
                      "snippet": " ".join(summary.split()[:30])})
        if len(items) >= limit:
            break
    return items


_FEED_CACHE = {}  # url -> items, so a feed shared by several groups is downloaded once per run


def collect_feed_items(outlets, timeout=15):
    """Returns (items, report). report maps outlet name -> item count or 'failed'."""
    items, report = [], {}
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        for o in outlets:
            if not o.get("feeds"):
                continue
            got = []
            for url in o["feeds"]:
                try:
                    if url not in _FEED_CACHE:
                        _FEED_CACHE[url] = entries_from_parsed(feedparser.parse(url), o["name"])
                    got.extend(_FEED_CACHE[url])
                except Exception:
                    pass
            report[o["name"]] = len(got) if got else "failed"
            items.extend(got)
    finally:
        socket.setdefaulttimeout(old_timeout)
    return items, report


# ── Claude call ─────────────────────────────────────────────────────────────
def blocks_text(blocks):
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def collect_urls(blocks):
    """Every URL the model could legitimately cite: search results and citations."""
    urls, errors = set(), []
    for b in blocks:
        if b.get("type") == "web_search_tool_result":
            content = b.get("content")
            if isinstance(content, list):
                for r in content:
                    if r.get("url"):
                        urls.add(r["url"])
            elif isinstance(content, dict) and content.get("error_code"):
                errors.append(content["error_code"])
        if b.get("type") == "text":
            for c in b.get("citations") or []:
                if c.get("url"):
                    urls.add(c["url"])
    return urls, errors


BLOCKED = set()  # domains the search tool told us it can't access (learned during this run)


class TruncatedResponse(Exception):
    pass


class BlockedDomains(Exception):
    def __init__(self, domains):
        super().__init__(f"search can't access: {domains}")
        self.domains = domains


def parse_blocked(message):
    m = re.search(r"not accessible to our user agent:\s*\[(.*?)\]", message or "", re.S)
    return re.findall(r"""['"]([^'"]+)['"]""", m.group(1)) if m else []


def call_claude(api_key, user_msg, domains, max_tokens=MAX_TOKENS):
    payload = {"model": MODEL, "max_tokens": max_tokens, "system": SYSTEM_PROMPT}
    if domains:  # with no searchable domains left, answer from the feed items alone
        payload["tools"] = [{"type": WEB_SEARCH_TOOL, "name": "web_search",
                             "max_uses": MAX_SEARCHES_PER_GROUP, "allowed_domains": domains}]
    messages = [{"role": "user", "content": user_msg}]
    all_blocks, searches = [], 0
    for _ in range(4):  # the server-side search loop can pause; continue if so
        data, last_err = None, None
        for _attempt in range(2):
            try:
                r = requests.post(
                    API_URL,
                    headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={**payload, "messages": messages},
                    timeout=300,
                )
                if r.status_code >= 400:
                    try:
                        message = r.json().get("error", {}).get("message", "")
                    except Exception:
                        message = r.text
                    blocked = parse_blocked(message)
                    if r.status_code == 400 and blocked:
                        raise BlockedDomains(blocked)
                    raise RuntimeError(f"API {r.status_code}: {r.text[:500]}")
                data = r.json()
                break
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                last_err = e
        if data is None:
            raise last_err
        blocks = data.get("content", [])
        all_blocks.extend(blocks)
        searches += (data.get("usage", {}).get("server_tool_use") or {}).get("web_search_requests", 0)
        stop = data.get("stop_reason")
        if stop == "pause_turn":
            messages.append({"role": "assistant", "content": blocks})
            continue
        if stop == "max_tokens":
            raise TruncatedResponse()
        break
    return all_blocks, searches


def extract_json(text):
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in model response")
    return json.loads(text[start:end + 1])


# ── validation ──────────────────────────────────────────────────────────────
def normalize_url(u):
    p = urlparse(u.strip())
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


def host_allowed(url, domains):
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def filter_stories(stories, seen_urls, domains):
    """Keep only stories whose link traces to a real feed item or search result
    and sits on an allowed domain. Returns (kept, dropped_count)."""
    seen = {normalize_url(u) for u in seen_urls}
    kept, used, dropped = [], set(), 0
    for s in stories:
        url = s.get("url") or ""
        key = normalize_url(url) if url else ""
        if not (s.get("headline") and url and key in seen and host_allowed(url, domains)) or key in used:
            dropped += 1
            continue
        used.add(key)
        kept.append({
            "headline": s["headline"],
            "outlet": s.get("outlet", ""),
            "url": url,
            "summary": s.get("summary", ""),
            "also_covered_by": [x for x in (s.get("also_covered_by") or []) if isinstance(x, str)],
        })
    return kept, dropped


# ── one group ───────────────────────────────────────────────────────────────
def describe(o):
    return f"{o['name']} ({o['domain']}" + (f", {o['note']}" if o.get("note") else "") + ")"


def build_user_message(group, outlets, feed_items, to_search, blocked_names, concise=False):
    lines = [f"Group: {group['name']} - {group['description']}",
             "Allowed outlets: " + ", ".join(describe(o) for o in outlets),
             "", "Feed items already collected (last 48h):"]
    lines += [f"[{i['outlet']}] {i['title']} - {i['snippet']} (URL: {i['url']})" for i in feed_items] or ["(none)"]
    lines += ["", "Outlets with no feed items, so search for them: " + (", ".join(to_search) or "(none)")]
    if blocked_names:
        lines += ["Outlets that block web search (use their feed items only, if any): " + ", ".join(blocked_names)]
    if concise:
        lines += ["", "Your previous answer was cut off. Be much shorter: at most 5 stories, one short sentence per summary, omit also_covered_by."]
    return "\n".join(lines)


def keyword_pattern(keywords):
    if not keywords:
        return None
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b", re.I)


def run_group(registry, group, api_key):
    own = resolve_outlets(registry, group["outlets"])
    extra = resolve_outlets(registry, group.get("extra_outlets", []))
    outlets = own + extra
    all_domains = sorted({o["domain"] for o in outlets})

    raw_items, feed_report = collect_feed_items(outlets)
    seen_feeds = {i["outlet"] for i in raw_items}      # outlets whose feed returned something
    own_names = {o["name"] for o in own}
    pattern = keyword_pattern(group.get("keywords"))
    # An outlet that belongs to the group keeps all its items. The big general outlets
    # (extras) only contribute items that match the group's topic keywords.
    feed_items = [i for i in raw_items
                  if i["outlet"] in own_names or pattern is None or pattern.search(f"{i['title']} {i['snippet']}")]

    blocks, concise = None, False
    for _ in range(5):
        search_domains = [d for d in all_domains if d not in BLOCKED]
        blocked_names = [o["name"] for o in outlets if o["domain"] in BLOCKED]
        to_search = [o["name"] for o in own if o["name"] not in seen_feeds and o["domain"] not in BLOCKED]
        msg = build_user_message(group, outlets, feed_items, to_search, blocked_names, concise)
        try:
            blocks, searches = call_claude(api_key, msg, search_domains, max_tokens=14000 if concise else MAX_TOKENS)
            break
        except BlockedDomains as b:
            new = [d for d in b.domains if d in all_domains and d not in BLOCKED]
            if not new:
                raise RuntimeError(f"Search rejected domains we couldn't match: {b.domains}")
            BLOCKED.update(new)
            print(f"  search can't access {', '.join(new)}; using their feeds only")
        except TruncatedResponse:
            if concise:
                raise RuntimeError("Response cut off even in short mode")
            concise = True
            print("  response was cut off; retrying in short mode")
    if blocks is None:
        raise RuntimeError("Could not find a searchable set of outlets")

    seen_urls, search_errors = collect_urls(blocks)
    seen_urls |= {i["url"] for i in feed_items}
    parsed = extract_json(blocks_text(blocks))
    stories, dropped = filter_stories(parsed.get("stories", []), seen_urls, all_domains)
    not_visible = [o["name"] for o in outlets if o["domain"] in BLOCKED and o["name"] not in seen_feeds]

    print(f"  feeds: {feed_report or 'none configured'}")
    print(f"  feed items kept after topic filter: {len(feed_items)} of {len(raw_items)}")
    print(f"  searches used: {searches}; stories kept: {len(stories)}, dropped (untraceable link): {dropped}")
    if not_visible:
        print(f"  not visible (blocked from search, no feed items): {', '.join(not_visible)}")
    if search_errors:
        print(f"  search errors: {sorted(set(search_errors))}")
    if not stories:
        raise RuntimeError("No verifiable stories returned")

    return {
        "description": group["description"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": parsed.get("overview", ""),
        "stories": stories,
        "gaps": parsed.get("gaps", ""),
        "outlets": [o["name"] for o in own],
        "not_visible": not_visible,
        "searches_used": searches,
    }


# ── publish ─────────────────────────────────────────────────────────────────
def upload_to_github(filename, content, token):
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{filename}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    existing = requests.get(url, headers=headers, timeout=30)
    data = {"message": f"Update {filename}",
            "content": base64.b64encode(content.encode("utf-8")).decode("utf-8")}
    if existing.status_code == 200:
        data["sha"] = existing.json()["sha"]
    return requests.put(url, headers=headers, json=data, timeout=60).status_code in (200, 201)


def load_existing():
    try:
        r = requests.get(RAW_URL, timeout=15)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def main(argv):
    dry_run = "--dry-run" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    if not wanted and os.environ.get("RADAR_GROUPS", "").strip():
        wanted = [g.strip() for g in os.environ["RADAR_GROUPS"].split(",") if g.strip()]

    registry = load_registry()
    groups = [g for g in registry["groups"] if (g["name"] in wanted if wanted else g.get("active"))]
    if wanted and len(groups) != len(wanted):
        known = {g["name"] for g in registry["groups"]}
        print("Unknown group name(s):", [w for w in wanted if w not in known], "- known:", sorted(known))
        sys.exit(2)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not api_key:
        print("No ANTHROPIC_API_KEY set.")
        sys.exit(2)

    output = load_existing() or {}
    output.setdefault("groups", {})
    succeeded = 0
    for g in groups:
        print(f"\n📡 {g['name']}")
        try:
            output["groups"][g["name"]] = run_group(registry, g, api_key)
            succeeded += 1
            print("  ✓ done")
        except Exception as e:
            print(f"  ✗ failed: {e}  (keeping previous data for this group, if any)")
    output["generated_at"] = datetime.now(timezone.utc).isoformat()

    if succeeded == 0:
        print("\nNo group succeeded; nothing published.")
        sys.exit(1)

    payload = json.dumps(output, ensure_ascii=False, indent=2)
    if dry_run or not token:
        with open(RADAR_FILE, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"\nWrote {RADAR_FILE} locally ({'dry run' if dry_run else 'no GITHUB_TOKEN'}).")
    else:
        ok = upload_to_github(RADAR_FILE, payload, token)
        print("\n✅ radar.json uploaded" if ok else "\n⚠️ Upload to GitHub failed")
        if not ok:
            sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
