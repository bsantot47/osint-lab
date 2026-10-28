from argparse import Namespace

import pytest

import core.requetes
from core.requetes import ErreurRequete
from outils import domaine
from outils.commun import ErreurOutil
from pilote import enquete as enq
from tests.conftest import T0
from tests.reseau import Routeur, config_avec_cles, sans_pause
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)

CRT = [
    {"id": 1, "common_name": "exemple.fr", "name_value": "exemple.fr\nwww.exemple.fr",
     "not_before": "2019-01-01T00:00:00", "not_after": "2019-04-01T00:00:00"},
    {"id": 2, "common_name": "*.exemple.fr", "name_value": "*.exemple.fr\nmail.exemple.fr",
     "not_before": "2024-01-01T00:00:00", "not_after": "2024-04-01T00:00:00"},
    {"id": 3, "common_name": "intranet.exemple.fr", "name_value": "intranet.exemple.fr\nautre.com",
     "not_before": "2023-05-01T00:00:00", "not_after": "2023-08-01T00:00:00"},
]
AMORCAGE = {"services": [[["com", "net"], ["https://rdap.verisign.com/com/v1/"]],
                         [["fr", "re"], ["https://rdap.nic.fr/"]]]}
RDAP = {
    "ldhName": "exemple.fr", "status": ["active"],
    "events": [{"eventAction": "registration", "eventDate": "2005-03-12T10:00:00Z"},
               {"eventAction": "expiration", "eventDate": "2027-03-12T10:00:00Z"}],
    "nameservers": [{"ldhName": "NS2.HEBERGEUR.NET"}, {"ldhName": "ns1.hebergeur.net"}],
    "secureDNS": {"delegationSigned": True},
    "entities": [
        {"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "OVH"]]]},
        {"roles": ["registrant"], "vcardArray": ["vcard", [
            ["fn", {}, "text", "REDACTED FOR PRIVACY"],
            ["org", {}, "text", "Dupont Conseil SAS"],
            ["email", {}, "text", "contact@exemple.fr"]]]},
    ],
}
SHODAN = {"domain": "exemple.fr", "subdomains": ["www", "vpn"],
          "data": [{"subdomain": "vpn", "type": "A", "value": "203.0.113.7",
                    "last_seen": "2026-09-01T00:00:00"},
                   {"subdomain": "", "type": "MX", "value": "mx.hebergeur.net",
                    "last_seen": "2026-09-02T00:00:00"}]}
ROUTES = {"crt.sh/": CRT, "data.iana.org/rdap/dns.json": AMORCAGE,
          "rdap.nic.fr/domain/exemple.fr": RDAP, "api.shodan.io/dns/domain/exemple.fr": SHODAN}


def opts(**kw):
    return Namespace(**{"sources": ",".join(domaine.SOURCES), "actifs_seulement": False, **kw})


@pytest.fixture
def enquete(racine, tmp_path, monkeypatch):  # noqa: F811
    config_avec_cles(tmp_path, monkeypatch)              # pas de clé Shodan par défaut
    with enq.creer("x", FINALITE, departs=[("domaine", "https://www.Exemple.fr/accueil")]) as e:
        yield e


def lancer(e, routeur, **kw):
    return domaine.lancer(e, e.stockage.entite(1), opts(**kw), maintenant=T0,
                          transport=routeur, limiteur=sans_pause())


# --- règle ferme : la cible n'est jamais contactée -------------------------------

def test_aucune_requete_vers_la_cible(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch, shodan="CLE")
    routeur = Routeur(ROUTES)
    lancer(enquete, routeur)
    assert routeur.hotes()
    for hote in routeur.hotes():
        assert not hote.endswith("exemple.fr")
    assert set(routeur.hotes()) <= {"crt.sh", "data.iana.org", "rdap.nic.fr", "api.shodan.io"}


def test_client_passif_refuse_la_cible_et_les_inconnus(enquete):
    routeur = Routeur({})
    client = domaine.ClientPassif(enquete.stockage, enquete.id, "domaine", transport=routeur,
                                  limiteur=sans_pause(), cible="exemple.fr")
    for url in ("https://exemple.fr/", "https://mail.exemple.fr/", "https://ailleurs.com/"):
        with pytest.raises(ErreurRequete, match="non autorisé"):
            client.get(url, verifier_robots=False)
    assert routeur.requetes == []


def test_redirection_vers_la_cible_bloquee(enquete):
    routeur = Routeur({"crt.sh/": (302, b"")})
    routeur.routes["crt.sh/robots.txt"] = (404, b"")

    class Redirige(Routeur):
        def __call__(self, req, timeout, taille_max):
            r = super().__call__(req, timeout, taille_max)
            if req.full_url.startswith("https://crt.sh/?"):
                r.entetes["location"] = "https://exemple.fr/piege"
            return r

    redirige = Redirige(routeur.routes)
    with pytest.raises(ErreurOutil):                      # crt.sh seul : aucune source
        lancer(enquete, redirige, sources="crtsh")
    assert "exemple.fr" not in redirige.hotes()


def test_serveur_rdap_ajoute_seulement_depuis_l_amorcage(enquete):
    routes = {**ROUTES, "data.iana.org/rdap/dns.json":
              {"services": [[["fr"], ["https://rdap.exemple.fr/"]]]}}   # serveur = la cible
    routeur = Routeur(routes)
    with pytest.raises(ErreurOutil, match="aucune source"):
        lancer(enquete, routeur, sources="rdap")
    assert "rdap.exemple.fr" not in routeur.hotes()


# --- analyse ---------------------------------------------------------------------

def test_normaliser_domaine():
    assert domaine.normaliser_domaine("https://www.Exemple.fr:443/accueil") == "exemple.fr"
    with pytest.raises(ErreurOutil):
        domaine.normaliser_domaine("pas un domaine")


def test_noms_certificats():
    noms = domaine.noms_certificats(CRT, "exemple.fr")
    assert list(noms) == ["exemple.fr", "intranet.exemple.fr", "mail.exemple.fr",
                          "www.exemple.fr"]
    assert "autre.com" not in noms
    assert noms["exemple.fr"] == {"certificats": 2, "debut": "2019-01-01", "fin": "2024-04-01"}


def test_serveur_rdap():
    assert domaine.serveur_rdap(AMORCAGE, "exemple.fr") == "https://rdap.nic.fr/"
    assert domaine.serveur_rdap(AMORCAGE, "exemple.zz") is None


def test_analyser_rdap_titulaire_masque_filtre():
    r = domaine.analyser_rdap(RDAP)
    assert r["registrar"] == "OVH"
    assert (r["creation"], r["expiration"]) == ("2005-03-12", "2027-03-12")
    assert r["serveurs_noms"] == ["ns1.hebergeur.net", "ns2.hebergeur.net"]
    assert r["dnssec"] is True
    assert r["titulaire"] == {"org": "Dupont Conseil SAS", "email": "contact@exemple.fr"}


@pytest.mark.parametrize("texte,cle,attendu", [
    ("crtsh,rdap,shodan", False, ["crtsh", "rdap"]),   # défaut : Shodan ignoré sans clé
    ("crtsh,rdap,shodan", True, ["crtsh", "rdap", "shodan"]),
    ("rdap", False, ["rdap"]),
])
def test_choisir_sources(texte, cle, attendu):
    assert domaine.choisir_sources(texte, cle)[0] == attendu


@pytest.mark.parametrize("texte", ["shodan", "crtsh,whois", ""])
def test_choisir_sources_refus(texte):
    with pytest.raises(ErreurOutil):
        domaine.choisir_sources(texte, False)


# --- exécution -------------------------------------------------------------------

def test_lancer_sans_cle_shodan(enquete):
    routeur = Routeur(ROUTES)
    res = lancer(enquete, routeur)
    texte = "\n".join(res.lignes)
    assert "Shodan : clé absente" in texte
    assert "api.shodan.io" not in routeur.hotes()
    assert routeur.params("crt.sh/") == {"q": "%.exemple.fr", "output": "json"}
    assert "crt.sh : 3 certificat(s), 4 nom(s) d'hôte" in texte
    assert "RDAP : bureau d'enregistrement OVH, créé le 2005-03-12, expire le 2027-03-12" in texte
    assert "titulaire : Dupont Conseil SAS, contact@exemple.fr" in texte
    assert 'ajouter entite entreprise "Dupont Conseil SAS"' in texte
    assert "ajouter entite email contact@exemple.fr" in texte
    assert "3 sous-domaine(s) publié(s) :" in texte and "intranet.exemple.fr (crt.sh)" in texte
    assert len(enquete.stockage.entites(enquete.id)) == 1          # rien créé


def test_lancer_avec_shodan(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch, shodan="CLE_SHODAN")
    routeur = Routeur(ROUTES)
    res = lancer(enquete, routeur)
    texte = "\n".join(res.lignes)
    assert routeur.params("api.shodan.io/dns/domain/exemple.fr") == {"key": "CLE_SHODAN"}
    assert "Shodan : 2 sous-domaine(s), 2 enregistrement(s) DNS" in texte
    assert "www.exemple.fr (Shodan, crt.sh)" in texte and "vpn.exemple.fr (Shodan)" in texte
    md = res.fichiers[-1].read_text(encoding="utf-8")
    assert "| vpn.exemple.fr | A | 203.0.113.7 | 2026-09-01 |" in md
    for j in enquete.stockage.journal(enquete.id):
        assert "CLE_SHODAN" not in j["url"] + j["requete"]


def test_actifs_seulement(enquete):
    routeur = Routeur(ROUTES)
    lancer(enquete, routeur, sources="crtsh", actifs_seulement=True)
    assert routeur.params("crt.sh/")["exclude"] == "expired"


def test_robots_de_crtsh_respecte(enquete):
    routes = {**ROUTES, "crt.sh/robots.txt": (200, b"User-agent: *\nDisallow: /\n")}
    texte = "\n".join(lancer(enquete, Routeur(routes)).lignes)
    assert "échec crt.sh : robots.txt interdit" in texte
    assert "RDAP : bureau d'enregistrement OVH" in texte    # les autres sources continuent


def test_toutes_les_sources_en_echec(enquete):
    with pytest.raises(ErreurOutil, match="aucune source n'a répondu"):
        lancer(enquete, Routeur({}))


def test_sorties(enquete):
    res = lancer(enquete, Routeur(ROUTES))
    assert [f.name for f in res.fichiers] == [
        "domaine-E1-20261001-090000.crtsh.json", "domaine-E1-20261001-090000.rdap.json",
        "domaine-E1-20261001-090000.md"]
    md = res.fichiers[-1].read_text(encoding="utf-8")
    assert "aucune requête vers le domaine lui-même" in md
    assert "<https://rdap.nic.fr/domain/exemple.fr>" in md


# --- par la CLI --------------------------------------------------------------------

def test_cli(racine, capsys, monkeypatch, tmp_path):  # noqa: F811
    config_avec_cles(tmp_path, monkeypatch)
    routeur = Routeur(ROUTES)
    monkeypatch.setattr(core.requetes, "transport_urllib", routeur)
    monkeypatch.setattr(core.requetes, "LIMITEUR_PARTAGE", sans_pause())
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "domaine:exemple.fr")
    code, out, _ = cli(capsys, "lancer", "domaine", "--entite", "1")
    assert code == 0 and "crt.sh : 3 certificat(s)" in out
    _, out, _ = cli(capsys, "statut")
    assert "domaine sur E1 (faite)" in out
    assert "Pivot suggéré : lancer archives --entite 1" in out
    assert not any(h.endswith("exemple.fr") for h in routeur.hotes())
