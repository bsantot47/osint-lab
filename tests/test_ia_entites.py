import ast
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from ia import entites as ie
from pilote import enquete as enq
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)

PAGE = """<html><head><title>Équipe</title><style>.x{color:red}</style>
<script>var nom = "Pas Moi";</script></head><body>
<h1>Dupont Conseil</h1>
<p>Fondée le 12/03/2015 à Lyon par Jean Dupont.</p>
<p>Depuis mars 2021, Jean Dupont travaille avec Claire Martin et l'ACME.</p>
<p>Réunion prévue le 1er juin 2024 à Lyon.</p>
</body></html>"""


class FauxNLP:
    """Imite un pipeline spaCy : reconnaît les chaînes données à chaque occurrence."""

    def __init__(self, connus):
        self.connus = connus

    def __call__(self, texte):
        ents = []
        for chaine, etiquette in self.connus.items():
            debut = texte.find(chaine)
            while debut >= 0:
                ents.append(SimpleNamespace(text=chaine, label_=etiquette, start_char=debut,
                                            end_char=debut + len(chaine)))
                debut = texte.find(chaine, debut + 1)
        return SimpleNamespace(ents=ents)


NLP = FauxNLP({"Jean Dupont": "PER", "Claire Martin": "PER", "Dupont Conseil": "ORG",
               "ACME": "ORG", "Lyon": "LOC", "Équipe": "MISC", "J.": "PER"})


@pytest.fixture
def reseau_coupe(monkeypatch):
    def interdit(*a, **k):
        raise AssertionError("appel réseau interdit dans ia/")
    monkeypatch.setattr(socket.socket, "connect", interdit)
    monkeypatch.setattr(socket, "create_connection", interdit)
    monkeypatch.setattr(socket, "getaddrinfo", interdit)


# --- aucun appel réseau ------------------------------------------------------------

def test_aucun_module_reseau_importe_dans_ia():
    interdits = {"socket", "urllib", "http", "requests", "httpx", "aiohttp", "core.requetes"}
    for fichier in Path("ia").glob("*.py"):
        for noeud in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
            noms = []
            if isinstance(noeud, ast.Import):
                noms = [a.name for a in noeud.names]
            elif isinstance(noeud, ast.ImportFrom) and noeud.module:
                noms = [noeud.module]
            for nom in noms:
                assert nom.split(".")[0] not in interdits and nom not in interdits, \
                    f"{fichier} importe {nom}"


def test_analyse_reseau_coupe(reseau_coupe):
    mentions, _ = ie.analyser(ie.html_vers_texte(PAGE), NLP)
    assert mentions


# --- texte -----------------------------------------------------------------------

def test_html_vers_texte():
    texte = ie.html_vers_texte(PAGE)
    assert "Pas Moi" not in texte and "color" not in texte
    assert "Fondée le 12/03/2015 à Lyon par Jean Dupont." in texte.splitlines()


def test_lire_page(tmp_path):
    (tmp_path / "p.html").write_text(PAGE, encoding="utf-8")
    (tmp_path / "p.txt").write_text("Jean Dupont", encoding="utf-8")
    assert "Pas Moi" not in ie.lire_page(tmp_path / "p.html")
    assert ie.lire_page(tmp_path / "p.txt") == "Jean Dupont"
    with pytest.raises(ie.ErreurIA):
        ie.lire_page(tmp_path / "absent.html")


@pytest.mark.parametrize("texte,date", [("le 12/03/2015 à", "12/03/2015"),
                                         ("le 2021-03-12.", "2021-03-12"),
                                         ("le 1er juin 2024", "1er juin 2024"),
                                         ("Depuis Mars 2021,", "Mars 2021"),
                                         ("le 3 août 2019", "3 août 2019")])
def test_dates_par_script(texte, date):
    mentions, _ = ie.analyser(texte, FauxNLP({}))
    assert [(m.type, m.texte) for m in mentions] == [("date", date)]


# --- analyse ----------------------------------------------------------------------

def test_analyse_regroupe_et_filtre():
    mentions, tronque = ie.analyser(ie.html_vers_texte(PAGE), NLP)
    par_type = {(m.type, m.texte): m for m in mentions}
    assert not tronque
    assert par_type[("personne", "Jean Dupont")].occurrences == 2
    assert par_type[("lieu", "Lyon")].occurrences == 2
    assert ("organisation", "ACME") in par_type
    assert ("date", "12/03/2015") in par_type and ("date", "1er juin 2024") in par_type
    assert not any(m.texte == "Équipe" for m in mentions)        # MISC ignoré
    assert not any(m.texte == "J." for m in mentions)            # trop court
    assert "Fondée le 12/03/2015 à Lyon par Jean Dupont" in par_type[("personne", "Jean Dupont")].contexte


def test_troncature(monkeypatch):
    monkeypatch.setattr(ie, "TAILLE_MAX", 20)
    _, tronque = ie.analyser("x" * 30, FauxNLP({}))
    assert tronque


def test_rapprochement_avec_les_fiches():
    mentions, _ = ie.analyser(ie.html_vers_texte(PAGE), NLP)
    entites = [{"id": 1, "type": "personne", "valeur": "jean dupont"},
               {"id": 2, "type": "entreprise", "valeur": "Dupont Conseil"},
               {"id": 3, "type": "personne", "valeur": "Dupont"},
               {"id": 4, "type": "entreprise", "valeur": "Jean Dupont"}]   # mauvais type
    ie.rapprocher(mentions, entites)
    par_texte = {m.texte: m for m in mentions}
    assert par_texte["Jean Dupont"].fiches == [1, 3]
    assert par_texte["Dupont Conseil"].fiches == [2]
    assert par_texte["Claire Martin"].fiches == []
    assert par_texte["Lyon"].fiches == []


def test_propositions():
    mentions, _ = ie.analyser(ie.html_vers_texte(PAGE), NLP)
    ie.rapprocher(mentions, [{"id": 1, "type": "personne", "valeur": "Jean Dupont"}])
    texte = "\n".join(ie.propositions(mentions, entite_source=5))
    assert "Jean Dupont ×2" in texte and "correspond à la fiche E1" in texte
    assert "pour lier : ajouter lien 5 1 mentionné" in texte
    assert 'pour l\'ajouter : ajouter entite personne "Claire Martin"' in texte
    assert "pour l'ajouter : ajouter entite entreprise ACME" in texte
    assert "Lieux (1) :" in texte and "Dates (3) :" in texte


def test_charger_modele_absent():
    with pytest.raises(ie.ErreurIA, match="python -m spacy download"):
        ie.charger_modele("modele_qui_n_existe_pas")


# --- vrai modèle (sauté s'il n'est pas installé) ------------------------------------

def test_vrai_modele_francais(reseau_coupe):
    spacy = pytest.importorskip("spacy")
    try:
        nlp = spacy.load(ie.MODELE_DEFAUT)
    except OSError:
        pytest.skip("modèle fr_core_news_md non installé")
    mentions, _ = ie.analyser("Jean Dupont dirige la société Michelin à Clermont-Ferrand.", nlp)
    types = {m.texte: m.type for m in mentions}
    assert types.get("Jean Dupont") == "personne"
    assert types.get("Clermont-Ferrand") == "lieu"


# --- par la CLI --------------------------------------------------------------------

def test_cli_analyser(racine, capsys, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(ie, "charger_modele", lambda nom: NLP)
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "personne:Jean Dupont")
    cli(capsys, "ajouter", "entite", "domaine", "dupont-conseil.fr")
    code, out, _ = cli(capsys, "analyser", str(page), "--entite", "2")
    assert code == 0
    assert "rien n'est enregistré" in out and "correspond à la fiche E1" in out
    assert "pour lier : ajouter lien 2 1 mentionné" in out
    with enq.ouvrir("x") as e:
        assert len(e.stockage.entites(e.id)) == 2 and e.stockage.liens(e.id) == []
        assert list(e.chemin("bruts").glob("analyse-page-*.txt"))


def test_cli_analyser_sans_spacy(racine, capsys, tmp_path):  # noqa: F811
    page = tmp_path / "page.txt"
    page.write_text("Jean Dupont", encoding="utf-8")
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE)
    code, _, err = cli(capsys, "analyser", str(page), "--modele", "modele_absent")
    assert code == 1 and "spacy" in err.lower()
