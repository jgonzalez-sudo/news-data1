import subprocess
import sys

def _ensure_packages():
    """Install any missing packages automatically. This means this single
    cell is now self-sufficient — no separate '!pip install' cell to
    remember to run first, which was the actual recurring problem tonight
    (Colab wipes installed packages every time a runtime fully resets)."""
    required = {'feedparser': 'feedparser', 'deep_translator': 'deep-translator', 'requests': 'requests'}
    for module_name, pip_name in required.items():
        try:
            __import__(module_name)
        except ImportError:
            print(f"Installing missing package: {pip_name}...")
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-q', pip_name])

_ensure_packages()

import feedparser
import json
from datetime import datetime, timedelta
import time
import requests
import base64
from deep_translator import GoogleTranslator
import re
import os
import socket

# ── GitHub credentials ──────────────────────────────────────────────────────
# SECURITY: don't hardcode the token. In Colab: click the key icon in the left
# sidebar -> Secrets -> add GITHUB_TOKEN -> toggle "Notebook access" on.
try:
    from google.colab import userdata
    GITHUB_TOKEN = userdata.get('GITHUB_TOKEN')
except Exception:
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')

try:
    from google.colab import userdata
    ANTHROPIC_API_KEY = userdata.get('ANTHROPIC_API_KEY')
except Exception:
    ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

try:
    from google.colab import userdata
    SLACK_WEBHOOK_URL = userdata.get('SLACK_WEBHOOK_URL')
except Exception:
    SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')

try:
    from google.colab import userdata
    REUTERS_CLIENT_ID = userdata.get('REUTERS_CLIENT_ID')
except Exception:
    REUTERS_CLIENT_ID = os.environ.get('REUTERS_CLIENT_ID', '')

try:
    from google.colab import userdata
    REUTERS_CLIENT_SECRET = userdata.get('REUTERS_CLIENT_SECRET')
except Exception:
    REUTERS_CLIENT_SECRET = os.environ.get('REUTERS_CLIENT_SECRET', '')

GITHUB_USERNAME = "jgonzalez-sudo"
GITHUB_REPO = "news-data1"

if not GITHUB_TOKEN:
    raise RuntimeError(
        "No GitHub token found. Add a Colab secret named GITHUB_TOKEN "
        "(key icon in the left sidebar) instead of pasting it into this script."
    )

# International / pan-regional feeds (checked against every country's keywords)
INTERNATIONAL_FEEDS = {
    'BBC': 'https://feeds.bbci.co.uk/news/world/rss.xml',
    'UPI': 'https://rss.upi.com/news/tn_int.rss',
    'Al Jazeera': 'https://www.aljazeera.com/xml/rss/all.xml',
    'Deutsche Welle': 'https://rss.dw.com/xml/rss-en-all',
    'France24': 'https://www.france24.com/en/rss',
    'The Guardian': 'https://www.theguardian.com/world/rss',
    'Xinhua': 'http://www.xinhuanet.com/english/rss/worldrss.xml',
    'Jeune Afrique': 'https://www.jeuneafrique.com/feed/',
    'The Africa Report': 'https://www.theafricareport.com/feed/',
    'Africanews': 'http://www.africanews.com/feed/rss',
    'AllAfrica': 'https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf',
    'How We Made It In Africa': 'https://www.howwemadeitinafrica.com/feed/',
}

# CURATED LatAm feeds - tested and reliable
latam_feeds = {
    'Argentina': {
        'Clarín': 'https://www.clarin.com/rss/lo-ultimo/',
        'La Nación': 'https://www.lanacion.com.ar/arc/outboundfeeds/rss/',
        'Infobae': 'https://www.infobae.com/feeds/rss/',
        'Ámbito': 'https://www.ambito.com/rss/home.xml',
        'Perfil': 'https://www.perfil.com/feed',
        'Página/12': 'https://www.pagina12.com.ar/rss/portada',
    },
    'Brazil': {
        'Folha': 'https://feeds.folha.uol.com.br/emcimadahora/rss091.xml',
        'O Globo': 'https://oglobo.globo.com/rss.xml',
        'UOL': 'https://rss.uol.com.br/feed/noticias.xml',
        'G1': 'https://g1.globo.com/rss/g1/',
        'Exame': 'https://exame.com/feed/',
        'Metrópoles': 'https://www.metropoles.com/feed',
    },
    'Mexico': {
        'La Jornada': 'https://www.jornada.com.mx/rss/edicion.xml',
        'Animal Político': 'https://www.animalpolitico.com/feed/',
        'Sin Embargo': 'https://www.sinembargo.mx/feed/',
        'Aristegui': 'https://aristeguinoticias.com/feed/',
        'Proceso': 'https://www.proceso.com.mx/rss',
        'Milenio': 'https://www.milenio.com/rss',
    },
    'Colombia': {
        'El Tiempo': 'https://www.eltiempo.com/rss.xml',
        'Semana': 'https://www.semana.com/feeds/articulos.xml',
        'El Espectador': 'https://www.elespectador.com/rss',
        'Portafolio': 'https://www.portafolio.co/rss',
        'Pulzo': 'https://www.pulzo.com/rss',
    },
    'Chile': {
        'BioBioChile': 'https://www.biobiochile.cl/lista/rss',
        'El Mostrador': 'https://www.elmostrador.cl/noticias/feed/',
        'La Tercera': 'https://www.latercera.com/feed/',
        'CIPER': 'https://www.ciperchile.cl/feed/',
        'El Dínamo': 'https://www.eldinamo.cl/feed/',
    },
    'Peru': {
        'RPP': 'https://rpp.pe/feed',
        'La República': 'https://larepublica.pe/rss',
        'Perú21': 'https://peru21.pe/rss/',
        'Gestión': 'https://gestion.pe/rss',
        'Trome': 'https://trome.pe/rss/',
    },
    'Venezuela': {
        'Efecto Cocuyo': 'https://efectococuyo.com/feed/',
        'Runrunes': 'https://runrun.es/feed/',
        'El Pitazo': 'https://elpitazo.net/feed/',
        'Tal Cual': 'https://talcualdigital.com/feed/',
        'El Nacional': 'https://www.elnacional.com/feed/',
    },
    'Ecuador': {
        'Primicias': 'https://www.primicias.ec/feed/',
        'El Comercio': 'https://www.elcomercio.com/feed/',
        'El Universo': 'https://www.eluniverso.com/feed/',
        'Plan V': 'https://www.planv.com.ec/feed',
        'Extra': 'https://www.extra.ec/feed/',
    },
    'Bolivia': {
        'Página Siete': 'https://www.paginasiete.bo/rss.xml',
        'El Deber': 'https://eldeber.com.bo/rss.xml',
        'Erbol': 'https://erbol.com.bo/rss.xml',
        'Los Tiempos': 'https://www.lostiempos.com/rss.xml',
    },
    'Uruguay': {
        'La Diaria': 'https://ladiaria.com.uy/rss',
        'El Observador': 'https://www.elobservador.com.uy/rss',
        'El País': 'https://www.elpais.com.uy/rss',
        'Búsqueda': 'https://www.busqueda.com.uy/rss',
    },
    'Paraguay': {
        'Última Hora': 'https://www.ultimahora.com/rss',
        'ABC Color': 'https://www.abc.com.py/rss',
        'La Nación': 'https://www.lanacion.com.py/feed/',
        '5 Días': 'https://www.5dias.com.py/feed/',
    },
    'Costa Rica': {
        'CRHoy': 'https://www.crhoy.com/rss/',
        'La República': 'https://www.larepublica.net/feed/',
        'Semanario U': 'https://semanariouniversidad.com/feed/',
    },
    'Panama': {
        'La Prensa': 'https://www.prensa.com/rss/',
        'La Estrella': 'https://www.laestrella.com.pa/rss',
        'El Siglo': 'https://elsiglo.com.pa/feed/',
    },
    # NEW
    'Guatemala': {
        'Prensa Libre': 'https://www.prensalibre.com/feed',
        'Soy502': 'https://www.soy502.com/feed',
    },
    'Honduras': {
        'La Prensa': 'https://www.laprensa.hn/arc/outboundfeeds/rss/',
        'El Heraldo': 'https://www.elheraldo.hn/rss.xml',
        'Proceso Digital': 'https://proceso.hn/feed/',
    },
    'El Salvador': {
        'El Faro': 'https://elfaro.net/es/rss',
        'La Prensa Gráfica': 'https://www.laprensagrafica.com/rss',
        'El Diario de Hoy': 'https://www.eldiariodehoy.com/rss',
    },
    'Dominican Republic': {
        'Diario Libre': 'https://www.diariolibre.com/rss',
        'Listín Diario': 'https://listindiario.com/rss',
        'El Nacional': 'https://elnacional.com.do/feed/',
    },
    'Nicaragua': {
        'La Prensa': 'https://www.laprensani.com/feed/',
        'Confidencial': 'https://confidencial.digital/feed/',
    },
}

# CURATED Africa feeds - tested and reliable
africa_feeds = {
    'Nigeria': {
        'The Guardian': 'https://guardian.ng/feed/',
        'Premium Times': 'https://www.premiumtimesng.com/feed',
        'Vanguard': 'https://www.vanguardngr.com/feed/',
        'The Punch': 'https://punchng.com/feed/',
        'Daily Trust': 'https://dailytrust.com/feed',
        'The Cable': 'https://www.thecable.ng/feed',
        'Sahara Reporters': 'https://saharareporters.com/feeds/latest/feed',
    },
    'South Africa': {
        'News24': 'https://feeds.news24.com/articles/news24/topstories/rss',
        'Daily Maverick': 'https://www.dailymaverick.co.za/dmrss/',
        'The Citizen': 'https://www.citizen.co.za/feed/',
        'Mail & Guardian': 'https://mg.co.za/feed/',
        'IOL': 'https://www.iol.co.za/rss',
        'BusinessTech': 'https://businesstech.co.za/news/feed/',
    },
    'Kenya': {
        'Capital FM': 'https://www.capitalfm.co.ke/news/feed/',
        'The Star': 'https://www.the-star.co.ke/feed/',
        'Citizen Digital': 'https://www.citizen.digital/feed',
        'Business Daily': 'https://www.businessdailyafrica.com/bd/rss',
        'Tuko': 'https://www.tuko.co.ke/rss/',
    },
    'Ghana': {
        'Citinewsroom': 'https://citinewsroom.com/feed/',
        'MyJoyOnline': 'https://www.myjoyonline.com/feed/',
        'Graphic Online': 'https://www.graphic.com.gh/feed',
        'Business Ghana': 'https://www.businessghana.com/site/feed',
        'Peace FM': 'https://www.peacefmonline.com/rss/news.xml',
    },
    'Ethiopia': {
        'Addis Standard': 'https://addisstandard.com/feed/',
        'The Reporter': 'https://www.thereporterethiopia.com/feed/',
        'Borkena': 'https://borkena.com/feed/',
        'Fana BC': 'https://www.fanabc.com/english/feed/',
    },
    'Tanzania': {
        'The Citizen': 'https://www.thecitizen.co.tz/tanzania/feed',
        'Mtanzania': 'https://mtanzania.co.tz/feed/',
        'Daily News': 'https://www.dailynews.co.tz/rss',
    },
    'Uganda': {
        'Observer': 'https://www.observer.ug/feed',
        'Independent': 'https://www.independent.co.ug/feed/',
        'Nile Post': 'https://nilepost.co.ug/feed/',
        'Watchdog': 'https://www.watchdoguganda.com/feed/',
    },
    'Egypt': {
        'Egypt Today': 'https://www.egypttoday.com/Rss',
        'Daily News Egypt': 'https://dailynewsegypt.com/feed/',
        'Egyptian Streets': 'https://egyptianstreets.com/feed/',
        'Mada Masr': 'https://www.madamasr.com/en/feed/',
    },
    'Morocco': {
        'Morocco World News': 'https://www.moroccoworldnews.com/feed/',
        'Hespress': 'https://www.hespress.com/feed',
        'Tel Quel': 'https://telquel.ma/feed',
    },
    # NEW
    'Senegal': {
        'Le Soleil': 'https://lesoleil.sn/feed/',
        'Seneweb': 'https://www.seneweb.com/news/rss.xml',
        'Walf Quotidien': 'https://www.walf-groupe.com/feed/',
    },
    'Rwanda': {
        'The New Times': 'https://www.newtimes.co.rw/rss.xml',
        'KT Press': 'https://www.ktpress.rw/feed/',
    },
    'Zimbabwe': {
        'The Herald': 'https://www.herald.co.zw/feed/',
        'NewsDay': 'https://www.newsday.co.zw/feed',
        'The Standard': 'https://www.thestandard.co.zw/feed/',
        'ZimLive': 'https://www.zimlive.com/feed/',
    },
    'Algeria': {
        'El Watan': 'https://www.elwatan.com/feed',
        'TSA': 'https://www.tsa-algerie.com/feed/',
        'Liberté': 'https://www.liberte-algerie.com/rss.xml',
    },
    'Ivory Coast': {
        'Fraternité Matin': 'https://www.fratmat.info/rss',
        'Abidjan.net': 'https://news.abidjan.net/rss/rss.asp',
    },
    'Tunisia': {
        'La Presse': 'https://lapresse.tn/feed/',
        'Tunisie Numérique': 'https://www.tunisienumerique.com/feed/',
        'Business News': 'https://www.businessnews.com.tn/rss.xml',
    },
    # NEW — countries previously missing entirely, so they never got picked up
    # even from BBC/Al Jazeera/Reuters/etc. Local feed URLs here are less
    # battle-tested than the rest of the list — check the ✓/✗ output closely.
    'DR Congo': {
        'Radio Okapi': 'https://www.radiookapi.net/feed',
        'Actualite.cd': 'https://actualite.cd/feed',
    },
    'Angola': {},
    'Sudan': {
        'Sudan Tribune': 'https://sudantribune.com/feed/',
    },
    'South Sudan': {},
    'Somalia': {
        'Hiiraan Online': 'https://www.hiiraan.com/rss.aspx',
    },
    'Libya': {
        'The Libya Observer': 'https://www.libyaobserver.ly/rss.xml',
    },
    'Zambia': {
        'Lusaka Times': 'https://www.lusakatimes.com/feed/',
    },
    'Mozambique': {
        'Club of Mozambique': 'https://clubofmozambique.com/feed/',
    },
    'Cameroon': {
        'Journal du Cameroun': 'https://www.journalducameroun.com/feed/',
    },
    'Mali': {},
    'Namibia': {},
    'Botswana': {},
    'Lesotho': {},
    'Eswatini': {},
    'Malawi': {},
}

COUNTRY_KEYWORDS = {
    'Argentina': ['argentina', 'argentine', 'buenos aires'],
    'Brazil': ['brazil', 'brazilian', 'brasília', 'são paulo', 'rio'],
    'Mexico': ['mexico', 'mexican', 'mexico city'],
    'Colombia': ['colombia', 'colombian', 'bogota', 'bogotá'],
    'Chile': ['chile', 'chilean', 'santiago'],
    'Peru': ['peru', 'peruvian', 'lima'],
    'Venezuela': ['venezuela', 'venezuelan', 'caracas'],
    'Ecuador': ['ecuador', 'ecuadorian', 'quito'],
    'Bolivia': ['bolivia', 'bolivian', 'la paz'],
    'Uruguay': ['uruguay', 'uruguayan', 'montevideo'],
    'Paraguay': ['paraguay', 'paraguayan', 'asuncion'],
    'Costa Rica': ['costa rica', 'costa rican', 'san jose'],
    'Panama': ['panama', 'panamanian', 'panama city'],
    'Guatemala': ['guatemala', 'guatemalan', 'guatemala city'],
    'Honduras': ['honduras', 'honduran', 'tegucigalpa'],
    'El Salvador': ['el salvador', 'salvadoran', 'san salvador'],
    'Dominican Republic': ['dominican republic', 'dominican', 'santo domingo'],
    'Nicaragua': ['nicaragua', 'nicaraguan', 'managua'],
    'Nigeria': ['nigeria', 'nigerian', 'lagos', 'abuja'],
    'South Africa': ['south africa', 'south african', 'pretoria', 'johannesburg', 'cape town'],
    'Kenya': ['kenya', 'kenyan', 'nairobi'],
    'Ghana': ['ghana', 'ghanaian', 'accra'],
    'Ethiopia': ['ethiopia', 'ethiopian', 'addis ababa'],
    'Tanzania': ['tanzania', 'tanzanian', 'dar es salaam'],
    'Uganda': ['uganda', 'ugandan', 'kampala'],
    'Egypt': ['egypt', 'egyptian', 'cairo'],
    'Morocco': ['morocco', 'moroccan', 'rabat', 'casablanca'],
    'Senegal': ['senegal', 'senegalese', 'dakar'],
    'Rwanda': ['rwanda', 'rwandan', 'kigali'],
    'Zimbabwe': ['zimbabwe', 'zimbabwean', 'harare'],
    'Algeria': ['algeria', 'algerian', 'algiers'],
    'Ivory Coast': ['ivory coast', "cote d'ivoire", 'côte d’ivoire', 'abidjan'],
    'Namibia': ['namibia', 'namibian', 'windhoek'],
    'Botswana': ['botswana', 'motswana', 'batswana', 'gaborone'],
    'Lesotho': ['lesotho', 'basotho', 'maseru'],
    'Eswatini': ['eswatini', 'swaziland', 'swazi', 'mbabane'],
    'Malawi': ['malawi', 'malawian', 'lilongwe'],
    'Tunisia': ['tunisia', 'tunisian', 'tunis'],
}

def translate_to_english(text):
    try:
        if not text or len(text.strip()) < 3:
            return text
        translator = GoogleTranslator(source='auto', target='en')
        return translator.translate(text)
    except:
        return text

def get_time_ago(published_time):
    try:
        pub_date = datetime(*published_time[:6])
        now = datetime.now()
        diff = now - pub_date
        hours = int(diff.total_seconds() / 3600)
        if hours < 1:
            return "Less than 1 hour ago"
        elif hours == 1:
            return "1 hour ago"
        elif hours < 24:
            return f"{hours} hours ago"
        else:
            days = int(hours / 24)
            return f"{days} day{'s' if days > 1 else ''} ago"
    except:
        return "Recently"

def is_within_48_hours(published_time):
    try:
        pub_date = datetime(*published_time[:6])
        now = datetime.now()
        diff = now - pub_date
        return diff.total_seconds() < 172800
    except:
        return False

def fetch_feed(url, max_entries=10, translate=False, timeout=15):
    """Fetch with timeout and better error handling"""
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        feed = feedparser.parse(url)
        if not feed.entries:
            return []
        entries = []
        for entry in feed.entries[:max_entries * 2]:  # Get more, filter later
            pub_time = entry.get('published_parsed') or entry.get('updated_parsed')
            if pub_time and not is_within_48_hours(pub_time):
                continue
            if len(entries) >= max_entries:
                break
            time_ago = get_time_ago(pub_time) if pub_time else "Recently"
            title = entry.get('title', 'No title')
            link = entry.get('link', '#')
            description = entry.get('summary', entry.get('description', ''))
            description = re.sub('<[^<]+?>', '', description)
            description = ' '.join(description.split()[:30])
            if translate:
                title = translate_to_english(title)
                description = translate_to_english(description)
            entries.append({'title': title, 'description': description, 'url': link, 'time': time_ago})
        return entries
    except Exception as e:
        return []
    finally:
        socket.setdefaulttimeout(old_timeout)

def fetch_reuters_connect(client_id, client_secret, max_entries=15, hours_back=48):
    """Fetch text items from Reuters Connect's official API.
    NOTE: endpoint paths/params follow the publicly documented pattern
    (developers.reutersconnect.com) but haven't been tested against a real
    account from here — verify against your account's actual docs if this
    doesn't work out of the box, since details can vary by contract/tier."""
    try:
        auth_resp = requests.post(
            "https://api.reutersconnect.com/auth/v1/token",
            json={"client_id": client_id, "client_secret": client_secret, "grant_type": "client_credentials"},
            timeout=15,
        )
        auth_resp.raise_for_status()
        token = auth_resp.json().get("access_token") or auth_resp.json().get("value")
        if not token:
            print("  [Reuters Connect] ✗ No token in auth response")
            return []

        date_from = (datetime.now() - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        search_resp = requests.get(
            "https://api.reutersconnect.com/content/v1/search",
            headers={"Authorization": f"Bearer {token}"},
            params={"mediaTypes": "T", "dateFrom": date_from, "limit": max_entries, "sort": "date"},
            timeout=15,
        )
        search_resp.raise_for_status()
        items = search_resp.json().get("items", search_resp.json().get("results", []))

        articles = []
        for item in items[:max_entries]:
            articles.append({
                "title": item.get("headline") or item.get("title", "No title"),
                "description": (item.get("description") or item.get("abstract", ""))[:200],
                "url": item.get("uri") or item.get("url") or item.get("href", "#"),
                "time": "Recently",
                "source": "Reuters",
            })
        print(f"  [intl] Reuters Connect... ✓ {len(articles)} stories")
        return articles
    except Exception as e:
        print(f"  [intl] Reuters Connect... ✗ Failed ({e})")
        return []


def fetch_all_international_articles(max_entries_per_feed=15):
    """Fetch each international/pan-regional feed exactly ONCE. Previously this
    ran once per country (hundreds of redundant downloads of the same BBC/
    Reuters/AlJazeera/etc feeds) — now it runs once per script execution and
    every country filters against this same shared pool."""
    all_articles = []
    for outlet, feed_url in INTERNATIONAL_FEEDS.items():
        print(f"  [intl] {outlet}...", end=" ")
        try:
            articles = fetch_feed(feed_url, max_entries=max_entries_per_feed, translate=True)
            for article in articles:
                article['source'] = outlet
            all_articles.extend(articles)
            print(f"✓ {len(articles)} stories")
        except Exception:
            print("✗ Failed")
        time.sleep(0.3)

    if REUTERS_CLIENT_ID and REUTERS_CLIENT_SECRET:
        all_articles.extend(fetch_reuters_connect(REUTERS_CLIENT_ID, REUTERS_CLIENT_SECRET, max_entries=max_entries_per_feed))

    return all_articles


def filter_international_for_country(all_intl_articles, country, max_entries=10):
    country_keywords = COUNTRY_KEYWORDS.get(country, [country.lower()])
    matches = []
    for article in all_intl_articles:
        text = (article['title'] + ' ' + article['description']).lower()
        if any(keyword in text for keyword in country_keywords):
            matches.append(article)
    matches.sort(key=lambda x: x['time'])
    return matches[:max_entries]


def fetch_international_coverage(country, max_entries=10):
    """Kept for backward compatibility — prefer fetch_all_international_articles()
    + filter_international_for_country() in fetch_all_feeds, which avoids
    re-downloading the same feeds for every country."""
    country_keywords = COUNTRY_KEYWORDS.get(country, [country.lower()])
    all_articles = []
    for outlet, feed_url in INTERNATIONAL_FEEDS.items():
        try:
            articles = fetch_feed(feed_url, max_entries=15, translate=True)
            for article in articles:
                text = (article['title'] + ' ' + article['description']).lower()
                if any(keyword in text for keyword in country_keywords):
                    article['source'] = outlet
                    all_articles.append(article)
            time.sleep(0.3)
        except:
            continue
    all_articles.sort(key=lambda x: x['time'])
    return all_articles[:max_entries]

from difflib import SequenceMatcher

def _similar(a, b, threshold=0.6):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold

def build_top_stories(country_papers, international_stories, top_n=3):
    """Cluster near-duplicate headlines across a country's sources so the
    same story doesn't show up N times — instead it shows once with a
    'covered by N sources' count, which doubles as a rough importance signal."""
    pool = []
    for paper, stories in country_papers.items():
        for s in stories:
            pool.append({**s, 'source': paper})
    for s in (international_stories or []):
        pool.append(s)

    clusters = []
    for story in pool:
        placed = False
        for c in clusters:
            if _similar(c['title'], story['title']):
                c['sources'].add(story.get('source', 'Unknown'))
                c['count'] += 1
                placed = True
                break
        if not placed:
            clusters.append({
                'title': story['title'],
                'url': story['url'],
                'time': story['time'],
                'sources': {story.get('source', 'Unknown')},
                'count': 1,
            })

    clusters.sort(key=lambda c: c['count'], reverse=True)
    return [
        {'title': c['title'], 'url': c['url'], 'time': c['time'],
         'sources': sorted(c['sources']), 'count': c['count']}
        for c in clusters[:top_n]
    ]


def generate_country_summary(country, papers_data, international_data=None, translate=False):
    all_headlines = []
    for headlines in papers_data.values():
        all_headlines.extend(headlines)
    if not all_headlines and not international_data:
        return f"Limited coverage from {country}."
    top_local = all_headlines[:8]
    snippets = [s['title'][:80] + "..." if len(s['title']) > 80 else s['title'] for s in top_local]
    local_summary = ". ".join(snippets[:5]) + "." if snippets else ""
    intl_summary = ""
    if international_data and len(international_data) > 0:
        intl_snippets = [f"{s.get('source', 'Intl')} reports {s['title'][:70]}" for s in international_data[:3]]
        if intl_snippets:
            intl_summary = " International: " + "; ".join(intl_snippets) + "."
    summary = local_summary + intl_summary
    if translate:
        summary = translate_to_english(summary)
    return summary

def build_corpus(country_papers_data, international_data, max_per_country=6):
    """Flatten a region's headlines into a compact text corpus for the model.
    Each line carries a URL so the model can cite a real source for whatever
    it picks as a top story, instead of us guessing after the fact."""
    lines = []
    for country, papers in country_papers_data.items():
        all_stories = []
        for stories in papers.values():
            all_stories.extend(stories)
        for s in all_stories[:max_per_country]:
            lines.append(f"[{country}] {s['title']} — {s.get('description','')[:150]} (URL: {s.get('url','')})")
        for s in (international_data.get(country) or [])[:3]:
            lines.append(f"[{country} / intl - {s.get('source','')}] {s['title']} (URL: {s.get('url','')})")
    return "\n".join(lines)


def generate_editorial_brief(corpus_text, api_key, region_label="Africa", countries=None):
    """Call Claude to produce an executive summary, per-country curated
    highlights, and section suggestions as JSON."""
    countries_line = f"Countries present in this data: {', '.join(countries)}.\n" if countries else ""
    system_prompt = (
        f"You are a senior news editor for Semafor {region_label}. You will be given a list of "
        "raw headlines gathered from RSS feeds across the region, tagged by country, each with a "
        "source URL. " + countries_line +
        "Editorial criteria (apply this SAME bar everywhere below, both continent-wide and per-country): "
        "prioritize (1) human stakes (violence, deaths, humanitarian crises), (2) major economic/policy "
        "shifts (rate decisions, pricing, sanctions, big financial/business moves), (3) cross-border or "
        "geopolitical significance, (4) exclusivity/scale. Do NOT pick a story just because it's repeated "
        "by many outlets — one strong source can outrank five weak ones. ACTIVELY EXCLUDE: local sports "
        "results/federation news, book/event launches, opinion columns, travel listicles, celebrity or "
        "entertainment gossip, and routine local-government announcements — unless one of those genuinely "
        "carries outsized national/regional significance. If a country's headlines are ALL low-value by "
        "this bar, give it an empty list rather than including filler.\n"
        "Respond with ONLY valid JSON (no markdown fences, no preamble) matching this exact shape:\n"
        "{\n"
        '  "executive_summary": [{"headline": "short rewritten headline", "url": "<exact URL from the '
        'input for this story>", "country": "country name"}, ...],  // 4-6 items, ranked most important, continent-wide\n'
        '  "country_highlights": {\n'
        '    "<Country Name>": [{"headline": "short rewritten headline", "url": "<exact URL>"}, ...]  '
        '// 0-2 items per country that actually appears in the input, same bar as above\n'
        "  },\n"
        '  "continental_briefing": {\n'
        '    "business_macro": [{"text": "bullet", "url": "<exact URL>"}, ...],\n'
        '    "climate_energy": [{"text": "bullet", "url": "<exact URL>"}, ...],\n'
        '    "geopolitics_politics": [{"text": "bullet", "url": "<exact URL>"}, ...],\n'
        '    "tech_deals": [{"text": "bullet", "url": "<exact URL>"}, ...]\n'
        "  },\n"
        '  "political_story_suggestions": [{"text": "one-line pitch", "url": "<exact URL of the story that inspired this pitch>"}, ...],\n'
        '  "business_tech_story_suggestions": [{"text": "one-line pitch", "url": "<exact URL>"}, ...],\n'
        '  "weekend_read_suggestion": {"text": "one human-interest/feature pitch", "url": "<exact URL>"} or null if none stands out\n'
        "}\n"
        "Keep every bullet/headline under 25 words. Only include items actually supported by the "
        "headlines given — do not invent stories, details, or URLs. Every url field MUST be copied "
        "exactly from the (URL: ...) tag on its matching input line. Cover every country listed above "
        "in country_highlights (with an empty list if nothing clears the bar) — do not skip any."
    )
    last_error = None
    for attempt in range(2):
        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-5",
                    "max_tokens": 16000,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": corpus_text}],
                },
                timeout=300,  # 16000-token generations can genuinely take a few minutes
            )
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("stop_reason") == "max_tokens":
                raise RuntimeError(
                    "Claude's response was cut off (hit max_tokens) before finishing — "
                    "increase max_tokens further if this recurs."
                )
            content_blocks = response_data.get("content", [])
            text_block = next((b for b in content_blocks if b.get("type") == "text"), None)
            if text_block is None:
                raise RuntimeError(f"No text block found in Claude response. Blocks were: {content_blocks}")
            text = text_block["text"].strip()
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
            return json.loads(text)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            print(f"  [brief] attempt {attempt + 1} failed ({e}) — retrying..." if attempt == 0 else "")
            continue
    raise last_error


def brief_to_slack_blocks(brief, region_label, tracker_url):
    def bullets(items):
        return "\n".join(f"• {i}" for i in items) if items else "_none flagged_"

    def exec_bullets(items):
        if not items:
            return "_none flagged_"
        lines = []
        for i in items:
            if isinstance(i, dict) and i.get("url"):
                lines.append(f"• <{i['url']}|{i.get('headline', 'Untitled')}>")
            else:
                lines.append(f"• {i.get('headline', i) if isinstance(i, dict) else i}")
        return "\n".join(lines)

    def linked_bullets(items):
        if not items:
            return "_none flagged_"
        lines = []
        for i in items:
            if isinstance(i, dict) and i.get("url"):
                lines.append(f"• <{i['url']}|{i.get('text', 'Untitled')}>")
            else:
                lines.append(f"• {i.get('text', i) if isinstance(i, dict) else i}")
        return "\n".join(lines)

    def weekend_read_link(item):
        if not item:
            return "_none flagged_"
        if isinstance(item, dict) and item.get("url"):
            return f"<{item['url']}|{item.get('text', 'Untitled')}>"
        return item.get("text", item) if isinstance(item, dict) else item

    cb = brief.get("continental_briefing", {})
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{region_label} Daily Brief"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Today's biggest stories:*\n" + exec_bullets(brief.get("executive_summary", []))}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Business & Macro*\n" + linked_bullets(cb.get("business_macro", []))}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Climate & Energy*\n" + linked_bullets(cb.get("climate_energy", []))}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Geopolitics & Politics*\n" + linked_bullets(cb.get("geopolitics_politics", []))}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Tech & Deals*\n" + linked_bullets(cb.get("tech_deals", []))}},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Political story pitches*\n" + linked_bullets(brief.get("political_story_suggestions", []))}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Business/tech story pitches*\n" + linked_bullets(brief.get("business_tech_story_suggestions", []))}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Weekend read idea*\n" + weekend_read_link(brief.get("weekend_read_suggestion"))}},
    ]
    if tracker_url:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{tracker_url}|Open the full tracker>"}]})
    return blocks


def send_slack_brief(webhook_url, brief, region_label, tracker_url=None):
    payload = {"blocks": brief_to_slack_blocks(brief, region_label, tracker_url)}
    resp = requests.post(webhook_url, json=payload, timeout=15)
    return resp.status_code == 200


def upload_to_github(filename, content, token, username, repo):
    url = f"https://api.github.com/repos/{username}/{repo}/contents/{filename}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    response = requests.get(url, headers=headers)
    sha = response.json()['sha'] if response.status_code == 200 else None
    content_base64 = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    data = {"message": f"Update {filename}", "content": content_base64}
    if sha:
        data["sha"] = sha
    response = requests.put(url, headers=headers, json=data)
    return response.status_code in [200, 201]

def fetch_all_feeds(feed_dict, translate_summaries=False):
    all_data, country_summaries, international_coverage = {}, {}, {}
    top_stories, last_checked = {}, {}
    working_count = 0
    failed_count = 0

    print("\n[fetching shared international feed pool once]")
    shared_intl_articles = fetch_all_international_articles(max_entries_per_feed=15)

    for country, papers in feed_dict.items():
        print(f"\n📰 {country}")
        print("=" * 40)
        country_papers = {}

        for paper_name, feed_url in papers.items():
            key = f"{country}-{paper_name}"
            print(f"  {paper_name}...", end=" ")
            headlines = fetch_feed(feed_url, max_entries=10, translate=translate_summaries)
            if headlines:
                print(f"✓ {len(headlines)} stories")
                working_count += 1
            else:
                print(f"✗ Failed")
                failed_count += 1
            all_data[key] = headlines
            country_papers[paper_name] = headlines
            time.sleep(0.5)

        international_coverage[country] = filter_international_for_country(shared_intl_articles, country, max_entries=8)
        country_summaries[country] = generate_country_summary(country, country_papers, international_coverage[country], translate_summaries)
        top_stories[country] = build_top_stories(country_papers, international_coverage[country], top_n=3)
        last_checked[country] = datetime.now().isoformat()
        print(f"  📝 Summary generated")
        time.sleep(1)

    print(f"\n✓ Working feeds: {working_count}")
    print(f"✗ Failed feeds: {failed_count}")
    if working_count + failed_count:
        print(f"Success rate: {working_count}/{working_count+failed_count} ({int(100*working_count/(working_count+failed_count))}%)")

    return all_data, country_summaries, international_coverage, top_stories, last_checked

def run_editorial_brief(region_data, region_international, region_feeds, region_label, tracker_filename, json_prefix):
    """Build the corpus, call Claude, upload the brief, and send to Slack —
    shared logic so Africa and LatAm don't need separate copy-pasted blocks."""
    if not ANTHROPIC_API_KEY:
        print(f"\nℹ️ No ANTHROPIC_API_KEY set — skipping {region_label} editorial brief.")
        return
    print(f"\n🧠 Generating {region_label} editorial brief...")
    try:
        grouped = {}
        for country in region_feeds:
            grouped[country] = {
                k.split('-', 1)[1]: v for k, v in region_data.items() if k.startswith(country + '-')
            }
        corpus = build_corpus(grouped, region_international)
        brief = generate_editorial_brief(corpus, ANTHROPIC_API_KEY, region_label=region_label, countries=list(region_feeds.keys()))
        brief['generated_at'] = datetime.now().isoformat()
        print(f"✓ {region_label} brief generated")

        upload_to_github(f"{json_prefix}_brief.json", json.dumps(brief, ensure_ascii=False, indent=2), GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPO)
        print(f"✓ Brief uploaded to GitHub ({json_prefix}_brief.json)")

        if SLACK_WEBHOOK_URL:
            sent = send_slack_brief(
                SLACK_WEBHOOK_URL, brief, region_label,
                tracker_url=f"https://news-data1.vercel.app/{tracker_filename}"
            )
            print("✓ Sent to Slack" if sent else "⚠️ Slack post failed")
        else:
            print("ℹ️ No SLACK_WEBHOOK_URL set — skipping Slack delivery. Brief:")
            print(json.dumps(brief, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"⚠️ {region_label} editorial brief failed: {e}")


if __name__ == "__main__":
    print("=" * 50)
    print("OPTIMIZED NEWS TRACKER")
    print("Curated, reliable feeds only")
    print("=" * 50)

    print("\n🌎 LATIN AMERICA")
    latam_data, latam_summaries, latam_international, latam_top_stories, latam_last_checked = fetch_all_feeds(latam_feeds, translate_summaries=True)

    print("\n🌍 AFRICA")
    africa_data, africa_summaries, africa_international, africa_top_stories, africa_last_checked = fetch_all_feeds(africa_feeds, translate_summaries=False)

    latam_output = {
        'headlines': latam_data, 'summaries': latam_summaries, 'international': latam_international,
        'top_stories': latam_top_stories, 'last_checked': latam_last_checked,
    }
    africa_output = {
        'headlines': africa_data, 'summaries': africa_summaries, 'international': africa_international,
        'top_stories': africa_top_stories, 'last_checked': africa_last_checked,
    }

    print("\n🔄 Uploading to GitHub...")
    latam_success = upload_to_github("latam_headlines.json", json.dumps(latam_output, ensure_ascii=False, indent=2), GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPO)
    africa_success = upload_to_github("africa_headlines.json", json.dumps(africa_output, ensure_ascii=False, indent=2), GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPO)

    if latam_success and africa_success:
        print("✅ DONE! Both files uploaded successfully")
    else:
        print("⚠️ Upload issues - check GitHub token")

    total_stories = sum(len(v) for v in list(latam_data.values()) + list(africa_data.values()))
    print(f"\n📊 Total stories collected: {total_stories}")

    # ── Editorial brief + Slack DM, now for both regions ──
    run_editorial_brief(africa_data, africa_international, africa_feeds, "Africa", "africa.html", "africa")
    run_editorial_brief(latam_data, latam_international, latam_feeds, "LatAm", "latam.html", "latam")

