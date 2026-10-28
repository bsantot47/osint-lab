"""Présence d'un pseudo sur les plateformes : sherlock et maigret, en sous-processus.

Chaque programme est lancé sans shell, avec une liste d'arguments fixée ici ;
l'appel global est journalisé (ligne de commande, code retour, durée), et
stdout/stderr ainsi que les rapports sont rangés dans bruts/.

Options de débit passées aux programmes (ils n'ont pas de délai entre requêtes) :
- sherlock : --timeout (délai max par site) ; sa concurrence n'est pas réglable
  en ligne de commande ;
- maigret : --timeout, --retries, --max-connections (requêtes simultanées,
  20 ici au lieu de 100 par défaut), --top-sites (nombre de sites testés).
Jamais de --tor ni de --proxy (pas de contournement). Pour maigret,
--no-recursion et --no-extracting : pas de recherche en cascade sur d'autres
identifiants, pas d'extraction de données des profils (minimisation).

Aucune entité ni aucun fait n'est créé : un même pseudo sur deux plateformes
peut appartenir à deux personnes. L'outil agrège les comptes trouvés et
propose pour chacun la commande `ajouter fait` avec le profil comme source.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime

from core.rapport import echapper_md, lien_md
from outils.commun import (ErreurOutil, Programme, Resultat, commande, controler_cible,
                           dossier_brut, fichier_brut)

NOM = "pseudo"
TYPES = ("compte",)
PROGRAMMES = ("sherlock", "maigret")
INSTALLATION = {"sherlock": "pipx install sherlock-project (dans la VM)",
                "maigret": "pipx install maigret (dans la VM)"}
PSEUDO_VALIDE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LIGNE_TROUVEE = re.compile(r"^\[\+\]\s*([^:]+?):\s*(https?://\S+)")


def options(parser) -> None:
    parser.add_argument("--programmes", default=",".join(PROGRAMMES),
                        help="sherlock, maigret ou les deux (défaut ; un programme absent"
                             " est alors ignoré)")
    parser.add_argument("--timeout", type=int, default=15,
                        help="délai max par site, en secondes (défaut : 15)")
    parser.add_argument("--connexions", type=int, default=20,
                        help="maigret : requêtes simultanées (défaut : 20)")
    parser.add_argument("--sites", type=int, default=500,
                        help="maigret : nombre de sites les plus fréquentés testés (défaut : 500)")
    parser.add_argument("--duree-max", type=int, default=900,
                        help="durée max de chaque programme, en secondes (défaut : 900)")


def verifier_pseudo(valeur: str) -> str:
    """Refuse tout ce qui pourrait être lu comme une option (« -… ») ou contenir
    des espaces : la valeur est passée telle quelle aux programmes."""
    pseudo = valeur.strip().removeprefix("@")
    if not PSEUDO_VALIDE.match(pseudo):
        raise ErreurOutil(f"pseudo inutilisable par sherlock/maigret : {valeur!r}"
                          " (lettres, chiffres, . _ -, sans espace ni tiret initial)")
    return pseudo


def args_sherlock(pseudo: str, opts, dossier) -> list[str]:
    return [pseudo, "--timeout", str(opts.timeout), "--print-found", "--no-color",
            "--csv", "--folderoutput", str(dossier)]


def args_maigret(pseudo: str, opts, dossier) -> list[str]:
    return [pseudo, "--timeout", str(opts.timeout), "--retries", "1",
            "--max-connections", str(opts.connexions), "--top-sites", str(opts.sites),
            "--no-recursion", "--no-extracting", "--no-color", "--no-progressbar",
            "--json", "simple", "--folderoutput", str(dossier)]


def lire_stdout(texte: str) -> list[tuple[str, str]]:
    """Repli commun : lignes « [+] Site: URL » affichées par les deux programmes."""
    return [(m.group(1).strip(), m.group(2)) for m in map(LIGNE_TROUVEE.match,
                                                         (texte or "").splitlines()) if m]


def lire_sherlock(dossier, pseudo: str, stdout: str) -> list[tuple[str, str]]:
    rapport = dossier / f"{pseudo}.csv"
    if not rapport.is_file():
        return lire_stdout(stdout)
    with open(rapport, newline="", encoding="utf-8") as f:
        return [(l.get("name", ""), l.get("url_user", "")) for l in csv.DictReader(f)
                if l.get("exists") == "Claimed" and l.get("url_user")]


def lire_maigret(dossier, pseudo: str, stdout: str) -> list[tuple[str, str]]:
    rapport = dossier / f"report_{pseudo}_simple.json"
    if not rapport.is_file():
        return lire_stdout(stdout)
    donnees = json.loads(rapport.read_text(encoding="utf-8"))
    trouves = []
    for site, d in donnees.items():
        statut = d.get("status") or {}
        url = d.get("url_user") or statut.get("url")
        if statut.get("status") == "Claimed" and url:
            trouves.append((site, url))
    return trouves


LECTEURS = {"sherlock": (args_sherlock, lire_sherlock),
            "maigret": (args_maigret, lire_maigret)}


def cle_url(url: str) -> str:
    return url.lower().removeprefix("https://").removeprefix("http://") \
        .removeprefix("www.").rstrip("/")


def choisir(texte: str, programmes: dict[str, Programme]) -> tuple[list[str], list[str]]:
    demandes = [p.strip() for p in texte.split(",") if p.strip()]
    if not demandes or any(p not in PROGRAMMES for p in demandes):
        raise ErreurOutil(f"programmes possibles : {', '.join(PROGRAMMES)}")
    absents = [p for p in demandes if not programmes[p].disponible()]
    explicite = texte != ",".join(PROGRAMMES)
    if absents and (explicite or len(absents) == len(demandes)):
        raise ErreurOutil("introuvable : " + " ; ".join(
            f"{p} ({programmes[p].installation})" for p in absents))
    notes = [f"{p} absent, ignoré ({programmes[p].installation})" for p in absents]
    return [p for p in demandes if p not in absents], notes


def lancer(enquete, entite, opts, maintenant: datetime | None = None,
           programmes: dict[str, Programme] | None = None) -> Resultat:
    maintenant = maintenant or datetime.now()
    entite = controler_cible(enquete, entite, TYPES)
    pseudo = verifier_pseudo(entite["valeur"])
    for nom, valeur, mini in (("--timeout", opts.timeout, 1), ("--connexions", opts.connexions, 1),
                              ("--sites", opts.sites, 1), ("--duree-max", opts.duree_max, 10)):
        if valeur < mini:
            raise ErreurOutil(f"{nom} doit valoir au moins {mini}")
    programmes = programmes or {p: Programme(p, INSTALLATION[p]) for p in PROGRAMMES}
    choisis, lignes = choisir(opts.programmes, programmes)
    dossier = dossier_brut(enquete.chemin("bruts"), NOM, entite["id"], maintenant)

    comptes: dict[str, dict] = {}
    reussis = 0
    for nom in choisis:
        construire, lire = LECTEURS[nom]
        sous_dossier = dossier / nom
        sous_dossier.mkdir(exist_ok=True)
        r = programmes[nom].executer(enquete, NOM, construire(pseudo, opts, sous_dossier),
                                     sous_dossier, opts.duree_max)
        trouves = lire(sous_dossier, pseudo, r.stdout)
        if r.returncode != 0 and not trouves:
            lignes.append(f"{nom} : échec (code {r.returncode}), voir {sous_dossier}")
            continue
        reussis += 1
        lignes.append(f"{nom} : {len(trouves)} compte(s) trouvé(s)")
        for site, url in trouves:
            c = comptes.setdefault(cle_url(url), {"site": site, "url": url, "par": set()})
            c["par"].add(nom)

    if not reussis:
        raise ErreurOutil("aucun programme n'a abouti : " + " ; ".join(lignes))

    tries = sorted(comptes.values(), key=lambda c: (-len(c["par"]), c["site"].lower()))
    lignes.append(f"{len(tries)} compte(s) distinct(s) pour « {pseudo} » (homonymes"
                  " possibles : ouvrir chaque profil avant de retenir) :")
    md = [f"# Pseudo — E{entite['id']} « {echapper_md(pseudo)} »", "",
          f"Recherché le {maintenant:%d/%m/%Y %H:%M} avec {', '.join(choisis)}."
          " Présence d'un compte portant ce pseudo, pas preuve d'identité.", "",
          "| Site | Profil | Trouvé par |", "|---|---|---|"]
    for c in tries:
        par = ", ".join(sorted(c["par"]))
        lignes.append(f"  {c['site']} : {c['url']} ({par})")
        md.append(f"| {echapper_md(c['site'])} | {lien_md(c['url'])} | {par} |")
    if tries:
        exemple = tries[0]
        lignes.append("  pour retenir un compte : " + commande(
            "ajouter", "fait", f"Compte {pseudo} présent sur {exemple['site']}",
            "--entite", str(entite["id"]), "--source", exemple["url"], "--outil", NOM))

    synthese = fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], "md", maintenant)
    synthese.write_text("\n".join(md) + "\n", encoding="utf-8")
    lignes.append(f"Synthèse enregistrée : {synthese}")
    return Resultat(lignes, [dossier, synthese])
