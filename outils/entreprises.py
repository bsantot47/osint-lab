"""Entreprises et mandats : API Recherche d'entreprises (data.gouv) et Pappers.

- Recherche d'entreprises (recherche-entreprises.api.gouv.fr) : API publique
  de l'État, sans clé ; toujours interrogée.
- Pappers (api.pappers.fr) : clé dans config.local.toml ; interrogée seulement
  pour une entreprise identifiée par son SIREN.

Pour une personne : entreprises où un dirigeant porte ce nom. Pour une
entreprise : sa fiche (par SIREN) ou les candidates (par nom).

Aucune entité n'est créée automatiquement : un homonyme est vite arrivé, et
les co-dirigeants sont des tiers. L'outil liste les trouvailles avec les
commandes `ajouter` prêtes à copier ; l'enquêteur choisit ce qui entre.
Les bénéficiaires effectifs, dont l'accès est restreint par la loi, ne sont
jamais lus ni conservés.
"""

from __future__ import annotations

import re
from datetime import datetime

from core.config import CleManquante
from core.rapport import echapper_md
from core.requetes import ClientHTTP
from outils.commun import (ErreurOutil, Resultat, commande, controler_cible, ecrire_json,
                           fichier_brut, normaliser)

NOM = "entreprises"
TYPES = ("personne", "entreprise")

API_RECHERCHE = "https://recherche-entreprises.api.gouv.fr/search"
API_PAPPERS = "https://api.pappers.fr/v2/entreprise"
FICHE_ANNUAIRE = "https://annuaire-entreprises.data.gouv.fr/entreprise/{}"
FICHE_PAPPERS = "https://www.pappers.fr/entreprise/{}"
PAR_PAGE_MAX = 25
CHAMPS_INTERDITS = ("beneficiaires_effectifs",)


def options(parser) -> None:
    parser.add_argument("--siren", help="SIREN de l'entreprise visée (9 chiffres)")
    parser.add_argument("--limite", type=int, default=10,
                        help=f"nombre de résultats (défaut : 10, max : {PAR_PAGE_MAX})")
    parser.add_argument("--sans-pappers", action="store_true",
                        help="ne pas interroger Pappers même si la clé est configurée")


def siren_valide(texte: str | None) -> str | None:
    chiffres = re.sub(r"\s", "", texte or "")
    return chiffres if re.fullmatch(r"\d{9}", chiffres) else None


def nom_dirigeant(d: dict) -> str:
    if d.get("type_dirigeant") == "personne morale":
        return d.get("denomination") or ""
    return " ".join(p for p in (d.get("prenoms"), d.get("nom")) if p)


def correspond(nom_cherche: str, nom: str) -> bool:
    """Tous les mots cherchés figurent dans le nom (sans accents ni casse)."""
    mots = set(normaliser(nom).split())
    return bool(mots) and set(normaliser(nom_cherche).split()) <= mots


def resumer_entreprise(r: dict) -> dict:
    siege = r.get("siege") or {}
    return {
        "siren": r.get("siren", ""),
        "nom": r.get("nom_complet") or r.get("nom_raison_sociale") or "",
        "etat": "active" if r.get("etat_administratif") == "A" else "cessée",
        "creation": r.get("date_creation") or "",
        "activite": r.get("activite_principale") or "",
        "adresse": siege.get("adresse") or "",
        "dirigeants": [{"nom": nom_dirigeant(d), "qualite": d.get("qualite") or "",
                        "annee_naissance": d.get("annee_de_naissance") or "",
                        "personne_morale": d.get("type_dirigeant") == "personne morale",
                        "siren": d.get("siren") or ""}
                       for d in r.get("dirigeants") or []],
    }


def purger_champs_interdits(donnees):
    """Retire récursivement les champs dont la conservation est exclue."""
    if isinstance(donnees, dict):
        return {k: purger_champs_interdits(v) for k, v in donnees.items()
                if k not in CHAMPS_INTERDITS}
    if isinstance(donnees, list):
        return [purger_champs_interdits(v) for v in donnees]
    return donnees


def _ligne_dirigeant(d: dict) -> str:
    details = ", ".join(x for x in (d["qualite"], d["annee_naissance"] and
                                    f"né(e) en {d['annee_naissance']}") if x)
    return f"{d['nom']}" + (f" ({details})" if details else "")


def lancer(enquete, entite, opts, maintenant: datetime | None = None,
           transport=None, limiteur=None) -> Resultat:
    maintenant = maintenant or datetime.now()
    entite = controler_cible(enquete, entite, TYPES)
    if not 1 <= opts.limite <= PAR_PAGE_MAX:
        raise ErreurOutil(f"--limite doit être entre 1 et {PAR_PAGE_MAX}")
    if opts.siren and not siren_valide(opts.siren):
        raise ErreurOutil(f"SIREN invalide : {opts.siren}")
    personne = entite["type"] == "personne"
    siren = siren_valide(opts.siren) or (None if personne else siren_valide(entite["valeur"]))
    q = siren or entite["valeur"].strip()

    client = ClientHTTP(enquete.stockage, enquete.id, NOM, transport=transport,
                        limiteur=limiteur)
    brut = client.get_json(API_RECHERCHE, params={"q": q, "per_page": str(opts.limite)},
                           verifier_robots=False,
                           requete=f"recherche d'entreprises : {q}")
    resultats = [resumer_entreprise(r) for r in (brut or {}).get("results", [])]
    if siren:
        resultats = [r for r in resultats if r["siren"] == siren]
    fichiers = [ecrire_json(fichier_brut(enquete.chemin("bruts"), NOM, entite["id"],
                                         "recherche.json", maintenant),
                          purger_champs_interdits(brut))]

    lignes, md = [], [f"# Entreprises — E{entite['id']} « {echapper_md(entite['valeur'])} »",
                      "", f"Interrogé le {maintenant:%d/%m/%Y %H:%M}."]
    if personne:
        resultats = [r for r in resultats
                     if any(correspond(q, d["nom"]) for d in r["dirigeants"])]
        lignes.append(f"{len(resultats)} entreprise(s) dont un dirigeant s'appelle « {q} »"
                      " (homonymes possibles : vérifier l'année de naissance) :")
    elif siren:
        lignes.append(f"Entreprise SIREN {siren} :" if resultats
                      else f"Aucune entreprise trouvée pour le SIREN {siren}.")
    else:
        lignes.append(f"{len(resultats)} entreprise(s) candidate(s) pour « {q} » :"
                      + (" préciser avec --siren pour la fiche complète." if resultats else ""))

    for r in resultats:
        source = FICHE_ANNUAIRE.format(r["siren"])
        lignes.append(f"  {r['nom']} — SIREN {r['siren']}, {r['etat']}, créée le "
                      f"{r['creation'] or '?'}, {r['adresse']}")
        lignes.append(f"    source : {source}")
        md += ["", f"## {echapper_md(r['nom'])} — SIREN {r['siren']}", "",
               f"- État : {r['etat']} ; création : {r['creation'] or '?'} ;"
               f" activité : {echapper_md(r['activite'])}",
               f"- Siège : {echapper_md(r['adresse'])}", f"- Source : <{source}>"]
        for d in r["dirigeants"]:
            vise = personne and correspond(q, d["nom"])
            lignes.append(f"    {'►' if vise else '-'} {_ligne_dirigeant(d)}")
            md.append(f"  - {'**' if vise else ''}{echapper_md(_ligne_dirigeant(d))}"
                      f"{'**' if vise else ''}")
        if personne:
            lignes.append("    pour retenir ce mandat : " + commande(
                "ajouter", "entite", "entreprise", r["nom"]))
        elif not siren:
            lignes.append("    pour la fiche complète : " + commande(
                "lancer", NOM, "--entite", str(entite["id"]), "--siren", r["siren"]))

    if siren and resultats and not opts.sans_pappers:
        try:
            cle = client.config.cle("pappers")
        except CleManquante:
            lignes.append("Pappers : clé absente de config.local.toml, non interrogé.")
        else:
            fiche = purger_champs_interdits(client.get_json(
                API_PAPPERS, params={"siren": siren, "api_token": cle}, verifier_robots=False,
                requete=f"fiche Pappers SIREN {siren}"))
            fichiers.append(ecrire_json(fichier_brut(enquete.chemin("bruts"), NOM,
                                                     entite["id"], "pappers.json", maintenant),
                                        fiche))
            representants = fiche.get("representants") or []
            source = FICHE_PAPPERS.format(siren)
            lignes.append(f"Pappers : {len(representants)} représentant(s), source : {source}")
            md += ["", "## Représentants (Pappers)", "", f"Source : <{source}>", ""]
            for rep in representants:
                nom = rep.get("denomination") if rep.get("personne_morale") else \
                    rep.get("nom_complet") or " ".join(
                        x for x in (rep.get("prenom"), rep.get("nom")) if x)
                texte = f"{nom} ({rep.get('qualite') or '?'}, depuis le " \
                        f"{rep.get('date_prise_de_poste') or '?'})"
                lignes.append(f"  - {texte}")
                md.append(f"- {echapper_md(texte)}")

    chemin = fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], "md", maintenant)
    chemin.write_text("\n".join(md) + "\n", encoding="utf-8")
    fichiers.append(chemin)
    lignes += [f"Synthèse enregistrée : {chemin}",
               "Pour citer une fiche comme source : ajouter source <fait> <url>"]
    return Resultat(lignes, fichiers)
