import urllib.error

import pytest

from core.config import Config
from core.requetes import (AccesRefuse, ClientHTTP, ErreurRequete, LimiteurDebit, Reponse,
                           TailleDepassee, masquer_url)
from core.stockage import EnqueteCloturee


class FauxTransport:
    """Simule le réseau : `pages` associe une URL à (statut, contenu[, entêtes])
    ou à une exception."""

    def __init__(self, pages):
        self.pages = pages
        self.appels = []

    def __call__(self, req, timeout, taille_max):
        self.appels.append(req)
        page = self.pages.get(req.full_url, (404, b""))
        if isinstance(page, Exception):
            raise page
        statut, contenu, *entetes = page
        return Reponse(req.full_url, statut, contenu, entetes[0] if entetes else {})


def redirection(vers, statut=302):
    return (statut, b"", {"location": vers})


class FausseHorloge:
    def __init__(self):
        self.t = 1000.0
        self.pauses = []

    def __call__(self):
        return self.t

    def dormir(self, duree):
        self.pauses.append(duree)
        self.t += duree


@pytest.fixture
def horloge():
    return FausseHorloge()


@pytest.fixture
def client(stockage, enquete_id, horloge):
    def fabriquer(pages, delai_s=2.0):
        transport = FauxTransport(pages)
        c = ClientHTTP(stockage, enquete_id, "archives",
                       config=Config(delai_s=delai_s, user_agent="osint-lab-test"),
                       limiteur=LimiteurDebit(horloge, horloge.dormir),
                       transport=transport)
        return c, transport
    return fabriquer


ROBOTS_OK = (200, b"User-agent: *\nDisallow: /prive/\n")


def test_refus_sans_finalite(stockage, enquete_id):
    # La contrainte SQL empêche une finalité vide ; on simule une base corrompue.
    stockage.cx.execute("PRAGMA ignore_check_constraints = ON")
    stockage.cx.execute("UPDATE enquete SET finalite = '' WHERE id = ?", (enquete_id,))
    from core.stockage import FinaliteManquante
    with pytest.raises(FinaliteManquante):
        ClientHTTP(stockage, enquete_id, "archives", config=Config())


def test_refus_si_enquete_close(stockage, enquete_id, client):
    c, _ = client({})
    stockage.cloturer(enquete_id)
    with pytest.raises(EnqueteCloturee):
        c.get("https://exemple.fr/")


def test_get_journalise(stockage, enquete_id, client):
    c, transport = client({"https://exemple.fr/robots.txt": ROBOTS_OK,
                           "https://exemple.fr/page": (200, b"bonjour")})
    r = c.get("https://exemple.fr/page", requete="page d'accueil")
    assert r.ok and r.texte() == "bonjour"
    assert transport.appels[-1].get_header("User-agent") == "osint-lab-test"
    robots, page = stockage.journal(enquete_id)
    assert robots["outil"] == "archives/robots"
    assert (page["outil"], page["requete"], page["url"]) == (
        "archives", "page d'accueil", "https://exemple.fr/page")
    assert page["resultat_resume"].startswith("HTTP 200, 7 octets")


def test_robots_interdit(stockage, enquete_id, client):
    c, transport = client({"https://exemple.fr/robots.txt": ROBOTS_OK})
    with pytest.raises(AccesRefuse):
        c.get("https://exemple.fr/prive/fiche")
    assert [a.full_url for a in transport.appels] == ["https://exemple.fr/robots.txt"]
    assert stockage.journal(enquete_id)[-1]["resultat_resume"] == "refusé par robots.txt"


def test_robots_absent_tout_permis(client):
    c, _ = client({"https://exemple.fr/x": (200, b"ok")})   # robots.txt → 404
    assert c.get("https://exemple.fr/x").ok


@pytest.mark.parametrize("robots", [(503, b""), (403, b""), urllib.error.URLError("dns")])
def test_robots_inaccessible_tout_interdit(client, robots):
    c, _ = client({"https://exemple.fr/robots.txt": robots, "https://exemple.fr/x": (200, b"")})
    with pytest.raises(AccesRefuse):
        c.get("https://exemple.fr/x")


def test_robots_lu_une_fois_par_hote(client):
    c, transport = client({"https://exemple.fr/robots.txt": ROBOTS_OK,
                           "https://exemple.fr/a": (200, b""), "https://exemple.fr/b": (200, b"")})
    c.get("https://exemple.fr/a")
    c.get("https://exemple.fr/b")
    assert sum(a.full_url.endswith("robots.txt") for a in transport.appels) == 1


def test_api_officielle_sans_robots_mentionnee_au_journal(stockage, enquete_id, client):
    c, transport = client({"https://api.exemple.fr/v1?q=x": (200, b"{}")})
    c.get("https://api.exemple.fr/v1", params={"q": "x"}, verifier_robots=False)
    assert len(transport.appels) == 1
    assert "robots.txt non vérifié (API officielle)" in stockage.journal(enquete_id)[-1]["resultat_resume"]


def test_delai_entre_appels_meme_domaine(client, horloge):
    c, _ = client({"https://a.fr/1": (200, b""), "https://a.fr/2": (200, b""),
                   "https://b.fr/1": (200, b"")}, delai_s=3.0)
    c.get("https://a.fr/1", verifier_robots=False)
    c.get("https://b.fr/1", verifier_robots=False)     # autre domaine : pas de pause
    assert horloge.pauses == []
    horloge.t += 1.0
    c.get("https://a.fr/2", verifier_robots=False)
    assert horloge.pauses == [pytest.approx(2.0)]


def test_limiteur_partage_entre_clients(stockage, enquete_id, horloge):
    limiteur = LimiteurDebit(horloge, horloge.dormir)
    transport = FauxTransport({"https://a.fr/": (200, b"")})
    for outil in ("dorks", "archives"):
        ClientHTTP(stockage, enquete_id, outil, config=Config(delai_s=2.0),
                   limiteur=limiteur, transport=transport).get("https://a.fr/", verifier_robots=False)
    assert horloge.pauses == [pytest.approx(2.0)]


def test_erreur_reseau_journalisee(stockage, enquete_id, client):
    c, _ = client({"https://a.fr/": urllib.error.URLError("connexion refusée")})
    with pytest.raises(ErreurRequete):
        c.get("https://a.fr/", verifier_robots=False)
    assert "erreur" in stockage.journal(enquete_id)[-1]["resultat_resume"]


def test_taille_depassee_journalisee(stockage, enquete_id, client):
    c, _ = client({"https://a.fr/": TailleDepassee("trop gros")})
    with pytest.raises(TailleDepassee):
        c.get("https://a.fr/", verifier_robots=False)
    assert "trop gros" in stockage.journal(enquete_id)[-1]["resultat_resume"]


def test_statut_http_erreur_renvoye_pas_leve(client):
    c, _ = client({"https://a.fr/": (429, b"")})
    assert c.get("https://a.fr/", verifier_robots=False).statut == 429


def test_get_json(client):
    c, _ = client({"https://a.fr/ok": (200, b'{"n": 1}'), "https://a.fr/ko": (500, b""),
                   "https://a.fr/bad": (200, b"<html>")})
    assert c.get_json("https://a.fr/ok", verifier_robots=False) == {"n": 1}
    for url in ("https://a.fr/ko", "https://a.fr/bad"):
        with pytest.raises(ErreurRequete):
            c.get_json(url, verifier_robots=False)


def test_schema_non_http_refuse(client):
    c, _ = client({})
    with pytest.raises(ErreurRequete):
        c.get("file:///etc/passwd", verifier_robots=False)


def test_cle_api_masquee_dans_le_journal(stockage, enquete_id, client):
    url = "https://api.pappers.fr/v2/entreprise?siren=123&api_token=SECRET"
    c, _ = client({url: (200, b"{}")})
    c.get("https://api.pappers.fr/v2/entreprise", params={"siren": "123", "api_token": "SECRET"},
          verifier_robots=False)
    j = stockage.journal(enquete_id)[-1]
    assert "SECRET" not in j["url"] + j["requete"]
    assert "siren=123" in j["url"] and "api_token=***" in j["url"]


def test_masquer_url():
    assert masquer_url("https://a.fr/x") == "https://a.fr/x"
    assert masquer_url("https://a.fr/x?key=abc&q=jean") == "https://a.fr/x?key=***&q=jean"


# --- redirections ------------------------------------------------------------

def test_redirection_meme_domaine_sans_revalider_robots(stockage, enquete_id, client):
    c, transport = client({"https://api.exemple.fr/v1": redirection("/v2"),
                           "https://api.exemple.fr/v2": (200, b"ok")})
    r = c.get("https://api.exemple.fr/v1", verifier_robots=False)
    assert r.ok and r.url == "https://api.exemple.fr/v2"
    assert [a.full_url for a in transport.appels] == [
        "https://api.exemple.fr/v1", "https://api.exemple.fr/v2"]
    premier, second = stockage.journal(enquete_id)
    assert "redirection vers https://api.exemple.fr/v2" in premier["resultat_resume"]
    assert second["requete"].endswith("(redirection 1)")


def test_redirection_hors_domaine_revalide_robots(stockage, enquete_id, client):
    c, transport = client({
        "https://api.exemple.fr/v1": redirection("https://autre.com/prive/x"),
        "https://autre.com/robots.txt": (200, b"User-agent: *\nDisallow: /prive/\n"),
        "https://autre.com/prive/x": (200, b"ne doit pas etre lu")})
    with pytest.raises(AccesRefuse):
        c.get("https://api.exemple.fr/v1", verifier_robots=False)
    assert "https://autre.com/prive/x" not in [a.full_url for a in transport.appels]
    assert stockage.journal(enquete_id)[-1]["resultat_resume"] == "refusé par robots.txt"


def test_redirection_hors_domaine_autorisee(client):
    c, transport = client({
        "https://exemple.fr/x": redirection("https://autre.com/y", 301),
        "https://autre.com/y": (200, b"ok")})   # robots.txt absent → permis
    assert c.get("https://exemple.fr/x", verifier_robots=False).ok
    assert [a.full_url for a in transport.appels] == [
        "https://exemple.fr/x", "https://autre.com/robots.txt", "https://autre.com/y"]


def test_entetes_non_transmis_a_un_autre_hote(client):
    c, transport = client({"https://api.exemple.fr/v1": redirection("https://autre.com/y"),
                           "https://autre.com/y": (200, b"")})
    c.get("https://api.exemple.fr/v1", entetes={"hibp-api-key": "SECRET"}, verifier_robots=False)
    assert transport.appels[0].get_header("Hibp-api-key") == "SECRET"
    assert transport.appels[-1].get_header("Hibp-api-key") is None


def test_trop_de_redirections(client):
    c, _ = client({"https://a.fr/boucle": redirection("/boucle")})
    with pytest.raises(ErreurRequete, match="redirections"):
        c.get("https://a.fr/boucle", verifier_robots=False)


def test_redirection_vers_schema_interdit(client):
    c, _ = client({"https://a.fr/x": redirection("file:///etc/passwd")})
    with pytest.raises(ErreurRequete, match="schéma"):
        c.get("https://a.fr/x", verifier_robots=False)


def test_transport_urllib_ne_suit_pas_les_redirections():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from core.requetes import transport_urllib
    import urllib.request

    class Gestionnaire(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://ailleurs.invalid/")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_):
            pass

    serveur = HTTPServer(("127.0.0.1", 0), Gestionnaire)
    threading.Thread(target=serveur.handle_request, daemon=True).start()
    try:
        r = transport_urllib(urllib.request.Request(
            f"http://127.0.0.1:{serveur.server_port}/"), 5, 1000)
    finally:
        serveur.server_close()
    assert r.statut == 302 and r.entetes["location"] == "http://ailleurs.invalid/"
