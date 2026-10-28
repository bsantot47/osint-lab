import pytest

from core.rapport import ecrire_rapport, generer_rapport
from core.stockage import sha256_fichier
from tests.conftest import T0


@pytest.fixture
def enquete_remplie(stockage, enquete_id, tmp_path):
    s, e = stockage, enquete_id
    jean = s.ajouter_entite(e, "personne", "Jean Dupont")
    societe = s.ajouter_entite(e, "entreprise", "Dupont Conseil SAS")
    homonyme = s.ajouter_entite(e, "compte", "jdupont_homonyme")
    voisin = s.ajouter_entite(e, "personne", "Voisin Horsperimetre")
    s.changer_statut_entite(homonyme, "ecarte")
    s.changer_statut_entite(voisin, "hors_perimetre")

    capture = tmp_path / "kbis.png"
    capture.write_bytes(b"capture")
    s.ajouter_preuve(societe, capture, maintenant=T0)

    confirme = s.ajouter_fait(e, "Jean Dupont dirige Dupont Conseil SAS", jean)
    s.ajouter_source(confirme, "https://www.pappers.fr/entreprise/dupont", "entreprises", T0)
    s.ajouter_source(confirme, "https://annuaire-entreprises.data.gouv.fr/e/1", "entreprises", T0)
    s.confirmer_fait(confirme)

    piste = s.ajouter_fait(e, "Ancien salarié de ACME", jean)
    s.ajouter_source(piste, "https://fr.linkedin.com/in/jdupont", "manuel", T0)

    s.ajouter_fait(e, "Fait sur le voisin SECRET_VOISIN", voisin)
    hors = s.ajouter_fait(e, "Fait hors périmètre SECRET_HORS", jean)
    s.changer_statut_fait(hors, "hors_perimetre")
    ecarte = s.ajouter_fait(e, "Fait écarté SECRET_ECARTE", jean)
    s.changer_statut_fait(ecarte, "ecarte")

    s.ajouter_lien(e, jean, societe, "dirigeant", "https://www.pappers.fr/entreprise/dupont")
    s.ajouter_lien(e, jean, voisin, "voisinage SECRET_LIEN")
    s.journaliser(e, "entreprises", "Dupont Conseil", maintenant=T0)
    s.journaliser(e, "archives/robots", "GET robots.txt", maintenant=T0)
    return capture


def test_exclusion_hors_perimetre_et_ecartes(stockage, enquete_id, enquete_remplie):
    texte = generer_rapport(stockage, enquete_id, maintenant=T0)
    for secret in ("Horsperimetre", "SECRET_VOISIN", "SECRET_HORS", "SECRET_ECARTE",
                   "SECRET_LIEN", "jdupont\\_homonyme", "jdupont_homonyme"):
        assert secret not in texte
    assert "Pistes écartées après vérification (non détaillées) : 2" in texte


def test_presence_des_sources(stockage, enquete_id, enquete_remplie):
    texte = generer_rapport(stockage, enquete_id, maintenant=T0)
    confirmes, a_verifier = texte.split("## Faits confirmés")[1].split("## Faits à vérifier")
    assert "Jean Dupont dirige Dupont Conseil SAS" in confirmes
    assert "<https://www.pappers.fr/entreprise/dupont>" in confirmes
    assert "<https://annuaire-entreprises.data.gouv.fr/e/1>" in confirmes
    assert "2 source(s), 2 domaine(s) indépendant(s)" in confirmes
    assert "Ancien salarié de ACME" in a_verifier
    assert "<https://fr.linkedin.com/in/jdupont>" in a_verifier
    assert "consulté le 01/10/2026 09:00" in a_verifier


def test_contenu_general(stockage, enquete_id, enquete_remplie):
    texte = generer_rapport(stockage, enquete_id, maintenant=T0)
    assert "Vérification préalable d'un partenaire commercial" in texte
    assert f"sha256 `{sha256_fichier(enquete_remplie)}`" in texte
    assert "dirigeant (source : <https://www.pappers.fr/entreprise/dupont>)" in texte
    assert "Outils utilisés : archives, entreprises" in texte
    assert "Requêtes journalisées : 2" in texte


def test_echappement_markdown(stockage, enquete_id):
    f = stockage.ajouter_fait(enquete_id, "# titre [lien](http://piege) <script>")
    stockage.ajouter_source(f, "https://exemple.fr/a b>c", "manuel")
    texte = generer_rapport(stockage, enquete_id)
    assert "<script>" not in texte and "[lien](" not in texte
    assert "<https://exemple.fr/a%20b%3Ec>" in texte


def test_rapport_vide(stockage, enquete_id):
    texte = generer_rapport(stockage, enquete_id)
    assert texte.count("_Aucun._") == 3 and "_Aucune._" in texte


def test_ecrire_rapport_journalise_empreinte(stockage, enquete_id, enquete_remplie, tmp_path):
    chemin = ecrire_rapport(stockage, enquete_id, tmp_path / "sortie" / "rapport.md", T0)
    assert chemin.read_text(encoding="utf-8").startswith("# Rapport d'enquête : test")
    j = stockage.journal(enquete_id)[-1]
    assert j["outil"] == "rapport" and j["resultat_resume"] == f"sha256 {sha256_fichier(chemin)}"


def test_rapport_apres_cloture(stockage, enquete_id, enquete_remplie):
    stockage.cloturer(enquete_id, maintenant=T0)
    texte = generer_rapport(stockage, enquete_id, maintenant=T0)
    assert "Close le** : 01/10/2026" in texte and "jusqu'au 31/10/2026" in texte
