"""Best-effort public RSS context; social signals never independently trigger BUY."""
import datetime as dt
import email.utils
import html
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UTC = dt.timezone.utc
NEWS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml", "news"),
    ("Cointelegraph", "https://cointelegraph.com/rss", "news"),
]
OFFICIAL = [
    ("Ethereum Foundation", "https://blog.ethereum.org/feed.xml", "official"),
]
# Google News may include indexed Reddit posts; not a live or exhaustive Reddit feed.
REDDIT_INDEX = [
    ("Reddit indexed posts", "https://news.google.com/rss/search?" +
     urllib.parse.urlencode({"q": "site:reddit.com/r/CryptoCurrency (Bitcoin OR Ethereum OR Solana OR XRP) when:1d",
                             "hl":"en-US","gl":"US","ceid":"US:en"}), "reddit-index")
]
TERMS = {
    "BTC-USD": ("bitcoin","btc"),
    "ETH-USD": ("ethereum","ether","eth"),
    "SOL-USD": ("solana","sol"),
    "XRP-USD": ("ripple","xrp"),
}
# Exclude generic short tickers unless bounded; avoid unrelated matches.
STOP = {"sol", "eth", "btc"}
POSITIVE = ("approval","approved","adoption","partnership","upgrade","launch","inflows","integrates")
NEGATIVE = ("hack","exploit","outage","lawsuit","ban","security breach","liquidation","investigation")
UA = {"User-Agent":"CryptoSwingAlertsNewsMonitor/1.0 contact: github.com/kdq2y9m8mp-arch/crypto-alert-bot"}

def _date(text):
    if not text:
        return None
    try:
        return email.utils.parsedate_to_datetime(text).astimezone(UTC)
    except (TypeError, ValueError):
        try:
            return dt.datetime.fromisoformat(text.replace("Z","+00:00")).astimezone(UTC)
        except (ValueError, TypeError):
            return None

def _content(item, name):
    v = item.findtext(name) or ""
    return re.sub(r"\s+", " ", html.unescape(re.sub("<[^>]+>", " ", v))).strip()

def _parse(payload, source, category, now):
    root = ET.fromstring(payload)
    collected = []
    rss = root.findall(".//item")
    atoms = [] if rss else root.findall("{http://www.w3.org/2005/Atom}entry")
    for entry in (rss or atoms)[:40]:
        if rss:
            title = _content(entry,"title")
            link = _content(entry,"link")
            published = _date(_content(entry,"pubDate") or _content(entry,"{http://purl.org/dc/elements/1.1/}date"))
        else:
            title = _content(entry,"{http://www.w3.org/2005/Atom}title")
            linknode = entry.find("{http://www.w3.org/2005/Atom}link")
            link = linknode.attrib.get("href","") if linknode is not None else ""
            published = _date(_content(entry,"{http://www.w3.org/2005/Atom}published") or
                              _content(entry,"{http://www.w3.org/2005/Atom}updated"))
        if not published or published > now + dt.timedelta(minutes=5):
            continue
        age = (now-published).total_seconds()
        if age < 0 or age > 6*3600:
            continue
        if not link.startswith("https://"):
            continue
        collected.append({"source":source, "category":category, "title":title[:170],
                          "url":link, "published":published, "age_seconds":age})
    return collected

def collect(now=None):
    now = now or dt.datetime.now(UTC)
    items, errors = [], []
    for name, url, category in NEWS+OFFICIAL+REDDIT_INDEX:
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=9) as response:
                payload = response.read(600000)
            items.extend(_parse(payload,name,category,now))
        except Exception as exc:
            errors.append(name+": "+type(exc).__name__)
    return items, errors

def _match(pair, text):
    for term in TERMS[pair]:
        if re.search(r"(?<![a-z0-9])"+re.escape(term)+r"(?![a-z0-9])", text.lower()):
            return True
    return False

def context(pair, items):
    relevant = [x for x in items if _match(pair,x["title"])]
    relevant.sort(key=lambda x:x["published"],reverse=True)
    news = [x for x in relevant if x["category"]=="news"]
    official = [x for x in relevant if x["category"]=="official"]
    reddit = [x for x in relevant if x["category"]=="reddit-index"]
    return {"news":news[:2],"official":official[:2],"reddit":reddit[:2]}

def describe(pair, items, errors):
    sources = context(pair,items)
    lines = []
    for label,key in (("Official","official"),("News","news"),("Reddit indexed","reddit")):
        for item in sources[key]:
            lines.append(f'{label}: {item["title"]} [{item["source"]}] {item["url"]}')
    if not lines:
        lines.append("No matching recent public headlines; sentiment is UNKNOWN.")
    if errors:
        lines.append("Unavailable feeds: "+", ".join(errors))
    lines.append("Headlines and Reddit-indexed posts are unverified context, NOT independent buy signals.")
    return "\n".join(lines)

def official_breaking(items, now):
    # Only official ETH announcements, no inference about sentiment or buying.
    return [x for x in items if x["category"]=="official"
            and x["age_seconds"] <= 5*60
            and _match("ETH-USD", x["title"])]
