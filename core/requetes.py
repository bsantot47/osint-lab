"""Couche HTTP unique de l'outil : tout appel réseau des modules Python passe ici.

Chaque requête :
- est refusée si l'enquête n'a pas de finalité ou est close ;
- respecte robots.txt (sauf API officielle, déclarée explicitement) ;
- attend le délai minimal configuré depuis le dernier appel au même domaine ;
- est journalisée (date, outil, requête, URL, résultat), clés d'API masquées.

Les redirections sont suivies ici, saut par saut, et non par urllib : chaque
saut est journalisé et rate-limité, et robots.txt est revérifié dès qu'un saut
quitte le domaine de départ (même pour une API officielle). Les en-têtes
fournis (dont les clés d'API) ne suivent pas un changement d'hôte.

Seul GET est proposé : l'outil consulte, il ne soumet rien.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from core.config import Config, charger
from core.stockage import Stockage, domaine_enregistrable

# Paramètres d'URL dont la valeur ne doit jamais apparaître dans le journal.
PARAMETRES_SENSIBLES = {"api_token", "api_key", "apikey", "key", "token", "access_token",
                        "secret", "password"}
STATUTS_REDIRECTION = {301, 302, 303, 307, 308}
MAX_REDIRECTIONS = 5


class ErreurRequete(Exception):
    """Échec réseau ou réponse inexploitable (déjà journalisé)."""


class AccesRefuse(ErreurRequete):
    """robots.txt interdit cette URL (déjà journalisé)."""


class TailleDepassee(ErreurRequete):
    """La réponse dépasse la taille maximale configurée."""


@dataclass
class Reponse:
    url: str
    statut: int
    contenu: bytes
    entetes: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.statut < 300

    def texte(self) -> str:
        return self.contenu.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.contenu)


Transport = Callable[[urllib.request.Request, float, int], Reponse]


def _lire_borne(flux, taille_max: int) -> bytes:
    contenu = flux.read(taille_max + 1)
    if len(contenu) > taille_max:
        raise TailleDepassee(f"réponse > {taille_max} octets")
    return contenu


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    """Une 3xx remonte comme réponse : ClientHTTP décide de la suivre."""

    def redirect_request(self, *_args, **_kwargs):
        return None


_OUVREUR = urllib.request.build_opener(_SansRedirection)


def transport_urllib(req: urllib.request.Request, timeout: float, taille_max: int) -> Reponse:
    try:
        with _OUVREUR.open(req, timeout=timeout) as r:
            return Reponse(r.geturl(), r.status, _lire_borne(r, taille_max),
                           {k.lower(): v for k, v in r.headers.items()})
    except urllib.error.HTTPError as e:
        # Une réponse 4xx/5xx reste une réponse : l'appelant décide.
        with e:
            return Reponse(e.geturl() or req.full_url, e.code, _lire_borne(e, taille_max),
                           {k.lower(): v for k, v in (e.headers or {}).items()})


def masquer_url(url: str) -> str:
    morceaux = urlsplit(url)
    if not morceaux.query:
        return url
    params = [(k, "***" if k.lower() in PARAMETRES_SENSIBLES else v)
              for k, v in parse_qsl(morceaux.query, keep_blank_values=True)]
    return urlunsplit(morceaux._replace(query=urlencode(params, safe="*")))


class LimiteurDebit:
    """Impose un délai minimal entre deux appels vers un même hôte."""

    def __init__(self, horloge: Callable[[], float] = time.monotonic,
                 dormir: Callable[[float], None] = time.sleep):
        self.horloge = horloge
        self.dormir = dormir
        self.derniers: dict[str, float] = {}

    def attendre(self, hote: str, delai_s: float) -> None:
        dernier = self.derniers.get(hote)
        if dernier is not None:
            reste = delai_s - (self.horloge() - dernier)
            if reste > 0:
                self.dormir(reste)
        self.derniers[hote] = self.horloge()


# Partagé par tous les clients d'un même processus, pour que deux outils ne
# puissent pas viser le même domaine en parallèle sans pause.
LIMITEUR_PARTAGE = LimiteurDebit()


class ClientHTTP:
    def __init__(self, stockage: Stockage, enquete_id: int, outil: str,
                 config: Config | None = None, limiteur: LimiteurDebit | None = None,
                 transport: Transport | None = None):
        stockage.exiger_collecte_possible(enquete_id)
        self.stockage = stockage
        self.enquete_id = enquete_id
        self.outil = outil
        self.config = config or charger()
        self.limiteur = limiteur or LIMITEUR_PARTAGE
        self.transport = transport or transport_urllib
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}

    def _journaliser(self, requete: str, url: str, resume: str, outil: str | None = None) -> None:
        self.stockage.journaliser(self.enquete_id, outil or self.outil, requete,
                                  masquer_url(url), resume)

    def _envoyer(self, url: str, entetes: dict[str, str]) -> Reponse:
        hote = urlsplit(url).hostname or ""
        self.limiteur.attendre(hote, self.config.delai_s)
        req = urllib.request.Request(
            url, headers={"User-Agent": self.config.user_agent, **entetes}, method="GET")
        return self.transport(req, self.config.timeout_s, self.config.taille_max_octets)

    def _robots_autorise(self, url: str) -> bool:
        morceaux = urlsplit(url)
        base = f"{morceaux.scheme}://{morceaux.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            url_robots = base + "/robots.txt"
            try:
                r = self._envoyer(url_robots, {})
                resume = f"HTTP {r.statut}"
            except (urllib.error.URLError, OSError, ErreurRequete) as e:
                r, resume = None, f"erreur : {e}"
            # Conventions RFC 9309 : absent (4xx) = tout permis ; inaccessible
            # (5xx, réseau) ou accès refusé (401/403) = tout interdit.
            if r is not None and r.ok:
                rp.parse(r.texte().splitlines())
            elif r is not None and 400 <= r.statut < 500 and r.statut not in (401, 403):
                rp.allow_all = True
            else:
                rp.disallow_all = True
            self._journaliser(f"GET {url_robots}", url_robots, resume, f"{self.outil}/robots")
            self._robots[base] = rp
        return self._robots[base].can_fetch(self.config.user_agent, url)

    def get(self, url: str, params: dict[str, str] | None = None,
            entetes: dict[str, str] | None = None, verifier_robots: bool = True,
            requete: str | None = None) -> Reponse:
        """GET journalisé.

        `verifier_robots=False` est réservé aux API officielles documentées,
        conçues pour un usage programmatique ; le journal le mentionne.
        `requete` décrit la recherche en clair (ex. « captures de exemple.fr »).
        Les réponses non 2xx sont renvoyées, pas levées : tester `.ok`.
        """
        self.stockage.exiger_collecte_possible(self.enquete_id)
        if params:
            url += ("&" if urlsplit(url).query else "?") + urlencode(params)
        libelle = requete or f"GET {masquer_url(url)}"
        domaine_depart = domaine_enregistrable(url)
        hote_depart = urlsplit(url).hostname
        entetes = entetes or {}

        for saut in range(MAX_REDIRECTIONS + 1):
            hors_domaine = domaine_enregistrable(url) != domaine_depart
            r = self._appel(url, libelle if saut == 0 else f"{libelle} (redirection {saut})",
                            entetes if urlsplit(url).hostname == hote_depart else {},
                            verifier_robots or hors_domaine)
            location = r.entetes.get("location")
            if r.statut not in STATUTS_REDIRECTION or not location:
                return r
            url = urljoin(url, location)
        self._journaliser(libelle, url, f"abandon : plus de {MAX_REDIRECTIONS} redirections")
        raise ErreurRequete(f"plus de {MAX_REDIRECTIONS} redirections")

    def _appel(self, url: str, libelle: str, entetes: dict[str, str],
               verifier_robots: bool) -> Reponse:
        """Un seul aller-retour HTTP, sans suivre de redirection."""
        if urlsplit(url).scheme not in ("http", "https"):
            self._journaliser(libelle, url, "refusé : schéma non autorisé")
            raise ErreurRequete(f"schéma non autorisé : {masquer_url(url)}")

        if verifier_robots:
            if not self._robots_autorise(url):
                self._journaliser(libelle, url, "refusé par robots.txt")
                raise AccesRefuse(f"robots.txt interdit {masquer_url(url)}")
            mention = ""
        else:
            mention = " ; robots.txt non vérifié (API officielle)"

        try:
            r = self._envoyer(url, entetes)
        except (urllib.error.URLError, OSError, ErreurRequete) as e:
            self._journaliser(libelle, url, f"erreur : {e}{mention}")
            if isinstance(e, ErreurRequete):
                raise
            raise ErreurRequete(str(e)) from e
        resume = f"HTTP {r.statut}, {len(r.contenu)} octets{mention}"
        if r.statut in STATUTS_REDIRECTION and r.entetes.get("location"):
            resume += f" ; redirection vers {masquer_url(urljoin(url, r.entetes['location']))}"
        self._journaliser(libelle, url, resume)
        return r

    def get_json(self, url: str, **kwargs):
        r = self.get(url, **kwargs)
        if not r.ok:
            raise ErreurRequete(f"HTTP {r.statut} sur {masquer_url(url)}")
        try:
            return r.json()
        except ValueError as e:
            raise ErreurRequete(f"JSON invalide sur {masquer_url(url)}") from e
