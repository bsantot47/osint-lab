import subprocess
from argparse import Namespace

import pytest

import core.requetes
from core.config import CleManquante
from outils import courriel
from outils.commun import ErreurOutil, Programme
from pilote import enquete as enq
from tests.conftest import T0
from tests.reseau import Routeur, config_avec_cles, sans_pause
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)
from tests.test_pseudo import FauxLanceur

HOLEHE_CSV = (
    "name,domain,method,frequent_rate_limit,rateLimit,exists,emailrecovery,phoneNumber,others\n"
    "twitter,twitter.com,register,False,False,True,,,\n"
    "spotify,spotify.com,register,True,False,True,,,\n"
    "amazon,amazon.com,login,False,False,False,,,\n"
)
HIBP = "haveibeenpwned.com/api/v3/breachedaccount/jean.dupont@exemple.fr"


def holehe(lanceur):
    return Programme("holehe", courriel.INSTALLATION, lambda n: "/opt/holehe", lanceur)


@pytest.fixture
def enquete(racine):  # noqa: F811
    with enq.creer("x", FINALITE, departs=[("email", "jean.dupont@exemple.fr")]) as e:
        yield e


def lancer(e, lanceur, **kw):
    return courriel.lancer(e, e.stockage.entite(1),
                           Namespace(**{"timeout": 15, "duree_max": 600, **kw}),
                           maintenant=T0, programme=holehe(lanceur))


# --- courriel (holehe) ---------------------------------------------------------

def test_arguments_sans_recuperation_de_mot_de_passe(enquete):
    l = FauxLanceur({"holehe_1_jean_results.csv": HOLEHE_CSV})
    lancer(enquete, l, timeout=9)
    [(cmd, _, kw)] = l.appels
    assert cmd[:2] == ["/opt/holehe", "jean.dupont@exemple.fr"]
    assert {"--only-used", "--no-password-recovery", "--csv"} <= set(cmd)
    assert cmd[cmd.index("-T") + 1] == "9"
    assert kw["timeout"] == 600


def test_sites_trouves(enquete):
    res = lancer(enquete, FauxLanceur({"holehe_1_jean_results.csv": HOLEHE_CSV}))
    texte = "\n".join(res.lignes)
    assert "holehe : 2 site(s)" in texte
    assert "spotify.com" in texte and "twitter.com" in texte and "amazon" not in texte
    assert ('ajouter fait "Adresse jean.dupont@exemple.fr associée à un compte spotify.com"'
            " --entite 1 --source https://spotify.com --outil courriel") in texte
    assert enquete.stockage.faits(enquete.id) == []
    [j] = enquete.stockage.journal(enquete.id)
    assert j["outil"] == "courriel" and "--no-password-recovery" in j["requete"]


def test_repli_stdout(enquete):
    texte = "\n".join(lancer(enquete, FauxLanceur(
        stdout="[+] twitter.com\n[-] amazon.com\n[x] instagram.com\n")).lignes)
    assert "holehe : 1 site(s)" in texte


def test_echec(enquete):
    with pytest.raises(ErreurOutil, match="holehe a échoué"):
        lancer(enquete, FauxLanceur(code=1))


def test_absent(enquete):
    with pytest.raises(ErreurOutil, match="holehe introuvable : pipx install holehe"):
        courriel.lancer(enquete, enquete.stockage.entite(1),
                        Namespace(timeout=15, duree_max=600),
                        programme=Programme("holehe", courriel.INSTALLATION, lambda n: None))


@pytest.mark.parametrize("valeur", ["--help", "jean dupont@x.fr", "pas-une-adresse",
                                    "-x@exemple.fr"])
def test_adresse_dangereuse_refusee(enquete, valeur):
    l = FauxLanceur()
    with pytest.raises(ErreurOutil, match="invalide"):
        courriel.lancer(enquete, brute(enquete, "email", valeur), Namespace(timeout=15, duree_max=600),
                        programme=holehe(l))
    assert l.appels == []


def test_interrompu(enquete):
    with pytest.raises(ErreurOutil, match="interrompu"):
        lancer(enquete, FauxLanceur(exception=subprocess.TimeoutExpired("holehe", 600)))


# --- fuite (HIBP) ------------------------------------------------------------------

def fuite(e, routeur, **kw):
    return courriel.lancer_fuite(e, e.stockage.entite(1),
                                 Namespace(**{"non_verifiees": False, **kw}),
                                 maintenant=T0, transport=routeur, limiteur=sans_pause())


def test_fuite_presence_seulement(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch, hibp="CLE_HIBP")
    routeur = Routeur({HIBP: [{"Name": "Dropbox"}, {"Name": "Adobe"}]})
    res = fuite(enquete, routeur)
    [req] = routeur.requetes
    assert req.get_header("Hibp-api-key") == "CLE_HIBP"
    assert routeur.params(HIBP) == {"truncateResponse": "true", "includeUnverified": "false"}
    texte = "\n".join(res.lignes)
    assert "présente dans 2 fuite(s) connue(s)" in texte
    assert "Adobe — https://haveibeenpwned.com/PwnedWebsites#Adobe" in texte
    [brut] = res.fichiers
    assert brut.read_text(encoding="utf-8").split() == ["[", '"Adobe",', '"Dropbox"', "]"]
    [j] = enquete.stockage.journal(enquete.id)
    assert j["outil"] == "fuite" and "CLE_HIBP" not in j["url"] + j["requete"]


def test_fuite_absente(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch, hibp="CLE")
    texte = "\n".join(fuite(enquete, Routeur({HIBP: (404, b"")})).lignes)
    assert "absente des fuites connues" in texte


def test_fuite_non_verifiees(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch, hibp="CLE")
    routeur = Routeur({HIBP: (404, b"")})
    fuite(enquete, routeur, non_verifiees=True)
    assert "includeUnverified" not in routeur.params(HIBP)


@pytest.mark.parametrize("statut,message", [(429, "trop de requêtes"), (401, "clé refusée")])
def test_fuite_erreurs(enquete, tmp_path, monkeypatch, statut, message):
    config_avec_cles(tmp_path, monkeypatch, hibp="CLE")
    with pytest.raises(ErreurOutil, match=message):
        fuite(enquete, Routeur({HIBP: (statut, b"")}))


def test_fuite_sans_cle(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch)
    routeur = Routeur({})
    with pytest.raises(CleManquante):
        fuite(enquete, routeur)
    assert routeur.requetes == []


# --- par la CLI --------------------------------------------------------------------

def test_cli_courriel_puis_fuite(racine, capsys, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(courriel, "Programme", lambda nom, installation: holehe(
        FauxLanceur({"holehe_1_jean_results.csv": HOLEHE_CSV})))
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "email:jean.dupont@exemple.fr")
    code, out, _ = cli(capsys, "lancer", "courriel", "--entite", "1")
    assert code == 0 and "holehe : 2 site(s)" in out
    _, out, _ = cli(capsys, "statut")
    assert "Pivot suggéré : lancer fuite --entite 1" in out

    config_avec_cles(tmp_path, monkeypatch)
    _, _, err = cli(capsys, "lancer", "fuite", "--entite", "1")
    assert "clé d'API « hibp » absente" in err

    config_avec_cles(tmp_path, monkeypatch, hibp="CLE")
    monkeypatch.setattr(core.requetes, "transport_urllib", Routeur({HIBP: (404, b"")}))
    monkeypatch.setattr(core.requetes, "LIMITEUR_PARTAGE", sans_pause())
    code, out, _ = cli(capsys, "lancer", "fuite", "--entite", "1")
    assert code == 0 and "absente" in out
    _, out, _ = cli(capsys, "statut")
    assert "fuite sur E1 (faite)" in out


def brute(e, type_, valeur):
    """Entité insérée sans validation, pour vérifier que l'outil refuse lui-même."""
    cur = e.stockage.cx.execute(
        "INSERT INTO entite (enquete_id, type, valeur, statut, origine, date)"
        " VALUES (?, ?, ?, 'a_verifier', 'manuel', '2026-10-01T09:00:00')",
        (e.id, type_, valeur))
    return {"id": cur.lastrowid}
