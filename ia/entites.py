"""Extraction des noms, organisations, lieux et dates d'une page collectée.

Répartition script / IA :
- script : lecture du fichier, HTML → texte, dates (expressions régulières),
  regroupement, rapprochement avec les fiches existantes (comparaison de noms
  normalisés) ;
- IA (spaCy, modèle français local) : uniquement la reconnaissance des noms de
  personnes, d'organisations et de lieux dans le texte libre.

Le résultat est une liste de propositions avec les commandes `ajouter` prêtes à
copier. Rien n'est écrit dans la base.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable, Mapping

from outils.commun import commande, normaliser

MODELE_DEFAUT = "fr_core_news_md"
INSTALLATION = ("pip install spacy && python -m spacy download fr_core_news_md"
                " (une fois, dans la VM ; ensuite tout est local)")
TAILLE_MAX = 1_000_000            # caractères, limite par défaut de spaCy
CONTEXTE = 60

# Étiquettes du modèle français (il n'a pas d'étiquette DATE : les dates sont
# extraites par script). MISC, trop vague, est ignoré.
ETIQUETTES = {"PER": "personne", "ORG": "organisation", "LOC": "lieu"}
TYPE_ENTITE = {"personne": "personne", "organisation": "entreprise"}

MOIS = ("janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|"
        "octobre|novembre|décembre|decembre")
DATES = re.compile(
    r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{4}"              # 12/03/2021
    r"|\d{4}-\d{2}-\d{2}"                               # 2021-03-12
    rf"|(?:1er|\d{{1,2}})\s+(?:{MOIS})\s+\d{{4}}"       # 12 mars 2021
    rf"|(?:{MOIS})\s+\d{{4}})\b",                       # mars 2021
    re.IGNORECASE)


class ErreurIA(Exception):
    """Analyse impossible (message destiné à l'utilisateur)."""


@dataclass
class Mention:
    type: str                      # personne, organisation, lieu, date
    texte: str
    occurrences: int = 1
    contexte: str = ""
    fiches: list[int] = field(default_factory=list)   # entités existantes rapprochées


# --- texte ---------------------------------------------------------------------

class _ExtracteurTexte(HTMLParser):
    IGNORES = {"script", "style", "noscript", "template", "svg"}
    BLOCS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
             "section", "article", "header", "footer", "td", "th", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.morceaux: list[str] = []
        self.profondeur_ignoree = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORES:
            self.profondeur_ignoree += 1
        elif tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORES and self.profondeur_ignoree:
            self.profondeur_ignoree -= 1
        elif tag in self.BLOCS:
            self.morceaux.append("\n")

    def handle_data(self, data):
        if not self.profondeur_ignoree:
            self.morceaux.append(data)


def html_vers_texte(html: str) -> str:
    p = _ExtracteurTexte()
    p.feed(html)
    p.close()
    lignes = (" ".join(l.split()) for l in "".join(p.morceaux).splitlines())
    return "\n".join(l for l in lignes if l)


def lire_page(chemin: str | Path) -> str:
    chemin = Path(chemin)
    if not chemin.is_file():
        raise ErreurIA(f"fichier introuvable : {chemin}")
    contenu = chemin.read_text(encoding="utf-8", errors="replace")
    if chemin.suffix.lower() in (".html", ".htm", ".xhtml") or "<html" in contenu[:2000].lower():
        return html_vers_texte(contenu)
    return contenu


# --- modèle ------------------------------------------------------------------------

def charger_modele(nom: str = MODELE_DEFAUT) -> Callable:
    """Charge le pipeline spaCy installé localement (aucun téléchargement)."""
    try:
        import spacy
    except ImportError as ex:
        raise ErreurIA(f"spaCy n'est pas installé : {INSTALLATION}") from ex
    try:
        return spacy.load(nom)
    except OSError as ex:
        raise ErreurIA(f"modèle {nom} absent : {INSTALLATION}") from ex


# --- analyse -------------------------------------------------------------------------

def _contexte(texte: str, debut: int, fin: int) -> str:
    extrait = texte[max(0, debut - CONTEXTE):fin + CONTEXTE]
    return " ".join(extrait.split())


def analyser(texte: str, nlp: Callable) -> tuple[list[Mention], bool]:
    """Mentions regroupées par (type, nom normalisé), dans l'ordre d'apparition.
    Renvoie aussi un indicateur de troncature du texte."""
    tronque = len(texte) > TAILLE_MAX
    texte = texte[:TAILLE_MAX]
    trouvees: list[tuple[str, str, int, int]] = []
    for ent in nlp(texte).ents:
        type_ = ETIQUETTES.get(ent.label_)
        nom = " ".join(ent.text.split()).strip(" .,;:'\"«»()")
        if type_ and len(normaliser(nom)) >= 3:
            trouvees.append((type_, nom, ent.start_char, ent.end_char))
    for m in DATES.finditer(texte):
        trouvees.append(("date", m.group(0), m.start(), m.end()))

    mentions: dict[tuple[str, str], Mention] = {}
    for type_, nom, debut, fin in sorted(trouvees, key=lambda t: t[2]):
        cle = (type_, normaliser(nom))
        if cle in mentions:
            mentions[cle].occurrences += 1
        else:
            mentions[cle] = Mention(type_, nom, contexte=_contexte(texte, debut, fin))
    return list(mentions.values()), tronque


def rapprocher(mentions: Iterable[Mention], entites: Iterable[Mapping]) -> None:
    """Rapproche chaque personne/organisation des fiches existantes du même type
    dont tous les mots figurent dans la mention (sans accents ni casse)."""
    entites = list(entites)
    for m in mentions:
        type_entite = TYPE_ENTITE.get(m.type)
        if not type_entite:
            continue
        mots = set(normaliser(m.texte).split())
        m.fiches = [e["id"] for e in entites
                    if e["type"] == type_entite and normaliser(e["valeur"])
                    and set(normaliser(e["valeur"]).split()) <= mots]


def propositions(mentions: Iterable[Mention], entite_source: int | None = None) -> list[str]:
    """Lignes à afficher : mentions par type, fiches rapprochées, commandes."""
    lignes = []
    mentions = list(mentions)
    for type_, titre in (("personne", "Personnes"), ("organisation", "Organisations"),
                         ("lieu", "Lieux"), ("date", "Dates")):
        du_type = [m for m in mentions if m.type == type_]
        if not du_type:
            continue
        lignes.append(f"{titre} ({len(du_type)}) :")
        for m in du_type:
            fois = f" ×{m.occurrences}" if m.occurrences > 1 else ""
            lignes.append(f"  {m.texte}{fois} — « {m.contexte} »")
            if m.fiches:
                lignes.append("    correspond à la fiche " + ", ".join(f"E{i}" for i in m.fiches))
                if entite_source is not None:
                    for i in m.fiches:
                        if i != entite_source:
                            lignes.append("    pour lier : " + commande(
                                "ajouter", "lien", str(entite_source), str(i), "mentionné"))
            elif m.type in TYPE_ENTITE:
                lignes.append("    pour l'ajouter : " + commande(
                    "ajouter", "entite", TYPE_ENTITE[m.type], m.texte))
    if not lignes:
        lignes.append("Aucune mention reconnue.")
    return lignes


def compter(mentions: Iterable[Mention]) -> Counter:
    return Counter(m.type for m in mentions)
