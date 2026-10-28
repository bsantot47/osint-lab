"""Adresse e-mail : holehe (sous-processus) et HIBP (API), deux outils distincts.

courriel — holehe teste, site par site, si l'adresse y est associée à un
compte. Options fixées ici :
- --only-used : ne garder que les sites où un compte existe ;
- --no-password-recovery : toujours. Holehe ne déclenche alors aucune
  procédure de récupération de mot de passe et ne collecte pas les
  fragments d'adresse ou de téléphone de secours ;
- -T : délai max par site. Holehe n'a ni délai entre requêtes ni réglage de
  concurrence en ligne de commande : les deux sont fixés par le programme.

fuite — HIBP (API officielle, clé payante) : vérifie seulement la
PRÉSENCE de l'adresse dans des fuites connues (truncateResponse=true : seuls
les noms des fuites sont renvoyés). Aucune donnée de fuite n'est téléchargée.

Aucun fait n'est créé : l'outil propose les commandes `ajouter fait`.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime
from urllib.parse import quote

from core.rapport import echapper_md
from core.requetes import ClientHTTP, ErreurRequete
from outils.commun import (ErreurOutil, Outil, Programme, Resultat, commande, controler_cible,
                           dossier_brut, ecrire_json, fichier_brut)

NOM = "courriel"
TYPES = ("email",)
INSTALLATION = "pipx install holehe (dans la VM)"
EMAIL_VALIDE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
LIGNE_TROUVEE = re.compile(r"^\[\+\]\s*(\S+)")

API_HIBP = "https://haveibeenpwned.com/api/v3/breachedaccount/{}"
PAGE_FUITE = "https://haveibeenpwned.com/PwnedWebsites#{}"


def verifier_email(valeur: str) -> str:
    """La valeur part telle quelle en argument : refuser ce qui n'est pas une adresse."""
    email = valeur.strip()
    if not EMAIL_VALIDE.match(email):
        raise ErreurOutil(f"adresse e-mail invalide : {valeur!r}")
    return email


# --- courriel (holehe) ---------------------------------------------------------

def options(parser) -> None:
    parser.add_argument("--timeout", type=int, default=15,
                        help="délai max par site, en secondes (défaut : 15)")
    parser.add_argument("--duree-max", type=int, default=600,
                        help="durée max de holehe, en secondes (défaut : 600)")


def args_holehe(email: str, opts) -> list[str]:
    return [email, "--only-used", "--no-color", "--no-clear", "--no-password-recovery",
            "--csv", "-T", str(opts.timeout)]


def lire_holehe(dossier, stdout: str) -> list[str]:
    rapports = sorted(dossier.glob("holehe_*_results.csv"))
    if not rapports:
        return [m.group(1) for m in map(LIGNE_TROUVEE.match, (stdout or "").splitlines()) if m]
    with open(rapports[-1], newline="", encoding="utf-8") as f:
        return [l.get("domain") or l.get("name", "") for l in csv.DictReader(f)
                if str(l.get("exists")).lower() == "true"]


def lancer(enquete, entite, opts, maintenant: datetime | None = None,
           programme: Programme | None = None) -> Resultat:
    maintenant = maintenant or datetime.now()
    entite = controler_cible(enquete, entite, TYPES)
    email = verifier_email(entite["valeur"])
    if opts.timeout < 1 or opts.duree_max < 10:
        raise ErreurOutil("--timeout ≥ 1 et --duree-max ≥ 10 requis")
    programme = programme or Programme("holehe", INSTALLATION)
    dossier = dossier_brut(enquete.chemin("bruts"), NOM, entite["id"], maintenant)
    r = programme.executer(enquete, NOM, args_holehe(email, opts), dossier, opts.duree_max)
    sites = sorted(set(lire_holehe(dossier, r.stdout)))
    if r.returncode != 0 and not sites:
        raise ErreurOutil(f"holehe a échoué (code {r.returncode}), voir {dossier}")

    lignes = [f"holehe : {len(sites)} site(s) où l'adresse est associée à un compte :"]
    lignes += [f"  {s}" for s in sites]
    md = [f"# Courriel — E{entite['id']} {echapper_md(email)}", "",
          f"Testé le {maintenant:%d/%m/%Y %H:%M} avec holehe (sans récupération de mot de"
          " passe). Indique qu'un compte existe, pas qui l'utilise.", ""]
    md += [f"- {echapper_md(s)}" for s in sites] or ["_Aucun site._"]
    if sites:
        lignes.append("  pour retenir : " + commande(
            "ajouter", "fait", f"Adresse {email} associée à un compte {sites[0]}",
            "--entite", str(entite["id"]), "--source", f"https://{sites[0]}", "--outil", NOM))
        lignes.append("  (holehe ne fournit pas de page de profil : la source pointe vers le"
                      " site ; une vérification indépendante reste nécessaire)")
    synthese = fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], "md", maintenant)
    synthese.write_text("\n".join(md) + "\n", encoding="utf-8")
    lignes.append(f"Synthèse enregistrée : {synthese}")
    return Resultat(lignes, [dossier, synthese])


# --- fuite (HIBP) ------------------------------------------------------------------

def options_fuite(parser) -> None:
    parser.add_argument("--non-verifiees", action="store_true",
                        help="inclure les fuites que HIBP n'a pas pu authentifier")


def lancer_fuite(enquete, entite, opts, maintenant: datetime | None = None,
                 transport=None, limiteur=None) -> Resultat:
    maintenant = maintenant or datetime.now()
    entite = controler_cible(enquete, entite, TYPES)
    email = verifier_email(entite["valeur"])
    client = ClientHTTP(enquete.stockage, enquete.id, "fuite", transport=transport,
                        limiteur=limiteur)
    cle = client.config.cle("hibp")
    params = {"truncateResponse": "true"}
    if not opts.non_verifiees:
        params["includeUnverified"] = "false"
    r = client.get(API_HIBP.format(quote(email, safe="@")), params=params,
                   entetes={"hibp-api-key": cle}, verifier_robots=False,
                   requete=f"présence de {email} dans les fuites HIBP")
    if r.statut == 404:
        fuites = []
    elif r.statut == 429:
        raise ErreurOutil("HIBP : trop de requêtes, réessayer après "
                          f"{r.entetes.get('retry-after', '?')} s")
    elif r.statut == 401:
        raise ErreurOutil("HIBP : clé refusée (vérifier [cles] hibp)")
    elif not r.ok:
        raise ErreurRequete(f"HIBP : HTTP {r.statut}")
    else:
        try:
            fuites = sorted(f["Name"] for f in r.json())
        except (ValueError, TypeError, KeyError) as ex:
            raise ErreurOutil("HIBP : réponse inattendue") from ex

    brut = ecrire_json(fichier_brut(enquete.chemin("bruts"), "fuite", entite["id"], "json",
                                    maintenant), fuites)
    if fuites:
        lignes = [f"HIBP : {email} présente dans {len(fuites)} fuite(s) connue(s) :"]
        lignes += [f"  {f} — {PAGE_FUITE.format(f)}" for f in fuites]
        lignes.append("  pour retenir : " + commande(
            "ajouter", "fait", f"Adresse {email} présente dans la fuite {fuites[0]}",
            "--entite", str(entite["id"]), "--source", PAGE_FUITE.format(fuites[0]),
            "--outil", "fuite"))
    else:
        lignes = [f"HIBP : {email} absente des fuites connues de HIBP."]
    lignes.append(f"Résultat enregistré : {brut} (noms des fuites seulement)")
    return Resultat(lignes, [brut])


FUITE = Outil("fuite", TYPES, options_fuite, lancer_fuite)
