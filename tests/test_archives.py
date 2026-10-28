import json
from argparse import Namespace
from urllib.parse import parse_qs, urlsplit

import pytest

import core.requetes
from core.requetes import LimiteurDebit, Reponse
from outils import archives
from outils.commun import ErreurOutil
from pilote import enquete as enq
from tests.conftest import T0
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)

CDX = [
    ["timestamp", "original", "statuscode", "mimetype", "digest"],
    ["20050312101500", "http://exemple.fr/", "200", "text/html", "AAA"],
    ["20120101000000", "http://www.exemple.fr/", "200", "text/html", "BBB"],
    ["20120615000000", "http://www.exemple.fr/", "301", "text/html", "CCC"],
    ["20240301080000", "https://exemple.fr/", "200", "text/html", "DDD"],
]


class TransportCDX:
    def __init__(self, contenu=CDX, statut=200):
        self.contenu = json.dumps(contenu).encode()
        self.statut = statut
        self.requetes = []

    def __call__(self, req, timeout, taille_max):
        self.requetes.append(req)
        return Reponse(req.full_url, self.statut, self.contenu)

    def params(self):
        [req] = self.requetes
        return {k: v[0] for k, v in parse_qs(urlsplit(req.full_url).query).items()}


def opts(**kw):
    defaut = dict(url=None, domaine_entier=False, depuis=None, jusqua=None, toutes=False,
                  limite=500)
    return Namespace(**{**defaut, **kw})


def sans_pause():
    return LimiteurDebit(dormir=lambda _: None)


@pytest.fixture
def enquete_domaine(racine):  # noqa: F811
    with enq.creer("x", FINALITE, departs=[("domaine", "exemple.fr")]) as e:
        yield e, e.stockage.entite(1)


# --- paramètres --------------------------------------------------------------

def test_parametres_par_defaut(enquete_domaine):
    _, entite = enquete_domaine
    cible, p = archives.parametres(entite, opts())
    assert cible == "exemple.fr"
    assert p == {"url": "exemple.fr", "output": "json", "limit": "500", "collapse": "digest",
                 "fl": "timestamp,original,statuscode,mimetype,digest"}


def test_parametres_domaine_entier_periode_toutes(enquete_domaine):
    _, entite = enquete_domaine
    _, p = archives.parametres(entite, opts(domaine_entier=True, depuis="2010",
                                            jusqua="2015", toutes=True))
    assert p["matchType"] == "domain" and (p["from"], p["to"]) == ("2010", "2015")
    assert "collapse" not in p


def test_url_du_meme_domaine_acceptee(enquete_domaine):
    _, entite = enquete_domaine
    cible, p = archives.parametres(entite, opts(url="https://blog.exemple.fr/contact"))
    assert cible == p["url"] == "https://blog.exemple.fr/contact"


@pytest.mark.parametrize("mauvais", [
    dict(url="https://autre.com/"),                       # hors du domaine de l'entité
    dict(url="https://exemple.fr/a", domaine_entier=True),
    dict(limite=0), dict(limite=archives.LIMITE_MAX + 1),
    dict(depuis="2O10"), dict(jusqua="15"),
])
def test_parametres_refuses(enquete_domaine, mauvais):
    _, entite = enquete_domaine
    with pytest.raises(ErreurOutil):
        archives.parametres(entite, opts(**mauvais))


# --- analyse -------------------------------------------------------------------

def test_analyser():
    captures = archives.analyser(CDX)
    assert len(captures) == 4 and captures[0]["digest"] == "AAA"
    assert archives.analyser([]) == []
    with pytest.raises(ErreurOutil):
        archives.analyser({"erreur": "x"})


def test_lien_capture():
    assert archives.lien_capture("20050312101500", "http://exemple.fr/") == \
        "https://web.archive.org/web/20050312101500/http://exemple.fr/"


# --- exécution -----------------------------------------------------------------

def test_lancer(enquete_domaine):
    e, entite = enquete_domaine
    transport = TransportCDX()
    res = archives.lancer(e, entite, opts(), maintenant=T0, transport=transport,
                          limiteur=sans_pause())

    assert transport.requetes[0].full_url.startswith(archives.API_CDX + "?")
    assert transport.params()["url"] == "exemple.fr"
    assert len(transport.requetes) == 1                  # API officielle : pas de robots.txt

    texte = "\n".join(res.lignes)
    assert "4 capture(s) de exemple.fr (versions distinctes)" in texte
    assert "première : 12/03/2005 https://web.archive.org/web/20050312101500/http://exemple.fr/" in texte
    assert "dernière : 01/03/2024" in texte
    assert "par année : 2005 : 1, 2012 : 2, 2024 : 1" in texte

    brut, liste = res.fichiers
    assert json.loads(brut.read_text(encoding="utf-8")) == CDX
    assert brut.name == "archives-E1-20261001-090000.json"
    md = liste.read_text(encoding="utf-8")
    assert md.count("<https://web.archive.org/web/") == 4

    [j] = e.stockage.journal(e.id)
    assert j["outil"] == "archives" and j["requete"] == "captures Wayback de exemple.fr (URL)"
    assert j["url"].startswith(archives.API_CDX) and "API officielle" in j["resultat_resume"]


def test_lancer_sans_capture(enquete_domaine):
    e, entite = enquete_domaine
    res = archives.lancer(e, entite, opts(), transport=TransportCDX([]), limiteur=sans_pause())
    assert res.lignes[0] == "0 capture(s) de exemple.fr (versions distinctes)"


def test_limite_atteinte_signalee(enquete_domaine):
    e, entite = enquete_domaine
    res = archives.lancer(e, entite, opts(limite=4), transport=TransportCDX(),
                          limiteur=sans_pause())
    assert any("limite de 4 atteinte" in l for l in res.lignes)


def test_erreur_http_journalisee(enquete_domaine):
    e, entite = enquete_domaine
    with pytest.raises(core.requetes.ErreurRequete):
        archives.lancer(e, entite, opts(), transport=TransportCDX(statut=503),
                        limiteur=sans_pause())
    assert "HTTP 503" in e.stockage.journal(e.id)[-1]["resultat_resume"]
    assert list(e.chemin("bruts").iterdir()) == []


# --- par la CLI ----------------------------------------------------------------

@pytest.fixture
def reseau_simule(monkeypatch):
    transport = TransportCDX()
    monkeypatch.setattr(core.requetes, "transport_urllib", transport)
    monkeypatch.setattr(core.requetes, "LIMITEUR_PARTAGE", sans_pause())
    return transport


def test_cli_lancer_archives(racine, capsys, reseau_simule):  # noqa: F811
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "domaine:exemple.fr")
    code, out, _ = cli(capsys, "lancer", "archives", "--entite", "1", "--depuis", "2010")
    assert code == 0 and "4 capture(s)" in out
    assert reseau_simule.params()["from"] == "2010"
    _, out, _ = cli(capsys, "statut")
    assert "archives sur E1 (faite)" in out
    assert "Pivot suggéré : lancer domaine --entite 1" in out


def test_cli_echec_ne_marque_pas_la_tache(racine, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(core.requetes, "transport_urllib", TransportCDX(statut=500))
    monkeypatch.setattr(core.requetes, "LIMITEUR_PARTAGE", sans_pause())
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "domaine:exemple.fr")
    code, _, err = cli(capsys, "lancer", "archives", "--entite", "1")
    assert code == 1 and "HTTP 500" in err
    with enq.ouvrir("x") as e:
        assert e.stockage.taches(e.id) == []
