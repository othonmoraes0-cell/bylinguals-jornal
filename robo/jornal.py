"""
The Bylinguals Daily — a edição do dia do Jornal do Club (09/10/2026; ver MAP_SYSTEM_CLUB_JORNAL_PROPOSTA.md no Portal).

Desde 09/10/2026 (tarde): 9 TEMAS, e em cada tema 3 notícias dos Estados Unidos, 3 do mundo e 3 do Brasil. Cada tema roda
num robô separado, em paralelo (variável TEMA). A notícia COMPLETA só sai quando 3 ou mais veículos noticiaram o assunto
(ou é da NASA); o resto da vaga vira NOTA curta (2 a 3 frases) de um veículo só, com a mesma conferência.

Roda grátis no GitHub Actions, com um modelo de IA aberto (Qwen2.5 14B, llama.cpp no processador):
 1. Lê os feeds. Fonte principal: The New York Times (seções) e CNN Brasil. Apoio: BBC, Guardian, DW, NPR e g1.
    De jornal, lê o título, o resumo do feed e o começo da matéria (até 300-500 palavras), só para tirar os FATOS: o aluno
    nunca vê o texto do jornal; o robô reescreve com as próprias palavras e frase copiada (8 palavras seguidas) é recusada.
    NASA (domínio público): a matéria inteira e a foto.
 2. Junta o mesmo assunto em vários veículos. Só entra notícia com 3 ou mais veículos (ou da NASA): com menos fatos a IA
    "completa" com o que não existe (piloto de 09/10/2026).
 3. Escreve do zero, em dois níveis (Everyday English e Real Conversations), com o número de frases que os fatos sustentam.
 4. Confere cada frase contra os fatos: a que não tem base sai. Se sobrar pouco, a notícia não entra.
 5. Traduz cada frase para o português (OPUS-MT) e entrega ao Portal (token OIDC do GitHub, sem senha).
"""
import hashlib
import html
import random
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests
import trafilatura

SITE = os.environ.get("SITE_URL", "https://www.bylinguals.com.br").rstrip("/")
API = os.environ.get("LLM_URL", "http://127.0.0.1:8080/v1/chat/completions")
MODELO = os.environ.get("MODELO_ROTULO", "qwen2.5-14b")
FORCAR = os.environ.get("FORCAR", "false") == "true"
SECO = os.environ.get("SECO", "false") == "true"  # só escreve e mostra; não entrega ao Portal
POR_REGIAO = int(os.environ.get("POR_REGIAO", "3"))
TEMA = os.environ.get("TEMA", "")
SO_FEEDS = os.environ.get("SO_FEEDS", "false") == "true"  # só confere os feeds (sem IA)
INICIO = time.time()
LIMITE_S = 5 * 3600
AGENTE = "BylingualsRobo/1.0 (+https://github.com/othonmoraes0-cell/bylinguals-jornal)"
UA = {"User-Agent": "Mozilla/5.0 (" + AGENTE + ")"}

NYT = "https://rss.nytimes.com/services/xml/rss/nyt/"
NPR = "https://feeds.npr.org/{}/rss.xml"
BBC = "https://feeds.bbci.co.uk/news/{}/rss.xml"
GUA = "https://www.theguardian.com/{}/rss"
FOLHA = "https://feeds.folha.uol.com.br/{}/rss091.xml"
G1 = "https://g1.globo.com/rss/g1/{}/"
CBS = "https://www.cbsnews.com/latest/rss/{}"
FOX = "https://moxie.foxnews.com/google-publisher/{}.xml"
ABC = "https://abcnews.go.com/abcnews/{}"
CNNBR = "https://www.cnnbrasil.com.br/{}/feed/"
NASA = "https://www.nasa.gov/news-release/feed/"

# Fontes por tema e região: (veículo, feed). Do jornal saem só os fatos (texto reescrito). Veículos dos dois lados do espectro
# político nos EUA (NYT, NPR, CBS, ABC e Fox) e várias redações no Brasil, para a notícia completa ter 3+ olhares.
TEMAS = {
    "politics": ("Politics & Elections", {
        "US": [("The New York Times", NYT + "Politics.xml"), ("NPR", NPR.format(1014)), ("CBS News", CBS.format("politics")), ("Fox News", FOX.format("politics")), ("ABC News", ABC.format("politicsheadlines"))],
        "World": [("The New York Times", NYT + "World.xml"), ("BBC", BBC.format("world")), ("The Guardian", GUA.format("world")), ("DW", "https://rss.dw.com/rdf/rss-en-all"), ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"), ("France 24", "https://www.france24.com/en/rss")],
        "Brazil": [("g1", G1.format("politica")), ("Folha de S.Paulo", FOLHA.format("poder")), ("CNN Brasil", CNNBR.format("politica")), ("Poder360", "https://www.poder360.com.br/feed/"), ("BBC News Brasil", "https://feeds.bbci.co.uk/portuguese/rss.xml")],
    }),
    "economy": ("Economy & Personal Finance", {
        "US": [("The New York Times", NYT + "Economy.xml"), ("The New York Times", NYT + "YourMoney.xml"), ("NPR", NPR.format(1017)), ("CNBC", "https://www.cnbc.com/id/21324812/device/rss/rss.html"), ("CBS News", CBS.format("moneywatch"))],
        "World": [("BBC", BBC.format("business")), ("The Guardian", GUA.format("business/economics")), ("DW", "https://rss.dw.com/rdf/rss-en-bus")],
        "Brazil": [("g1", G1.format("economia")), ("InfoMoney", "https://www.infomoney.com.br/economia/feed/"), ("InfoMoney", "https://www.infomoney.com.br/minhas-financas/feed/"), ("Folha de S.Paulo", FOLHA.format("mercado")), ("CNN Brasil", CNNBR.format("economia"))],
    }),
    "business": ("Business", {
        "US": [("The New York Times", NYT + "Business.xml"), ("NPR", NPR.format(1006)), ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"), ("ABC News", ABC.format("moneyheadlines")), ("The New York Times", NYT + "Technology.xml")],
        "World": [("The Guardian", GUA.format("business")), ("BBC", BBC.format("technology")), ("DW", "https://rss.dw.com/rdf/rss-en-bus"), ("BBC", BBC.format("business"))],
        "Brazil": [("Exame", "https://exame.com/feed/"), ("g1", G1.format("tecnologia")), ("CNN Brasil", CNNBR.format("economia/negocios")), ("Folha de S.Paulo", FOLHA.format("mercado"))],
    }),
    "science": ("Science", {
        "US": [("The New York Times", NYT + "Science.xml"), ("NPR", NPR.format(1007)), ("CBS News", CBS.format("science")), ("ScienceDaily", "https://www.sciencedaily.com/rss/top/science.xml")],
        "World": [("BBC", BBC.format("science_and_environment")), ("The Guardian", GUA.format("science")), ("New Scientist", "https://www.newscientist.com/feed/home/"), ("DW", "https://rss.dw.com/rdf/rss-en-sci")],
        "Brazil": [("g1", G1.format("ciencia")), ("Folha de S.Paulo", FOLHA.format("ciencia")), ("Jornal da USP", "https://jornal.usp.br/feed/"), ("Pesquisa FAPESP", "https://revistapesquisa.fapesp.br/feed/")],
    }),
    "health": ("Health", {
        "US": [("The New York Times", NYT + "Health.xml"), ("NPR", NPR.format(1128)), ("CBS News", CBS.format("health")), ("ABC News", ABC.format("healthheadlines")), ("Fox News", FOX.format("health"))],
        "World": [("BBC", BBC.format("health")), ("The Guardian", GUA.format("society/health")), ("WHO", "https://www.who.int/rss-feeds/news-english.xml")],
        "Brazil": [("g1", G1.format("saude")), ("Folha de S.Paulo", FOLHA.format("equilibrioesaude")), ("CNN Brasil", CNNBR.format("saude")), ("g1", G1.format("ciencia-e-saude"))],
    }),
    "sports": ("Sports", {
        "US": [("The New York Times", NYT + "Sports.xml"), ("ESPN", "https://www.espn.com/espn/rss/news"), ("CBS Sports", "https://www.cbssports.com/rss/headlines/"), ("Fox News", FOX.format("sports"))],
        "World": [("BBC", "https://feeds.bbci.co.uk/sport/rss.xml"), ("The Guardian", GUA.format("sport")), ("ESPN", "https://www.espn.com/espn/rss/soccer/news")],
        "Brazil": [("ge", "https://ge.globo.com/rss/ge/"), ("Folha de S.Paulo", FOLHA.format("esporte")), ("CNN Brasil", CNNBR.format("esportes")), ("UOL", "https://rss.uol.com.br/feed/esporte.xml")],
    }),
    "culture": ("Culture", {
        "US": [("The New York Times", NYT + "Arts.xml"), ("The New York Times", NYT + "Books.xml"), ("NPR", NPR.format(1008)), ("CBS News", CBS.format("entertainment"))],
        "World": [("BBC", BBC.format("entertainment_and_arts")), ("The Guardian", GUA.format("culture")), ("The Guardian", GUA.format("books")), ("DW", "https://rss.dw.com/rdf/rss-en-cul")],
        "Brazil": [("g1", G1.format("pop-arte")), ("Folha de S.Paulo", FOLHA.format("ilustrada")), ("CNN Brasil", CNNBR.format("entretenimento"))],
    }),
    "space": ("Space & Earth", {
        "US": [("NASA", NASA), ("The New York Times", NYT + "Space.xml"), ("The New York Times", NYT + "Climate.xml"), ("Space.com", "https://www.space.com/feeds/all"), ("NPR", NPR.format(1025))],
        "World": [("ESA", "https://www.esa.int/rssfeed/Our_Activities/Space_News"), ("The Guardian", GUA.format("environment")), ("BBC", BBC.format("science_and_environment")), ("Space.com", "https://www.space.com/feeds/all")],
        "Brazil": [("g1", G1.format("natureza")), ("Folha de S.Paulo", FOLHA.format("ambiente")), ("g1", G1.format("ciencia")), ("Jornal da USP", "https://jornal.usp.br/feed/")],
    }),
    "entertainment": ("Entertainment & Curiosities", {
        "US": [("The New York Times", NYT + "Movies.xml"), ("The New York Times", NYT + "Television.xml"), ("Variety", "https://variety.com/feed/"), ("The Hollywood Reporter", "https://www.hollywoodreporter.com/feed/"), ("Smithsonian", "https://www.smithsonianmag.com/rss/latest_articles/")],
        "World": [("The Guardian", GUA.format("film")), ("The Guardian", GUA.format("music")), ("BBC", BBC.format("entertainment_and_arts")), ("Mental Floss", "https://www.mentalfloss.com/rss.xml")],
        "Brazil": [("g1", G1.format("pop-arte")), ("CNN Brasil", CNNBR.format("entretenimento")), ("UOL", "https://rss.uol.com.br/feed/entretenimento.xml"), ("Folha de S.Paulo", FOLHA.format("ilustrada"))],
    }),
}
REGIOES = ("US", "World", "Brazil")
# Todos os veículos (na nota, nenhum nome de veículo aparece no texto).
VEICULOS = {v for _, regioes in TEMAS.values() for feeds in regioes.values() for v, _ in feeds} | {"Fox News", "Reuters", "AP", "Associated Press", "CNN", "BBC"}
# Título que não é notícia: chamada para leitores, galeria de fotos, coluna assinada ("… | Fulano de Tal"), homenagem.
NAO_E_NOTICIA = re.compile(
    r"(send us|your questions|tell us|in pictures|week in images|photos of|^watch|^listen|quiz|crossword|an appreciation|"
    r"\| [A-ZÀ-Ú][a-zà-ú]+ [A-ZÀ-Ú]|what to watch|best of the week|newsletter|^veja (fotos|vídeo)|ao vivo|horóscopo|globoesporte\.com|"
    r"\b\w+ x \w+ - campeonato)",
    re.I,
)
# Link que não é notícia (opinião, ao vivo, vídeo, podcast, quiz, newsletter).
FORA = re.compile(r"/(opinion|opiniao|colunas|blogs?|live|ao-vivo|video|videos|podcasts?|quiz|newsletters?|interactive|crosswords|games)/", re.I)

PARADAS = set(
    """the a an and or but of to in on at for with from by as is are was were be been has have had will would can could
    this that these those it its their his her they he she we you after before over under into about more most new says said
    amid than also just what when where who why how which while there here not no yes may might one two three live updates
    de da do das dos e o a os as em no na nos nas um uma para por com que se ao à é foi são será ser como mais sobre após diz""".split()
)


# ------------------------------------------------------------------ feeds

def baixar(url, limite=3_000_000):
    r = requests.get(url, headers=UA, timeout=40)
    r.raise_for_status()
    return r.content[:limite].decode("utf-8", "replace")


def limpar(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(t or ""))).strip()


CACHE_DE_FEEDS = {}


def itens_do_feed(url, veiculo):
    if url in CACHE_DE_FEEDS:
        return [dict(x, veiculo=veiculo) for x in CACHE_DE_FEEDS[url]]
    try:
        # Os bytes como vieram: o próprio XML diz a codificação (a Folha e o UOL usam ISO-8859-1).
        r = requests.get(url, headers=UA, timeout=40)
        r.raise_for_status()
        raiz = ET.fromstring(r.content[:3_000_000])
    except Exception as e:  # noqa: BLE001
        print(f"  feed falhou: {veiculo} {url}: {str(e)[:120]}", flush=True)
        return []
    itens = []
    for it in raiz.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        d = {"titulo": "", "link": "", "resumo": "", "veiculo": veiculo}
        for f in it:
            t = f.tag.split("}")[-1]
            if t == "title":
                d["titulo"] = limpar(f.text)
            elif t == "link":
                d["link"] = (f.text or f.get("href") or "").strip()
            elif t in ("description", "summary") and not d["resumo"]:
                d["resumo"] = limpar(f.text)[:700]
        if d["titulo"] and d["link"].startswith("https://"):
            itens.append(d)
    print(f"  feed ok: {veiculo} {url} ({len(itens)})", flush=True)
    CACHE_DE_FEEDS[url] = itens
    return [dict(x) for x in itens]


CACHE_DE_MATERIAS = {}
BARREIRA = re.compile(r"(subscribe|subscription|sign in to|log in to|assine|assinante|cookies? (policy|settings)|enable javascript)", re.I)


def texto_da_materia(it, palavras=300):
    """Mais contexto (09/10/2026, pedido do usuário): o começo da matéria original, só para o robô tirar os FATOS. O texto
    do jornal nunca vai para o aluno: o robô reescreve com as próprias palavras (frase copiada é recusada mais adiante).
    Paywall, página de cookies ou texto curto: fica o resumo do feed."""
    link = it["link"]
    if link not in CACHE_DE_MATERIAS:
        texto = ""
        try:
            bruto = baixar(link)
            texto = trafilatura.extract(bruto, include_comments=False, include_tables=False, favor_precision=True) or ""
        except Exception:  # noqa: BLE001
            texto = ""
        texto = " ".join(texto.split())
        if len(texto.split()) < 80 or BARREIRA.search(texto[:600]):
            texto = ""
        CACHE_DE_MATERIAS[link] = texto
    texto = CACHE_DE_MATERIAS[link]
    return " ".join(texto.split()[:palavras]) if texto else ""


def copiada(frase, fatos, tamanho=8):
    """A frase repete 8 palavras seguidas da fonte? Então não foi escrita com as próprias palavras."""
    fonte = " " + " ".join(re.findall(r"[a-z0-9']+", fatos.lower())) + " "
    p = re.findall(r"[a-z0-9']+", frase.lower())
    return any(" " + " ".join(p[i : i + tamanho]) + " " in fonte for i in range(0, max(0, len(p) - tamanho + 1)))


def palavras_chave(texto):
    return {w for w in re.findall(r"[a-zà-ú0-9][a-zà-ú0-9'-]{3,}", texto.lower()) if w not in PARADAS}


def nomes(texto):
    return {n for n in re.findall(r"\b[A-ZÀ-Ú][a-zà-ú]{2,}(?:\s[A-ZÀ-Ú][a-zà-ú]{2,})*", texto) if n.split()[0].lower() not in PARADAS}


# Nomes que aparecem em muitos assuntos diferentes (países, continentes, chefes de governo): sozinhos não juntam notícias.
NOMES_GENERICOS = {
    "brazil", "brasil", "china", "chinese", "europe", "european", "united", "states", "america", "american", "americans",
    "russia", "russian", "ukraine", "israel", "israeli", "gaza", "india", "britain", "british", "france", "french",
    "germany", "german", "japan", "africa", "african", "world", "trump", "lula", "president", "minister", "government",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "washington", "london", "paris",
}


_PERFIL = {}


def perfil(it):
    """Palavras e nomes de um item, calculados uma vez só (o plano compara milhares de pares)."""
    chave = it["link"]
    if chave not in _PERFIL:
        t = it["titulo"] + " " + it["resumo"]
        _PERFIL[chave] = (palavras_chave(t), {p for n in nomes(t) for p in n.split() if len(p) > 3}, t.lower(), palavras_chave(it["titulo"]))
    return _PERFIL[chave]


def mesmo_assunto(a, b):
    """Mesmo acontecimento, não só o mesmo país: nomes específicos em comum, título parecido e boa parte das palavras."""
    ka, na, _, tka = perfil(a)
    kb, _, nb_txt, tkb = perfil(b)
    comuns = ka & kb
    if len(comuns) < 3:
        return False
    nomes_comuns = {p.lower() for p in na if p.lower() in nb_txt}
    especificos = nomes_comuns - NOMES_GENERICOS
    titulos = tka & tkb
    jaccard = len(comuns) / max(1, len(ka | kb))
    return len(nomes_comuns) >= 2 and len(comuns) >= 3 and jaccard >= 0.12 and ((len(especificos) >= 1 and len(titulos) >= 1) or len(titulos) >= 3)


def parecido(a, b):
    """Para não repetir o acontecimento (mais largo que `mesmo_assunto`): o mesmo nome próprio específico e o título parecido."""
    if mesmo_assunto(a, b):
        return True
    _, na, _, tka = perfil(a)
    _, _, nb_txt, tkb = perfil(b)
    especificos = {p.lower() for p in na if p.lower() in nb_txt} - NOMES_GENERICOS
    return len(especificos) >= 2 and len(tka & tkb) >= 2


# ------------------------------------------------------------------ IA

def ia(mensagens, max_tokens=1600, temperatura=0.3):
    corpo = {"messages": mensagens, "temperature": temperatura, "max_tokens": max_tokens, "response_format": {"type": "json_object"}}
    r = requests.post(API, json=corpo, timeout=3000)
    r.raise_for_status()
    conteudo = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", conteudo, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        return {}


SISTEMA = (
    "You are a news editor at Bylinguals, an English school in Brazil. You write short news stories for adult Brazilian learners of English. "
    "Write ONLY in English. Use ONLY facts that appear in the FACTS below. Never add names, numbers, dates, places, quotes, reactions, feelings "
    "or consequences that are not in the FACTS. Background facts may only come from the BACKGROUND section. Write in your own words: never copy "
    "a sentence from the FACTS. Strictly neutral and factual: no opinions, no judging adjectives, no speculation. If sources disagree, say what each source reports."
)

PORTUGUES = re.compile(r"\b(não|são|está|também|após|pessoas|governo|então|foram|ainda|segundo|disse)\b", re.I)


def escrever(fatos, n_every, n_real, extra="", n_glossario=8):
    pedido = (
        f"{fatos}\n\nWrite a JSON object with exactly these keys:\n"
        '- "headline_everyday": a short headline (max 10 words), simple English\n'
        f'- "everyday_sentences": a list of EXACTLY {n_every} sentences for CEFR A2 learners. Each sentence is complete (subject + verb), 8 to 14 words, common words, simple present or simple past.\n'
        '- "headline_real": a headline (max 12 words)\n'
        f'- "real_sentences": a list of EXACTLY {n_real} sentences for CEFR B1 learners, natural English, 12 to 22 words each: the main facts first, then what each source adds, then context from BACKGROUND (if any).\n'
        f'- "glossary": a list of EXACTLY {n_glossario} objects {{"word": an English word or expression that appears in real_sentences, "pt": its meaning in Brazilian Portuguese}}\n'
        'Everything must be in English except the "pt" values. If a FACT is in Portuguese, translate it into English. Do not repeat a fact. '
        f"If there are not enough facts for the number of sentences, write simpler sentences with the same facts: never invent. Return only the JSON.{extra}"
    )
    return ia([{"role": "system", "content": SISTEMA}, {"role": "user", "content": pedido}])


def conferir_frases(fatos, every, real):
    """Segundo passo: cada frase tem base nos fatos? A que não tem sai."""
    lista = [f"E{i + 1}: {f}" for i, f in enumerate(every)] + [f"R{i + 1}: {f}" for i, f in enumerate(real)]
    pedido = (
        f"{fatos}\n\nSENTENCES:\n" + "\n".join(lista) + "\n\n"
        "For each sentence, decide if EVERYTHING it says is stated in the FACTS or BACKGROUND above (simple rewording is fine). "
        "A sentence with any detail, reaction, feeling, consequence, opinion or number that is not in the FACTS or BACKGROUND is NOT supported. "
        'Return JSON: {"unsupported": [the ids of the sentences that are NOT supported, like "E3" or "R5"]}'
    )
    r = ia([{"role": "system", "content": "You are a strict fact-checker. You only answer with JSON."}, {"role": "user", "content": pedido}], max_tokens=300, temperatura=0)
    fora = {str(x).strip().upper() for x in r.get("unsupported", []) if isinstance(x, (str, int))}
    return [f for i, f in enumerate(every) if f"E{i + 1}" not in fora], [f for i, f in enumerate(real) if f"R{i + 1}" not in fora], sorted(fora)


COMUNS = set(
    "The A An I It In On At Of And But Or So If As For To By With From This That These Those He She They We You His Her Their Our "
    "Its There Here When Where What Who Why How Today Yesterday Tomorrow Monday Tuesday Wednesday Thursday Friday Saturday Sunday "
    "January February March April May June July August September October November December Brazil Brazilian Brazilians English "
    "Portuguese People Many Some More Most Also Now Then After Before During Last Next First Second Third One Two Three New However "
    "Meanwhile According Earlier Later Other Others Both Each Every".split()
)


def numeros_e_nomes_inventados(texto, fatos):
    fonte_min = fatos.lower()
    numeros = {re.sub(r"[,.]", "", n) for n in re.findall(r"\d[\d,.]*", fatos)}
    ruins = []
    for n in re.findall(r"\d[\d,.]*", texto):
        limpo = re.sub(r"[,.]", "", n.rstrip(".,"))
        if limpo and limpo not in numeros:
            ruins.append(n)
    for nome in set(re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-zà-ú]+(?:\s[A-Z][a-zà-ú]+)*)", texto, re.M)):
        for p in nome.split():
            if p not in COMUNS and p.lower() not in fonte_min:
                ruins.append(p)
    return ruins


def tirar_inventadas(frases, fatos):
    return [f for f in frases if not numeros_e_nomes_inventados(f, fatos)]


NOTA_PEDIDO = (
    "\nThis is a BRIEF: the main fact first, then the most important details and context from the FACTS. Never name a news outlet or source (do not write 'BBC reported', "
    "'according to', 'Fox News says'). Never comment on what the news shows or means."
)


def produzir(fatos, n_every, n_real, nota=False, veiculos=()):
    base = NOTA_PEDIDO if nota else "\nNever comment on what the news shows or means."
    extra = base
    n_gl = 6 if nota else 8
    for tentativa in range(1, 4):
        d = escrever(fatos, n_every, n_real, extra, n_gl)
        every = [str(x).strip() for x in d.get("everyday_sentences") or [] if str(x).strip()]
        real = [str(x).strip() for x in d.get("real_sentences") or [] if str(x).strip()]
        defeitos = []
        if PORTUGUES.search(" ".join(every + real)):
            defeitos.append("some sentences are in Portuguese")
        if len(every) < n_every - 1 or len(real) < n_real - 1:
            defeitos.append("not enough sentences")
        gl = [g for g in d.get("glossary") or [] if isinstance(g, dict) and g.get("word") and g.get("pt")]
        if len(gl) < (2 if nota else 5):
            defeitos.append("the glossary is empty or too short")
        if not defeitos:
            break
        print(f"    tentativa {tentativa}: {defeitos}", flush=True)
        extra = base + "\nIMPORTANT: your last answer had problems: " + "; ".join(defeitos) + ". Fix them."
    if PORTUGUES.search(" ".join(every + real)):
        return None, "português na saída"
    every = tirar_inventadas(every, fatos)
    real = tirar_inventadas(real, fatos)
    copiadas = [f for f in every + real if copiada(f, fatos)]
    if copiadas:
        print(f"    frase copiada da fonte tirada: {copiadas}", flush=True)
    every = [f for f in every if f not in copiadas]
    real = [f for f in real if f not in copiadas]
    every, fora_e = sem_comentario(every, veiculos, nota)
    real, fora_r = sem_comentario(real, veiculos, nota)
    if fora_e or fora_r:
        print(f"    comentário/veículo tirado: {fora_e + fora_r}", flush=True)
    every, real, fora = conferir_frases(fatos, every, real)
    if fora:
        print(f"    conferência tirou: {fora}", flush=True)
    if (len(every) < 3 or len(real) < 3) if nota else (len(every) < 4 or len(real) < 5):
        return None, f"sobrou pouco depois da conferência ({len(every)}/{len(real)})"
    texto_real = " ".join(real).lower()
    glossario = []
    for g in d.get("glossary") or []:
        if isinstance(g, dict) and g.get("word") and g.get("pt") and str(g["word"]).lower() in texto_real and len(glossario) < 10:
            if not any(x["termo"].lower() == str(g["word"]).lower() for x in glossario):
                glossario.append({"termo": str(g["word"]).strip()[:60], "traducao": str(g["pt"]).strip()[:120]})
    return {
        "tipo": "NOTA" if nota else "COMPLETA",
        "manchete": str(d.get("headline_real") or "").strip()[:160],
        "mancheteEveryday": str(d.get("headline_everyday") or "").strip()[:160],
        "everyday": every[:7] if nota else every[:10],
        "real": real[:8] if nota else real[:12],
        "glossario": glossario,
        "perguntas": gerar_perguntas(every, real, 3),
    }, None


def gerar_perguntas(every, real, quantas):
    """Perguntas de compreensão (09/10/2026): em inglês, sobre os fatos da notícia, respondíveis só com o texto do
    Everyday. Cada uma é conferida: a IA responde de novo lendo só o Everyday; se não acertar, a pergunta sai."""
    simples, completo = " ".join(every), " ".join(real)
    pedido = (
        f"STORY (simple version):\n{simples}\n\nSTORY (full version):\n{completo}\n\n"
        f"Write {quantas + 1} multiple-choice reading comprehension questions in English for adult learners (CEFR A2-B1) about this story.\n"
        "Rules: every question must be answerable using ONLY the simple version. Ask about the main facts: who, what, where, why, "
        "how many, what happened, what will happen. Never ask what a word means. Never ask about something that is not in the story. "
        "Each question has EXACTLY 3 options, short (max 8 words), all about this story's topic and plausible, only one correct. "
        "No 'all of the above' or 'none of the above'.\n"
        'Return JSON: {"questions": [{"question": "...", "options": ["...", "...", "..."], "answer": 0, '
        '"explanation": "one short sentence in Brazilian Portuguese saying where the answer is in the story"}]}'
    )
    try:
        d = ia([{"role": "system", "content": "You write fair reading comprehension questions. You only answer with JSON."}, {"role": "user", "content": pedido}], max_tokens=900, temperatura=0.4)
    except Exception:  # noqa: BLE001
        return []
    boas = []
    for q in d.get("questions") or []:
        if len(boas) >= quantas or not isinstance(q, dict):
            continue
        enunciado = str(q.get("question") or "").strip()
        opcoes = [str(o).strip() for o in q.get("options") or [] if str(o).strip()]
        try:
            certa = int(q.get("answer"))
        except (TypeError, ValueError):
            continue
        if len(enunciado) < 8 or len(opcoes) != 3 or not 0 <= certa < 3 or len({o.lower() for o in opcoes}) != 3:
            continue
        if PORTUGUES.search(enunciado + " " + " ".join(opcoes)) or re.search(r"\bmean(s|ing)?\b", enunciado, re.I):
            continue
        # Conferência: responder lendo só o texto simples. Errou ou ficou em dúvida: a pergunta sai.
        letras = "ABC"
        conferencia = (
            f"TEXT:\n{simples}\n\nQUESTION: {enunciado}\n" + "\n".join(f"{letras[i]}) {o}" for i, o in enumerate(opcoes))
            + '\n\nAnswer using ONLY the TEXT. If the TEXT does not give the answer, say "none". Return JSON: {"answer": "A" or "B" or "C" or "none"}'
        )
        try:
            r = ia([{"role": "system", "content": "You are a careful reader. You only answer with JSON."}, {"role": "user", "content": conferencia}], max_tokens=40, temperatura=0)
        except Exception:  # noqa: BLE001
            continue
        if str(r.get("answer", "")).strip().upper()[:1] != letras[certa]:
            print(f"    pergunta descartada na conferência: {enunciado}", flush=True)
            continue
        # Embaralha as alternativas (a IA quase sempre põe a certa em primeiro), sempre do mesmo jeito para a mesma pergunta.
        rnd = random.Random(int(hashlib.sha1(enunciado.encode()).hexdigest()[:8], 16))
        ordem = list(range(3))
        rnd.shuffle(ordem)
        explicacao = str(q.get("explanation") or "").strip()[:300]
        boas.append({"enunciado": enunciado[:300], "opcoes": [opcoes[i][:200] for i in ordem], "correta": ordem.index(certa), "explicacao": explicacao})
    return boas


# ------------------------------------------------------------------ contexto e tradução

def contexto_wikipedia(fontes):
    """Um parágrafo da Wikipedia sobre o nome que mais se repete nas fontes (só fatos, reescritos pela IA)."""
    contagem = {}
    for f in fontes:
        for n in nomes(f["titulo"] + " " + f["resumo"]):
            if " " in n:
                contagem[n] = contagem.get(n, 0) + 1
    for nome, vezes in sorted(contagem.items(), key=lambda x: -x[1])[:3]:
        if vezes < 2:
            break
        try:
            d = requests.get("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(nome.replace(" ", "_")), headers=UA, timeout=20).json()
            if d.get("type") == "standard" and d.get("extract"):
                return f"BACKGROUND (Wikipedia, {d.get('title', nome)}): {d['extract'][:900]}"
        except Exception:  # noqa: BLE001
            continue
    return ""


TRADUTOR = {}


def tradutor():
    if "m" not in TRADUTOR:
        import torch
        from transformers import MarianMTModel, MarianTokenizer

        torch.set_num_threads(os.cpu_count() or 4)
        nome = "Helsinki-NLP/opus-mt-tc-big-en-pt"
        tok = MarianTokenizer.from_pretrained(nome)
        mod = MarianMTModel.from_pretrained(nome).eval()
        vocab = tok.get_vocab()
        prefixo = next((p for p in (">>pob<<", ">>por<<", ">>pt_br<<", ">>pt<<") if p in vocab), "")
        TRADUTOR.update(m=mod, tok=tok, prefixo=prefixo, torch=torch)
    return TRADUTOR


def traduzir(textos, lote=12):
    if not textos:
        return []
    t = tradutor()
    saida = []
    for i in range(0, len(textos), lote):
        pedaco = [(t["prefixo"] + " " + x).strip() for x in textos[i : i + lote]]
        entrada = t["tok"](pedaco, return_tensors="pt", padding=True, truncation=True, max_length=256)
        with t["torch"].inference_mode():
            gerado = t["m"].generate(**entrada, num_beams=2, max_new_tokens=256)
        saida += [x.strip() for x in t["tok"].batch_decode(gerado, skip_special_tokens=True)]
    return saida


# ------------------------------------------------------------------ Portal

def token():
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=bylinguals-portal"
    r = requests.get(url, headers={"Authorization": "bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]}, timeout=30)
    r.raise_for_status()
    return r.json()["value"]


def portal(metodo, caminho, corpo=None):
    for i in range(3):
        try:
            r = requests.request(metodo, SITE + caminho, json=corpo, headers={"Authorization": "Bearer " + token(), "User-Agent": AGENTE}, timeout=120)
            if r.status_code >= 500 and i < 2:
                time.sleep(15 * (i + 1))
                continue
            return r
        except requests.RequestException:
            if i == 2:
                raise
            time.sleep(15 * (i + 1))


def aviso(msg):
    print(f"::notice title=Jornal::{msg}", flush=True)


# ------------------------------------------------------------------ edição

def itens_da_regiao(feeds):
    """Os itens de todos os feeds de uma região, com a posição no feed (o que o jornal pôs em cima vem primeiro)."""
    vistos, itens = set(), []
    for veiculo, url in feeds:
        if url == NASA:
            continue
        for posicao, it in enumerate(itens_do_feed(url, veiculo)):
            if it["link"] in vistos or FORA.search(it["link"]) or NAO_E_NOTICIA.search(it["titulo"]) or len(it["resumo"].split()) < 12:
                continue
            vistos.add(it["link"])
            it["posicao"] = posicao
            itens.append(it)
    return sorted(itens, key=lambda x: x["posicao"])


def apoio_da_regiao(regiao):
    """Todos os feeds da região, de todos os temas: o mesmo acontecimento pode estar na seção de outro jornal."""
    feeds, vistos = [], set()
    for _, regioes in TEMAS.values():
        for veiculo, url in regioes[regiao]:
            if url not in vistos:
                vistos.add(url)
                feeds.append((veiculo, url))
    return itens_da_regiao(feeds)


def grupos_da_regiao(itens, quantos, apoio_extra=()):
    """Notícias completas: o mesmo acontecimento em 3+ veículos (uma fonte por veículo, até 6). O assunto vem dos feeds
    do tema; as outras fontes podem vir de qualquer feed da região."""
    grupos, usados = [], set()
    pool = list(itens) + [x for x in apoio_extra if x["link"] not in {i["link"] for i in itens}]
    for p in itens:
        if len(grupos) >= quantos or p["link"] in usados or any(parecido(p, f) for g in grupos for f in g["fontes"]):
            continue
        apoio = [it for it in pool if it["link"] != p["link"] and it["link"] not in usados and mesmo_assunto(p, it)]
        escolhidas, vistos = [p], {p["veiculo"]}
        for a in apoio:
            if a["veiculo"] not in vistos and len(escolhidas) < 6:
                escolhidas.append(a)
                vistos.add(a["veiculo"])
        if len(vistos) < 3:
            continue
        for f in escolhidas:
            usados.add(f["link"])
        # O que também fala do mesmo assunto não vira nota repetida.
        for a in apoio:
            usados.add(a["link"])
        grupos.append({"fontes": escolhidas})
    return grupos, usados


def notas_da_regiao(itens, usados, quantos):
    """Notas curtas: um veículo, assuntos diferentes entre si e das completas, alternando os veículos."""
    escolhidas, por_veiculo = [], {}
    for it in sorted(itens, key=lambda x: (x["posicao"], por_veiculo.get(x["veiculo"], 0))):
        if len(escolhidas) >= quantos:
            break
        if it["link"] in usados or any(parecido(it, e) for e in escolhidas) or por_veiculo.get(it["veiculo"], 0) >= 2:
            continue
        por_veiculo[it["veiculo"]] = por_veiculo.get(it["veiculo"], 0) + 1
        escolhidas.append(it)
    return escolhidas


def da_nasa():
    for it in itens_do_feed(NASA, "NASA")[:6]:
        try:
            bruto = baixar(it["link"])
        except Exception:  # noqa: BLE001
            continue
        texto = trafilatura.extract(bruto) or ""
        if len(texto.split()) < 200:
            continue
        meta = trafilatura.extract_metadata(bruto)
        imagem = None
        if meta and meta.image and re.match(r"https://([a-z0-9-]+\.)*nasa\.gov/", meta.image):
            imagem = {"url": meta.image, "credito": "NASA"}
        return {"fontes": [{**it, "resumo": " ".join(texto.split()[:900])}], "imagem": imagem, "nasa": True}
    return None


# Esporte, cultura e entretenimento: o nome que mais se repete costuma ser time, filme ou artista, e a Wikipedia trazia
# fato que não tinha nada a ver ("Tampa Bay is a large natural harbor…", 09/10/2026). Nesses temas, sem fundo.
SEM_WIKIPEDIA = {"sports", "culture", "entertainment"}

# Comentário do robô ("this news shows how…") nunca entra; na nota, nome de veículo também não ("The BBC reported…").
COMENTARIO = re.compile(r"\b(this (news|story|report)|the news (shows|is)|which shows|shows how|it is (important|interesting)|we can see)\b", re.I)
ATRIBUICAO = re.compile(r"\b(reported|reports|according to|says that|said that|news outlet|newspaper)\b", re.I)


def sem_comentario(frases, veiculos, nota):
    fora = []
    saida = []
    for f in frases:
        ruim = COMENTARIO.search(f) or (nota and (ATRIBUICAO.search(f) or any(v.lower() in f.lower() for v in veiculos)))
        (fora if ruim else saida).append(f)
    return saida, fora


def escrever_noticia(g, nota):
    principal = g["fontes"][0]
    if g.get("nasa"):
        fatos = "FACTS:\n" + "\n".join(f"- {f['veiculo']}: {f['titulo']}. {f['resumo']}" for f in g["fontes"])
    else:
        # Nota: uma fonte, até 500 palavras da matéria. Completa: até 300 palavras de cada uma das 4 primeiras fontes.
        linhas = []
        for k, f in enumerate(g["fontes"]):
            corpo = texto_da_materia(f, 500 if nota else 300) if (nota or k < 4) else ""
            linhas.append(f"- {f['veiculo']}: {f['titulo']}. {corpo or f['resumo']}")
        fatos = "FACTS:\n" + "\n".join(linhas)
    if not nota and not g.get("nasa") and TEMA not in SEM_WIKIPEDIA:
        fundo = contexto_wikipedia(g["fontes"])
        if fundo:
            fatos += "\n\n" + fundo
    palavras = len(fatos.split())
    # Mais texto para o aluno ler e responder (09/10/2026): nota com 5-6 frases; completa com 7-10.
    if nota:
        n_every, n_real = (5, 6) if palavras >= 120 else (4, 4)
    else:
        n_every, n_real = (8, 10) if g.get("nasa") or palavras >= 400 else ((7, 9) if palavras >= 180 else (5, 6))
    try:
        n, motivo = produzir(fatos, n_every, n_real, nota=nota, veiculos={f["veiculo"] for f in g["fontes"]} | VEICULOS)
    except Exception as e:  # noqa: BLE001
        n, motivo = None, f"erro: {e}"
    if not n or not n["manchete"] or not n["mancheteEveryday"]:
        return None, motivo or "sem manchete"
    n["fontes"] = [{"veiculo": f["veiculo"], "titulo": f["titulo"][:300], "link": f["link"]} for f in g["fontes"]]
    n["linkPrincipal"] = principal["link"]
    n["imagem"] = g.get("imagem")
    return n, None


# Ordem em que os temas escolhem as notícias: do mais específico ao mais geral. Assim o furacão fica em Space & Earth e
# não em Politics, e o mesmo acontecimento nunca sai em dois temas. Todo robô calcula o mesmo plano (os mesmos feeds),
# e cada um escreve só o seu tema.
ORDEM_DE_ESCOLHA = ["space", "science", "health", "sports", "entertainment", "culture", "economy", "business", "politics"]
# Fora de Politics & Elections, assunto de eleição e de governo não entra como notícia do tema.
ELEICAO = re.compile(
    r"(elei[çc]|eleitor|\bturno\b|candidat|campanha|pesquisa (eleitoral|datafolha|quaest|atlas)|\blula\b|bolsonaro|\btse\b|\bstf\b|"
    r"midterm|election|campaign|ballot|\bsenate\b|\bcongress\b|governor|governador|deputad|senador|centr[ãa]o)",
    re.I,
)


# Região Brazil: feed brasileiro também traz notícia de fora ("Trump demite diretora do Fed", g1, 09/10/2026). Fica de fora
# o que fala de outro país sem nada do Brasil.
DE_FORA = re.compile(
    r"\b(trump|biden|eua|estados unidos|washington|casa branca|israel|gaza|hamas|ucr[âa]ni|r[úu]ssia|putin|china|chin[êe]s|xi jinping|"
    r"jap[ãa]o|[íi]ndia|europa|uni[ãa]o europeia|reino unido|londres|fran[çc]a|alemanha|ir[ãa]\b|venezuela|maduro|argentin|milei|"
    r"m[ée]xico|canad[áa]|vaticano|papa le[ãa]o|otan|onu)",
    re.I,
)
DO_BRASIL = re.compile(
    r"(brasil|lula|bolsonaro|\bstf\b|\btse\b|congresso|c[âa]mara|senado|planalto|petrobras|banco central|\bibge\b|\bsus\b|"
    r"itamaraty|haddad|alckmin|tarc[íi]sio|s[ãa]o paulo|rio de janeiro|minas gerais|bahia|paran[áa]|pernambuco|cear[áa]|rio grande|"
    r"santa catarina|goi[áa]s|amazonas|amaz[ôo]nia|bras[íi]lia|belo horizonte|salvador|recife|fortaleza|curitiba|porto alegre|manaus|bel[ée]m|"
    r"flamengo|palmeiras|corinthians|s[ãa]o paulo fc|santos|vasco|botafogo|fluminense|gr[êe]mio|internacional|cruzeiro|atl[ée]tico|brasileir[ãa]o|sele[çc][ãa]o)",
    re.I,
)


def do_brasil(it):
    texto = it["titulo"] + " " + it["resumo"][:300]
    return not DE_FORA.search(texto) or bool(DO_BRASIL.search(texto))


def planejar():
    """Para cada tema e região: as notícias completas (grupos de 3+ veículos) e as candidatas a nota, sem repetir assunto."""
    escolhidos = []  # itens já usados por algum tema (para não repetir o acontecimento)
    links_usados = set()
    plano = {}

    def repetido(it):
        return it["link"] in links_usados or any(parecido(it, e) for e in escolhidos)

    for chave in ORDEM_DE_ESCOLHA:
        nome, regioes = TEMAS[chave]
        plano[chave] = {}
        for regiao in REGIOES:
            itens = [it for it in itens_da_regiao(regioes[regiao]) if chave == "politics" or not ELEICAO.search(it["titulo"] + " " + it["resumo"][:200])]
            if regiao == "Brazil":
                itens = [it for it in itens if do_brasil(it)]
            livres = [it for it in itens if not repetido(it)]
            grupos, usados = grupos_da_regiao(livres, POR_REGIAO, apoio_da_regiao(regiao))
            for g in grupos:
                escolhidos.extend(g["fontes"])
                links_usados.update(f["link"] for f in g["fontes"])
            links_usados.update(usados)
            notas = notas_da_regiao([it for it in livres if not repetido(it)], usados, POR_REGIAO * 2)
            for it in notas[:POR_REGIAO]:
                escolhidos.append(it)
                links_usados.add(it["link"])
            plano[chave][regiao] = {"grupos": grupos, "notas": notas, "nasa": any(url == NASA for _, url in regioes[regiao])}
    return plano


def so_feeds():
    """Teste rápido (sem IA): quantos itens cada feed traz e quantas notícias completas e notas sairiam."""
    linhas = ["# Feeds do Jornal", ""]
    plano = planejar()
    for chave, (nome, _) in TEMAS.items():
        for regiao in REGIOES:
            p = plano[chave][regiao]
            grupos = p["grupos"]
            notas = p["notas"][: max(0, POR_REGIAO - len(grupos))]
            linhas.append(f"- **{nome} · {regiao}**: {len(grupos)} completas, {len(notas)} notas")
            for g in grupos:
                linhas.append(f"  - COMPLETA ({', '.join(f['veiculo'] for f in g['fontes'])}): {g['fontes'][0]['titulo'][:110]}")
            for it in notas:
                linhas.append(f"  - NOTA ({it['veiculo']}): {it['titulo'][:110]}")
    texto = "\n".join(linhas)
    print(texto, flush=True)
    os.makedirs("saida", exist_ok=True)
    with open("saida/feeds.md", "w", encoding="utf-8") as f:
        f.write(texto)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(texto)
    return 0


def main():
    if SO_FEEDS:
        return so_feeds()
    if TEMA not in TEMAS:
        print(f"TEMA inválido: {TEMA!r}. Use um de: {', '.join(TEMAS)}", flush=True)
        return 1
    nome, regioes = TEMAS[TEMA]
    hoje = datetime.now(timezone(timedelta(hours=-3))).date().isoformat()
    dia = os.environ.get("DIA") or hoje
    print(f"Edição de {dia} · {nome} ({MODELO})", flush=True)
    if not SECO and not FORCAR:
        r = portal("GET", f"/api/robo/jornal?dia={dia}&tema={urllib.parse.quote(nome)}")
        if r is not None and r.status_code == 200 and r.json().get("existe"):
            aviso(f"{nome}: já está no Portal ({r.json().get('noticias')} notícias). Nada a fazer.")
            return 0

    plano = planejar()[TEMA]
    noticias, descartadas = [], []
    for regiao in REGIOES:
        grupos = plano[regiao]["grupos"]
        if plano[regiao]["nasa"]:
            nasa = da_nasa()
            if nasa:
                grupos = [nasa] + grupos[: POR_REGIAO - 1]
        feitas = 0
        for g in grupos:
            if feitas >= POR_REGIAO or time.time() - INICIO > LIMITE_S:
                break
            print(f"[{regiao}] COMPLETA: {g['fontes'][0]['titulo']} ({len(g['fontes'])} fontes)", flush=True)
            t0 = time.time()
            n, motivo = escrever_noticia(g, nota=False)
            if n:
                n.update(secao=nome, regiao=regiao)
                noticias.append(n)
                feitas += 1
                print(f"  ok em {round(time.time() - t0)} s", flush=True)
            else:
                descartadas.append(f"[{regiao}] {g['fontes'][0]['titulo']} ({motivo})")
                print(f"  descartada: {motivo}", flush=True)
        # O que faltou para chegar a POR_REGIAO vira nota curta (com folga, porque a conferência pode tirar alguma).
        for it in plano[regiao]["notas"]:
            if feitas >= POR_REGIAO or time.time() - INICIO > LIMITE_S:
                break
            print(f"[{regiao}] NOTA: {it['titulo']} ({it['veiculo']})", flush=True)
            n, motivo = escrever_noticia({"fontes": [it]}, nota=True)
            if n:
                n.update(secao=nome, regiao=regiao)
                noticias.append(n)
                feitas += 1
            else:
                descartadas.append(f"[{regiao}] nota: {it['titulo']} ({motivo})")
                print(f"  descartada: {motivo}", flush=True)

    if not noticias:
        aviso(f"{nome}: nenhuma notícia passou na conferência hoje.")
        return 1
    print("Traduzindo…", flush=True)
    for n in noticias:
        n["everydayPt"] = traduzir(n["everyday"])
        n["realPt"] = traduzir(n["real"])
    edicao = {"dia": dia, "modelo": MODELO, "temas": [nome], "noticias": noticias}
    os.makedirs("saida", exist_ok=True)
    with open("saida/edicao.json", "w", encoding="utf-8") as f:
        json.dump({**edicao, "descartadas": descartadas}, f, ensure_ascii=False, indent=1)
    resumo = [f"# {nome} — {dia}", "", f"{len(noticias)} notícias · {len(descartadas)} descartadas", ""]
    for n in noticias:
        resumo += [f"## [{n['regiao']} · {n['tipo']}] {n['manchete']}", " ".join(n["real"]), "", "Fontes: " + ", ".join(f["veiculo"] for f in n["fontes"]), ""]
    resumo += ["## Descartadas", *[f"- {d}" for d in descartadas]]
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(resumo))
    with open("saida/edicao.md", "w", encoding="utf-8") as f:
        f.write("\n".join(resumo))
    if SECO:
        aviso(f"Teste seco ({nome}): {len(noticias)} notícias escritas, nada entregue.")
        return 0
    r = portal("POST", "/api/robo/jornal", edicao)
    aviso(f"{nome} → Portal: HTTP {r.status_code} {r.text[:300]}")
    return 0 if r.status_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
