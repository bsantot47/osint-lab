"""Toutes les captures Wayback Machine d'une URL ou d'un domaine (API CDX).

API officielle et publique d'Internet Archive, sans clé. Un seul appel par
exécution, via ClientHTTP (journal, délai, robots.txt revérifié si une
redirection sort de archive.org).

Par défaut, les captures au contenu identique sont regroupées
(collapse=digest) : chaque ligne est une version différente de la page.
Sorties : la réponse brute (JSON) et une liste Markdown des captures, dont
chaque lien peut servir de source à un fait (`ajouter source`).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime

from core.rapport import echapper_md, lien_md
from core.requetes import ClientHTTP
from core.stockage import domaine_enregistrable
from outils.commun import ErreurOutil, Resultat, controler_cible, fichier_brut

NOM = "archives"
TYPES = ("domaine",)

API_CDX = "https://web.archive.org/cdx/search/cdx"
CHAMPS = ("timestamp", "original", "statuscode", "mimetype", "digest")
LIMITE_MAX = 10000


def options(parser) -> None:
    parser.add_argument("--url", help="page précise du domaine (défaut : le domaine lui-même)")
    parser.add_argument("--domaine-entier", action="store_true",
                        help="toutes les URL du domaine et de ses sous-domaines")
    parser.add_argument("--depuis", metavar="AAAA", help="année de début")
    parser.add_argument("--jusqua", metavar="AAAA", help="année de fin")
    parser.add_argument("--toutes", action="store_true",
                        help="garder les captures identiques (pas de regroupement)")
    parser.add_argument("--limite", type=int, default=500,
                        help=f"nombre maximal de captures (défaut : 500, max : {LIMITE_MAX})")


def lien_capture(timestamp: str, original: str) -> str:
    return f"https://web.archive.org/web/{timestamp}/{original}"


def _date(timestamp: str) -> datetime:
    return datetime.strptime(timestamp[:14].ljust(14, "0"), "%Y%m%d%H%M%S")


def parametres(entite, opts) -> tuple[str, dict[str, str]]:
    """Cible et paramètres CDX, après contrôle qu'on reste dans le domaine de l'entité."""
    domaine = entite["valeur"].strip().lower()
    cible = opts.url or domaine
    if domaine_enregistrable(cible) != domaine_enregistrable(domaine):
        raise ErreurOutil(f"{cible} n'appartient pas au domaine {domaine} de l'entité"
                          f" E{entite['id']} : ajouter d'abord ce domaine comme entité")
    if opts.domaine_entier and opts.url:
        raise ErreurOutil("--url et --domaine-entier sont incompatibles")
    if not 1 <= opts.limite <= LIMITE_MAX:
        raise ErreurOutil(f"--limite doit être entre 1 et {LIMITE_MAX}")
    params = {"url": domaine if opts.domaine_entier else cible, "output": "json",
              "fl": ",".join(CHAMPS), "limit": str(opts.limite)}
    if opts.domaine_entier:
        params["matchType"] = "domain"
    if not opts.toutes:
        params["collapse"] = "digest"
    for cle, annee in (("from", opts.depuis), ("to", opts.jusqua)):
        if annee:
            if not (annee.isdigit() and len(annee) == 4):
                raise ErreurOutil(f"année invalide : {annee}")
            params[cle] = annee
    return params["url"], params


def analyser(donnees) -> list[dict[str, str]]:
    """Lignes CDX (première ligne = en-têtes) → dictionnaires."""
    if not donnees:
        return []
    if not isinstance(donnees, list) or not all(isinstance(l, list) for l in donnees):
        raise ErreurOutil("réponse CDX inattendue")
    entetes, *lignes = donnees
    return [dict(zip(entetes, l)) for l in lignes if len(l) == len(entetes)]


def lancer(enquete, entite, opts, maintenant: datetime | None = None,
           transport=None, limiteur=None) -> Resultat:
    maintenant = maintenant or datetime.now()
    entite = controler_cible(enquete, entite, TYPES)
    cible, params = parametres(entite, opts)
    client = ClientHTTP(enquete.stockage, enquete.id, NOM, transport=transport,
                        limiteur=limiteur)
    portee = "domaine entier" if opts.domaine_entier else "URL"
    donnees = client.get_json(API_CDX, params=params, verifier_robots=False,
                              requete=f"captures Wayback de {cible} ({portee})")
    captures = analyser(donnees)

    brut = fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], "json", maintenant)
    brut.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")

    titre = f"{len(captures)} capture(s) de {cible}" + (
        " (versions distinctes)" if not opts.toutes else "")
    md = [f"# Archives — E{entite['id']} {echapper_md(cible)}", "",
          f"Interrogé le {maintenant:%d/%m/%Y %H:%M} ({portee}). {echapper_md(titre)}."]
    lignes = [titre]
    if captures:
        dates = [_date(c["timestamp"]) for c in captures]
        par_annee = Counter(d.year for d in dates)
        resume_annees = ", ".join(f"{a} : {n}" for a, n in sorted(par_annee.items()))
        premiere, derniere = min(captures, key=lambda c: c["timestamp"]), \
            max(captures, key=lambda c: c["timestamp"])
        lignes += [f"  première : {_date(premiere['timestamp']):%d/%m/%Y} "
                   f"{lien_capture(premiere['timestamp'], premiere['original'])}",
                   f"  dernière : {_date(derniere['timestamp']):%d/%m/%Y} "
                   f"{lien_capture(derniere['timestamp'], derniere['original'])}",
                   f"  par année : {resume_annees}"]
        if len(captures) >= opts.limite:
            lignes.append(f"  limite de {opts.limite} atteinte : resserrer avec --depuis/"
                          f"--jusqua ou relever --limite")
        md += ["", f"Par année : {resume_annees}.", "", "| Date | Statut | Type | Capture |",
               "|---|---|---|---|"]
        for c, d in sorted(zip(captures, dates), key=lambda x: x[0]["timestamp"]):
            md.append(f"| {d:%d/%m/%Y %H:%M} | {echapper_md(c.get('statuscode', ''))} | "
                      f"{echapper_md(c.get('mimetype', ''))} | "
                      f"{lien_md(lien_capture(c['timestamp'], c['original']))} |")
    chemin = fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], "md", maintenant)
    chemin.write_text("\n".join(md) + "\n", encoding="utf-8")
    lignes += [f"Liste enregistrée : {chemin}",
               "Pour citer une capture : ajouter source <fait> <lien de la capture>"]
    return Resultat(lignes, [brut, chemin])
