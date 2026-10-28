import pytest

from pilote.guide import METHODES, REGLES, Methode, Regle, suggerer


def entite(id_, type_, statut="a_verifier", origine="manuel", valeur="v"):
    return {"id": id_, "type": type_, "statut": statut, "origine": origine, "valeur": valeur}


def methodes(suggestions):
    return [(s.methode.nom, s.entite_id) for s in suggestions]


@pytest.mark.parametrize("type_,attendu", [
    ("personne", ["dorks", "entreprises"]),       # nom seul : dorks, puis entreprises
    ("compte", ["pseudo"]),
    ("email", ["courriel", "fuite"]),             # email, puis vérification de fuite
    ("entreprise", ["entreprises"]),
    ("domaine", ["domaine", "archives"]),
    ("image", ["exif", "recherche_inversee"]),
    ("numero", []),
    ("document", []),
])
def test_suggestion_pour_chaque_etat_d_entree(type_, attendu):
    assert [m for m, _ in methodes(suggerer([entite(1, type_)], set()))] == attendu


def test_methode_deja_faite_plus_suggeree():
    s = suggerer([entite(1, "personne")], {("dorks", 1)})
    assert methodes(s) == [("entreprises", 1)]
    assert suggerer([entite(1, "personne")], {("dorks", 1), ("entreprises", 1)}) == []


def test_methode_faite_sur_une_autre_entite_reste_suggeree():
    s = suggerer([entite(1, "compte"), entite(2, "compte")], {("pseudo", 1)})
    assert methodes(s) == [("pseudo", 2)]


@pytest.mark.parametrize("statut", ["ecarte", "hors_perimetre"])
def test_jamais_de_collecte_sur_entite_ecartee_ou_hors_perimetre(statut):
    assert suggerer([entite(1, "email", statut=statut)], set()) == []


def test_entite_confirmee_reste_active():
    assert methodes(suggerer([entite(1, "compte", statut="confirme")], set())) == [("pseudo", 1)]


def test_depart_en_premier_puis_ordre_d_ajout():
    s = suggerer([entite(1, "compte"), entite(3, "domaine"),
                  entite(2, "personne", origine="depart")], set())
    assert [e for _, e in methodes(s)] == [2, 2, 1, 3, 3]


def test_commandes():
    [auto, manuelle] = suggerer([entite(4, "image")], set())
    assert auto.commande == "lancer exif --entite 4"
    assert manuelle.commande == "(manuel) puis : marquer recherche_inversee --entite 4"


def test_table_coherente():
    for regle in REGLES:
        for nom in regle.methodes:
            assert nom in METHODES


def test_extension_par_la_table():
    regles = REGLES + (Regle("numero", ("annuaire",), "un numéro"),)
    table = {**METHODES, "annuaire": Methode("annuaire", "annuaire inversé")}
    s = suggerer([entite(1, "numero")], set(), regles, table)
    assert methodes(s) == [("annuaire", 1)]
