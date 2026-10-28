import sqlite3
from datetime import timedelta

import pytest

from core.stockage import (ConfirmationImpossible, EnqueteCloturee, ErreurStockage,
                           FinaliteManquante, domaine_enregistrable, sha256_fichier)
from tests.conftest import T0


# --- enquête et finalité -------------------------------------------------

@pytest.mark.parametrize("finalite", ["", "   ", None])
def test_creation_refusee_sans_finalite(stockage, finalite):
    with pytest.raises(FinaliteManquante):
        stockage.creer_enquete("x", finalite=finalite)


def test_finalite_vide_refusee_meme_en_sql_direct(stockage):
    with pytest.raises(sqlite3.IntegrityError):
        stockage.cx.execute(
            "INSERT INTO enquete (nom, finalite, date_creation, duree_conservation_jours)"
            " VALUES ('x', ' ', '2026-01-01', 30)")


def test_creation_enquete(stockage, enquete_id):
    e = stockage.enquete(enquete_id)
    assert e.nom == "test"
    assert e.date_creation == T0
    assert e.duree_conservation_jours == 30
    assert not e.cloturee
    assert stockage.enquete_par_nom("test").id == enquete_id


def test_nom_enquete_unique(stockage, enquete_id):
    with pytest.raises(sqlite3.IntegrityError):
        stockage.creer_enquete("test", finalite="autre")


def test_duree_conservation_positive(stockage):
    with pytest.raises(ErreurStockage):
        stockage.creer_enquete("x", finalite="f", duree_conservation_jours=0)


# --- entités ---------------------------------------------------------------

def test_ajout_entite_et_deduplication(stockage, enquete_id):
    a = stockage.ajouter_entite(enquete_id, "email", "jean@exemple.fr")
    b = stockage.ajouter_entite(enquete_id, "email", " jean@exemple.fr ")
    assert a == b
    [en] = stockage.entites(enquete_id)
    assert en["statut"] == "a_verifier"


def test_type_entite_inconnu(stockage, enquete_id):
    with pytest.raises(ErreurStockage):
        stockage.ajouter_entite(enquete_id, "adresse_ip", "1.2.3.4")


def test_statut_entite(stockage, enquete_id):
    en = stockage.ajouter_entite(enquete_id, "compte", "jdupont")
    stockage.changer_statut_entite(en, "hors_perimetre")
    assert stockage.entites(enquete_id, ("hors_perimetre",))[0]["id"] == en
    with pytest.raises(ErreurStockage):
        stockage.changer_statut_entite(en, "valide")


# --- faits, sources, règle des deux domaines ------------------------------

@pytest.mark.parametrize("url,domaine", [
    ("https://www.linkedin.com/in/x", "linkedin.com"),
    ("https://fr.linkedin.com/in/x", "linkedin.com"),
    ("http://LinkedIn.com./in/x", "linkedin.com"),
    ("https://alice.github.io/", "alice.github.io"),
    ("https://annuaire-entreprises.data.gouv.fr/e/1", "data.gouv.fr"),
    ("https://www.bbc.co.uk/news", "bbc.co.uk"),
    ("exemple.fr/page", "exemple.fr"),
    ("http://192.168.0.1/x", "192.168.0.1"),
    ("pas une url", ""),
])
def test_domaine_enregistrable(url, domaine):
    assert domaine_enregistrable(url) == domaine


def test_deux_sources_meme_domaine_comptent_pour_une(stockage, enquete_id):
    f = stockage.ajouter_fait(enquete_id, "Gérant de la société X")
    stockage.ajouter_source(f, "https://www.pappers.fr/entreprise/x", "entreprises")
    stockage.ajouter_source(f, "https://pappers.fr/dirigeant/y", "manuel")
    assert stockage.domaines_independants(f) == {"pappers.fr"}
    assert not stockage.confirmation_possible(f)
    with pytest.raises(ConfirmationImpossible):
        stockage.confirmer_fait(f)
    assert stockage.fait(f)["statut"] == "a_verifier"


def test_confirmation_signalee_mais_jamais_automatique(stockage, enquete_id):
    f = stockage.ajouter_fait(enquete_id, "Gérant de la société X")
    stockage.ajouter_source(f, "https://www.pappers.fr/entreprise/x", "entreprises")
    stockage.ajouter_source(f, "https://annuaire-entreprises.data.gouv.fr/e/x", "entreprises")
    assert stockage.confirmation_possible(f)
    assert stockage.fait(f)["statut"] == "a_verifier"   # l'outil ne tranche pas
    stockage.confirmer_fait(f)                          # action de l'utilisateur
    assert stockage.fait(f)["statut"] == "confirme"
    assert not stockage.confirmation_possible(f)


def test_confirmer_seulement_par_confirmer_fait(stockage, enquete_id):
    f = stockage.ajouter_fait(enquete_id, "énoncé")
    with pytest.raises(ErreurStockage):
        stockage.changer_statut_fait(f, "confirme")
    stockage.changer_statut_fait(f, "ecarte")
    assert stockage.fait(f)["statut"] == "ecarte"


def test_fait_rattache_a_entite_d_une_autre_enquete(stockage, enquete_id):
    autre = stockage.creer_enquete("autre", finalite="f")
    en = stockage.ajouter_entite(autre, "personne", "X")
    with pytest.raises(ErreurStockage):
        stockage.ajouter_fait(enquete_id, "énoncé", entite_id=en)


def test_source_url_invalide(stockage, enquete_id):
    f = stockage.ajouter_fait(enquete_id, "énoncé")
    with pytest.raises(ErreurStockage):
        stockage.ajouter_source(f, "", "manuel")


# --- preuves ---------------------------------------------------------------

def test_preuve_sha256_et_integrite(stockage, enquete_id, tmp_path):
    fichier = tmp_path / "capture.png"
    fichier.write_bytes(b"contenu de la capture")
    en = stockage.ajouter_entite(enquete_id, "compte", "jdupont")
    p = stockage.ajouter_preuve(en, fichier, maintenant=T0)
    [ligne] = stockage.preuves(en)
    assert ligne["sha256"] == sha256_fichier(fichier)
    assert len(ligne["sha256"]) == 64
    assert ligne["date"] == T0.isoformat()
    assert stockage.verifier_preuve(p)
    fichier.write_bytes(b"contenu modifie")
    assert not stockage.verifier_preuve(p)
    fichier.unlink()
    assert not stockage.verifier_preuve(p)


def test_sha256_valeur_connue(tmp_path):
    fichier = tmp_path / "f"
    fichier.write_bytes(b"abc")
    assert sha256_fichier(fichier) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_preuve_fichier_absent(stockage, enquete_id, tmp_path):
    en = stockage.ajouter_entite(enquete_id, "compte", "jdupont")
    with pytest.raises(ErreurStockage):
        stockage.ajouter_preuve(en, tmp_path / "absent.png")


# --- liens -----------------------------------------------------------------

def test_lien(stockage, enquete_id):
    a = stockage.ajouter_entite(enquete_id, "personne", "Jean Dupont")
    b = stockage.ajouter_entite(enquete_id, "entreprise", "X SAS")
    stockage.ajouter_lien(enquete_id, a, b, "dirigeant", "https://pappers.fr/x")
    [l] = stockage.liens(enquete_id)
    assert (l["entite_a"], l["entite_b"], l["source"]) == (a, b, "https://pappers.fr/x")
    with pytest.raises(sqlite3.IntegrityError):
        stockage.ajouter_lien(enquete_id, a, a, "soi-même")


# --- journal ---------------------------------------------------------------

def test_journalisation(stockage, enquete_id):
    stockage.journaliser(enquete_id, "dorks", "site:exemple.fr", "https://exemple.fr",
                         "12 requêtes générées", maintenant=T0)
    [j] = stockage.journal(enquete_id)
    assert (j["outil"], j["requete"], j["url"], j["resultat_resume"], j["date"]) == (
        "dorks", "site:exemple.fr", "https://exemple.fr", "12 requêtes générées",
        T0.isoformat())


# --- clôture et expiration --------------------------------------------------

def test_cloture_fixe_expiration_et_fige(stockage, enquete_id):
    stockage.ajouter_entite(enquete_id, "personne", "X")
    expiration = stockage.cloturer(enquete_id, maintenant=T0)
    assert expiration == T0 + timedelta(days=30)
    e = stockage.enquete(enquete_id)
    assert e.cloturee and e.date_expiration == expiration
    with pytest.raises(EnqueteCloturee):
        stockage.ajouter_entite(enquete_id, "personne", "Y")
    with pytest.raises(EnqueteCloturee):
        stockage.cloturer(enquete_id)


def test_expiration_a_la_cloture(stockage, enquete_id, tmp_path):
    fichier = tmp_path / "preuve.png"
    fichier.write_bytes(b"x")
    en = stockage.ajouter_entite(enquete_id, "compte", "jdupont")
    stockage.ajouter_preuve(en, fichier)
    f = stockage.ajouter_fait(enquete_id, "énoncé", en)
    stockage.ajouter_source(f, "https://exemple.fr", "manuel")
    stockage.journaliser(enquete_id, "pseudo", "jdupont")
    autre = stockage.creer_enquete("autre", finalite="f")
    stockage.ajouter_entite(autre, "personne", "intact")

    stockage.cloturer(enquete_id, maintenant=T0)
    assert stockage.purger_expirees(T0 + timedelta(days=29)) == []
    assert fichier.exists()

    [purgee] = stockage.purger_expirees(T0 + timedelta(days=30))
    assert purgee.id == enquete_id
    assert not fichier.exists()
    assert stockage.entites(enquete_id) == []
    assert stockage.faits(enquete_id) == []
    assert stockage.cx.execute("SELECT count(*) FROM source").fetchone()[0] == 0
    assert stockage.cx.execute("SELECT count(*) FROM preuve").fetchone()[0] == 0
    journal = stockage.journal(enquete_id)            # conservé pour la traçabilité
    assert [j["outil"] for j in journal] == ["pseudo", "cloture", "purge"]
    assert "1 fichier(s)" in journal[-1]["resultat_resume"]
    assert stockage.enquete(enquete_id).date_purge is not None
    assert len(stockage.entites(autre)) == 1          # autre enquête intacte
    assert stockage.purger_expirees(T0 + timedelta(days=60)) == []   # une seule fois


def test_purge_refusee_si_non_close(stockage, enquete_id):
    with pytest.raises(ErreurStockage):
        stockage.purger_enquete(enquete_id)


# --- origine et tâches --------------------------------------------------------

def test_origine_entite(stockage, enquete_id):
    a = stockage.ajouter_entite(enquete_id, "personne", "Jean Dupont", origine="depart")
    b = stockage.ajouter_entite(enquete_id, "domaine", "exemple.fr")
    assert stockage.entite(a)["origine"] == "depart"
    assert stockage.entite(b)["origine"] == "manuel"


def test_tache_unique_par_methode_et_entite(stockage, enquete_id):
    en = stockage.ajouter_entite(enquete_id, "image", "photo.jpg")
    stockage.marquer_tache(enquete_id, "exif", en)
    stockage.marquer_tache(enquete_id, "exif", en, etat="ignoree")
    stockage.marquer_tache(enquete_id, "dorks")
    stockage.marquer_tache(enquete_id, "dorks")
    assert [(t["methode"], t["entite_id"], t["etat"]) for t in stockage.taches(enquete_id)] == [
        ("exif", en, "ignoree"), ("dorks", None, "faite")]
    with pytest.raises(ErreurStockage):
        stockage.marquer_tache(enquete_id, "exif", en, etat="en_cours")


# --- « confirmé » : un seul chemin, la règle des deux domaines ---------------

def test_entite_jamais_confirmee(stockage, enquete_id):
    en = stockage.ajouter_entite(enquete_id, "personne", "X")
    with pytest.raises(ErreurStockage):
        stockage.changer_statut_entite(en, "confirme")
    with pytest.raises(sqlite3.IntegrityError):
        stockage.cx.execute("UPDATE entite SET statut = 'confirme' WHERE id = ?", (en,))


def test_fait_ne_nait_pas_confirme_meme_en_sql(stockage, enquete_id):
    with pytest.raises(sqlite3.IntegrityError, match="naître confirmé"):
        stockage.cx.execute(
            "INSERT INTO fait (enquete_id, enonce, statut, date) VALUES (?, 'x', 'confirme', 'd')",
            (enquete_id,))


@pytest.mark.parametrize("urls,autorise", [
    ([], False),
    (["https://a.fr/1"], False),
    (["https://www.a.fr/1", "https://fr.a.fr/2"], False),   # même domaine : une source
    (["https://a.fr/1", "https://b.fr/2"], True),
])
def test_regle_deux_domaines_garantie_par_la_base(stockage, enquete_id, urls, autorise):
    f = stockage.ajouter_fait(enquete_id, "énoncé")
    for url in urls:
        stockage.ajouter_source(f, url, "manuel")
    requete = "UPDATE fait SET statut = 'confirme' WHERE id = ?"
    if autorise:
        stockage.cx.execute(requete, (f,))
        assert stockage.fait(f)["statut"] == "confirme"
    else:
        with pytest.raises(sqlite3.IntegrityError, match="domaines indépendants"):
            stockage.cx.execute(requete, (f,))


def test_confirmation_impossible_hors_de_l_outil(stockage, enquete_id):
    f = stockage.ajouter_fait(enquete_id, "énoncé")
    stockage.ajouter_source(f, "https://a.fr/1", "manuel")
    stockage.ajouter_source(f, "https://b.fr/2", "manuel")
    stockage.cx.commit()
    brute = sqlite3.connect(stockage.chemin_db)       # sans la fonction domaine()
    try:
        with pytest.raises(sqlite3.OperationalError, match="domaine"):
            brute.execute("UPDATE fait SET statut = 'confirme' WHERE id = ?", (f,))
    finally:
        brute.close()
    assert stockage.fait(f)["statut"] == "a_verifier"
