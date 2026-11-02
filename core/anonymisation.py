"""Pseudonymisation d'un rapport : valeurs réelles ↔ étiquettes génériques.

Script pur, sans réseau ni IA. Le texte est parcouru en une seule passe : une
étiquette déjà posée n'est jamais relue, et les formes sont essayées de la plus
longue à la plus courte pour éviter les remplacements partiels.

Une étiquette se pose sur toutes les formes d'une valeur (casse, accents,
échappement Markdown, séparateurs : « Jean Dupont », « JEAN DUPONT »,
« jean\\_dupont », « Dupont Jean »). Seule la forme canonique figure dans la
table ; les autres sont notées avec leur rang d'occurrence, ce qui rend la
restitution exacte tant que le rapport anonymisé n'a pas été modifié. S'il l'a
été (empreinte différente), chaque étiquette reprend la forme canonique.

Les étiquettes sont ensuite cherchées à l'identique, casse comprise :
« une personne a signé » n'est pas « PERSONNE A ».

Les URL sont remplacées en entier par la nature de la source, numérotée s'il en
existe plusieurs de même nature, pour rester réversible.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import count
from string import ascii_uppercase
from typing import Iterable, Iterator
from urllib.parse import urlsplit

from core.rapport import TITRE_RAPPORT, echapper_md


class ErreurAnonymisation(Exception):
    """Anonymisation ou restitution impossible (message destiné à l'utilisateur)."""


# Type d'entité → préfixe, et numérotation par lettres (sinon par chiffres).
ETIQUETTES = {
    "personne": ("PERSONNE", True), "compte": ("PSEUDO", True), "email": ("EMAIL", True),
    "entreprise": ("ENTREPRISE", False), "domaine": ("DOMAINE", False),
    "numero": ("NUMERO", False), "document": ("DOCUMENT", False), "image": ("IMAGE", False),
}
TYPE_DOSSIER = "enquete"

# Hôte (ou domaine parent) → nature de la source ; la première ligne qui
# correspond l'emporte. Pour ajouter un site, ajouter une ligne.
NATURES = (
    ("fiche Pappers", ("pappers.fr",)),
    ("fiche Societe.com", ("societe.com",)),
    ("archive web", ("archive.org", "archive.ph", "archive.today", "archive.is")),
    ("registre officiel", ("annuaire-entreprises.data.gouv.fr",
                           "recherche-entreprises.api.gouv.fr", "infogreffe.fr", "inpi.fr",
                           "bodacc.fr", "sirene.fr", "insee.fr", "nic.fr", "iana.org")),
    ("compte public", ("linkedin.com", "x.com", "twitter.com", "facebook.com", "instagram.com",
                       "tiktok.com", "youtube.com", "github.com", "gitlab.com", "reddit.com",
                       "mastodon.social", "bsky.app", "threads.net", "viadeo.com")),
    ("article de presse", ("lemonde.fr", "lefigaro.fr", "liberation.fr", "leparisien.fr",
                           "ouest-france.fr", "lesechos.fr", "latribune.fr", "mediapart.fr",
                           "francetvinfo.fr", "20minutes.fr", "bfmtv.com", "afp.com",
                           "reuters.com", "bbc.co.uk", "bbc.com", "nytimes.com")),
    ("document officiel", ("gouv.fr", "legifrance.fr", "service-public.fr", "europa.eu")),
)
# Aucune nature connue : ne pas en affirmer une fausse.
NATURE_DEFAUT = "source web"
TOUTES_NATURES = tuple(n for n, _ in NATURES) + (NATURE_DEFAUT,)

FORMES_JURIDIQUES = {"sa", "sas", "sasu", "sarl", "eurl", "sci", "snc", "scop", "scp", "selarl",
                     "ei", "eirl", "earl", "gmbh", "ltd", "inc", "llc"}
# Trop courants pour désigner une entité à eux seuls.
MOTS_GENERIQUES = FORMES_JURIDIQUES | {
    "conseil", "conseils", "groupe", "societe", "services", "service", "france", "international",
    "holding", "compagnie", "entreprise", "association", "com", "org", "net", "www", "gmail",
    "outlook", "hotmail", "yahoo", "orange", "free", "les", "des", "and", "the",
}
LONGUEUR_FRAGMENT_MIN = 3

_AVANT = r"(?<![^\W_])"
_APRES = r"(?![^\W_])"
_SEPARATEUR = r"(?:\\?[\s\-_.+'’()&/,]|%20)*"
_SEPARATEUR_NUMERO = r"(?:\\?[\s.\-()/])*"
_PREFIXE_FR = r"(?:\\?\+33|0033|0)"
_DIACRITIQUES = "[\u0300-\u036f]*"
_LIGATURES = {"œ": "oe", "æ": "ae"}

# Lien du rapport (`lien_md`, seul à produire un < non échappé) ou URL dans le texte.
_MOTIF_URL = (r"(?<!\\)<[^<>\s]+>"
              r"|\b(?:https?://|www\.)(?:[^\s<>\\\[\]`|]|\\[_~*+#!])*"
              r"(?:[^\s<>\\\[\]`|.,;:!?'\"]|\\[_~*+#!])")

_ETIQUETTE_QUELCONQUE = re.compile(
    r"\b(?:(?:PERSONNE|PSEUDO|EMAIL) [A-Z]+|(?:ENTREPRISE|DOMAINE|NUMERO|DOCUMENT|IMAGE|DOSSIER)"
    r" \d+)\b|\[(?:" + "|".join(map(re.escape, TOUTES_NATURES)) + r")(?: \d+)?\]")


def _table_accents() -> dict[str, set[str]]:
    table = defaultdict(set)
    for code in range(0xC0, 0x180):
        lettre = chr(code)
        forme = unicodedata.normalize("NFD", lettre)
        if len(forme) > 1 and forme[0].isascii():
            table[forme[0].lower()].add(lettre.lower())
    return table


_ACCENTS = _table_accents()


def _replier(texte: str) -> str:
    """Minuscules sans accents, pour comparer des mots."""
    forme = unicodedata.normalize("NFD", texte.casefold())
    return "".join(c for c in forme if not unicodedata.combining(c))


def _motif_lettre(c: str) -> str:
    c = c.lower()
    if c in _LIGATURES:
        return f"(?:{c}|{_LIGATURES[c]})"
    base = unicodedata.normalize("NFD", c)[0]
    variantes = {c, base} | _ACCENTS.get(base, set())
    if len(variantes) == 1:
        return re.escape(c) + _DIACRITIQUES
    return "[" + "".join(map(re.escape, sorted(variantes))) + "]" + _DIACRITIQUES


def _motif_mot(mot: str) -> str:
    return "".join(_motif_lettre(c) for c in mot)


def _mots(valeur: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFC", valeur))


def _motifs_numero(valeur: str) -> list[tuple[int, str]]:
    chiffres = re.sub(r"\D", "", valeur)
    if len(chiffres) == 10 and chiffres.startswith("0"):
        national = chiffres[1:]
    elif len(chiffres) == 11 and chiffres.startswith("33"):
        national = chiffres[2:]
    elif len(chiffres) == 13 and chiffres.startswith("0033"):
        national = chiffres[4:]
    else:
        return [(len(chiffres), r"(?:\\?\+)?" + _SEPARATEUR_NUMERO.join(chiffres))]
    return [(len(chiffres), _PREFIXE_FR + _SEPARATEUR_NUMERO + _SEPARATEUR_NUMERO.join(national))]


def _sans_forme_juridique(mots: list[str]) -> list[str]:
    reste = list(mots)
    while reste and reste[-1].lower() in FORMES_JURIDIQUES:
        reste.pop()
    while reste and reste[0].lower() in FORMES_JURIDIQUES:
        reste.pop(0)
    return reste


def _motif_domaine(domaine: str) -> str:
    return r"\.".join(_SEPARATEUR.join(map(_motif_mot, _mots(p)))
                      for p in domaine.split(".") if _mots(p))


def _formes(type_: str, valeur: str) -> list[tuple[int, str]]:
    """Formes reconnues d'une valeur : (longueur, motif), la forme entière d'abord."""
    valeur = unicodedata.normalize("NFC", valeur)
    if type_ == "numero" and re.search(r"\d", valeur):
        return _motifs_numero(valeur)
    if type_ == "domaine" and _mots(valeur):
        return [(len(valeur), _motif_domaine(valeur))]
    if type_ == "email" and "@" in valeur:
        local, _, hote = valeur.rpartition("@")
        if _mots(local) and _mots(hote):
            return [(len(valeur), _SEPARATEUR.join(map(_motif_mot, _mots(local)))
                     + r"(?:\s*@\s*|%40)" + _motif_domaine(hote))]
    mots = _mots(valeur)
    if not mots:
        return [(len(valeur), re.escape(echapper_md(valeur)))]
    formes = [(len(valeur), _SEPARATEUR.join(map(_motif_mot, mots)))]
    if type_ == "personne" and len(mots) > 1:
        formes.append((len(valeur), _SEPARATEUR.join(map(_motif_mot, reversed(mots)))))
    if type_ == "entreprise":
        reste = _sans_forme_juridique(mots)
        if reste and reste != mots and not (len(reste) == 1 and _generique(reste[0])):
            longueur = len(" ".join(reste))
            formes.append((longueur, _SEPARATEUR.join(map(_motif_mot, reste))))
    return formes


def _generique(mot: str) -> bool:
    return _replier(mot) in MOTS_GENERIQUES or len(mot) < LONGUEUR_FRAGMENT_MIN or mot.isdigit()


def nature_source(url: str) -> str:
    """Nature d'une source d'après son hôte."""
    url = url.strip("<>").replace("\\", "")
    try:
        hote = (urlsplit(url if "//" in url else "//" + url).hostname or "").lower().rstrip(".")
    except ValueError:
        return NATURE_DEFAUT
    for nature, hotes in NATURES:
        if any(hote == h or hote.endswith("." + h) for h in hotes):
            return nature
    return NATURE_DEFAUT


def _etiquettes(prefixe: str, par_lettres: bool) -> Iterator[str]:
    if not par_lettres:
        yield from (f"{prefixe} {n}" for n in count(1))
    for n in count(1):
        rang, lettres = n, ""
        while rang:
            rang, reste = divmod(rang - 1, 26)
            lettres = ascii_uppercase[reste] + lettres
        yield f"{prefixe} {lettres}"


def _empreinte(texte: str) -> str:
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


# --- table de correspondance ------------------------------------------------------

@dataclass
class Correspondance:
    entites: list[tuple[str, str, str]] = field(default_factory=list)  # étiquette, type, valeur
    sources: dict[str, str] = field(default_factory=dict)              # étiquette → forme brute
    variantes: dict[tuple[str, int], str] = field(default_factory=dict)  # (étiquette, rang) → forme
    empreinte: str = ""

    @property
    def nb_entites(self) -> int:
        return sum(t != TYPE_DOSSIER for _, t, _ in self.entites)

    def canoniques(self) -> dict[str, str]:
        return {**{e: echapper_md(v) for e, _, v in self.entites}, **self.sources}


_SECTION_ENTITES = "## Entités"
_SECTION_SOURCES = "## Sources"
_SECTION_VARIANTES = "## Formes exactes"
_LIGNE_EMPREINTE = re.compile(r"^- \*\*Empreinte du rapport anonymisé\*\* : sha256 `([0-9a-f]{64})`$",
                              re.M)


def _cellule(texte: str) -> str:
    return texte.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n")


def _lire_cellules(ligne: str) -> list[str]:
    morceaux, courant, i = [], "", 0
    corps = ligne.strip().removeprefix("|")
    while i < len(corps):
        c = corps[i]
        if c == "\\" and i + 1 < len(corps):
            courant += {"n": "\n"}.get(corps[i + 1], corps[i + 1])
            i += 2
            continue
        if c == "|":
            morceaux.append(courant.strip(" "))
            courant = ""
        else:
            courant += c
        i += 1
    return morceaux


def ecrire_correspondance(c: Correspondance) -> str:
    def table(entetes: list[str], lignes: list[list[str]]) -> list[str]:
        return (["| " + " | ".join(entetes) + " |", "|" + " --- |" * len(entetes)]
                + ["| " + " | ".join(map(_cellule, l)) + " |" for l in lignes] + [""])

    l = ["# Correspondance des étiquettes", "",
         "> Ce fichier rétablit les identités du rapport anonymisé. "
         "Ne jamais le transmettre avec lui.", "",
         f"- **Empreinte du rapport anonymisé** : sha256 `{c.empreinte}`", "",
         _SECTION_ENTITES, ""]
    l += table(["Étiquette", "Type", "Valeur réelle"], [list(e) for e in c.entites])
    l += [_SECTION_SOURCES, ""]
    l += table(["Étiquette", "Source"], [[e, u] for e, u in c.sources.items()])
    l += [_SECTION_VARIANTES, "",
          "Occurrences écrites autrement que la valeur réelle (casse, séparateurs…).", ""]
    l += table(["Étiquette", "Occurrence", "Forme"],
               [[e, str(r), f] for (e, r), f in c.variantes.items()])
    return "\n".join(l)


def lire_correspondance(texte: str) -> Correspondance:
    empreinte = _LIGNE_EMPREINTE.search(texte)
    if not empreinte or _SECTION_ENTITES not in texte:
        raise ErreurAnonymisation("table de correspondance illisible")
    c = Correspondance(empreinte=empreinte.group(1))
    section, entete_vue = "", 0
    for ligne in texte.splitlines():
        if ligne.startswith("## "):
            section, entete_vue = ligne, 0
            continue
        if not ligne.startswith("|"):
            continue
        entete_vue += 1
        if entete_vue <= 2:
            continue
        cellules = _lire_cellules(ligne)
        try:
            if section == _SECTION_ENTITES:
                etiquette, type_, valeur = cellules
                c.entites.append((etiquette, type_, valeur))
            elif section == _SECTION_SOURCES:
                etiquette, source = cellules
                c.sources[etiquette] = source
            elif section == _SECTION_VARIANTES:
                etiquette, rang, forme = cellules
                c.variantes[(etiquette, int(rang))] = forme
        except ValueError:
            raise ErreurAnonymisation(f"table de correspondance illisible : {ligne}") from None
    return c


# --- anonymiser -------------------------------------------------------------------

@dataclass
class Anonymise:
    texte: str
    correspondance: Correspondance
    fragments: list[str]   # morceaux de noms encore présents : à relire


@dataclass
class _Cible:
    type: str
    valeur: str
    canonique: str
    etiquette: str = ""


def _fragments(texte: str, etiquettes: Iterable[str], valeurs: Iterable[str]) -> list[str]:
    """Mots issus des valeurs réelles encore présents hors étiquettes (« M. Dupont »
    pour « Jean Dupont ») : rien ne permet de décider à la place de l'enquêteur si
    « Blanc » désigne la personne ou la couleur, ils sont donc signalés."""
    for e in sorted(etiquettes, key=len, reverse=True):
        texte = texte.replace(e, " ")
    trouves, vus = [], set()
    for valeur in valeurs:
        for mot in _mots(valeur):
            cle = _replier(mot)
            if cle in vus or _generique(mot):
                continue
            vus.add(cle)
            if re.search(_AVANT + _motif_mot(mot) + _APRES, texte, re.I):
                trouves.append(mot)
    return trouves


def anonymiser(texte: str, entites: Iterable[tuple[str, str]], nom_dossier: str = "") -> Anonymise:
    """`entites` : couples (type, valeur), dans l'ordre d'attribution des étiquettes.

    Seules les entités présentes dans le texte reçoivent une étiquette et figurent
    dans la table, sans trou de numérotation : une entité exclue du rapport ne
    laisse aucune trace. Lève une erreur si la restitution ne redonne pas le texte.
    """
    cibles: list[_Cible] = []
    alternatives: list[tuple[int, int, str]] = []
    groupes: dict[str, _Cible] = {}
    deja_vus: set[str] = set()
    valeurs = []
    for type_, valeur in entites:
        valeurs.append(valeur)
        formes = _formes(type_, valeur)
        if formes[0][1] in deja_vus:     # même valeur à la casse près : même étiquette
            continue
        deja_vus.add(formes[0][1])
        cible = _Cible(type_, valeur, echapper_md(valeur))
        cibles.append(cible)
        for rang, (longueur, motif) in enumerate(formes):
            groupe = f"e{len(groupes)}"
            groupes[groupe] = cible
            alternatives.append((longueur, rang, f"(?P<{groupe}>{_AVANT}{motif}{_APRES})"))
    alternatives.sort(key=lambda a: (-a[0], a[1]))

    parties = [f"(?P<url>{_MOTIF_URL})"] + [a[2] for a in alternatives]
    dossier = None
    if nom_dossier:
        etiquette = next(e for e in _etiquettes("DOSSIER", False) if e not in texte)
        dossier = _Cible(TYPE_DOSSIER, nom_dossier, echapper_md(nom_dossier), etiquette)
        valeurs.append(nom_dossier)
        parties.insert(0, rf"(?P<dossier>(?<=^{re.escape(TITRE_RAPPORT)}){re.escape(dossier.canonique)}$)")
    motif = re.compile("|".join(parties), re.I | re.M)

    # Numérotation parmi les seules entités présentes.
    presentes = {id(groupes[m.lastgroup]) for m in motif.finditer(texte) if m.lastgroup in groupes}
    generateurs: dict[str, Iterator[str]] = {}
    for cible in cibles:
        if id(cible) not in presentes:
            continue
        prefixe, par_lettres = ETIQUETTES[cible.type]
        suite = generateurs.setdefault(prefixe, _etiquettes(prefixe, par_lettres))
        cible.etiquette = next(e for e in suite if e not in texte)

    c = Correspondance()
    occurrences: Counter[str] = Counter()
    urls: dict[str, str] = {}
    natures: dict[str, Iterator[int]] = {}

    def etiquette_source(brut: str) -> str:
        if brut in urls:
            return urls[brut]
        nature = nature_source(brut)
        suite = natures.setdefault(nature, count(1))
        etiquette = next(e for e in (f"[{nature}]" if n == 1 else f"[{nature} {n}]" for n in suite)
                         if e not in texte)
        urls[brut] = etiquette
        c.sources[etiquette] = brut
        return etiquette

    def remplacer(m: re.Match) -> str:
        brut = m.group(0)
        if m.lastgroup == "url":
            return etiquette_source(brut) if ("." in brut or "://" in brut) else brut
        cible = dossier if m.lastgroup == "dossier" else groupes[m.lastgroup]
        occurrences[cible.etiquette] += 1
        if brut != cible.canonique:
            c.variantes[(cible.etiquette, occurrences[cible.etiquette])] = brut
        return cible.etiquette

    resultat = motif.sub(remplacer, texte)
    if dossier and occurrences[dossier.etiquette]:
        c.entites.append((dossier.etiquette, dossier.type, dossier.valeur))
    c.entites += [(x.etiquette, x.type, x.valeur) for x in cibles if id(x) in presentes]
    c.empreinte = _empreinte(resultat)

    if desanonymiser(resultat, c).texte != texte:
        raise ErreurAnonymisation("la restitution ne redonnerait pas le rapport d'origine :"
                                  " rien n'a été écrit")
    return Anonymise(resultat, c, _fragments(resultat, c.canoniques(), valeurs))


# --- désanonymiser ----------------------------------------------------------------

@dataclass
class Restitue:
    texte: str
    exact: bool                # fichier inchangé : chaque forme d'origine est rétablie
    inconnues: list[str]       # étiquettes absentes de la table


def desanonymiser(texte: str, c: Correspondance) -> Restitue:
    canoniques = c.canoniques()
    exact = _empreinte(texte) == c.empreinte
    if canoniques:
        # Fichier inchangé : ses étiquettes sont exactement celles qu'on a posées, parfois
        # collées (entité sans lettre). Sinon, ne pas en voir une dans un mot plus long.
        def motif_etiquette(e: str) -> str:
            if exact or e.startswith("["):
                return re.escape(e)
            return _AVANT + re.escape(e) + _APRES
        motif = re.compile("|".join(motif_etiquette(e)
                                    for e in sorted(canoniques, key=len, reverse=True)))
        occurrences: Counter[str] = Counter()

        def restituer(m: re.Match) -> str:
            e = m.group(0)
            occurrences[e] += 1
            if exact:
                return c.variantes.get((e, occurrences[e]), canoniques[e])
            return canoniques[e]
        restitue = motif.sub(restituer, texte)
        reste = motif.sub(" ", texte)
    else:
        restitue, reste = texte, texte
    return Restitue(restitue, exact, sorted(set(_ETIQUETTE_QUELCONQUE.findall(reste))))
