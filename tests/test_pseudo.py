import json
import subprocess
import sys
from argparse import Namespace

import pytest

from outils import pseudo
from outils.commun import ErreurOutil, Programme
from pilote import enquete as enq
from tests.conftest import T0
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)

SHERLOCK_CSV = (
    "username,name,url_main,url_user,exists,http_status,response_time_s\n"
    "jdupont,GitHub,https://github.com,https://github.com/jdupont,Claimed,200,0.4\n"
    "jdupont,Reddit,https://reddit.com,https://www.reddit.com/user/jdupont,Claimed,200,0.6\n"
    "jdupont,Twitch,https://twitch.tv,https://twitch.tv/jdupont,Available,404,0.3\n"
)
MAIGRET_JSON = {
    "GitHub": {"url_user": "https://github.com/jdupont/", "status": {"status": "Claimed"}},
    "Keybase": {"url_user": "https://keybase.io/jdupont", "status": {"status": "Claimed"}},
    "VK": {"url_user": "https://vk.com/jdupont", "status": {"status": "Available"}},
}


class FauxLanceur:
    """Simule un programme : écrit ses rapports dans cwd, renvoie stdout et code."""

    def __init__(self, fichiers=None, stdout="", code=0, exception=None):
        self.fichiers = fichiers or {}
        self.stdout, self.code, self.exception = stdout, code, exception
        self.appels = []

    def __call__(self, cmd, cwd, **kw):
        self.appels.append((cmd, cwd, kw))
        if self.exception:
            raise self.exception
        for nom, contenu in self.fichiers.items():
            (cwd / nom).write_text(contenu, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.code, self.stdout, "")


def programmes(sherlock=None, maigret=None):
    def fabriquer(nom, lanceur):
        trouver = (lambda n: f"/opt/{n}") if lanceur else (lambda n: None)
        return Programme(nom, pseudo.INSTALLATION[nom], trouver, lanceur or FauxLanceur())
    return {"sherlock": fabriquer("sherlock", sherlock), "maigret": fabriquer("maigret", maigret)}


def opts(**kw):
    return Namespace(**{"programmes": "sherlock,maigret", "timeout": 15, "connexions": 20,
                        "sites": 500, "duree_max": 900, **kw})


@pytest.fixture
def enquete(racine):  # noqa: F811
    with enq.creer("x", FINALITE, departs=[("compte", "@jdupont")]) as e:
        yield e


def lancer(e, progs, **kw):
    return pseudo.lancer(e, e.stockage.entite(1), opts(**kw), maintenant=T0, programmes=progs)


def sherlock_ok():
    return FauxLanceur({"jdupont.csv": SHERLOCK_CSV})


def maigret_ok():
    return FauxLanceur({"report_jdupont_simple.json": json.dumps(MAIGRET_JSON)})


# --- arguments : options de débit, jamais de contournement -------------------------

def test_arguments_passes(enquete):
    s, m = sherlock_ok(), maigret_ok()
    lancer(enquete, programmes(s, m), timeout=7, connexions=5, sites=100)
    [(cmd_s, cwd_s, kw_s)] = s.appels
    [(cmd_m, cwd_m, _)] = m.appels
    assert cmd_s[0] == "/opt/sherlock" and cmd_s[1] == "jdupont"
    assert cmd_s[cmd_s.index("--timeout") + 1] == "7"
    assert cmd_s[cmd_s.index("--folderoutput") + 1] == str(cwd_s)
    assert cmd_m[cmd_m.index("--max-connections") + 1] == "5"
    assert cmd_m[cmd_m.index("--top-sites") + 1] == "100"
    assert {"--no-recursion", "--no-extracting"} <= set(cmd_m)
    assert kw_s["timeout"] == 900 and kw_s["stdin"] == subprocess.DEVNULL
    assert "shell" not in kw_s
    for cmd in (cmd_s, cmd_m):
        assert not {"--tor", "--unique-tor", "--proxy", "-p"} & set(cmd)


@pytest.mark.parametrize("valeur", ["--help", "-p http://proxy", "jean dupont", "", "a;rm -rf"])
def test_pseudo_dangereux_refuse_avant_tout_lancement(enquete, valeur):
    s = sherlock_ok()
    with pytest.raises(ErreurOutil, match="pseudo inutilisable"):
        pseudo.lancer(enquete, brute(enquete, "compte", valeur), opts(), programmes=programmes(s))
    assert s.appels == []


def test_arobase_retire():
    assert pseudo.verifier_pseudo(" @jdupont ") == "jdupont"


@pytest.mark.parametrize("mauvais", [dict(timeout=0), dict(connexions=0), dict(sites=0),
                                     dict(duree_max=5)])
def test_options_refusees(enquete, mauvais):
    with pytest.raises(ErreurOutil):
        lancer(enquete, programmes(sherlock_ok(), maigret_ok()), **mauvais)


# --- agrégation ------------------------------------------------------------------

def test_agregation_des_deux_programmes(enquete):
    res = lancer(enquete, programmes(sherlock_ok(), maigret_ok()))
    texte = "\n".join(res.lignes)
    assert "sherlock : 2 compte(s) trouvé(s)" in texte
    assert "maigret : 2 compte(s) trouvé(s)" in texte
    assert "3 compte(s) distinct(s) pour « jdupont »" in texte
    assert "GitHub : https://github.com/jdupont (maigret, sherlock)" in texte   # dédoublonné
    assert "Twitch" not in texte and "VK" not in texte
    assert texte.index("GitHub") < texte.index("Keybase")       # trouvé par deux d'abord
    assert ('ajouter fait "Compte jdupont présent sur GitHub" --entite 1'
            " --source https://github.com/jdupont --outil pseudo") in texte
    assert len(enquete.stockage.entites(enquete.id)) == 1       # rien créé
    assert enquete.stockage.faits(enquete.id) == []


def test_repli_sur_stdout_sans_rapport(enquete):
    s = FauxLanceur(stdout="[*] Checking jdupont\n[+] GitHub: https://github.com/jdupont\n"
                           "[-] Twitch: Not Found!\n")
    texte = "\n".join(lancer(enquete, programmes(s), programmes="sherlock").lignes)
    assert "sherlock : 1 compte(s) trouvé(s)" in texte


def test_journal_et_sorties(enquete):
    res = lancer(enquete, programmes(sherlock_ok(), maigret_ok()))
    journal = enquete.stockage.journal(enquete.id)
    assert [j["outil"] for j in journal] == ["pseudo", "pseudo"]
    assert journal[0]["requete"].startswith("sherlock jdupont --timeout 15")
    assert journal[1]["requete"].startswith("maigret jdupont")
    assert journal[0]["resultat_resume"].startswith("code retour 0")
    dossier, synthese = res.fichiers
    assert dossier.name == "pseudo-E1-20261001-090000"
    assert (dossier / "sherlock" / "jdupont.csv").is_file()
    assert (dossier / "maigret" / "maigret.stdout.txt").is_file()
    assert "<https://keybase.io/jdupont>" in synthese.read_text(encoding="utf-8")


# --- disponibilité et échecs -----------------------------------------------------

def test_programme_absent_ignore_par_defaut(enquete):
    texte = "\n".join(lancer(enquete, programmes(sherlock=sherlock_ok())).lignes)
    assert "maigret absent, ignoré (pipx install maigret (dans la VM))" in texte
    assert "sherlock : 2 compte(s)" in texte


def test_programme_absent_demande_explicitement(enquete):
    with pytest.raises(ErreurOutil, match="introuvable : maigret"):
        lancer(enquete, programmes(sherlock=sherlock_ok()), programmes="maigret")


def test_aucun_programme_installe(enquete):
    with pytest.raises(ErreurOutil, match="introuvable"):
        lancer(enquete, programmes())


def test_un_echec_n_empeche_pas_l_autre(enquete):
    texte = "\n".join(lancer(enquete, programmes(FauxLanceur(code=1), maigret_ok())).lignes)
    assert "sherlock : échec (code 1)" in texte and "maigret : 2 compte(s)" in texte


def test_tous_en_echec(enquete):
    with pytest.raises(ErreurOutil, match="aucun programme n'a abouti"):
        lancer(enquete, programmes(FauxLanceur(code=2), FauxLanceur(code=2)))


def test_depassement_de_duree_journalise(enquete):
    s = FauxLanceur(exception=subprocess.TimeoutExpired("sherlock", 900))
    with pytest.raises(ErreurOutil, match="interrompu"):
        lancer(enquete, programmes(s), programmes="sherlock")
    assert enquete.stockage.journal(enquete.id)[-1]["resultat_resume"] == "interrompu après 900 s"


# --- Programme.executer avec un vrai sous-processus ---------------------------------

def python_comme(nom):
    return Programme(nom, "", trouver=lambda _: sys.executable)


def test_vrai_sous_processus(enquete, tmp_path):
    r = python_comme("essai").executer(
        enquete, "pseudo", ["-c", "import os; print(os.getcwd())"], tmp_path, 30)
    assert r.returncode == 0 and r.stdout.strip() == str(tmp_path)
    assert (tmp_path / "essai.stdout.txt").read_text().strip() == str(tmp_path)
    j = enquete.stockage.journal(enquete.id)[-1]
    assert j["requete"] == "essai -c 'import os; print(os.getcwd())'"


def test_vrai_sous_processus_sans_shell(enquete, tmp_path):
    r = python_comme("essai").executer(
        enquete, "pseudo", ["-c", "import sys; print(sys.argv[1])", "$(echo injecte); ls"],
        tmp_path, 30)
    assert r.stdout.strip() == "$(echo injecte); ls"


def test_vrai_sous_processus_interrompu(enquete, tmp_path):
    with pytest.raises(ErreurOutil, match="interrompu après 1 s"):
        python_comme("lent").executer(enquete, "pseudo", ["-c", "import time; time.sleep(30)"],
                                      tmp_path, 1)


def test_refus_si_enquete_close(enquete, tmp_path):
    enquete.stockage.cloturer(enquete.id)
    lanceur = FauxLanceur()
    with pytest.raises(Exception, match="close"):
        Programme("sherlock", "", lambda n: "/opt/sherlock", lanceur).executer(
            enquete, "pseudo", [], tmp_path, 10)
    assert lanceur.appels == []


# --- par la CLI --------------------------------------------------------------------

def test_cli(racine, capsys, monkeypatch):  # noqa: F811
    lanceurs = {"sherlock": sherlock_ok(), "maigret": maigret_ok()}
    monkeypatch.setattr(pseudo, "Programme", lambda nom, installation: Programme(
        nom, installation, lambda n: f"/opt/{n}", lanceurs[nom]))
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "compte:jdupont")
    code, out, _ = cli(capsys, "lancer", "pseudo", "--entite", "1")
    assert code == 0 and "3 compte(s) distinct(s)" in out
    _, out, _ = cli(capsys, "statut")
    assert "pseudo sur E1 (faite)" in out


def brute(e, type_, valeur):
    """Entité insérée sans validation, pour vérifier que l'outil refuse lui-même."""
    cur = e.stockage.cx.execute(
        "INSERT INTO entite (enquete_id, type, valeur, statut, origine, date)"
        " VALUES (?, ?, ?, 'a_verifier', 'manuel', '2026-10-01T09:00:00')",
        (e.id, type_, valeur))
    return {"id": cur.lastrowid}
