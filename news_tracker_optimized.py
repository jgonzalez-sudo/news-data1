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
from urllib.parse import urlparse

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
    SLACK_WEBHOOK_URL_AFRICA_TEAM = userdata.get('SLACK_WEBHOOK_URL_AFRICA_TEAM')
except Exception:
    SLACK_WEBHOOK_URL_AFRICA_TEAM = os.environ.get('SLACK_WEBHOOK_URL_AFRICA_TEAM', '')

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
    'BBC Africa': 'https://feeds.bbci.co.uk/news/world/africa/rss.xml',
    'The Guardian': 'https://www.theguardian.com/world/rss',
    'The Guardian Africa': 'https://www.theguardian.com/world/africa/rss',
    'AllAfrica': 'https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf',
    'AllAfrica Business': 'https://allafrica.com/tools/headlines/rdf/business/headlines.rdf',
    'Africanews': 'http://www.africanews.com/feed/rss',
    'Al Jazeera': 'https://www.aljazeera.com/xml/rss/all.xml',
    'UPI': 'https://rss.upi.com/news/tn_int.rss',
    'Deutsche Welle': 'https://rss.dw.com/xml/rss-en-all',
    'France24': 'https://www.france24.com/en/rss',
    'Xinhua': 'http://www.xinhuanet.com/english/rss/worldrss.xml',
    'Jeune Afrique': 'https://www.jeuneafrique.com/feed/',
    'The Africa Report': 'https://www.theafricareport.com/feed/',
    'How We Made It In Africa': 'https://www.howwemadeitinafrica.com/feed/',
}
# Only feeds that aren't in English get run through the (slow) translator.
INTERNATIONAL_TRANSLATE = {'Jeune Afrique'}

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
        'Guardian Nigeria': 'https://guardian.ng/feed/',
        'Premium Times': 'https://www.premiumtimesng.com/feed',
        'Vanguard': 'https://www.vanguardngr.com/feed/',
        'The Punch': 'https://punchng.com/feed/',
        'Daily Trust': 'https://dailytrust.com/feed',
        'The Cable': 'https://www.thecable.ng/feed',
        'Sahara Reporters': 'https://saharareporters.com/feeds/latest/feed',
        'BusinessDay': 'https://businessday.ng/feed/',
    },
    'South Africa': {
        'News24': 'https://feeds.news24.com/articles/news24/topstories/rss',
        'Daily Maverick': 'https://www.dailymaverick.co.za/dmrss/',
        'The Citizen': 'https://www.citizen.co.za/feed/',
        'Mail & Guardian': 'https://mg.co.za/feed/',
        'IOL': 'https://www.iol.co.za/rss',
        'BusinessTech': 'https://businesstech.co.za/news/feed/',
        'Moneyweb': 'https://www.moneyweb.co.za/feed/',
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


_INTL_CACHE = None


def fetch_all_international_articles(max_entries_per_feed=15):
    """Fetch each international/pan-regional feed once per run (cached, so running
    both regions doesn't download everything twice). Every country then filters
    against this shared pool."""
    global _INTL_CACHE
    if _INTL_CACHE is not None:
        return _INTL_CACHE
    all_articles = []
    for outlet, feed_url in INTERNATIONAL_FEEDS.items():
        print(f"  [intl] {outlet}...", end=" ")
        try:
            articles = fetch_feed(feed_url, max_entries=max_entries_per_feed,
                                  translate=(outlet in INTERNATIONAL_TRANSLATE))
            for article in articles:
                article['source'] = outlet
            all_articles.extend(articles)
            print(f"✓ {len(articles)} stories")
        except Exception:
            print("✗ Failed")
        time.sleep(0.3)

    if REUTERS_CLIENT_ID and REUTERS_CLIENT_SECRET:
        all_articles.extend(fetch_reuters_connect(REUTERS_CLIENT_ID, REUTERS_CLIENT_SECRET, max_entries=max_entries_per_feed))

    _INTL_CACHE = all_articles
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

# ── Source priority ─────────────────────────────────────────────────────────
# Stories from these outlets are starred in the AI's input and preferred when
# several outlets cover the same story. Edit this set to change the priority.
TOP_SOURCE_DOMAINS = {
    'reuters.com', 'apnews.com', 'afp.com', 'bloomberg.com',
    'bbc.com', 'bbc.co.uk',
    'theguardian.com', 'guardian.ng', 'mg.co.za',
    'allafrica.com', 'africanews.com',
    'ft.com', 'nytimes.com', 'wsj.com', 'economist.com', 'aljazeera.com',
}
SOURCE_NAMES = {
    'reuters.com': 'Reuters', 'apnews.com': 'AP', 'afp.com': 'AFP', 'bloomberg.com': 'Bloomberg',
    'bbc.com': 'BBC', 'bbc.co.uk': 'BBC', 'theguardian.com': 'The Guardian', 'guardian.ng': 'Guardian Nigeria',
    'mg.co.za': 'Mail & Guardian', 'allafrica.com': 'AllAfrica', 'africanews.com': 'Africanews',
    'ft.com': 'FT', 'nytimes.com': 'NYT', 'wsj.com': 'WSJ', 'economist.com': 'The Economist',
    'aljazeera.com': 'Al Jazeera', 'dw.com': 'DW', 'france24.com': 'France24',
    'theafricareport.com': 'The Africa Report', 'jeuneafrique.com': 'Jeune Afrique',
    'premiumtimesng.com': 'Premium Times', 'vanguardngr.com': 'Vanguard', 'punchng.com': 'The Punch',
    'businessday.ng': 'BusinessDay', 'dailymaverick.co.za': 'Daily Maverick', 'news24.com': 'News24',
    'moneyweb.co.za': 'Moneyweb', 'businessdailyafrica.com': 'Business Daily',
    'thecitizen.co.tz': 'The Citizen', 'howwemadeitinafrica.com': 'How We Made It In Africa',
}
BUSINESS_TERMS = re.compile(
    r"\b(billion|million|bn|deal|investment|invest|investors?|loan|debt|imf|world bank|bond|budget|gdp|"
    r"inflation|central bank|interest rate|currency|exchange rate|shilling|naira|rand|cedi|pula|kwacha|birr|"
    r"dollar|oil|gas|lng|pipeline|refinery|mining|mine|lithium|cobalt|copper|gold|port|railway|infrastructure|"
    r"export|import|trade|tariff|revenue|profit|earnings|ipo|stock|bank|fintech|startup|funding|raises|"
    r"acquisition|merger|tender|contract|power|solar|energy|project|economy|economic|growth|fund)\b", re.I)
MONEY_PATTERN = re.compile(r"[$€£]\s?\d|\b\d[\d.,]*\s?(?:bn|billion|million|trillion)\b", re.I)


def _host(url):
    try:
        return (urlparse(url).hostname or '').lower()
    except Exception:
        return ''


def _domain_matches(host, domain):
    return host == domain or host.endswith('.' + domain)


def is_top_source(url):
    host = _host(url)
    return any(_domain_matches(host, d) for d in TOP_SOURCE_DOMAINS)


def source_from_url(url):
    host = _host(url)
    for d, name in SOURCE_NAMES.items():
        if _domain_matches(host, d):
            return name
    parts = host.split('.')
    if len(parts) >= 3 and parts[-2] in ('co', 'com', 'org', 'gov', 'ac'):
        base = parts[-3]
    else:
        base = parts[-2] if len(parts) >= 2 else host
    return base.replace('-', ' ').title()


def normalize_url(u):
    p = urlparse(str(u).strip())
    return f"{p.scheme.lower()}://{p.netloc.lower()}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


def story_score(story):
    """Higher = more likely to make the cut: top-tier source, plus business substance."""
    text = f"{story.get('title', '')} {story.get('description', '')}"
    score = 3 if is_top_source(story.get('url', '')) else 0
    score += min(len({m.lower() for m in BUSINESS_TERMS.findall(text)}), 3)
    if MONEY_PATTERN.search(text):
        score += 2
    return score


def corpus_entries(country_papers_data, international_data, max_per_country=8):
    """The headlines that go to the model, as structured entries. Per country, top-tier
    sources and business stories are ranked first, so they survive the cutoff."""
    entries = []
    for country, papers in country_papers_data.items():
        items = []
        for paper, stories in papers.items():
            items.extend((paper, s) for s in stories)
        items.extend((s.get('source', 'Intl'), s) for s in (international_data.get(country) or []))
        items.sort(key=lambda it: story_score(it[1]), reverse=True)  # stable: ties keep feed order
        for outlet, s in items[:max_per_country]:
            entries.append({"country": country, "outlet": outlet, "top": is_top_source(s.get('url', '')),
                            "title": s['title'], "description": s.get('description', ''), "url": s.get('url', '')})
    return entries


def build_corpus(country_papers_data, international_data, max_per_country=8):
    """Flatten a region's headlines into text for the model. Each line carries the
    outlet name, a star for top-tier sources, and the URL."""
    lines = []
    for e in corpus_entries(country_papers_data, international_data, max_per_country):
        star = ' ★' if e['top'] else ''
        lines.append(f"[{e['country']} | {e['outlet']}{star}] {e['title']} — {e['description'][:150]} (URL: {e['url']})")
    return "\n".join(lines)


def business_signal(text):
    hits = len({m.lower() for m in BUSINESS_TERMS.findall(text)})
    return hits + (2 if MONEY_PATTERN.search(text) else 0)


def item_is_business(item):
    """Trust the model's own tag; fall back to keywords if an item has none."""
    t = str(item.get('type', '')).strip().lower()
    if t in ('business', 'other'):
        return t == 'business'
    return business_signal(item.get('headline', '')) >= 2


def ensure_business_floor(brief, entries, minimum):
    """Guarantee at least `minimum` business or economy stories among the top stories.
    If the model fell short, swap in the strongest unused business headlines from the
    day's feeds (top-tier sources first), replacing the lowest-ranked non-business items."""
    items = brief.get('executive_summary') or []
    have = sum(1 for i in items if item_is_business(i))
    if minimum <= 0 or have >= minimum:
        return brief, 0
    used = {normalize_url(i['url']) for i in items if i.get('url')}
    pool = []
    for e in entries:
        if normalize_url(e['url']) in used:
            continue
        sig = business_signal(f"{e['title']} {e['description']}")
        if sig >= 2:
            pool.append((sig + (3 if e['top'] else 0), e))
    pool.sort(key=lambda x: -x[0])  # stable
    replace_at = [i for i in range(len(items) - 1, -1, -1) if not item_is_business(items[i])]
    swaps = 0
    for _, e in pool:
        if have + swaps >= minimum:
            break
        new = {"headline": e['title'] if len(e['title']) <= 110 else e['title'][:109] + '…',
               "url": e['url'], "source": e['outlet'], "country": e['country'], "type": "business"}
        if replace_at:
            items[replace_at.pop(0)] = new
        elif len(items) < 5:
            items.append(new)
        else:
            break
        swaps += 1
    brief['executive_summary'] = items
    return brief, swaps


def clean_brief(brief, corpus_text):
    """Drop any item whose link isn't in the input (guards against invented URLs)
    and fill in a source name from the URL if the model left it out."""
    allowed = set()
    for line in corpus_text.splitlines():
        m = re.search(r"\(URL: (.*)\)\s*$", line)
        if m:
            allowed.add(normalize_url(m.group(1)))
    dropped = 0

    def ok(item):
        nonlocal dropped
        url = item.get('url') if isinstance(item, dict) else None
        if url and normalize_url(url) in allowed:
            item['source'] = (item.get('source') or source_from_url(url)).strip()
            return True
        dropped += 1
        return False

    brief['executive_summary'] = [i for i in brief.get('executive_summary') or [] if ok(i)]
    brief['country_highlights'] = {c: [i for i in items or [] if ok(i)]
                                   for c, items in (brief.get('country_highlights') or {}).items()}
    cb = brief.get('continental_briefing') or {}
    brief['continental_briefing'] = {k: [i for i in v or [] if ok(i)] for k, v in cb.items()}
    for key in ('political_story_suggestions', 'business_tech_story_suggestions'):
        brief[key] = [i for i in brief.get(key) or [] if ok(i)]
    w = brief.get('weekend_read_suggestion')
    brief['weekend_read_suggestion'] = w if (isinstance(w, dict) and ok(w)) else None
    return brief, dropped


CITE_TAG = re.compile(r"</?(?:antml:)?cite\b[^>]*>", re.I)


def strip_cite_tags(obj):
    """Remove stray citation tags from every string in the model's JSON."""
    if isinstance(obj, dict):
        return {k: strip_cite_tags(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_cite_tags(v) for v in obj]
    if isinstance(obj, str):
        return CITE_TAG.sub("", obj).strip()
    return obj


DEFAULT_BUSINESS_RULE = "At least 2 of the 5 executive_summary items must be business or economy stories if the data has any."
AFRICA_BUSINESS_RULE = (
    "Business and economy stories belong in the top stories regularly: aim for 2 to 3 of the 5 executive_summary items "
    "to be business or economy stories (deals and investment, energy and mining projects, debt, central-bank and "
    "currency moves, trade, major corporate news). Never fewer than 2 if the data has any, and never more than 3, "
    "so human-stakes and politics stories keep their place."
)


def generate_editorial_brief(corpus_text, api_key, region_label="Africa", countries=None, business_rule=None):
    """Call Claude to produce a short executive summary, per-country highlights and
    newsletter suggestions as JSON. Every item carries a URL and a source name."""
    countries_line = f"Countries in this data: {', '.join(countries)}.\n" if countries else ""
    business_rule = business_rule or DEFAULT_BUSINESS_RULE
    system_prompt = (
        f"You are a senior news editor for Semafor {region_label}. You will receive headlines gathered from RSS "
        "feeds across the region, each tagged [Country | Outlet] with a source URL. " + countries_line +
        "SELECTION: pick what matters most for a global business-and-politics newsletter audience. Weigh these equally: "
        "(1) human stakes (violence, deaths, humanitarian crises); (2) business and economics: major deals and "
        "investment (for example a government seeking a $42 billion LNG project), energy and mining projects, debt, "
        "central-bank and currency moves, trade, large corporate news; (3) politics and geopolitics with cross-border "
        "significance. " + business_rule + " Do not pick a story just because many outlets repeat it. Exclude local sports, book or event launches, "
        "opinion columns, travel listicles, celebrity news and routine local-government announcements unless they carry "
        "real national significance. If a country has nothing worth including, give it an empty list.\n"
        "SOURCES: lines marked with a star come from top-tier sources (news agencies, Bloomberg, BBC, The Guardian, "
        "AllAfrica, Africanews and similar). Prefer them: when several outlets cover the same story, cite the starred "
        "outlet's URL, and give extra weight to stories they carry. Every item needs a source: the outlet name exactly "
        "as shown in the input line.\n"
        "LENGTH: be brief. Headlines and bullets under 15 words, no filler.\n"
        "Respond with ONLY valid JSON (no markdown fences, no preamble) in exactly this shape:\n"
        "{\n"
        '  "executive_summary": [{"headline": "...", "url": "<exact URL>", "source": "...", "country": "...", "type": "business" or "other"}],  // exactly 5, most important first; type is "business" for business and economy stories\n'
        '  "country_highlights": {"<Country>": [{"headline": "...", "url": "<exact URL>", "source": "..."}]},  // 0-2 per country\n'
        '  "continental_briefing": {\n'
        '    "business_macro": [{"text": "...", "url": "<exact URL>", "source": "..."}],  // up to 2 per category\n'
        '    "climate_energy": [same shape],\n'
        '    "geopolitics_politics": [same shape],\n'
        '    "tech_deals": [same shape]\n'
        "  },\n"
        '  "political_story_suggestions": [{"text": "...", "url": "<exact URL>", "source": "..."}],  // up to 2\n'
        '  "business_tech_story_suggestions": [same shape],  // up to 2, lean towards business\n'
        '  "weekend_read_suggestion": {"text": "...", "url": "<exact URL>", "source": "..."} or null\n'
        "}\n"
        "Only include items supported by the input; never invent stories, details or URLs. Every url must be copied "
        "exactly from the (URL: ...) tag of its input line. Cover every country listed above in country_highlights "
        "(an empty list if nothing qualifies)."
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
                timeout=300,
            )
            response.raise_for_status()
            response_data = response.json()
            if response_data.get("stop_reason") == "max_tokens":
                raise RuntimeError("Claude's response was cut off (hit max_tokens) before finishing.")
            content_blocks = response_data.get("content", [])
            text_block = next((b for b in content_blocks if b.get("type") == "text"), None)
            if text_block is None:
                raise RuntimeError(f"No text block found in Claude response. Blocks were: {content_blocks}")
            text = text_block["text"].strip()
            text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
            return strip_cite_tags(json.loads(text))
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            print(f"  [brief] attempt {attempt + 1} failed ({e})" + (" - retrying..." if attempt == 0 else ""))
            continue
    raise last_error


def _slack_escape(s):
    return str(s or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _slack_url(u):
    return str(u).replace(' ', '%20').replace('|', '%7C').replace('<', '%3C').replace('>', '%3E')


def _linked(item, text_key):
    """One Slack line: the linked headline, then the outlet it came from."""
    if not isinstance(item, dict):
        return f"• {_slack_escape(item)}"
    text = _slack_escape(item.get(text_key) or 'Untitled')
    if item.get('url'):
        source = _slack_escape(item.get('source') or source_from_url(item['url']))
        return f"• <{_slack_url(item['url'])}|{text}> — _{source}_"
    return f"• {text}"


def brief_to_slack_blocks(brief, region_label, tracker_url):
    def section(text):
        return {"type": "section", "text": {"type": "mrkdwn", "text": text}}

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": f"{region_label} Daily Brief"}}]
    top = [_linked(i, 'headline') for i in brief.get('executive_summary') or []]
    if top:
        blocks.append(section("*Top stories*\n" + "\n".join(top)))

    cb = brief.get('continental_briefing') or {}
    categories = [("Business & Macro", 'business_macro'), ("Climate & Energy", 'climate_energy'),
                  ("Geopolitics & Politics", 'geopolitics_politics'), ("Tech & Deals", 'tech_deals')]
    category_blocks = []
    for label, key in categories:
        lines = [_linked(i, 'text') for i in cb.get(key) or []]
        if lines:
            category_blocks.append(section(f"*{label}*\n" + "\n".join(lines)))
    if category_blocks:
        blocks.append({"type": "divider"})
        blocks.extend(category_blocks)

    pitch_blocks = []
    for label, key in (("Political pitches", 'political_story_suggestions'),
                       ("Business/tech pitches", 'business_tech_story_suggestions')):
        lines = [_linked(i, 'text') for i in brief.get(key) or []]
        if lines:
            pitch_blocks.append(section(f"*{label}*\n" + "\n".join(lines)))
    weekend = brief.get('weekend_read_suggestion')
    if weekend:
        pitch_blocks.append(section("*Weekend read*\n" + _linked(weekend, 'text')))
    if pitch_blocks:
        blocks.append({"type": "divider"})
        blocks.extend(pitch_blocks)

    if tracker_url:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{tracker_url}|Open the full tracker>"}]})
    return blocks


def send_slack_brief(webhook_url, brief, region_label, tracker_url=None):
    payload = {"text": f"{region_label} Daily Brief",
               "blocks": brief_to_slack_blocks(brief, region_label, tracker_url)}
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

def slack_targets(region_label):
    """Where this region's brief goes. Your test webhook (DM) always gets it. The
    Africa team channel only gets Africa, and only once its secret is set."""
    targets = []
    if SLACK_WEBHOOK_URL:
        targets.append(("your DM (test webhook)", SLACK_WEBHOOK_URL))
    if region_label == "Africa":
        if SLACK_WEBHOOK_URL_AFRICA_TEAM and SLACK_WEBHOOK_URL_AFRICA_TEAM != SLACK_WEBHOOK_URL:
            targets.append(("Africa team channel", SLACK_WEBHOOK_URL_AFRICA_TEAM))
        elif not SLACK_WEBHOOK_URL_AFRICA_TEAM:
            print("ℹ️ Africa team channel not connected yet (SLACK_WEBHOOK_URL_AFRICA_TEAM is not set).")
    return targets


def run_editorial_brief(region_data, region_international, region_feeds, region_label, tracker_filename, json_prefix,
                        business_rule=None, business_floor=0):
    """Build the corpus, call Claude, upload the brief, and send it to Slack.
    Returns True only if the brief was generated AND GitHub accepted the upload
    AND every Slack destination accepted the message."""
    if not ANTHROPIC_API_KEY:
        print(f"\n❌ No ANTHROPIC_API_KEY found - cannot generate the {region_label} brief. "
              "Check the secret name and value in GitHub (Settings > Secrets and variables > Actions).")
        return False
    print(f"\n🧠 Generating {region_label} editorial brief...")
    try:
        grouped = {}
        for country in region_feeds:
            grouped[country] = {
                k.split('-', 1)[1]: v for k, v in region_data.items() if k.startswith(country + '-')
            }
        corpus = build_corpus(grouped, region_international)
        brief = generate_editorial_brief(corpus, ANTHROPIC_API_KEY, region_label=region_label,
                                         countries=list(region_feeds.keys()), business_rule=business_rule)
        brief, dropped = clean_brief(brief, corpus)
        if dropped:
            print(f"  dropped {dropped} item(s) whose link wasn't in the input")
        if business_floor:
            brief, swapped = ensure_business_floor(brief, corpus_entries(grouped, region_international), business_floor)
            n_biz = sum(1 for i in brief.get('executive_summary') or [] if item_is_business(i))
            print(f"  top stories: {n_biz} business/economy of {len(brief.get('executive_summary') or [])}"
                  + (f" (swapped in {swapped} to reach the minimum of {business_floor})" if swapped else ""))
        if not brief.get('executive_summary'):
            print(f"❌ {region_label} brief had no usable top stories after checking links.")
            return False
        brief['generated_at'] = datetime.now().isoformat()
        print(f"✓ {region_label} brief generated")

        uploaded = upload_to_github(f"{json_prefix}_brief.json", json.dumps(brief, ensure_ascii=False, indent=2), GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPO)
        if not uploaded:
            print(f"❌ GitHub rejected the upload of {json_prefix}_brief.json. "
                  "Check Settings > Actions > General > Workflow permissions is 'Read and write'.")
            return False
        print(f"✓ Brief uploaded to GitHub ({json_prefix}_brief.json)")

        targets = slack_targets(region_label)
        if not targets:
            print("ℹ️ No Slack webhook set - skipping Slack delivery.")
            return True
        all_sent = True
        for name, url in targets:
            sent = send_slack_brief(url, brief, region_label, tracker_url=f"https://news-data1.vercel.app/{tracker_filename}")
            print(f"✓ Sent to Slack: {name}" if sent else f"⚠️ Slack post failed: {name} (check that webhook secret)")
            all_sent = all_sent and bool(sent)
        return all_sent
    except Exception as e:
        print(f"❌ {region_label} editorial brief failed: {e}")
        return False


REGION_CONFIG = {
    'africa': dict(label='Africa', feeds=africa_feeds, translate=False, page='africa.html', prefix='africa',
                   business_rule=AFRICA_BUSINESS_RULE, business_floor=2),
    'latam': dict(label='LatAm', feeds=latam_feeds, translate=True, page='latam.html', prefix='latam',
                  business_rule=None, business_floor=0),
}


def parse_regions(argv):
    """`python news_tracker_optimized.py africa` runs only Africa; `latam` only LatAm;
    no argument runs both (also what happens in Colab)."""
    wanted = {a.lower() for a in argv if a.lower() in REGION_CONFIG}
    if not wanted and os.environ.get('TRACKER_REGIONS'):
        wanted = {r.strip().lower() for r in os.environ['TRACKER_REGIONS'].split(',') if r.strip().lower() in REGION_CONFIG}
    return wanted or set(REGION_CONFIG)


def main():
    regions = parse_regions(sys.argv[1:])
    print("=" * 50)
    print("NEWS TRACKER - running: " + ", ".join(REGION_CONFIG[r]['label'] for r in REGION_CONFIG if r in regions))
    print("=" * 50)

    results = {}
    total_stories = 0
    for key in ('africa', 'latam'):  # Africa first, so its brief lands even if LatAm is slow
        if key not in regions:
            continue
        cfg = REGION_CONFIG[key]
        label = cfg['label']
        print(f"\n{'🌍' if key == 'africa' else '🌎'} {label.upper()}")
        data, summaries, international, top_stories, last_checked = fetch_all_feeds(cfg['feeds'], translate_summaries=cfg['translate'])
        output = {'headlines': data, 'summaries': summaries, 'international': international,
                  'top_stories': top_stories, 'last_checked': last_checked}
        print(f"\n🔄 Uploading {label} headlines to GitHub...")
        ok = upload_to_github(f"{cfg['prefix']}_headlines.json", json.dumps(output, ensure_ascii=False, indent=2), GITHUB_TOKEN, GITHUB_USERNAME, GITHUB_REPO)
        print(f"✅ {label} headlines uploaded" if ok else f"⚠️ {label} headline upload failed - check the GitHub token / workflow permissions")
        results[f"{label} headlines"] = bool(ok)
        total_stories += sum(len(v) for v in data.values())
        results[f"{label} brief + Slack"] = run_editorial_brief(data, international, cfg['feeds'], label, cfg['page'], cfg['prefix'],
                                                                  business_rule=cfg['business_rule'], business_floor=cfg['business_floor'])

    print(f"\n📊 Total stories collected: {total_stories}")
    print("\n" + "=" * 50)
    print("RUN SUMMARY")
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    print("=" * 50)

    # In GitHub Actions, turn the run red if anything did not land, so a green
    # check really means the data updated. (Skipped inside Colab.)
    if not all(results.values()) and "google.colab" not in sys.modules:
        sys.exit(1)


if __name__ == "__main__":
    main()
