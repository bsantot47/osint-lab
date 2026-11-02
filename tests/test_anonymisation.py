"""Anonymisation : plus aucun nom réel, et desanonymiser(anonymiser(x)) == x."""

import re
from datetime import timedelta

import pytest

from core.anonymisation import (Correspondance, ErreurAnonymisation, anonymiser, desanonymiser,
                                ecrire_correspondance, lire_correspondance, nature_source)
from core.rapport import generer_rapport
from pilote import enquete as enq
from tests.conftest import T0
from tests.test_pilote import FINALITE, cli, racine  # noqa: F401 (fixture)

JEAN = ("personne", "Jean Dupont")
SOCIETE = ("entreprise", "Dupont Conseil SAS")


def aller_retour(texte, entites, nom=""):
    a = anonymiser(texte, entites, nom)
    assert desanonymiser(a.texte, a.correspondance).texte == texte
    # La table relue depuis son fichier suffit à la restitution.
    table = lire_correspondance(ecrire_correspondance(a.correspondance))
    assert desanonymiser(a.texte, table).texte == texte
    return a


# --- étiquettes -------------------------------------------------------------------

def test_etiquettes_par_type():
    entites = [JEAN, ("personne", "Marie Curie"), SOCIETE, ("entreprise", "Acme"),
               ("compte", "jdupont"), ("email", "jean@exemple.fr"), ("domaine", "exemple.org"),
               ("numero", "01 23 45 67 89"), ("document", "bilan2024.pdf"),
               ("image", "profil.png")]
    texte = ("Jean Dupont, Marie Curie, Dupont Conseil SAS, Acme, jdupont, jean@exemple.fr, "
             "exemple.org, 01 23 45 67 89, bilan2024.pdf, profil.png")
    assert aller_retour(texte, entites).texte == (
        "PERSONNE A, PERSONNE B, ENTREPRISE 1, ENTREPRISE 2, PSEUDO A, EMAIL A, "
        "DOMAINE 1, NUMERO 1, DOCUMENT 1, IMAGE 1")


def test_au_dela_de_z():
    entites = [("personne", f"Nomxx{chr(97 + i // 26)}{chr(97 + i % 26)}") for i in range(28)]
    a = aller_retour(" ".join(v for _, v in entites), entites)
    assert a.texte.endswith("PERSONNE Z PERSONNE AA PERSONNE AB")


def test_etiquette_stable_dans_le_texte():
    a = aller_retour("Jean Dupont. JEAN DUPONT. jean dupont.", [JEAN])
    assert a.texte == "PERSONNE A. PERSONNE A. PERSONNE A."
    assert [e for e, _, _ in a.correspondance.entites] == ["PERSONNE A"]


def test_etiquette_stable_quand_une_entite_s_ajoute():
    avant = anonymiser("Jean Dupont et Marie Curie", [JEAN, ("personne", "Marie Curie")])
    apres = anonymiser("Jean Dupont et Marie Curie",
                       [JEAN, ("personne", "Marie Curie"), ("personne", "Paul Neuf")])
    assert avant.texte == apres.texte


def test_plus_long_d_abord():
    entites = [("entreprise", "Dupont"), ("entreprise", "Dupont Conseil"), SOCIETE]
    a = aller_retour("Dupont Conseil SAS, Dupont Conseil, Dupont", entites)
    assert a.texte == "ENTREPRISE 3, ENTREPRISE 2, ENTREPRISE 1"   # ordre des entités fournies
    assert a.correspondance.entites == [("ENTREPRISE 1", "entreprise", "Dupont"),
                                        ("ENTREPRISE 2", "entreprise", "Dupont Conseil"),
                                        ("ENTREPRISE 3", "entreprise", "Dupont Conseil SAS")]


def test_email_contenant_un_domaine_connu():
    a = aller_retour("Écrire à jean@exemple.fr, site exemple.fr",
                     [("domaine", "exemple.fr"), ("email", "jean@exemple.fr")])
    assert a.texte == "Écrire à EMAIL A, site DOMAINE 1"


@pytest.mark.parametrize("forme", [
    r"j.dupont\+contact@dupont-conseil.fr", "J.DUPONT+CONTACT@DUPONT-CONSEIL.FR",
    "j.dupont+contact%40dupont-conseil.fr",
])
def test_email_a_tiret_et_plus(forme):
    a = aller_retour(f"Vu {forme}.", [("email", "j.dupont+contact@dupont-conseil.fr")])
    assert a.texte == "Vu EMAIL A."


def test_pas_de_coupure_dans_un_mot():
    a = aller_retour("Alain et Alfred parlent à Al.", [("personne", "Al")])
    assert a.texte == "Alain et Alfred parlent à PERSONNE A."


def test_entite_absente_sans_trace_ni_trou():
    a = aller_retour("Marie Curie seule", [JEAN, ("personne", "Marie Curie")])
    assert a.texte == "PERSONNE A seule"
    assert a.correspondance.entites == [("PERSONNE A", "personne", "Marie Curie")]
    assert "Jean" not in ecrire_correspondance(a.correspondance)


# --- formes d'une même valeur ------------------------------------------------------

@pytest.mark.parametrize("forme", [
    "Jean Dupont", "JEAN DUPONT", "jean dupont", "jEaN dUpOnT", "Jean  Dupont", "jean-dupont",
    "jean_dupont", r"jean\_dupont", "jean.dupont", "jeandupont", "Dupont Jean", "DUPONT Jean",
    "jean%20dupont",
])
def test_formes_d_un_nom(forme):
    a = aller_retour(f"Vu : {forme} !", [JEAN])
    assert a.texte == "Vu : PERSONNE A !"


@pytest.mark.parametrize("valeur,forme", [
    ("Hélène Lefèvre", "HELENE LEFEVRE"), ("Hélène Lefèvre", "HÉLÈNE LEFÈVRE"),
    ("Hélène Lefèvre", "helene-lefevre"), ("Helene Lefevre", "Hélène Lefèvre"),
    ("Hélène Lefèvre", "Hélène Lefèvre"), ("Cœur Vaillant", "COEUR VAILLANT"),
    ("Çelik Öztürk", "celik ozturk"),
])
def test_accents_et_ligatures(valeur, forme):
    a = aller_retour(f"Vu : {forme}.", [("personne", valeur)])
    assert a.texte == "Vu : PERSONNE A."


@pytest.mark.parametrize("forme", [
    "06 12 34 56 78", "0612345678", "06.12.34.56.78", "06-12-34-56-78", "+33 6 12 34 56 78",
    r"\+33 6 12 34 56 78", "+33612345678", "0033 6 12 34 56 78",
])
def test_formes_d_un_numero(forme):
    a = aller_retour(f"Tél. {forme}.", [("numero", "06 12 34 56 78")])
    assert a.texte == "Tél. NUMERO 1."


def test_numero_dans_un_nombre_plus_long():
    a = aller_retour("Réf. 50612345678", [("numero", "0612345678")])
    assert a.texte == "Réf. 50612345678"


def test_forme_juridique_facultative():
    a = aller_retour("Dupont Conseil a signé. DUPONT CONSEIL SAS aussi.", [SOCIETE])
    assert a.texte == "ENTREPRISE 1 a signé. ENTREPRISE 1 aussi."


def test_mot_generique_seul_pas_remplace():
    a = aller_retour("Le conseil a voté.", [("entreprise", "Conseil SAS")])
    assert a.texte == "Le conseil a voté."


def test_compte_echappe_par_le_rapport():
    a = aller_retour(r"Compte jdupont\_pro et JDUPONT_PRO", [("compte", "jdupont_pro")])
    assert a.texte == "Compte PSEUDO A et PSEUDO A"


# --- URL --------------------------------------------------------------------------

@pytest.mark.parametrize("url,nature", [
    ("https://www.pappers.fr/entreprise/dupont-123", "fiche Pappers"),
    ("https://api.pappers.fr/v2/entreprise", "fiche Pappers"),
    ("https://www.societe.com/societe/dupont-123.html", "fiche Societe.com"),
    ("https://annuaire-entreprises.data.gouv.fr/entreprise/123", "registre officiel"),
    ("https://recherche-entreprises.api.gouv.fr/search?q=x", "registre officiel"),
    ("https://www.infogreffe.fr/x", "registre officiel"),
    ("https://www.lemonde.fr/article/x", "article de presse"),
    ("https://web.archive.org/web/2020/https://dupont.fr", "archive web"),
    ("https://fr.linkedin.com/in/jdupont", "compte public"),
    ("https://github.com/jdupont", "compte public"),
    ("https://www.legifrance.gouv.fr/x", "document officiel"),
    ("https://blog-inconnu.example/x", "source web"),
    ("https://faux-pappers.fr/x", "source web"),
])
def test_nature_source(url, nature):
    assert nature_source(url) == nature
    assert aller_retour(f"- <{url}> — manuel", []).texte == f"- [{nature}] — manuel"


def test_url_identifiante_entierement_remplacee():
    a = aller_retour("<https://www.pappers.fr/entreprise/jean-dupont> et "
                     "<https://dupont.fr/Jean%20Dupont>", [JEAN])
    assert a.texte == "[fiche Pappers] et [source web]"
    assert "upont" not in a.texte


def test_url_sans_chevrons_et_ponctuation_finale():
    a = aller_retour(r"Voir https://www.lemonde.fr/a\_b. Puis www.dupont.fr, fin", [])
    assert a.texte == "Voir [article de presse]. Puis [source web], fin"


def test_urls_distinctes_de_meme_nature_numerotees():
    a = aller_retour("<https://pappers.fr/a> <https://pappers.fr/b> <https://pappers.fr/a>", [])
    assert a.texte == "[fiche Pappers] [fiche Pappers 2] [fiche Pappers]"


def test_chevrons_echappes_pas_des_liens():
    assert aller_retour(r"\<script\> reste", []).texte == r"\<script\> reste"


# --- réversibilité : les pièges ------------------------------------------------------

def test_casse_d_origine_restituee():
    texte = "JEAN DUPONT, puis Jean Dupont, puis jean-dupont, puis Dupont Jean"
    assert aller_retour(texte, [JEAN]).texte.count("PERSONNE A") == 4


def test_etiquette_deja_dans_le_texte_evitee():
    texte = "UNE PERSONNE A SIGNÉ avec Jean Dupont"
    a = aller_retour(texte, [JEAN])
    assert a.texte == "UNE PERSONNE A SIGNÉ avec PERSONNE B"


def test_nature_deja_dans_le_texte_evitee():
    a = aller_retour("[fiche Pappers] <https://pappers.fr/a>", [])
    assert a.texte == "[fiche Pappers] [fiche Pappers 2]"


def test_casse_d_une_etiquette_jamais_confondue():
    a = anonymiser("Jean Dupont", [JEAN])
    r = desanonymiser("Une personne a signé. Personne A. PERSONNE AB. PERSONNE A.",
                      a.correspondance)
    assert r.texte == "Une personne a signé. Personne A. PERSONNE AB. Jean Dupont."


def test_entite_homonyme_d_une_etiquette():
    a = aller_retour("Vu Entreprise 1, et PERSONNE", [("entreprise", "Entreprise 1"),
                                                      ("personne", "PERSONNE")])
    assert "Vu ENTREPRISE 1" in a.texte


def test_valeurs_a_caracteres_speciaux_dans_la_table():
    entites = [("personne", "Barre | Oblique"), ("compte", r"anti\slash"),
               ("entreprise", "Dupont (SAS) & Fils")]
    aller_retour(r"Barre \| Oblique, anti\\slash, Dupont \(SAS\) & Fils", entites)


def test_entite_sans_lettre():
    a = aller_retour("Vu --- ici", [("compte", "---")])
    assert a.texte == "Vu PSEUDO A ici"
    aller_retour("Vu ------ ici ---+33612345678", [("compte", "---"), ("numero", "+33612345678")])


def test_texte_vide_ou_sans_entite():
    assert aller_retour("", []).texte == ""
    assert aller_retour("Rien de spécial.", [JEAN]).texte == "Rien de spécial."


def test_table_illisible():
    with pytest.raises(ErreurAnonymisation):
        lire_correspondance("pas une table")
    c = ecrire_correspondance(Correspondance(empreinte="0" * 64))
    with pytest.raises(ErreurAnonymisation):
        lire_correspondance(c.replace("| --- | --- | --- |\n\n## Sources",
                                      "| --- | --- | --- |\n| seule |\n\n## Sources"))


def test_garde_refuse_une_restitution_infidele(racine, capsys, monkeypatch):  # noqa: F811
    from core import anonymisation
    vrai = anonymisation.desanonymiser
    monkeypatch.setattr(anonymisation, "desanonymiser",
                        lambda t, c: anonymisation.Restitue(vrai(t, c).texte + "x", True, []))
    preparer(capsys)
    cli(capsys, "rapport")
    code, _, err = cli(capsys, "anonymiser")
    assert code == 1 and "rien n'a été écrit" in err
    assert not (racine / "dupont" / "rapport-anonyme.md").exists()
    assert not (racine / "dupont" / "correspondance.md").exists()


# --- fichier modifié --------------------------------------------------------------

def test_fichier_modifie_formes_canoniques():
    a = anonymiser("JEAN DUPONT et <https://pappers.fr/a>", [JEAN])
    r = desanonymiser("Note : PERSONNE A, selon [fiche Pappers].", a.correspondance)
    assert not r.exact
    assert r.texte == "Note : Jean Dupont, selon <https://pappers.fr/a>."


def test_fichier_modifie_valeur_reechappee():
    a = anonymiser(r"jdupont\_pro", [("compte", "jdupont_pro")])
    assert desanonymiser("Note PSEUDO A", a.correspondance).texte == r"Note jdupont\_pro"


def test_etiquette_inconnue_signalee():
    a = anonymiser("Jean Dupont", [JEAN])
    r = desanonymiser("PERSONNE A et PERSONNE Z et [fiche Pappers 9]", a.correspondance)
    assert r.inconnues == ["PERSONNE Z", "[fiche Pappers 9]"]
    assert "PERSONNE Z" in r.texte


# --- fragments -------------------------------------------------------------------

def test_fragment_de_nom_signale_pas_remplace():
    a = aller_retour("Jean Dupont a vu M. Dupont.", [JEAN])
    assert a.texte == "PERSONNE A a vu M. Dupont."
    assert a.fragments == ["Dupont"]


def test_fragment_insensible_casse_et_accents():
    a = aller_retour("Vu LEFEVRE.", [("personne", "Hélène Lefèvre")])
    assert a.fragments == ["Lefèvre"]


def test_aucun_fragment_si_tout_est_masque():
    assert aller_retour("Jean Dupont, <https://dupont.fr/jean>", [JEAN]).fragments == []


def test_mots_generiques_et_courts_pas_signales():
    assert aller_retour("Conseil de Dupont SAS : un rendez-vous", [("entreprise", "Conseil Lu SAS")]
                        ).fragments == []


# --- sur un vrai rapport -----------------------------------------------------------

@pytest.fixture
def e(racine):  # noqa: F811
    with enq.creer("dupont", FINALITE, "Activité de Jean Dupont",
                   departs=[JEAN]) as en:
        s = en.stockage
        societe = s.ajouter_entite(en.id, *SOCIETE)
        compte = s.ajouter_entite(en.id, "compte", "jdupont_pro")
        s.ajouter_entite(en.id, "email", "j.dupont+contact@dupont-conseil.fr")
        s.ajouter_entite(en.id, "domaine", "dupont-conseil.fr")
        s.ajouter_entite(en.id, "numero", "06 12 34 56 78")
        f = s.ajouter_fait(en.id, "JEAN DUPONT dirige Dupont Conseil SAS (06.12.34.56.78)", 1)
        s.ajouter_source(f, "https://www.pappers.fr/entreprise/dupont-conseil-123", "entreprises")
        s.ajouter_source(f, "https://annuaire-entreprises.data.gouv.fr/entreprise/123", "manuel")
        s.confirmer_fait(f)
        g = s.ajouter_fait(en.id, "Écrit sous jdupont_pro depuis dupont-conseil.fr", compte)
        s.ajouter_source(g, "https://fr.linkedin.com/in/jean-dupont", "manuel")
        s.ajouter_lien(en.id, 1, societe, "dirigeant de Dupont Conseil",
                       "https://www.societe.com/societe/dupont-conseil-123.html")
        yield en


def noms_reels(texte):
    return re.findall(r"(?i)jean|dupont|06.?12.?34", texte)


def test_rapport_reel_aller_retour(e):
    x = generer_rapport(e.stockage, e.id, T0)
    a = aller_retour(x, e._entites_a_masquer(), e.nom)
    assert noms_reels(a.texte) == []
    assert a.fragments == []
    assert "https://" not in a.texte and "<" not in a.texte
    assert a.texte.startswith("# Rapport d'enquête : DOSSIER 1\n")
    assert "[fiche Pappers]" in a.texte and "[fiche Societe.com]" in a.texte
    assert "[registre officiel]" in a.texte and "[compte public]" in a.texte
    assert r"PERSONNE A dirige ENTREPRISE 1 \(NUMERO 1\)" in a.texte


def test_entites_exclues_masquees_dans_le_texte_sans_trou_ni_trace(e):
    s = e.stockage
    voisin = s.ajouter_entite(e.id, "personne", "Paul Voisin")
    homonyme = s.ajouter_entite(e.id, "personne", "Marc Homonyme")
    absent = s.ajouter_entite(e.id, "personne", "Luc Absent")
    for x in (voisin, homonyme, absent):
        s.changer_statut_entite(x, "hors_perimetre")
    s.ajouter_fait(e.id, "Jean Dupont connaît Marc Homonyme", 1)
    x = generer_rapport(s, e.id, T0)
    a = aller_retour(x, e._entites_a_masquer(), e.nom)
    assert "Homonyme" not in a.texte and "PERSONNE B" in a.texte
    table = ecrire_correspondance(a.correspondance)
    assert "Marc Homonyme" in table
    assert "Paul Voisin" not in table and "Luc Absent" not in table


def test_exclusion_d_une_entite_ne_change_pas_les_autres(e):
    avant = anonymiser("Jean Dupont, Marie Curie",
                       [("personne", "Jean Dupont"), ("personne", "Marie Curie")])
    e.stockage.ajouter_entite(e.id, "personne", "Marie Curie")
    tiers = e.stockage.ajouter_entite(e.id, "personne", "Tiers Premier")
    e.stockage.changer_statut_entite(tiers, "hors_perimetre")
    apres = anonymiser("Jean Dupont, Marie Curie", e._entites_a_masquer())
    assert apres.texte == avant.texte


# --- commandes --------------------------------------------------------------------

def preparer(capsys, *departs):
    cli(capsys, "nouvelle", "dupont", "--finalite", FINALITE,
        "--depart", "personne:Jean Dupont", *departs)
    cli(capsys, "ajouter", "entite", "entreprise", "Dupont Conseil SAS")
    cli(capsys, "ajouter", "fait", "JEAN DUPONT dirige Dupont Conseil SAS", "--entite", "1",
        "--source", "https://www.pappers.fr/entreprise/jean-dupont")


def test_commandes_aller_retour(racine, capsys):  # noqa: F811
    preparer(capsys)
    code, out, _ = cli(capsys, "rapport")
    source = out.split("Rapport écrit : ")[1].strip()
    code, out, _ = cli(capsys, "anonymiser")
    assert code == 0 and "2 entité(s) et 1 source(s)" in out
    assert "pas anonyme au sens du RGPD" in out
    dossier = racine / "dupont"
    anonyme = (dossier / "rapport-anonyme.md").read_text(encoding="utf-8")
    table = (dossier / "correspondance.md").read_text(encoding="utf-8")
    assert noms_reels(anonyme) == []
    assert "| PERSONNE A | personne | Jean Dupont |" in table
    assert "| JEAN DUPONT |" in table
    assert "| [fiche Pappers] | <https://www.pappers.fr/entreprise/jean-dupont> |" in table

    code, out, _ = cli(capsys, "desanonymiser")
    assert code == 0 and "rapport-final.md" in out and "différent" not in out
    final = (dossier / "rapport-final.md").read_text(encoding="utf-8")
    assert final == open(source, encoding="utf-8").read()


def test_desanonymiser_autre_fichier(racine, capsys, tmp_path):  # noqa: F811
    preparer(capsys)
    cli(capsys, "rapport")
    cli(capsys, "anonymiser")
    relu = tmp_path / "relu.md"
    relu.write_text("Synthèse : PERSONNE A dirige ENTREPRISE 1 ; PERSONNE Z ?", encoding="utf-8")
    code, out, _ = cli(capsys, "desanonymiser", str(relu))
    assert code == 0 and "reprend la valeur de la table" in out and "PERSONNE Z" in out
    assert (racine / "dupont" / "rapport-final.md").read_text(encoding="utf-8") == (
        "Synthèse : Jean Dupont dirige Dupont Conseil SAS ; PERSONNE Z ?")


def test_dernier_rapport_d_apres_le_journal(racine, capsys):  # noqa: F811
    """rapport-final-… se trie après rapport-2…, mais c'est l'ordre réel qui compte."""
    preparer(capsys)
    with enq.ouvrir("dupont") as e:
        e.rapport("rapport-final-20260101.md", T0)
        dernier = e.rapport("rapport-20260102-000000.md", T0 + timedelta(days=1))
        assert e.dernier_rapport() == dernier
        dernier.unlink()
        with pytest.raises(enq.ErreurPilote, match="introuvable"):
            e.dernier_rapport()


def test_rien_a_anonymiser_sans_rapport(racine, capsys):  # noqa: F811
    preparer(capsys)
    code, _, err = cli(capsys, "anonymiser")
    assert code == 1 and "lancer d'abord `rapport`" in err
    assert not (racine / "dupont" / "correspondance.md").exists()


def test_desanonymiser_sans_table_ou_sans_fichier(racine, capsys):  # noqa: F811
    preparer(capsys)
    code, _, err = cli(capsys, "desanonymiser")
    assert code == 1 and "correspondance.md introuvable" in err
    cli(capsys, "rapport")
    cli(capsys, "anonymiser")
    code, _, err = cli(capsys, "desanonymiser", "absent.md")
    assert code == 1 and "fichier introuvable" in err


def test_desanonymiser_fichier_binaire(racine, capsys, tmp_path):  # noqa: F811
    preparer(capsys)
    cli(capsys, "rapport")
    cli(capsys, "anonymiser")
    f = tmp_path / "x.bin"
    f.write_bytes(b"\xff\xfe\x00")
    code, _, err = cli(capsys, "desanonymiser", str(f))
    assert code == 1 and "UTF-8" in err


def test_option_e(racine, capsys):  # noqa: F811
    preparer(capsys)
    cli(capsys, "rapport")
    cli(capsys, "nouvelle", "autre", "--finalite", FINALITE)
    assert cli(capsys, "-e", "dupont", "anonymiser")[0] == 0
    assert (racine / "dupont" / "rapport-anonyme.md").is_file()
    assert not (racine / "autre" / "rapport-anonyme.md").exists()


def test_fragments_affiches_mais_pas_journalises(racine, capsys):  # noqa: F811
    preparer(capsys)
    cli(capsys, "ajouter", "fait", "Propos de M. Dupont", "--entite", "1")
    cli(capsys, "rapport")
    _, out, _ = cli(capsys, "anonymiser")
    assert "À relire" in out and "« Dupont »" in out
    with enq.ouvrir("dupont") as e:
        j = e.stockage.journal(e.id)[-1]
    assert j["outil"] == "anonymiser" and "1 fragment(s) à relire" in j["resultat_resume"]
    assert "upont" not in j["requete"] + j["resultat_resume"]


@pytest.fixture
def produits(racine, capsys):  # noqa: F811
    preparer(capsys)
    cli(capsys, "rapport")
    cli(capsys, "anonymiser")
    cli(capsys, "desanonymiser")
    return racine / "dupont"


def copies(dossier):
    return sorted(p.name for p in dossier.glob("*-modifie-*.md"))


def test_relance_sans_modification_ni_copie_ni_avertissement(produits, capsys):
    _, out, _ = cli(capsys, "anonymiser")
    assert "Attention" not in out
    _, out, _ = cli(capsys, "desanonymiser")
    assert "Attention" not in out
    assert copies(produits) == []


@pytest.mark.parametrize("nom,commande", [
    ("rapport-final.md", "desanonymiser"),
    ("rapport-anonyme.md", "anonymiser"),
    ("correspondance.md", "anonymiser"),
])
def test_sortie_modifiee_conservee_et_signalee(produits, capsys, nom, commande):
    genere = (produits / nom).read_text(encoding="utf-8")
    (produits / nom).write_text(genere + "\nMes notes.\n", encoding="utf-8")
    code, out, _ = cli(capsys, commande)
    assert code == 0 and f"Attention : {nom} avait été modifié" in out
    [copie] = copies(produits)
    assert re.fullmatch(rf"{nom[:-3]}-modifie-\d{{8}}-\d{{6}}\.md", copie)
    assert copie in out
    assert (produits / copie).read_text(encoding="utf-8") == genere + "\nMes notes.\n"
    assert (produits / nom).read_text(encoding="utf-8") == genere
    # La nouvelle génération fait référence : relancer ne recrée pas de copie.
    _, out, _ = cli(capsys, commande)
    assert "Attention" not in out and copies(produits) == [copie]


def test_deux_copies_dans_la_meme_seconde(produits):
    with enq.ouvrir("dupont") as e:
        for n in (1, 2):
            (produits / "rapport-final.md").write_text(f"version {n}", encoding="utf-8")
            assert e._conserver_si_modifie("rapport-final.md", T0).read_text(
                encoding="utf-8") == f"version {n}"
    assert copies(produits) == ["rapport-final-modifie-20261001-090000-2.md",
                                "rapport-final-modifie-20261001-090000.md"]


def test_fichier_sans_empreinte_journalisee_conserve(racine, capsys):  # noqa: F811
    """Un fichier que l'outil n'a pas généré n'est pas le sien : ne pas l'écraser."""
    preparer(capsys)
    cli(capsys, "rapport")
    cli(capsys, "anonymiser")
    (racine / "dupont" / "rapport-final.md").write_text("écrit à la main", encoding="utf-8")
    _, out, _ = cli(capsys, "desanonymiser")
    assert "Attention : rapport-final.md" in out
    assert len(copies(racine / "dupont")) == 1


def test_desanonymiser_rapport_final_lui_meme(produits, capsys):
    (produits / "rapport-final.md").write_text("PERSONNE A, ajout", encoding="utf-8")
    code, out, _ = cli(capsys, "desanonymiser", str(produits / "rapport-final.md"))
    assert code == 0 and "Attention" in out
    assert (produits / "rapport-final.md").read_text(encoding="utf-8") == "Jean Dupont, ajout"


def test_conservation_journalisee_sans_echec_rien_deplace(produits, capsys, monkeypatch):
    from core import anonymisation
    (produits / "rapport-anonyme.md").write_text("modifié", encoding="utf-8")
    vrai = anonymisation.desanonymiser
    with monkeypatch.context() as m:       # garde déclenchée : rien ne bouge
        m.setattr(anonymisation, "desanonymiser",
                  lambda t, c: anonymisation.Restitue(vrai(t, c).texte + "x", True, []))
        assert cli(capsys, "anonymiser")[0] == 1
    assert copies(produits) == []
    assert (produits / "rapport-anonyme.md").read_text(encoding="utf-8") == "modifié"
    cli(capsys, "anonymiser")
    [copie] = copies(produits)
    with enq.ouvrir("dupont") as e:
        assert f"conservé : {copie}" in e.stockage.journal(e.id)[-1]["resultat_resume"]


def test_copies_modifiees_purgees(produits, capsys):
    (produits / "rapport-final.md").write_text("notes", encoding="utf-8")
    (produits / "correspondance.md").write_text(
        (produits / "correspondance.md").read_text(encoding="utf-8") + "\n", encoding="utf-8")
    cli(capsys, "desanonymiser")
    cli(capsys, "anonymiser")
    assert len(copies(produits)) == 2
    cli(capsys, "cloturer", "--oui")
    with enq.ouvrir("dupont") as e:
        expiration = e.enquete.date_expiration
    assert enq.purger_expirees(maintenant=expiration + timedelta(days=1)) == ["dupont"]
    assert copies(produits) == []
    assert list((produits / "rapport").glob("rapport-final-*.md"))    # l'archive reste


def test_journalisation(racine, capsys):  # noqa: F811
    from core.stockage import sha256_fichier
    preparer(capsys)
    cli(capsys, "rapport")
    cli(capsys, "anonymiser")
    cli(capsys, "desanonymiser")
    dossier = racine / "dupont"
    with enq.ouvrir("dupont") as e:
        *_, ano, des = e.stockage.journal(e.id)
    assert ano["outil"] == "anonymiser" and des["outil"] == "desanonymiser"
    assert ano["resultat_resume"].endswith(f"sha256 {sha256_fichier(dossier / 'rapport-anonyme.md')}")
    assert des["resultat_resume"].startswith("exacte")
    assert des["resultat_resume"].endswith(f"sha256 {sha256_fichier(dossier / 'rapport-final.md')}")


def test_anonymisation_absente_de_la_methodologie(racine, capsys):  # noqa: F811
    preparer(capsys)
    cli(capsys, "rapport")
    cli(capsys, "anonymiser")
    cli(capsys, "desanonymiser")
    with enq.ouvrir("dupont") as e:
        texte = generer_rapport(e.stockage, e.id, T0)
    assert "Requêtes journalisées : 0" in texte and "Outils utilisés : aucun" in texte


def test_apres_cloture_puis_purge(racine, capsys):  # noqa: F811
    preparer(capsys)
    cli(capsys, "cloturer", "--oui")
    # L'enquête est figée, mais son rapport archivé reste anonymisable.
    assert cli(capsys, "anonymiser")[0] == 0
    assert cli(capsys, "desanonymiser")[0] == 0
    dossier = racine / "dupont"
    with enq.ouvrir("dupont") as e:
        expiration = e.enquete.date_expiration
    assert enq.purger_expirees(maintenant=expiration + timedelta(days=1)) == ["dupont"]
    for nom in ("correspondance.md", "rapport-anonyme.md", "rapport-final.md"):
        assert not (dossier / nom).exists()
    assert list((dossier / "rapport").glob("rapport-final-*.md"))
    code, _, err = cli(capsys, "anonymiser")
    assert code == 1 and "purgée" in err
    code, _, err = cli(capsys, "desanonymiser")
    assert code == 1 and "correspondance.md introuvable" in err


# --- aide -------------------------------------------------------------------------

@pytest.mark.parametrize("sujet,attendus", [
    ("anonymiser", ["PERSONNE A", "correspondance.md", "Exemples :", "pseudonymisation",
                    "pas écrasée"]),
    ("desanonymiser", ["final.md", "fichier", "Exemples :", "casse comprise", "pas écrasée"]),
])
def test_aide(racine, capsys, sujet, attendus):  # noqa: F811
    code, out, _ = cli(capsys, "aide", sujet)
    assert code == 0
    for attendu in attendus:
        assert attendu in " ".join(out.split())


def test_aide_generale_mentionne_les_commandes(racine, capsys):  # noqa: F811
    _, out, _ = cli(capsys, "aide")
    assert "anonymiser" in out and "desanonymiser" in out
