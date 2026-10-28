import pytest

from ia import confiance
from pilote import enquete as enq
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)


@pytest.fixture
def e(racine):  # noqa: F811
    with enq.creer("x", FINALITE, departs=[("personne", "Jean Dupont"),
                                           ("email", "jdupont@exemple.fr")]) as en:
        yield en


def photo(tmp_path, nom, contenu):
    f = tmp_path / nom
    f.write_bytes(contenu)
    return f


def par_id(resultats):
    return {r.entite_id: r for r in resultats}


def raisons(r):
    return [nom for nom, _, _ in r.raisons]


# --- règles ------------------------------------------------------------------------

def test_photo_identique_a_une_reference(e, tmp_path):
    s = e.stockage
    compte = s.ajouter_entite(e.id, "compte", "jd_lyon")
    e.ajouter_preuve(1, photo(tmp_path, "a.jpg", b"visage"))
    e.ajouter_preuve(compte, photo(tmp_path, "b.jpg", b"visage"))
    r = par_id(confiance.indices(s, e.id))[compte]
    assert raisons(r) == ["photo_identique"] and r.score == 3
    assert "E1 Jean Dupont" in r.raisons[0][2]


def test_lien_avec_reference(e):
    s = e.stockage
    societe = s.ajouter_entite(e.id, "entreprise", "Dupont Conseil")
    s.ajouter_lien(e.id, 1, societe, "dirigeant")
    r = par_id(confiance.indices(s, e.id))[societe]
    assert raisons(r) == ["lien_avec_reference"] and r.niveau == "indices moyens"


@pytest.mark.parametrize("type_,valeur,attendu", [
    ("compte", "jdupont", True),                 # = partie locale de jdupont@exemple.fr
    ("compte", "j.dupont", True),                # ponctuation ignorée
    ("compte", "jeandupont", True),              # nom accolé de Jean Dupont
    ("domaine", "exemple.fr", True),             # = domaine de l'adresse de départ
    ("compte", "jdurand", False),
    ("compte", "jd", False),                     # trop court pour conclure
])
def test_identifiant_partage(e, type_, valeur, attendu):
    i = e.stockage.ajouter_entite(e.id, type_, valeur)
    r = par_id(confiance.indices(e.stockage, e.id))[i]
    assert ("identifiant_partage" in raisons(r)) is attendu


def test_sources_independantes(e):
    s = e.stockage
    compte = s.ajouter_entite(e.id, "compte", "xyz123")
    f = s.ajouter_fait(e.id, "profil actif", compte)
    s.ajouter_source(f, "https://a.fr/1", "manuel")
    assert par_id(confiance.indices(s, e.id))[compte].score == 0
    s.ajouter_source(f, "https://b.fr/1", "manuel")
    assert raisons(par_id(confiance.indices(s, e.id))[compte]) == ["sources_independantes"]


def test_indice_contraire_photo_d_une_ecartee(e, tmp_path):
    s = e.stockage
    homonyme = s.ajouter_entite(e.id, "compte", "jean_d_marseille")
    e.ajouter_preuve(homonyme, photo(tmp_path, "h.jpg", b"autre visage"))
    s.changer_statut_entite(homonyme, "ecarte")
    nouveau = s.ajouter_entite(e.id, "compte", "jd13")
    e.ajouter_preuve(nouveau, photo(tmp_path, "n.jpg", b"autre visage"))
    r = par_id(confiance.indices(s, e.id))[nouveau]
    assert r.score == -2 and r.niveau == "indices contraires"


def test_cumul_et_niveaux(e, tmp_path):
    s = e.stockage
    compte = s.ajouter_entite(e.id, "compte", "jdupont")
    s.ajouter_lien(e.id, 1, compte, "même photo de profil")
    e.ajouter_preuve(1, photo(tmp_path, "a.jpg", b"v"))
    e.ajouter_preuve(compte, photo(tmp_path, "b.jpg", b"v"))
    r = par_id(confiance.indices(s, e.id))[compte]
    assert r.score == 6 and r.niveau == "indices forts"
    assert raisons(r) == ["photo_identique", "lien_avec_reference", "identifiant_partage"]


def test_entite_avec_fait_confirme_devient_reference(e):
    s = e.stockage
    societe = s.ajouter_entite(e.id, "entreprise", "Dupont Conseil")
    f = s.ajouter_fait(e.id, "SIREN 123456789", societe)
    s.ajouter_source(f, "https://pappers.fr/x", "manuel")
    s.ajouter_source(f, "https://annuaire-entreprises.data.gouv.fr/x", "manuel")
    s.confirmer_fait(f)
    associe = s.ajouter_entite(e.id, "personne", "Claire Martin")
    s.ajouter_lien(e.id, associe, societe, "associée")
    resultats = par_id(confiance.indices(s, e.id))
    assert societe not in resultats                      # référence, pas candidate
    assert raisons(resultats[associe]) == ["lien_avec_reference"]


# --- ce que l'IA ne fait jamais ------------------------------------------------------

def test_ordre_des_entites_pas_des_scores(e, tmp_path):
    s = e.stockage
    faible = s.ajouter_entite(e.id, "compte", "inconnu42")
    fort = s.ajouter_entite(e.id, "compte", "jdupont")
    s.ajouter_lien(e.id, 1, fort, "x")
    assert [r.entite_id for r in confiance.indices(s, e.id)] == [faible, fort]


def test_aucun_statut_modifie(e, tmp_path):
    s = e.stockage
    compte = s.ajouter_entite(e.id, "compte", "jdupont")
    s.ajouter_lien(e.id, 1, compte, "x")
    f = s.ajouter_fait(e.id, "profil", compte)
    avant = [dict(x) for x in s.entites(e.id)] + [dict(x) for x in s.faits(e.id)]
    confiance.indices(s, e.id)
    confiance.evaluer(s, e.id)
    assert [dict(x) for x in s.entites(e.id)] + [dict(x) for x in s.faits(e.id)] == avant
    assert s.fait(f)["statut"] == "a_verifier"


def test_ecartees_et_hors_perimetre_non_candidates(e):
    s = e.stockage
    a = s.ajouter_entite(e.id, "compte", "a1234")
    b = s.ajouter_entite(e.id, "compte", "b1234")
    s.changer_statut_entite(a, "ecarte")
    s.changer_statut_entite(b, "hors_perimetre")
    assert confiance.indices(s, e.id) == []


# --- mesure ------------------------------------------------------------------------

def test_evaluer(e, tmp_path):
    s = e.stockage
    # vrai positif : lié à la référence, fait confirmé
    vp = s.ajouter_entite(e.id, "compte", "jdupont")
    s.ajouter_lien(e.id, 1, vp, "x")
    f = s.ajouter_fait(e.id, "profil de Jean", vp)
    s.ajouter_source(f, "https://github.com/jdupont", "pseudo")
    s.ajouter_source(f, "https://keybase.io/jdupont", "pseudo")
    s.confirmer_fait(f)
    # faux négatif : fait confirmé mais aucune règle
    fn = s.ajouter_entite(e.id, "compte", "zz_99")
    f2 = s.ajouter_fait(e.id, "autre profil", fn)
    s.ajouter_source(f2, "https://a.fr/1", "manuel")
    s.ajouter_source(f2, "https://b.fr/1", "manuel")
    s.confirmer_fait(f2)
    # faux positif : identifiant + lien mais écarté
    fp = s.ajouter_entite(e.id, "compte", "j.dupont")
    s.ajouter_lien(e.id, 1, fp, "x")
    s.changer_statut_entite(fp, "ecarte")
    # vrai négatif : écarté sans indice
    vn = s.ajouter_entite(e.id, "compte", "qwerty77")
    s.changer_statut_entite(vn, "ecarte")
    # sans décision : ignoré
    s.ajouter_entite(e.id, "compte", "jdupont69")

    m = confiance.evaluer(s, e.id, seuil=2)
    assert (m.vp, m.fp, m.fn, m.vn) == (1, 1, 1, 1)
    assert m.precision == 0.5 and m.rappel == 0.5
    assert not m.suffisant
    m3 = confiance.evaluer(s, e.id, seuil=4)
    assert m3.vp == 0 and m3.fn == 2


def test_evaluer_sans_decision(e):
    m = confiance.evaluer(e.stockage, e.id)
    assert m.decisions == 0 and m.precision is None and m.rappel is None


# --- par la CLI --------------------------------------------------------------------

def test_cli_confiance(racine, capsys):  # noqa: F811
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "email:jdupont@exemple.fr")
    cli(capsys, "ajouter", "entite", "compte", "jdupont")
    cli(capsys, "ajouter", "lien", "1", "2", "même avatar")
    code, out, _ = cli(capsys, "confiance")
    assert code == 0 and "aucune décision prise" in out
    assert "E2 [compte] jdupont : indices moyens (+3)" in out
    assert "+2 lié à E1 jdupont@exemple.fr" in out
    _, out, _ = cli(capsys, "confiance", "--evaluer")
    assert "0 décision(s)" in out and "trop peu pour conclure" in out


def test_mesure_sans_fuite_de_la_decision(e):
    """Un fait confirmé a toujours ≥ 2 domaines : la règle des sources ne doit pas
    s'en servir, sinon elle « prédit » la décision qu'on mesure."""
    s = e.stockage
    i = s.ajouter_entite(e.id, "compte", "zz_99")
    f = s.ajouter_fait(e.id, "profil", i)
    s.ajouter_source(f, "https://a.fr/1", "manuel")
    s.ajouter_source(f, "https://b.fr/1", "manuel")
    s.confirmer_fait(f)
    ctx = confiance.contexte(s, e.id)
    assert "sources_independantes" not in raisons(confiance.indice(ctx, i))
