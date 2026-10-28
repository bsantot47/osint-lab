"""Faux réseau pour les tests d'outils : aucune requête ne sort de la machine."""

import json
from urllib.parse import parse_qs, urlsplit

from core.requetes import LimiteurDebit, Reponse


class Routeur:
    """`routes` associe « hôte/chemin » à un objet JSON, à (statut, objet) ou à une
    exception. Toute URL non routée répond 404 ; tout est enregistré."""

    def __init__(self, routes):
        self.routes = routes
        self.requetes = []

    def __call__(self, req, timeout, taille_max):
        self.requetes.append(req)
        u = urlsplit(req.full_url)
        reponse = self.routes.get(f"{u.hostname}{u.path}", (404, None))
        if isinstance(reponse, Exception):
            raise reponse
        statut, corps = reponse if isinstance(reponse, tuple) else (200, reponse)
        contenu = corps if isinstance(corps, bytes) else json.dumps(corps).encode()
        return Reponse(req.full_url, statut, contenu)

    def hotes(self):
        return [urlsplit(r.full_url).hostname for r in self.requetes]

    def params(self, hote_chemin):
        for r in self.requetes:
            u = urlsplit(r.full_url)
            if f"{u.hostname}{u.path}" == hote_chemin:
                return {k: v[0] for k, v in parse_qs(u.query).items()}
        raise AssertionError(f"{hote_chemin} jamais appelé")


def sans_pause():
    return LimiteurDebit(dormir=lambda _: None)


def config_avec_cles(tmp_path, monkeypatch, **cles):
    f = tmp_path / "config.local.toml"
    f.write_text("[cles]\n" + "".join(f'{k} = "{v}"\n' for k, v in cles.items()),
                 encoding="utf-8")
    monkeypatch.setenv("OSINT_LAB_CONFIG", str(f))
