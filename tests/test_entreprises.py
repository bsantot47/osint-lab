from argparse import Namespace

import pytest

import core.requetes
from outils import entreprises
from outils.commun import ErreurOutil, commande, normaliser
from pilote import enquete as enq
from tests.conftest import T0
from tests.reseau import Routeur, config_avec_cles, sans_pause
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)

RECHERCHE = "recherche-entreprises.api.gouv.fr/search"
PAPPERS = "api.pappers.fr/v2/entreprise"


def societe(siren, nom, dirigeants, etat="A"):
    return {"siren": siren, "nom_complet": nom, "etat_administratif": etat,
            "date_creation": "2015-03-01", "activite_principale": "70.22Z",
            "siege": {"adresse": "1 rue de la Paix 69001 Lyon"}, "dirigeants": dirigeants}


JEAN = {"type_dirigeant": "personne physique", "nom": "DUPONT", "prenoms": "Jean Marc",
        "annee_de_naissance": "1975", "qualite": "Président"}
AUTRE = {"type_dirigeant": "personne physique", "nom": "MARTIN", "prenoms": "Claire",
         "annee_de_naissance": "1980", "qualite": "Directeur général"}
HOLDING = {"type_dirigeant": "personne morale", "denomination": "DUPONT HOLDING",
           "siren": "111111111", "qualite": "Associé"}
RESULTATS = {"results": [
    societe("123456789", "DUPONT CONSEIL", [JEAN, AUTRE]),
    societe("987654321", "MARTIN DUPONT IMMO", [AUTRE, HOLDING], etat="C"),
]}
FICHE_PAPPERS = {"siren": "123456789", "representants": [
    {"nom_complet": "Jean Marc DUPONT", "qualite": "Président", "personne_morale": False,
     "date_prise_de_poste": "2015-03-01"}],
    "beneficiaires_effectifs": [{"nom": "SECRET_RBE"}]}


def opts(**kw):
    return Namespace(**{"siren": None, "limite": 10, "sans_pappers": False, **kw})


@pytest.fixture
def enquete(racine):  # noqa: F811
    with enq.creer("x", FINALITE, departs=[("personne", "Jean Dupont"),
                                           ("entreprise", "123 456 789"),
                                           ("entreprise", "Dupont Conseil")]) as e:
        yield e


def lancer(e, entite_id, routeur, **kw):
    return entreprises.lancer(e, e.stockage.entite(entite_id), opts(**kw), maintenant=T0,
                              transport=routeur, limiteur=sans_pause())


# --- utilitaires ------------------------------------------------------------

def test_normaliser_et_correspondance():
    assert normaliser("  Jean-Marc DUPONT, Hélène ") == "jean marc dupont helene"
    assert entreprises.correspond("Jean Dupont", "Jean Marc DUPONT")
    assert entreprises.correspond("jéan dupont", "JEAN DUPONT")
    assert not entreprises.correspond("Jean Dupont", "Claire DUPONT")
    assert not entreprises.correspond("Jean Dupont", "")


@pytest.mark.parametrize("texte,attendu", [("123456789", "123456789"),
                                            ("123 456 789", "123456789"),
                                            ("12345678", None), ("Dupont", None), (None, None)])
def test_siren_valide(texte, attendu):
    assert entreprises.siren_valide(texte) == attendu


def test_commande_prete_a_copier():
    assert commande("ajouter", "entite", "entreprise", 'DUPONT "CONSEIL"') == \
        'ajouter entite entreprise "DUPONT \\"CONSEIL\\""'


def test_beneficiaires_effectifs_purges_partout():
    donnees = {"a": 1, "beneficiaires_effectifs": [1], "b": [{"beneficiaires_effectifs": 2}]}
    assert entreprises.purger_champs_interdits(donnees) == {"a": 1, "b": [{}]}


# --- personne ----------------------------------------------------------------

def test_personne_entreprises_ou_elle_est_dirigeante(enquete):
    routeur = Routeur({RECHERCHE: RESULTATS})
    res = lancer(enquete, 1, routeur)
    texte = "\n".join(res.lignes)
    assert routeur.params(RECHERCHE)["q"] == "Jean Dupont"
    assert "1 entreprise(s) dont un dirigeant s'appelle « Jean Dupont »" in texte
    assert "DUPONT CONSEIL — SIREN 123456789, active" in texte
    assert "MARTIN DUPONT IMMO" not in texte            # « Dupont » seul ne suffit pas
    assert "► Jean Marc DUPONT (Président, né(e) en 1975)" in texte
    assert "- Claire MARTIN" in texte
    assert "source : https://annuaire-entreprises.data.gouv.fr/entreprise/123456789" in texte
    assert 'ajouter entite entreprise "DUPONT CONSEIL"' in texte
    assert routeur.hotes() == ["recherche-entreprises.api.gouv.fr"]   # Pappers : SIREN requis
    assert len(enquete.stockage.entites(enquete.id)) == 3              # rien créé


# --- entreprise ----------------------------------------------------------------

def test_entreprise_par_siren_sans_cle_pappers(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch)
    routeur = Routeur({RECHERCHE: RESULTATS})
    res = lancer(enquete, 2, routeur)
    texte = "\n".join(res.lignes)
    assert routeur.params(RECHERCHE)["q"] == "123456789"
    assert "Entreprise SIREN 123456789 :" in texte and "MARTIN DUPONT IMMO" not in texte
    assert "Pappers : clé absente" in texte


def test_entreprise_par_siren_avec_pappers(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch, pappers="CLE_PAPPERS")
    routeur = Routeur({RECHERCHE: RESULTATS, PAPPERS: FICHE_PAPPERS})
    res = lancer(enquete, 2, routeur)
    texte = "\n".join(res.lignes)
    assert routeur.params(PAPPERS) == {"siren": "123456789", "api_token": "CLE_PAPPERS"}
    assert "Pappers : 1 représentant(s)" in texte
    assert "Jean Marc DUPONT (Président, depuis le 2015-03-01)" in texte
    for f in res.fichiers:
        assert "SECRET_RBE" not in f.read_text(encoding="utf-8")
    for j in enquete.stockage.journal(enquete.id):
        assert "CLE_PAPPERS" not in j["url"] + j["requete"]


def test_sans_pappers(enquete, tmp_path, monkeypatch):
    config_avec_cles(tmp_path, monkeypatch, pappers="CLE")
    routeur = Routeur({RECHERCHE: RESULTATS})
    lancer(enquete, 2, routeur, sans_pappers=True)
    assert routeur.hotes() == ["recherche-entreprises.api.gouv.fr"]


def test_entreprise_par_nom_liste_les_candidates(enquete):
    routeur = Routeur({RECHERCHE: RESULTATS})
    texte = "\n".join(lancer(enquete, 3, routeur).lignes)
    assert "2 entreprise(s) candidate(s) pour « Dupont Conseil »" in texte
    assert "lancer entreprises --entite 3 --siren 123456789" in texte


def test_option_siren_prioritaire(enquete):
    routeur = Routeur({RECHERCHE: RESULTATS})
    texte = "\n".join(lancer(enquete, 3, routeur, siren="987654321").lignes)
    assert routeur.params(RECHERCHE)["q"] == "987654321"
    assert "MARTIN DUPONT IMMO — SIREN 987654321, cessée" in texte


def test_siren_introuvable(enquete):
    texte = "\n".join(lancer(enquete, 2, Routeur({RECHERCHE: {"results": []}})).lignes)
    assert "Aucune entreprise trouvée pour le SIREN 123456789" in texte


@pytest.mark.parametrize("mauvais", [dict(limite=0), dict(limite=26), dict(siren="12")])
def test_options_refusees(enquete, mauvais):
    with pytest.raises(ErreurOutil):
        lancer(enquete, 1, Routeur({}), **mauvais)


def test_sorties_et_journal(enquete):
    res = lancer(enquete, 1, Routeur({RECHERCHE: RESULTATS}))
    assert [f.name for f in res.fichiers] == ["entreprises-E1-20261001-090000.recherche.json",
                                              "entreprises-E1-20261001-090000.md"]
    [j] = enquete.stockage.journal(enquete.id)
    assert j["requete"] == "recherche d'entreprises : Jean Dupont"
    assert "API officielle" in j["resultat_resume"]


# --- par la CLI ----------------------------------------------------------------

def test_cli(racine, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(core.requetes, "transport_urllib", Routeur({RECHERCHE: RESULTATS}))
    monkeypatch.setattr(core.requetes, "LIMITEUR_PARTAGE", sans_pause())
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "personne:Jean Dupont")
    code, out, _ = cli(capsys, "lancer", "entreprises", "--entite", "1")
    assert code == 0 and "DUPONT CONSEIL" in out
    _, out, _ = cli(capsys, "statut")
    assert "entreprises sur E1 (faite)" in out
