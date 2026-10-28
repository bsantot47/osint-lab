"""Domaine : lecture de sources publiées, sans jamais contacter la cible.

Règle ferme : aucun scan, aucune sonde, aucune requête vers les serveurs du
domaine (ni HTTP, ni résolution DNS, ni whois sur ses serveurs). Toutes les
requêtes partent vers des tiers qui publient des données déjà collectées :

- crt.sh : journaux Certificate Transparency (certificats émis, donc noms
  d'hôtes déclarés) ; robots.txt vérifié ;
- RDAP : données d'enregistrement publiées par le registre (successeur
  normalisé de whois), serveur trouvé via le fichier d'amorçage de l'IANA ;
- Shodan : enregistrements DNS déjà collectés par Shodan (clé requise).

Censys n'est pas branché : son API a changé de modèle en 2025 et le format
dépend du type de compte ; à ajouter une fois le compte connu.

Aucune entité n'est créée automatiquement : les sous-domaines et le titulaire
sont listés avec les commandes `ajouter` prêtes à copier.
"""

from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from core.config import CleManquante
from core.rapport import echapper_md
from core.requetes import ClientHTTP, ErreurRequete
from core.stockage import domaine_enregistrable
from outils.commun import (ErreurOutil, Resultat, commande, controler_cible, ecrire_json,
                           fichier_brut)

NOM = "domaine"
TYPES = ("domaine",)

CRTSH = "https://crt.sh/"
AMORCAGE_RDAP = "https://data.iana.org/rdap/dns.json"
SHODAN_DNS = "https://api.shodan.io/dns/domain/{}"
SOURCES = ("crtsh", "rdap", "shodan")
MASQUES = ("redacted", "data protected", "non publiable", "not disclosed", "privacy")
AFFICHAGE_MAX = 30

# Hôtes autorisés : tout autre hôte (dont la cible) est refusé avant l'envoi.
HOTES_AUTORISES_FIXES = {"crt.sh", "data.iana.org", "api.shodan.io"}


def options(parser) -> None:
    parser.add_argument("--sources", default=",".join(SOURCES),
                        help="sources à interroger parmi crtsh, rdap, shodan (défaut : toutes ;"
                             " shodan est ignoré sans clé, sauf demande explicite)")
    parser.add_argument("--actifs-seulement", action="store_true",
                        help="crt.sh : ignorer les certificats expirés")


def normaliser_domaine(valeur: str) -> str:
    d = valeur.strip().lower()
    for prefixe in ("https://", "http://"):
        d = d.removeprefix(prefixe)
    d = d.split("/")[0].split(":")[0].rstrip(".").removeprefix("www.")
    if not domaine_enregistrable(d):
        raise ErreurOutil(f"domaine invalide : {valeur}")
    return d


class ClientPassif(ClientHTTP):
    """ClientHTTP restreint aux sources publiées : la cible n'est jamais contactée."""

    def __init__(self, *args, cible: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.cible = cible
        self.hotes_autorises = set(HOTES_AUTORISES_FIXES)

    def autoriser(self, url: str) -> None:
        """Ajoute un serveur RDAP lu dans le fichier d'amorçage de l'IANA."""
        self.hotes_autorises.add(urlsplit(url).hostname or "")

    def _envoyer(self, url, entetes):
        hote = urlsplit(url).hostname or ""
        if (hote not in self.hotes_autorises
                or domaine_enregistrable(hote) == domaine_enregistrable(self.cible)):
            raise ErreurRequete(f"hôte non autorisé pour une collecte passive : {hote}")
        return super()._envoyer(url, entetes)


# --- crt.sh ------------------------------------------------------------------

def noms_certificats(certificats: list, domaine: str) -> dict[str, dict]:
    """Noms d'hôtes du domaine vus dans les certificats, avec première et
    dernière date de validité et nombre de certificats."""
    noms: dict[str, dict] = {}
    for c in certificats:
        for nom in str(c.get("name_value", "")).splitlines() + [str(c.get("common_name", ""))]:
            nom = nom.strip().lower().removeprefix("*.")
            if not (nom == domaine or nom.endswith("." + domaine)):
                continue
            info = noms.setdefault(nom, {"certificats": set(), "debut": "", "fin": ""})
            info["certificats"].add(c.get("id"))
            debut, fin = str(c.get("not_before", ""))[:10], str(c.get("not_after", ""))[:10]
            if debut and (not info["debut"] or debut < info["debut"]):
                info["debut"] = debut
            if fin > info["fin"]:
                info["fin"] = fin
    return {n: {**i, "certificats": len(i["certificats"])} for n, i in sorted(noms.items())}


# --- RDAP --------------------------------------------------------------------

def serveur_rdap(amorcage: dict, domaine: str) -> str | None:
    tld = domaine.rsplit(".", 1)[-1]
    for suffixes, urls in amorcage.get("services", []):
        if tld in suffixes and urls:
            return urls[0].rstrip("/") + "/"
    return None


def _vcard(entite: dict, champ: str) -> str:
    for prop in (entite.get("vcardArray") or [None, []])[1]:
        if prop and prop[0] == champ:
            valeur = prop[3]
            return " ".join(valeur) if isinstance(valeur, list) else str(valeur)
    return ""


def masque(valeur: str) -> bool:
    return not valeur or any(m in valeur.lower() for m in MASQUES)


def analyser_rdap(donnees: dict) -> dict:
    evenements = {e.get("eventAction"): str(e.get("eventDate", ""))[:10]
                  for e in donnees.get("events", [])}
    resultat = {"registrar": "", "titulaire": {}, "creation": evenements.get("registration", ""),
                "expiration": evenements.get("expiration", ""),
                "modification": evenements.get("last changed", ""),
                "statut": donnees.get("status", []),
                "serveurs_noms": sorted(ns.get("ldhName", "").lower()
                                        for ns in donnees.get("nameservers", [])),
                "dnssec": bool((donnees.get("secureDNS") or {}).get("delegationSigned"))}
    for e in donnees.get("entities", []):
        roles = e.get("roles", [])
        if "registrar" in roles:
            resultat["registrar"] = _vcard(e, "fn")
        if "registrant" in roles:
            titulaire = {c: _vcard(e, c) for c in ("fn", "org", "email")}
            resultat["titulaire"] = {c: v for c, v in titulaire.items() if not masque(v)}
    return resultat


# --- exécution -----------------------------------------------------------------

def choisir_sources(texte: str, cle_shodan: bool) -> tuple[list[str], list[str]]:
    demandees = [s.strip() for s in texte.split(",") if s.strip()]
    inconnues = [s for s in demandees if s not in SOURCES]
    if inconnues or not demandees:
        raise ErreurOutil(f"sources inconnues : {', '.join(inconnues) or 'aucune'}"
                          f" (possibles : {', '.join(SOURCES)})")
    notes = []
    if "shodan" in demandees and not cle_shodan:
        if texte == ",".join(SOURCES):          # défaut : ignoré sans bruit
            demandees.remove("shodan")
            notes.append("Shodan : clé absente de config.local.toml, non interrogé.")
        else:
            raise ErreurOutil("Shodan demandé mais clé absente de config.local.toml")
    return demandees, notes


def lancer(enquete, entite, opts, maintenant: datetime | None = None,
           transport=None, limiteur=None) -> Resultat:
    maintenant = maintenant or datetime.now()
    entite = controler_cible(enquete, entite, TYPES)
    domaine = normaliser_domaine(entite["valeur"])
    client = ClientPassif(enquete.stockage, enquete.id, NOM, transport=transport,
                          limiteur=limiteur, cible=domaine)
    try:
        cle_shodan = client.config.cle("shodan")
    except CleManquante:
        cle_shodan = ""
    sources, lignes = choisir_sources(opts.sources, bool(cle_shodan))

    def brut(suffixe: str, donnees):
        chemin = fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], suffixe, maintenant)
        fichiers.append(ecrire_json(chemin, donnees))

    fichiers, erreurs = [], []
    sous_domaines: dict[str, set[str]] = {}
    md = [f"# Domaine — E{entite['id']} {echapper_md(domaine)}", "",
          f"Interrogé le {maintenant:%d/%m/%Y %H:%M}, sources publiées uniquement "
          f"({', '.join(sources)}) : aucune requête vers le domaine lui-même."]

    if "crtsh" in sources:
        params = {"q": f"%.{domaine}", "output": "json"}
        if opts.actifs_seulement:
            params["exclude"] = "expired"
        try:
            certificats = client.get_json(CRTSH, params=params,
                                          requete=f"certificats CT de {domaine}")
            brut("crtsh.json", certificats)
            noms = noms_certificats(certificats or [], domaine)
            for n in noms:
                sous_domaines.setdefault(n, set()).add("crt.sh")
            lignes.append(f"crt.sh : {len(certificats or [])} certificat(s),"
                          f" {len(noms)} nom(s) d'hôte")
            md += ["", "## Certificats (crt.sh)", "", "| Nom | Certificats | Première validité |"
                   " Dernière fin |", "|---|---|---|---|"]
            md += [f"| {echapper_md(n)} | {i['certificats']} | {i['debut']} | {i['fin']} |"
                   for n, i in noms.items()]
        except ErreurRequete as ex:
            erreurs.append(f"crt.sh : {ex}")

    if "rdap" in sources:
        try:
            amorcage = client.get_json(AMORCAGE_RDAP, verifier_robots=False,
                                       requete="amorçage RDAP de l'IANA")
            racine = domaine_enregistrable(domaine)
            serveur = serveur_rdap(amorcage, racine)
            if serveur is None:
                raise ErreurRequete(f"aucun serveur RDAP publié pour .{racine.rsplit('.', 1)[-1]}")
            client.autoriser(serveur)
            donnees = client.get_json(f"{serveur}domain/{racine}", verifier_robots=False,
                                      requete=f"RDAP de {racine}")
            brut("rdap.json", donnees)
            r = analyser_rdap(donnees)
            titulaire = ", ".join(r["titulaire"].values()) or "masqué ou absent"
            lignes += [f"RDAP : bureau d'enregistrement {r['registrar'] or '?'}, créé le "
                       f"{r['creation'] or '?'}, expire le {r['expiration'] or '?'}",
                       f"  serveurs de noms : {', '.join(r['serveurs_noms']) or '?'}",
                       f"  titulaire : {titulaire}"]
            for champ, type_ in (("org", "entreprise"), ("email", "email")):
                if r["titulaire"].get(champ):
                    lignes.append(f"    pour le retenir : "
                                  + commande("ajouter", "entite", type_, r["titulaire"][champ]))
            md += ["", "## Enregistrement (RDAP)", "",
                   f"- Bureau d'enregistrement : {echapper_md(r['registrar'] or '?')}",
                   f"- Création : {r['creation'] or '?'} ; expiration : {r['expiration'] or '?'}"
                   f" ; dernière modification : {r['modification'] or '?'}",
                   f"- Statut : {echapper_md(', '.join(r['statut']) or '?')} ;"
                   f" DNSSEC : {'oui' if r['dnssec'] else 'non'}",
                   f"- Serveurs de noms : {echapper_md(', '.join(r['serveurs_noms']) or '?')}",
                   f"- Titulaire : {echapper_md(titulaire)}",
                   f"- Source : <{serveur}domain/{racine}>"]
        except ErreurRequete as ex:
            erreurs.append(f"RDAP : {ex}")

    if "shodan" in sources:
        try:
            donnees = client.get_json(SHODAN_DNS.format(domaine), params={"key": cle_shodan},
                                      verifier_robots=False, requete=f"DNS Shodan de {domaine}")
            brut("shodan.json", donnees)
            for s in donnees.get("subdomains", []):
                nom = f"{s}.{domaine}" if s else domaine
                sous_domaines.setdefault(nom.lower(), set()).add("Shodan")
            enregistrements = donnees.get("data", [])
            lignes.append(f"Shodan : {len(donnees.get('subdomains', []))} sous-domaine(s),"
                          f" {len(enregistrements)} enregistrement(s) DNS")
            md += ["", "## Enregistrements DNS connus de Shodan", "",
                   "| Nom | Type | Valeur | Vu le |", "|---|---|---|---|"]
            for e in enregistrements:
                nom = f"{e.get('subdomain')}.{domaine}" if e.get("subdomain") else domaine
                md.append(f"| {echapper_md(nom)} | {echapper_md(e.get('type', ''))} | "
                          f"{echapper_md(e.get('value', ''))} | "
                          f"{str(e.get('last_seen', ''))[:10]} |")
        except ErreurRequete as ex:
            erreurs.append(f"Shodan : {ex}")

    if erreurs and not fichiers:
        raise ErreurOutil("aucune source n'a répondu : " + " ; ".join(erreurs))
    lignes += [f"échec {e}" for e in erreurs]

    autres = sorted(n for n in sous_domaines if n != domaine)
    if autres:
        lignes.append(f"{len(autres)} sous-domaine(s) publié(s) :")
        lignes += [f"  {n} ({', '.join(sorted(sous_domaines[n]))})" for n in autres[:AFFICHAGE_MAX]]
        if len(autres) > AFFICHAGE_MAX:
            lignes.append(f"  … et {len(autres) - AFFICHAGE_MAX} autres (voir la synthèse)")
        lignes.append("  pour en suivre un : " + commande("ajouter", "entite", "domaine", autres[0]))
        md += ["", "## Sous-domaines publiés", ""]
        md += [f"- {echapper_md(n)} ({', '.join(sorted(sous_domaines[n]))})" for n in autres]

    chemin = fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], "md", maintenant)
    chemin.write_text("\n".join(md) + "\n", encoding="utf-8")
    fichiers.append(chemin)
    lignes.append(f"Synthèse enregistrée : {chemin}")
    return Resultat(lignes, fichiers)
