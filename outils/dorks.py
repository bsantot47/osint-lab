"""Génère des requêtes Google/Bing prêtes à cliquer.

Aucune requête n'est envoyée : interroger un moteur de recherche par script
enfreint ses CGU. L'outil fabrique les liens, l'enquêteur les ouvre.

Les modèles sont déclaratifs, par type d'entité. Un modèle dont une variable
n'existe pas pour l'entité (ex. nom inversé d'un nom d'un seul mot) est
ignoré. Volontairement absents : les recherches de fichiers tableurs/CSV ou de
sites de collage, qui mènent surtout à des fuites de données.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import quote_plus

from core.rapport import echapper_md
from outils.commun import Resultat, controler_cible, fichier_brut

NOM = "dorks"
TYPES = ("personne", "entreprise", "email", "compte", "domaine", "numero")

MOTEURS = {
    "Google": "https://www.google.com/search?q={}",
    "Bing": "https://www.bing.com/search?q={}",
}

# Domaines de messagerie grand public : chercher « @gmail.com » n'apprend rien.
WEBMAILS = {"gmail.com", "googlemail.com", "outlook.com", "outlook.fr", "hotmail.com",
            "hotmail.fr", "live.com", "live.fr", "yahoo.com", "yahoo.fr", "orange.fr",
            "free.fr", "sfr.fr", "laposte.net", "wanadoo.fr", "icloud.com", "me.com",
            "proton.me", "protonmail.com", "gmx.fr", "gmx.com"}

REGISTRES = "(site:pappers.fr OR site:societe.com OR site:annuaire-entreprises.data.gouv.fr)"

MODELES: dict[str, tuple[tuple[str, str], ...]] = {
    "personne": (
        ("Nom exact", "{q}"),
        ("Nom inversé", "{inverse}"),
        ("Profils professionnels", "{q} site:linkedin.com/in"),
        ("Réseaux sociaux", "{q} (site:facebook.com OR site:instagram.com OR site:x.com"
                            " OR site:tiktok.com)"),
        ("Documents", "{q} (filetype:pdf OR filetype:doc OR filetype:docx)"),
        ("Registres d'entreprises", "{q} " + REGISTRES),
    ),
    "entreprise": (
        ("Nom exact", "{q}"),
        ("Registres", "{q} " + REGISTRES),
        ("Annonces légales", "{q} site:bodacc.fr"),
        ("Documents", "{q} filetype:pdf"),
        ("Avis et litiges", "{q} (avis OR litige OR plainte OR arnaque)"),
    ),
    "email": (
        ("Adresse exacte", "{q}"),
        ("Identifiant seul", '"{local}"'),
        ("Autres adresses du domaine", '"@{domaine}" -{q}'),
        ("Documents", "{q} (filetype:pdf OR filetype:doc OR filetype:docx)"),
    ),
    "compte": (
        ("Pseudo exact", "{q}"),
        ("Pseudo dans les URL", "inurl:{v}"),
        ("Plateformes", "{q} (site:github.com OR site:reddit.com OR site:x.com"
                        " OR site:instagram.com OR site:tiktok.com OR site:youtube.com)"),
    ),
    "domaine": (
        ("Pages indexées", "site:{v}"),
        ("Sous-domaines", "site:*.{v} -site:www.{v}"),
        ("Documents publiés", "site:{v} (filetype:pdf OR filetype:doc OR filetype:docx"
                              " OR filetype:pptx)"),
        ("Mentions externes", '"{v}" -site:{v}'),
    ),
    "numero": (
        ("Numéro tel que saisi", "{q}"),
        ("Format national", "{national}"),
        ("Format international", "{international}"),
    ),
}


def options(parser) -> None:
    parser.add_argument("--contexte", default="",
                        help="termes ajoutés à chaque requête pour écarter les homonymes"
                             " (ex. \"Lyon\" ou \"kinésithérapeute\")")


def variables(type_: str, valeur: str) -> dict[str, str]:
    v = valeur.replace('"', "").replace("`", "").strip()
    vars_ = {"v": v, "q": f'"{v}"'}
    if type_ == "personne":
        mots = v.split()
        if len(mots) >= 2:
            vars_["inverse"] = f'"{mots[-1]} {" ".join(mots[:-1])}"'
    elif type_ == "email" and "@" in v:
        local, _, domaine = v.rpartition("@")
        vars_["local"] = local
        if domaine.lower() not in WEBMAILS:
            vars_["domaine"] = domaine.lower()
    elif type_ == "numero":
        chiffres = re.sub(r"\D", "", v)
        if re.fullmatch(r"33[1-9]\d{8}", chiffres):
            chiffres = "0" + chiffres[2:]
        if re.fullmatch(r"0[1-9]\d{8}", chiffres):
            paires = [chiffres[i:i + 2] for i in range(0, 10, 2)]
            vars_["national"] = '"' + " ".join(paires) + '"'
            vars_["international"] = f'"+33 {paires[0][1]} {" ".join(paires[1:])}"'
    return vars_


def generer(type_: str, valeur: str, contexte: str = "") -> list[tuple[str, str]]:
    """(libellé, requête) pour une entité, sans doublon."""
    vars_ = variables(type_, valeur)
    requetes, vues = [], set()
    for libelle, modele in MODELES.get(type_, ()):
        try:
            requete = modele.format_map(vars_)
        except KeyError:
            continue
        if contexte.strip():
            requete = f"{requete} {contexte.strip()}"
        if requete not in vues:
            vues.add(requete)
            requetes.append((libelle, requete))
    return requetes


def liens(requete: str) -> dict[str, str]:
    return {moteur: modele.format(quote_plus(requete)) for moteur, modele in MOTEURS.items()}


def lancer(enquete, entite, opts, maintenant: datetime | None = None) -> Resultat:
    maintenant = maintenant or datetime.now()
    entite = controler_cible(enquete, entite, TYPES)
    requetes = generer(entite["type"], entite["valeur"], opts.contexte)
    chemin = fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], "md", maintenant)

    md = [f"# Dorks — E{entite['id']} {entite['type']} « {echapper_md(entite['valeur'])} »", "",
          f"Générées le {maintenant:%d/%m/%Y %H:%M}"
          + (f", contexte : `{opts.contexte}`" if opts.contexte else "") + ".",
          "Aucune requête n'a été envoyée par l'outil : les ouvrir manuellement, et "
          "enregistrer chaque trouvaille avec sa source (`ajouter`).", ""]
    lignes = [f"{len(requetes)} requêtes pour E{entite['id']} « {entite['valeur']} »"
              " (à ouvrir manuellement) :"]
    for libelle, requete in requetes:
        urls = liens(requete)
        md.append(f"- **{libelle}** : `{requete}` — "
                  + " · ".join(f"[{m}]({u})" for m, u in urls.items()))
        lignes.append(f"  {libelle} : {requete}")
        lignes.extend(f"    {m} : {u}" for m, u in urls.items())
    chemin.write_text("\n".join(md) + "\n", encoding="utf-8")
    lignes.append(f"Liste enregistrée : {chemin}")

    enquete.stockage.journaliser(
        enquete.id, NOM, f"génération de requêtes pour E{entite['id']}",
        resultat_resume=f"{len(requetes)} requêtes, aucune envoyée ; {chemin.name}",
        maintenant=maintenant)
    return Resultat(lignes, [chemin])
