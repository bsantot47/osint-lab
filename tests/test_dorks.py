from argparse import Namespace
from urllib.parse import parse_qs, urlsplit

import pytest

from outils import dorks
from pilote import enquete as enq
from tests.conftest import T0
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)


def requetes(type_, valeur, contexte=""):
    return dict(dorks.generer(type_, valeur, contexte))


def test_personne():
    r = requetes("personne", "Jean Dupont")
    assert r["Nom exact"] == '"Jean Dupont"'
    assert r["Nom inversé"] == '"Dupont Jean"'
    assert r["Profils professionnels"] == '"Jean Dupont" site:linkedin.com/in'
    assert "site:pappers.fr" in r["Registres d'entreprises"]


def test_nom_d_un_seul_mot_sans_inversion():
    assert "Nom inversé" not in requetes("personne", "Madonna")


def test_guillemets_et_accents_graves_neutralises():
    r = requetes("personne", 'Jean "le" `Dupont`')
    assert r["Nom exact"] == '"Jean le Dupont"'


def test_email_domaine_pro_et_webmail():
    pro = requetes("email", "jean.dupont@dupont-conseil.fr")
    assert pro["Identifiant seul"] == '"jean.dupont"'
    assert pro["Autres adresses du domaine"] == '"@dupont-conseil.fr" -"jean.dupont@dupont-conseil.fr"'
    assert "Autres adresses du domaine" not in requetes("email", "jean@gmail.com")


@pytest.mark.parametrize("saisie", ["0612345678", "06.12.34.56.78", "+33 6 12 34 56 78",
                                    "+33612345678"])
def test_numero_francais_normalise(saisie):
    r = requetes("numero", saisie)
    assert r["Format national"] == '"06 12 34 56 78"'
    # déjà au format international : dédoublonné avec la saisie
    assert '"+33 6 12 34 56 78"' in r.values()
    assert len(r.values()) == len(set(r.values()))


def test_numero_etranger_tel_quel():
    assert list(requetes("numero", "+1 202 555 0143")) == ["Numéro tel que saisi"]


def test_domaine():
    r = requetes("domaine", "exemple.fr")
    assert r["Pages indexées"] == "site:exemple.fr"
    assert r["Sous-domaines"] == "site:*.exemple.fr -site:www.exemple.fr"
    assert r["Mentions externes"] == '"exemple.fr" -site:exemple.fr'


def test_compte():
    assert requetes("compte", "jdupont")["Pseudo dans les URL"] == "inurl:jdupont"


def test_contexte_ajoute_partout():
    r = requetes("personne", "Jean Dupont", "Lyon")
    assert all(q.endswith(" Lyon") for q in r.values())


def test_pas_de_recherche_menant_aux_fuites():
    for type_ in dorks.TYPES:
        for _, modele in dorks.MODELES[type_]:
            for interdit in ("filetype:csv", "filetype:xls ", "filetype:sql", "pastebin"):
                assert interdit not in modele + " "


def test_types_couverts():
    assert set(dorks.TYPES) == set(dorks.MODELES)


def test_liens_moteurs():
    urls = dorks.liens('"Jean Dupont" site:linkedin.com/in')
    assert set(urls) == {"Google", "Bing"}
    for url in urls.values():
        assert parse_qs(urlsplit(url).query)["q"] == ['"Jean Dupont" site:linkedin.com/in']


def test_lancer_n_envoie_rien_et_journalise(racine):  # noqa: F811
    with enq.creer("x", FINALITE, departs=[("personne", "Jean Dupont")]) as e:
        entite = e.stockage.entite(1)
        res = dorks.lancer(e, entite, Namespace(contexte=""), maintenant=T0)
        [fichier] = res.fichiers
        assert fichier.parent == e.chemin("bruts")
        assert fichier.name == "dorks-E1-20261001-090000.md"
        contenu = fichier.read_text(encoding="utf-8")
        assert "Aucune requête n'a été envoyée" in contenu
        assert "[Google](https://www.google.com/search?q=" in contenu
        [j] = e.stockage.journal(e.id)
        assert j["outil"] == "dorks" and j["url"] == ""
        assert "aucune envoyée" in j["resultat_resume"]


def test_cli_lancer_dorks(racine, capsys):  # noqa: F811
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "personne:Jean Dupont")
    code, out, _ = cli(capsys, "lancer", "dorks", "--entite", "1", "--contexte", "Lyon")
    assert code == 0
    assert '"Dupont Jean" Lyon' in out and "Google : https://www.google.com/search?q=" in out
    _, out, _ = cli(capsys, "statut")
    assert "dorks sur E1 (faite)" in out
    assert "Pivot suggéré : lancer entreprises --entite 1" in out


def test_cli_refus_type_et_statut(racine, capsys):  # noqa: F811
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "image:photo.jpg",
        "--depart", "personne:Voisin")
    _, _, err = cli(capsys, "lancer", "dorks", "--entite", "1")
    assert "ne s'applique pas à une entité image" in err
    cli(capsys, "statuer", "entite", "2", "hors_perimetre")
    code, _, err = cli(capsys, "lancer", "dorks", "--entite", "2")
    assert code == 1 and "aucune collecte" in err
    with enq.ouvrir("x") as e:
        assert e.stockage.taches(e.id) == [] and e.stockage.journal(e.id) == []


def test_cli_methode_manuelle(racine, capsys):  # noqa: F811
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "image:photo.jpg")
    _, _, err = cli(capsys, "lancer", "recherche_inversee", "--entite", "1")
    assert "méthode manuelle" in err and "marquer recherche_inversee" in err
