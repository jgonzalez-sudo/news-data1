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
- Write plain text only in every field: no citation tags (such as <cite>), no markup.
- Rank by newsworthiness for a global news audience: human stakes, major economic or policy moves, cross-border significance, and stories several outlets are converging on. Skip routine items, opinion columns, listicles and lifestyle.
- Many outlets are paywalled, so you will usually see only headlines and snippets. Write each summary in your own words (one sentence, two at most), based only on what you actually saw. If you only saw a headline, keep the summary modest. Never quote more than a few words.
- Every url must be copied exactly from a search result or feed item. Never invent or guess URLs, headlines or details.
- If several outlets cover the same story, list it once and name the others in also_covered_by (only outlets where you actually saw it).
- Give 5-8 stories, most important first.
- Give every story a type: exactly one of Politics, Geopolitics, Security, Economy, Business, Energy, Climate, Health, Tech, Society, Culture, Media, Law, whichever best describes what the story is about.

Respond with ONLY valid JSON, no preamble and no markdown:
{"overview": "2-3 sentences on what these outlets are collectively focused on", "stories": [{"type": "...", "headline": "...", "outlet": "...", "url": "...", "summary": "...", "also_covered_by": ["..."]}], "gaps": "outlets you could find nothing recent from, or an empty string"}"""


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


# ── cleaning ────────────────────────────────────────────────────────────────
# With web search on, the model sometimes leaves <cite index="..."> markers inside its text.
CITE_TAG = re.compile(r"</?(?:antml:)?cite\b[^>]*>", re.I)


def clean_text_fields(obj):
    """Recursively remove citation tags from every string in the model's JSON."""
    if isinstance(obj, dict):
        return {k: clean_text_fields(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_text_fields(v) for v in obj]
    if isinstance(obj, str):
        return CITE_TAG.sub("", obj).strip()
    return obj


# ── story types and balance ─────────────────────────────────────────────────
STORY_TYPES = ["Politics", "Geopolitics", "Security", "Economy", "Business", "Energy", "Climate", "Health",
               "Tech", "Society", "Culture", "Media", "Law"]
_TYPE_LOOKUP = {t.lower(): t for t in STORY_TYPES}
_TYPE_HINTS = [  # used only when the model gives a missing or unknown type
    ("Health", r"\b(ebola|outbreak|vaccines?|virus|disease|cholera|malaria|mpox|health|hospitals?|epidemic)\b"),
    ("Security", r"\b(attacks?|militants?|terror\w*|insurgen\w*|troops|army|military|gunmen|coup|missiles?|drones?)\b"),
    ("Geopolitics", r"\b(sanctions|summit|treaty|envoy|diplomat\w*|nato|alliance|ceasefire|war)\b"),
    ("Energy", r"\b(oil|gas|lng|electricity|grid|solar|coal|refiner\w*|pipeline)\b"),
    ("Business", r"\b(deal|acquisition|merger|ipo|compan\w*|shares|stock|earnings|profit|investment|startups?|funding|billion)\b"),
    ("Economy", r"\b(inflation|gdp|central bank|interest rates?|currency|debt|imf|world bank|budget|trade|tariffs?|economy|economic|growth|prices|exports?|imports?)\b"),
    ("Tech", r"\b(ai|artificial intelligence|chips?|software|cyber\w*|data centers?)\b"),
    ("Politics", r"\b(elections?|vote|president|parliament|minister|government|opposition|protests?)\b"),
]
BUSINESS_TERMS = re.compile(
    r"\b(billion|million|bn|deal|investment|invest|investors?|loan|debt|imf|world bank|bond|budget|gdp|"
    r"inflation|central bank|interest rate|currency|exchange rate|dollar|oil|gas|lng|pipeline|refinery|mining|mine|"
    r"lithium|cobalt|copper|gold|port|railway|infrastructure|export|import|trade|tariff|revenue|profit|earnings|"
    r"ipo|stock|bank|fintech|startup|funding|raises|acquisition|merger|tender|contract|power|solar|energy|"
    r"project|economy|economic|growth|fund)\b", re.I)
MONEY_PATTERN = re.compile(r"[$€£]\s?\d|\b\d[\d.,]*\s?(?:bn|billion|million|trillion)\b", re.I)


def business_signal(text):
    hits = len({m.lower() for m in BUSINESS_TERMS.findall(text)})
    return hits + (2 if MONEY_PATTERN.search(text) else 0)


def infer_type(text):
    for label, pattern in _TYPE_HINTS:
        if re.search(pattern, text, re.I):
            return label
    return ""


def normalize_type(value, fallback_text=""):
    """The model's type if it is one of the allowed labels, otherwise a best guess (or blank)."""
    t = _TYPE_LOOKUP.get(str(value or "").strip().lower())
    return t or infer_type(fallback_text)


def is_business_type(t):
    return t in ("Business", "Economy")


def collect_search_titles(blocks):
    """url -> title for every web search result, so headlines can be reused."""
    titles = {}
    for b in blocks:
        if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            for r in b["content"]:
                if r.get("url") and r.get("title"):
                    titles[r["url"]] = r["title"]
    return titles


def outlet_for_url(url, domain_names):
    host = (urlparse(url).hostname or "").lower()
    for d, name in domain_names.items():
        if host == d or host.endswith("." + d):
            return name
    return host


def ensure_business_balance(stories, pool, minimum, max_total=8):
    """Make sure a regional beat shows at least `minimum` business or economy stories (when the
    day's feed items and search results contain any), so it is not only politics and security.
    Adds the strongest unused business headlines at the end of the list."""
    have = sum(1 for s in stories if is_business_type(s.get("type")))
    if minimum <= 0 or have >= minimum:
        return stories, 0
    used = {normalize_url(s["url"]) for s in stories}
    cands = [(business_signal(f"{c['headline']} {c['summary']}"), c) for c in pool if normalize_url(c["url"]) not in used]
    cands = sorted([x for x in cands if x[0] >= 2], key=lambda x: -x[0])
    added = 0
    for _, c in cands:
        if have + added >= minimum:
            break
        new = {"type": "Business", "headline": c["headline"], "outlet": c["outlet"], "url": c["url"],
               "summary": c["summary"], "also_covered_by": []}
        if len(stories) < max_total:
            stories.append(new)
        else:
            idx = next((i for i in range(len(stories) - 1, -1, -1) if not is_business_type(stories[i].get("type"))), None)
            if idx is None:
                break
            stories[idx] = new
        added += 1
    return stories, added


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
            "type": normalize_type(s.get("type"), f"{s['headline']} {s.get('summary', '')}"),
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
    bal = group.get("balance")
    if bal:
        lines += ["", f"Balance for this beat: include at least {bal.get('min_business', 2)} business or economy stories "
                      "(deals, investment, markets, trade, central banks, corporate news) whenever the candidates include any, "
                      f"and no more than {bal.get('max_same', 3)} stories of the same type, so the beat is not only politics and security. "
                      "Keep the most newsworthy first."]
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

    search_urls, search_errors = collect_urls(blocks)
    feed_urls = {i["url"] for i in feed_items}
    parsed = clean_text_fields(extract_json(blocks_text(blocks)))
    stories, dropped = filter_stories(parsed.get("stories", []), search_urls | feed_urls, all_domains)

    bal = group.get("balance") or {}
    domain_names = {o["domain"]: o["name"] for o in registry["outlets"].values()}
    pool = [{"headline": i["title"], "outlet": i["outlet"], "url": i["url"], "summary": i["snippet"]} for i in feed_items]
    pool += [{"headline": t, "outlet": outlet_for_url(u, domain_names), "url": u, "summary": ""}
             for u, t in collect_search_titles(blocks).items() if host_allowed(u, all_domains)]
    stories, balance_added = ensure_business_balance(stories, pool, bal.get("min_business", 0))
    n_biz = sum(1 for st in stories if is_business_type(st.get("type")))

    # Where did each published story come from: an RSS feed item, a web search result, or both?
    feed_keys = {normalize_url(u) for u in feed_urls}
    search_keys = {normalize_url(u) for u in search_urls}
    mix = {"feed": 0, "search": 0, "both": 0}
    for st in stories:
        k = normalize_url(st["url"])
        in_feed, in_search = k in feed_keys, k in search_keys
        st["via"] = "both" if (in_feed and in_search) else ("feed" if in_feed else "search")
        mix[st["via"]] += 1
    not_visible = [o["name"] for o in outlets if o["domain"] in BLOCKED and o["name"] not in seen_feeds]

    print(f"  feeds: {feed_report or 'none configured'}")
    print(f"  feed items kept after topic filter: {len(feed_items)} of {len(raw_items)}")
    print(f"  searches used: {searches}; stories kept: {len(stories)}, dropped (untraceable link): {dropped}")
    if bal:
        print(f"  business/economy stories: {n_biz} of {len(stories)}"
              + (f" (added {balance_added} to reach the minimum of {bal.get('min_business', 2)})" if balance_added else ""))
    print(f"  stories by origin: {mix['feed']} from feeds, {mix['search']} from web search, {mix['both']} found in both")
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
        "sources_mix": mix,
        "balance_added": balance_added,
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


# ── Newsletter picks ────────────────────────────────────────────────────────
NEWSLETTERS_FILE = "newsletters.json"
PICKS_MAX = 7

PICKS_SYSTEM = """You help Semafor editors choose which stories to consider for one specific newsletter. You get the newsletter's editorial guide and a numbered list of candidate stories that other outlets ran in the last day or two.
Choose the 5-7 best fits, ranked, and say in one short sentence (under 25 words) why each fits THIS newsletter's habits.
Rules:
- Let the editorial guide decide. Prefer stories where several outlets converge, or that a top-tier source ran, but only when they fit the guide.
- Do not pick the same story twice. If fewer than 5 genuinely fit, return fewer.
- Mix story types where the guide allows: no more than 3 picks of the same type, and include at least one business or economy pick if any fit.
- Use only the numbers shown in the list. Never invent a story.
- Give an intro: one sentence on what today's candidates suggest for this newsletter.
Respond with ONLY valid JSON, no preamble: {"intro": "...", "picks": [{"id": 12, "why": "..."}]}"""


def load_newsletters(path=NEWSLETTERS_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("newsletters", {})
    except FileNotFoundError:
        return {}


def ask_claude(api_key, system, user, max_tokens=3000):
    """Plain Claude call (no web search) that returns the text of the answer."""
    last_err = None
    for _ in range(2):
        try:
            r = requests.post(
                API_URL,
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": MODEL, "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]},
                timeout=180,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"API {r.status_code}: {r.text[:300]}")
            data = r.json()
            if data.get("stop_reason") == "max_tokens":
                raise RuntimeError("Response cut off (hit max_tokens)")
            return blocks_text(data.get("content", []))
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_err = e
    raise last_err


def newsletter_candidates(output_groups, group_names):
    """Stories from the beats a newsletter draws on, de-duplicated by link and numbered."""
    seen, cands = set(), []
    for gname in group_names:
        for st in (output_groups.get(gname) or {}).get("stories", []):
            key = normalize_url(st["url"])
            if key in seen:
                continue
            seen.add(key)
            cands.append({"id": len(cands) + 1, "group": gname, "headline": st["headline"], "url": st["url"],
                          "outlet": st.get("outlet", ""), "summary": st.get("summary", ""), "type": st.get("type", ""),
                          "also_covered_by": st.get("also_covered_by", [])})
    return cands


def build_newsletter_picks(name, cfg, output_groups, api_key):
    cands = newsletter_candidates(output_groups, cfg.get("groups", []))
    if len(cands) < 3:
        raise RuntimeError(f"only {len(cands)} candidate stories available")
    lines = []
    for c in cands:
        also = f" (also: {', '.join(c['also_covered_by'][:3])})" if c["also_covered_by"] else ""
        kind = f" / {c['type']}" if c["type"] else ""
        lines.append(f"[{c['id']}] ({c['group']}{kind}) {c['outlet']}: {c['headline']} - {c['summary'][:160]}{also}")
    user = (f"Newsletter: {name} ({cfg.get('cadence', '')})\n\nEditorial guide:\n{cfg['guide']}\n\n"
            "Candidate stories:\n" + "\n".join(lines))
    parsed = clean_text_fields(extract_json(ask_claude(api_key, PICKS_SYSTEM, user)))
    by_id = {c["id"]: c for c in cands}
    picks, used = [], set()
    for p in parsed.get("picks", []):
        try:
            i = int(p.get("id"))
        except (TypeError, ValueError):
            continue
        c = by_id.get(i)
        if c is None or i in used:
            continue  # ignore numbers that weren't in the list, and repeats
        used.add(i)
        # the link comes from our own list, never from the model
        picks.append({"headline": c["headline"], "url": c["url"], "outlet": c["outlet"], "type": c["type"],
                      "group": c["group"], "why": str(p.get("why", "")).strip()})
        if len(picks) >= PICKS_MAX:
            break
    if not picks:
        raise RuntimeError("no valid picks returned")
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "intro": str(parsed.get("intro", "")).strip(),
            "picks": picks, "confidence": cfg.get("confidence", "draft"), "candidates": len(cands)}


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
    overall = {"feed": 0, "search": 0, "both": 0}
    for g in groups:
        print(f"\n📡 {g['name']}")
        try:
            result = run_group(registry, g, api_key)
            output["groups"][g["name"]] = result
            for k, v in result.get("sources_mix", {}).items():
                overall[k] += v
            succeeded += 1
            print("  ✓ done")
        except Exception as e:
            print(f"  ✗ failed: {e}  (keeping previous data for this group, if any)")
    output["generated_at"] = datetime.now(timezone.utc).isoformat()
    total = sum(overall.values())
    if total:
        pct = lambda n: round(100 * n / total)
        print(f"\nOVERALL: {total} stories published this run: {pct(overall['feed'])}% from RSS feeds, "
              f"{pct(overall['search'])}% from web search, {pct(overall['both'])}% found by both")

    if succeeded == 0:
        print("\nNo group succeeded; nothing published.")
        sys.exit(1)

    newsletters = load_newsletters()
    if newsletters:
        print("\n📰 Newsletter picks")
        output.setdefault("newsletters", {})
        for nl_name, cfg in newsletters.items():
            if cfg.get("active", True) is False:
                continue
            try:
                output["newsletters"][nl_name] = build_newsletter_picks(nl_name, cfg, output["groups"], api_key)
                print(f"  ✓ {nl_name}: {len(output['newsletters'][nl_name]['picks'])} picks")
            except Exception as e:
                print(f"  ✗ {nl_name} failed: {e}  (keeping previous picks, if any)")

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
