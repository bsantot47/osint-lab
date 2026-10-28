"""Garde-fous légaux vérifiés sur l'ensemble du projet, pas outil par outil.

Chaque test correspond à un engagement de la spec ; DOC_TECHNIQUE.md renvoie ici.
"""

import ast
import sqlite3
from argparse import Namespace
from pathlib import Path

import pytest

from core.rapport import generer_rapport
from core.stockage import (ConfirmationImpossible, EnqueteCloturee, ErreurStockage,
                           FinaliteManquante)
from outils import OUTILS
from outils.commun import ErreurOutil
from pilote import enquete as enq
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)

RACINE = Path(__file__).resolve().parent.parent
CODE = [p for d in ("core", "pilote", "outils", "ia") for p in (RACINE / d).glob("*.py")]
RESEAU = {"socket", "http", "requests", "httpx", "aiohttp", "ftplib", "smtplib",
          "telnetlib", "ssl"}


def imports(fichier: Path) -> list[str]:
    noms = []
    for n in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            noms += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module:
            noms.append(n.module)
    return noms


@pytest.fixture
def e(racine):  # noqa: F811
    with enq.creer("x", FINALITE, departs=[("personne", "Jean Dupont")]) as en:
        yield en


# --- finalité --------------------------------------------------------------------

def test_refus_sans_finalite(racine, capsys):  # noqa: F811
    with pytest.raises(FinaliteManquante):
        enq.creer("y", "   ")
    code, _, err = cli(capsys, "nouvelle", "z")
    assert code != 0
    with enq.creer("w", FINALITE) as en:
        with pytest.raises(sqlite3.IntegrityError):
            en.stockage.cx.execute("UPDATE enquete SET finalite = ' ' WHERE id = ?", (en.id,))


# --- réseau et sous-processus : points d'entrée uniques ------------------------------

def test_reseau_uniquement_par_core_requetes():
    for f in CODE:
        for nom in imports(f):
            racine_mod = nom.split(".")[0]
            if f.name == "requetes.py" and f.parent.name == "core":
                continue
            assert racine_mod not in RESEAU, f"{f} importe {nom}"
            assert nom != "urllib.request", f"{f} importe urllib.request"


def test_sous_processus_uniquement_par_programme():
    for f in CODE:
        texte = f.read_text(encoding="utf-8")
        assert "shell=True" not in texte, f
        assert "os.system" not in texte and "os.popen" not in texte, f
        if f.name != "commun.py":
            assert "subprocess" not in imports(f), f"{f} importe subprocess"


def test_ia_sans_reseau_ni_core_requetes():
    for f in (RACINE / "ia").glob("*.py"):
        for nom in imports(f):
            assert nom.split(".")[0] not in RESEAU | {"urllib"} and nom != "core.requetes", f


# --- aucune collecte hors cadre, même en appelant l'outil directement ----------------

@pytest.mark.parametrize("nom", sorted(OUTILS))
@pytest.mark.parametrize("statut", ["ecarte", "hors_perimetre"])
def test_outil_refuse_entite_inactive(e, nom, statut):
    outil = OUTILS[nom]
    i = e.stockage.ajouter_entite(e.id, outil.TYPES[0], "cible@exemple.fr"
                                  if outil.TYPES[0] == "email" else "cible")
    e.stockage.changer_statut_entite(i, statut)
    with pytest.raises(ErreurOutil, match="aucune collecte"):
        outil.lancer(e, e.stockage.entite(i), Namespace())


@pytest.mark.parametrize("nom", sorted(OUTILS))
def test_outil_refuse_enquete_close(e, nom):
    outil = OUTILS[nom]
    i = e.stockage.ajouter_entite(e.id, outil.TYPES[0], "cible")
    entite = e.stockage.entite(i)
    e.cloturer()
    with pytest.raises(EnqueteCloturee):
        outil.lancer(e, entite, Namespace())


@pytest.mark.parametrize("nom", sorted(OUTILS))
def test_outil_refuse_mauvais_type(e, nom):
    outil = OUTILS[nom]
    autre = next(t for t in ("image", "numero", "personne") if t not in outil.TYPES)
    i = e.stockage.ajouter_entite(e.id, autre, "cible")
    with pytest.raises(ErreurOutil, match="ne s'applique pas"):
        outil.lancer(e, e.stockage.entite(i), Namespace())


def test_outil_refuse_entite_d_une_autre_enquete(e):
    autre = e.stockage.creer_enquete("autre", FINALITE)
    etrangere = e.stockage.ajouter_entite(autre, "personne", "Paul Martin")
    with pytest.raises(ErreurOutil, match="n'appartient pas"):
        OUTILS["dorks"].lancer(e, {"id": etrangere}, Namespace(contexte=""))


# --- journal ----------------------------------------------------------------------

def test_journal_en_ajout_seul(e):
    e.stockage.journaliser(e.id, "test", "requête")
    with pytest.raises(sqlite3.IntegrityError, match="journal"):
        e.stockage.cx.execute("UPDATE journal SET requete = 'effacé'")
    with pytest.raises(sqlite3.IntegrityError, match="journal"):
        e.stockage.cx.execute("DELETE FROM journal")


def test_journal_survit_a_la_purge(e):
    from datetime import timedelta
    e.stockage.journaliser(e.id, "test", "requête")
    _, expiration = e.cloturer()
    avant = [tuple(j) for j in e.stockage.journal(e.id)]
    e.stockage.purger_expirees(expiration + timedelta(days=1))
    apres = [tuple(j) for j in e.stockage.journal(e.id)]
    assert apres[:len(avant)] == avant                 # rien d'effacé
    assert "purge" in " ".join(map(str, apres[-1]))    # la purge elle-même est tracée
    assert e.stockage.entites(e.id) == []


def test_outil_sans_reseau_journalise(e):
    OUTILS["dorks"].lancer(e, e.stockage.entite(1), Namespace(contexte=""))
    assert [j["outil"] for j in e.stockage.journal(e.id)][-1] == "dorks"


# --- confirmation verrouillée -------------------------------------------------------

def test_confirmation_verrouillee(e):
    s = e.stockage
    f = s.ajouter_fait(e.id, "énoncé", 1)
    s.ajouter_source(f, "https://a.fr/1", "manuel")
    s.ajouter_source(f, "https://www.a.fr/2", "manuel")           # même domaine
    for tentative in (lambda: s.changer_statut_fait(f, "confirme"),
                      lambda: s.changer_statut_entite(1, "confirme"),
                      lambda: s.confirmer_fait(f)):
        with pytest.raises((ErreurStockage, ConfirmationImpossible)):
            tentative()
    with pytest.raises(sqlite3.IntegrityError):
        s.cx.execute("UPDATE fait SET statut = 'confirme' WHERE id = ?", (f,))
    with pytest.raises(sqlite3.IntegrityError):
        s.cx.execute("INSERT INTO fait (enquete_id, enonce, statut, date) VALUES"
                     " (?, 'x', 'confirme', '2026-01-01')", (e.id,))
    assert s.fait(f)["statut"] == "a_verifier"


def test_seuls_stockage_et_cli_touchent_a_la_confirmation():
    """confirmer_fait n'est appelé que par la commande `confirmer` ; aucun autre
    module n'écrit de statut en SQL."""
    for f in CODE:
        if f.name == "stockage.py":
            continue
        texte = f.read_text(encoding="utf-8")
        assert "SET statut" not in texte, f
        if "confirmer_fait(" in texte:
            assert f.name == "cli.py", f
            assert texte.count("confirmer_fait(") == 1, "un seul chemin : la commande confirmer"


# --- hors périmètre absent du rapport ----------------------------------------------

def test_hors_perimetre_absent_du_rapport(e):
    s = e.stockage
    tiers = s.ajouter_entite(e.id, "personne", "Voisin Sans Rapport")
    f = s.ajouter_fait(e.id, "adresse du voisin", tiers)
    s.ajouter_source(f, "https://voisin.fr", "manuel")
    s.ajouter_lien(e.id, 1, tiers, "voisin")
    s.changer_statut_entite(tiers, "hors_perimetre")
    rapport = generer_rapport(s, e.id)
    assert "Voisin" not in rapport and "voisin" not in rapport


# --- pas de scan actif, pas de fuite téléchargée ------------------------------------

def test_domaine_ne_contacte_jamais_la_cible(e):
    from core.requetes import ErreurRequete
    from outils import domaine
    envoyes = []
    client = domaine.ClientPassif(e.stockage, e.id, "domaine",
                                  transport=lambda *a, **k: envoyes.append(a),
                                  cible="exemple.fr")
    client.autoriser("https://www.exemple.fr/")       # même si on l'y force
    for url in ("https://exemple.fr/", "https://mail.exemple.fr/", "https://autre.org/"):
        with pytest.raises(ErreurRequete, match="non autorisé"):
            client._envoyer(url, {})
    assert envoyes == []


def test_aucun_outil_de_scan():
    for f in CODE:
        texte = f.read_text(encoding="utf-8").lower()
        for interdit in ("nmap", "masscan", "zmap", "whatweb", "nikto", "dnsrecon", "gobuster"):
            assert interdit not in texte, f"{f} mentionne {interdit}"


def test_fuite_ne_recupere_que_la_presence():
    from outils import courriel
    src = Path(courriel.__file__).read_text(encoding="utf-8")
    assert '"truncateResponse": "true"' in src
    assert "pasteaccount" not in src.lower()


def test_dorks_sans_fuites():
    from outils import dorks
    tout = " ".join(m for modeles in dorks.MODELES.values() for _, m in modeles).lower()
    for interdit in ("filetype:csv", "filetype:xls", "filetype:sql", "pastebin", "ghostbin"):
        assert interdit not in tout
