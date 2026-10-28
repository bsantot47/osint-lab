from datetime import timedelta

import pytest

from core.stockage import FinaliteManquante
from pilote import enquete as enq
from pilote.cli import main
from tests.conftest import T0

FINALITE = "Vérification préalable d'un partenaire commercial"


@pytest.fixture
def racine(tmp_path, monkeypatch):
    r = tmp_path / "enquetes"
    monkeypatch.setenv("OSINT_LAB_ENQUETES", str(r))
    return r


def cli(capsys, *argv):
    code = main(list(argv))
    sortie = capsys.readouterr()
    return code, sortie.out, sortie.err


# --- enquete.py --------------------------------------------------------------

def test_creation_arborescence_et_cadrage(racine):
    with enq.creer("dupont", FINALITE, "pro", 30, [("personne", "Jean Dupont")],
                   maintenant=T0) as e:
        for sous in ("fiches", "preuves", "bruts", "rapport"):
            assert (racine / "dupont" / sous).is_dir()
        cadrage = (racine / "dupont" / "cadrage.md").read_text(encoding="utf-8")
        assert FINALITE in cadrage and "personne : Jean Dupont" in cadrage
        [d] = e.stockage.entites(e.id)
        assert d["origine"] == "depart"
        assert (racine / "dupont" / "fiches" / f"E{d['id']}.md").is_file()
    assert enq.courante() == "dupont"
    assert enq.lister() == ["dupont"]


@pytest.mark.parametrize("finalite", ["", "  "])
def test_creation_refusee_sans_finalite_et_rien_cree(racine, finalite):
    with pytest.raises(FinaliteManquante):
        enq.creer("x", finalite)
    assert not (racine / "x").exists()


@pytest.mark.parametrize("nom", ["../evasion", "Majuscules", "", "a b"])
def test_nom_invalide(racine, nom):
    with pytest.raises(enq.ErreurPilote):
        enq.creer(nom, FINALITE)


def test_doublon_refuse(racine):
    enq.creer("x", FINALITE).fermer()
    with pytest.raises(enq.ErreurPilote):
        enq.creer("x", FINALITE)


def test_preuve_copiee_et_horodatee(racine, tmp_path):
    original = tmp_path / "capture.png"
    original.write_bytes(b"image")
    with enq.creer("x", FINALITE) as e:
        en = e.stockage.ajouter_entite(e.id, "compte", "jdupont")
        p = e.ajouter_preuve(en, original, maintenant=T0)
        [ligne] = e.stockage.preuves(en)
        copie = racine / "x" / "preuves" / f"20261001-090000-E{en}-capture.png"
        assert ligne["chemin_fichier"] == str(copie) and copie.read_bytes() == b"image"
        original.write_bytes(b"modifie")           # l'original ne fait plus foi
        assert e.stockage.verifier_preuve(p)


def test_cloture_puis_purge_au_lancement(racine, tmp_path):
    fichier = tmp_path / "f.png"
    fichier.write_bytes(b"x")
    with enq.creer("x", FINALITE, duree_conservation_jours=10) as e:
        en = e.stockage.ajouter_entite(e.id, "image", "f.png")
        e.ajouter_preuve(en, fichier)
        (e.chemin("bruts") / "sortie.json").write_text("{}")
        e.ecrire_fiches()
        rapport, expiration = e.cloturer(maintenant=T0)
    assert expiration == T0 + timedelta(days=10)

    assert enq.purger_expirees(maintenant=T0 + timedelta(days=9)) == []
    assert enq.purger_expirees(maintenant=T0 + timedelta(days=10)) == ["x"]
    dossier = racine / "x"
    for sous in ("fiches", "preuves", "bruts"):
        assert not (dossier / sous).exists()
    assert rapport.is_file() and (dossier / "cadrage.md").is_file()
    with enq.ouvrir("x") as e:
        assert e.stockage.entites(e.id) == []
        assert e.stockage.journal(e.id)[-1]["outil"] == "purge"
        with pytest.raises(enq.ErreurPilote):
            e.rapport()                            # l'archive n'est pas écrasée


# --- cli.py ------------------------------------------------------------------

def test_nouvelle_sans_finalite_refusee(racine, capsys):
    code, _, err = cli(capsys, "nouvelle", "x")
    assert code == 1 and "finalité" in err
    assert not (racine / "x").exists()


def test_scenario_complet(racine, capsys, tmp_path):
    code, out, _ = cli(capsys, "nouvelle", "dupont", "--finalite", FINALITE,
                       "--depart", "personne:Jean Dupont", "--conservation", "30")
    assert code == 0
    assert "E1 personne « Jean Dupont »" in out
    assert "Pivot suggéré : lancer dorks --entite 1" in out

    capture = tmp_path / "kbis.png"
    capture.write_bytes(b"kbis")
    assert cli(capsys, "ajouter", "entite", "entreprise", "Dupont Conseil SAS",
               "--preuve", str(capture))[0] == 0
    assert cli(capsys, "ajouter", "lien", "1", "2", "dirigeant")[0] == 0
    code, out, _ = cli(capsys, "ajouter", "fait", "Jean Dupont dirige Dupont Conseil SAS",
                       "--entite", "1", "--source", "https://www.pappers.fr/e/dupont")
    assert "F1 ajouté (1 source(s))" in out
    _, _, err = cli(capsys, "confirmer", "1")
    assert "1 domaine(s) indépendant(s)" in err   # refusé : une seule source

    _, out, _ = cli(capsys, "ajouter", "source", "1",
                    "https://annuaire-entreprises.data.gouv.fr/e/1")
    assert "confirmation possible, à valider → confirmer 1" in out
    _, out, _ = cli(capsys, "statut")
    assert "à valider → confirmer 1" in out
    assert "E2 entreprise « Dupont Conseil SAS »" in out
    assert cli(capsys, "confirmer", "1")[0] == 0

    cli(capsys, "ajouter", "entite", "personne", "Voisin Horsperimetre")
    cli(capsys, "statuer", "entite", "3", "hors_perimetre")
    cli(capsys, "marquer", "dorks", "--entite", "1")
    _, out, _ = cli(capsys, "statut")
    assert "Pivot suggéré : lancer entreprises --entite 1" in out
    assert "dorks sur E1 (faite)" in out
    assert "E3" not in out.split("À faire")[1]   # rien suggéré sur le hors-périmètre

    _, out, _ = cli(capsys, "entites")
    assert "→ E2 Dupont Conseil SAS : dirigeant" in out
    _, out, _ = cli(capsys, "recouper")
    assert "confirmé" in out and "https://www.pappers.fr/e/dupont" in out

    code, out, _ = cli(capsys, "rapport")
    rapport = out.split("Rapport écrit : ")[1].strip()
    texte = open(rapport, encoding="utf-8").read()
    assert "Jean Dupont dirige Dupont Conseil SAS" in texte and "Horsperimetre" not in texte

    fiche = (racine / "dupont" / "fiches" / "E2.md").read_text(encoding="utf-8")
    assert "sha256" in fiche and "← E1 : dirigeant" in fiche

    _, _, err = cli(capsys, "cloturer")            # pas de terminal : confirmation exigée
    assert "clôture annulée" in err
    code, out, _ = cli(capsys, "cloturer", "--oui")
    assert code == 0 and "rapport-final-" in out
    _, _, err = cli(capsys, "ajouter", "entite", "email", "x@exemple.fr")
    assert "close" in err


def test_lancer_refuse_si_close_et_outils_absents(racine, capsys):
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "personne:Jean Dupont")
    code, _, err = cli(capsys, "lancer", "whois", "--entite", "1")
    assert code == 1 and "pas encore disponible" in err
    cli(capsys, "cloturer", "--oui")
    _, _, err = cli(capsys, "lancer", "dorks", "--entite", "1")
    assert "close" in err


def test_option_enquete_et_ouvrir(racine, capsys):
    cli(capsys, "nouvelle", "a", "--finalite", FINALITE)
    cli(capsys, "nouvelle", "b", "--finalite", FINALITE)
    cli(capsys, "-e", "a", "ajouter", "entite", "domaine", "exemple.fr")
    _, out, _ = cli(capsys, "statut")
    assert "« b »" in out and "exemple.fr" not in out
    cli(capsys, "ouvrir", "a")
    _, out, _ = cli(capsys, "liste")
    assert "* a (en cours)" in out and "  b (en cours)" in out


def test_statut_confirme_interdit_par_statuer(racine, capsys):
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE)
    with pytest.raises(SystemExit):
        main(["statuer", "fait", "1", "confirme"])


def test_depart_mal_forme(racine, capsys):
    with pytest.raises(SystemExit):
        main(["nouvelle", "x", "--finalite", FINALITE, "--depart", "Jean Dupont"])


def test_statuer_entite_confirme_refuse(racine, capsys):
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "personne:X")
    with pytest.raises(SystemExit):
        main(["statuer", "entite", "1", "confirme"])
    with enq.ouvrir("x") as e:
        assert e.stockage.entite(1)["statut"] == "a_verifier"


# --- aide -------------------------------------------------------------------------

def test_aide_generale(racine, capsys):  # noqa: F811
    code, out, _ = cli(capsys, "aide")
    assert code == 0
    assert "Parcours type" in out and "confirmer" in out
    assert "pseudo       compte" in out and "recherche_inversee" in out


@pytest.mark.parametrize("sujet,attendu", [
    (["ajouter"], "Exemples :"),
    (["ajouter", "fait"], "--source URL"),
    (["statuer"], "Jamais confirme"),
    (["lancer"], "Outils : archives"),
    (["lancer", "fuite"], "--non-verifiees"),
    (["lancer", "dorks"], "Types d'entité acceptés"),
])
def test_aide_commande(racine, capsys, sujet, attendu):  # noqa: F811
    code, out, _ = cli(capsys, "aide", *sujet)
    assert code == 0 and attendu in out


def test_aide_exemples_valides(racine, capsys):  # noqa: F811
    """Chaque exemple affiché par `aide` est accepté par le parseur."""
    from pilote.cli import EXEMPLES, construire_parser
    import shlex
    p = construire_parser()
    for exemples in EXEMPLES.values():
        for x in exemples:
            args = shlex.split(x)
            if args[0] == "-e":
                args = args[2:]
            if args[0] == "lancer" and len(args) > 1:
                from pilote.cli import parser_outil
                from outils import OUTILS
                parser_outil(OUTILS[args[1]]).parse_args(args[2:])
            p.parse_args(shlex.split(x))


@pytest.mark.parametrize("sujet", [["inconnue"], ["lancer", "whois"], ["ajouter", "truc"]])
def test_aide_sujet_inconnu(racine, capsys, sujet):  # noqa: F811
    code, _, err = cli(capsys, "aide", *sujet)
    assert code == 1 and "erreur" in err


def test_journal(racine, capsys):  # noqa: F811
    cli(capsys, "nouvelle", "x", "--finalite", FINALITE, "--depart", "personne:Jean Dupont")
    cli(capsys, "lancer", "dorks", "--entite", "1")
    code, out, _ = cli(capsys, "journal")
    assert code == 0 and "dorks" in out and "aucune envoyée" in out
    _, out, _ = cli(capsys, "journal", "--outil", "pseudo")
    assert out.strip() == "Journal vide."
