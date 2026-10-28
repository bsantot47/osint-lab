"""Stockage SQLite d'une enquête : entités, faits, sources, preuves, liens, journal.

Règles imposées ici, au plus bas niveau, pour qu'aucune couche supérieure ne
puisse les contourner :

- pas d'enquête sans finalité ;
- un fait ne passe « confirmé » que par `confirmer_fait`, appelé sur action de
  l'utilisateur, et seulement s'il a des sources sur ≥ 2 domaines différents ;
- chaque preuve porte l'empreinte sha256 du fichier au moment de l'ajout ;
- la clôture fixe une date d'expiration ; `purger_expirees` supprime alors les
  données brutes.

Les statuts sont stockés en ASCII (a_verifier, confirme, ecarte,
hors_perimetre) pour rester faciles à taper en ligne de commande.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

TYPES_ENTITE = ("compte", "email", "numero", "document", "entreprise", "personne",
                "domaine", "image")
STATUTS = ("a_verifier", "confirme", "ecarte", "hors_perimetre")
# La confirmation porte sur les faits : une entité n'est jamais « confirmée ».
STATUTS_ENTITE = ("a_verifier", "ecarte", "hors_perimetre")
ETATS_TACHE = ("faite", "ignoree")
SOURCES_MIN_CONFIRMATION = 2

# Suffixes à plusieurs niveaux sous lesquels chaque sous-domaine appartient à un
# titulaire distinct. Approximation volontairement courte de la Public Suffix
# List, pour rester sans dépendance : à compléter si un cas réel l'exige.
SUFFIXES_MULTIPLES = {
    "gouv.fr", "asso.fr", "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au",
    "co.jp", "com.br", "co.nz", "github.io", "gitlab.io", "blogspot.com",
    "wordpress.com", "netlify.app", "vercel.app", "pages.dev",
}
_HOTE_VALIDE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")

SCHEMA = f"""
BEGIN;
CREATE TABLE IF NOT EXISTS enquete (
    id INTEGER PRIMARY KEY,
    nom TEXT NOT NULL UNIQUE,
    finalite TEXT NOT NULL CHECK (trim(finalite) <> ''),
    perimetre TEXT NOT NULL DEFAULT '',
    date_creation TEXT NOT NULL,
    duree_conservation_jours INTEGER NOT NULL CHECK (duree_conservation_jours > 0),
    date_cloture TEXT,
    date_expiration TEXT,
    date_purge TEXT
);
CREATE TABLE IF NOT EXISTS entite (
    id INTEGER PRIMARY KEY,
    enquete_id INTEGER NOT NULL REFERENCES enquete(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN {TYPES_ENTITE}),
    valeur TEXT NOT NULL,
    statut TEXT NOT NULL DEFAULT 'a_verifier' CHECK (statut IN {STATUTS_ENTITE}),
    origine TEXT NOT NULL DEFAULT 'manuel',
    date TEXT NOT NULL,
    UNIQUE (enquete_id, type, valeur)
);
CREATE TABLE IF NOT EXISTS fait (
    id INTEGER PRIMARY KEY,
    enquete_id INTEGER NOT NULL REFERENCES enquete(id) ON DELETE CASCADE,
    enonce TEXT NOT NULL CHECK (trim(enonce) <> ''),
    entite_id INTEGER REFERENCES entite(id) ON DELETE CASCADE,
    statut TEXT NOT NULL DEFAULT 'a_verifier' CHECK (statut IN {STATUTS}),
    date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source (
    id INTEGER PRIMARY KEY,
    fait_id INTEGER NOT NULL REFERENCES fait(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    outil TEXT NOT NULL,
    date_consultation TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS preuve (
    id INTEGER PRIMARY KEY,
    entite_id INTEGER NOT NULL REFERENCES entite(id) ON DELETE CASCADE,
    chemin_fichier TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lien (
    id INTEGER PRIMARY KEY,
    enquete_id INTEGER NOT NULL REFERENCES enquete(id) ON DELETE CASCADE,
    entite_a INTEGER NOT NULL REFERENCES entite(id) ON DELETE CASCADE,
    entite_b INTEGER NOT NULL REFERENCES entite(id) ON DELETE CASCADE,
    nature TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    CHECK (entite_a <> entite_b)
);
CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY,
    enquete_id INTEGER NOT NULL REFERENCES enquete(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    outil TEXT NOT NULL,
    requete TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    resultat_resume TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tache (
    id INTEGER PRIMARY KEY,
    enquete_id INTEGER NOT NULL REFERENCES enquete(id) ON DELETE CASCADE,
    methode TEXT NOT NULL,
    entite_id INTEGER REFERENCES entite(id) ON DELETE CASCADE,
    etat TEXT NOT NULL CHECK (etat IN {ETATS_TACHE}),
    date TEXT NOT NULL
);
-- Règle des deux domaines garantie par la base elle-même, même en SQL direct.
-- domaine() est la fonction Python domaine_enregistrable, enregistrée à la
-- connexion ; hors de l'outil elle n'existe pas et toute confirmation échoue.
CREATE TRIGGER IF NOT EXISTS fait_confirme_a_la_creation
BEFORE INSERT ON fait WHEN NEW.statut = 'confirme'
BEGIN
    SELECT RAISE(ABORT, 'un fait ne peut pas naître confirmé');
END;
CREATE TRIGGER IF NOT EXISTS fait_confirme_deux_domaines
BEFORE UPDATE OF statut ON fait WHEN NEW.statut = 'confirme'
BEGIN
    SELECT RAISE(ABORT, 'confirmation refusée : moins de {SOURCES_MIN_CONFIRMATION} domaines indépendants')
    WHERE (SELECT count(DISTINCT domaine(url)) FROM source
           WHERE fait_id = NEW.id AND domaine(url) <> '') < {SOURCES_MIN_CONFIRMATION};
END;
-- Journal en ajout seul : ni modifié ni supprimé, même à la purge.
CREATE TRIGGER IF NOT EXISTS journal_sans_modification
BEFORE UPDATE ON journal
BEGIN
    SELECT RAISE(ABORT, 'le journal ne se modifie pas');
END;
CREATE TRIGGER IF NOT EXISTS journal_sans_suppression
BEFORE DELETE ON journal
BEGIN
    SELECT RAISE(ABORT, 'le journal ne se supprime pas');
END;
COMMIT;
"""


class ErreurStockage(Exception):
    """Opération refusée par les règles du stockage."""


class FinaliteManquante(ErreurStockage):
    """Aucune collecte ni enquête sans finalité déclarée."""


class ConfirmationImpossible(ErreurStockage):
    """Le fait n'a pas assez de sources indépendantes."""


class EnqueteCloturee(ErreurStockage):
    """L'enquête est close : plus de collecte ni de modification."""


@dataclass(frozen=True)
class Enquete:
    id: int
    nom: str
    finalite: str
    perimetre: str
    date_creation: datetime
    duree_conservation_jours: int
    date_cloture: datetime | None
    date_expiration: datetime | None
    date_purge: datetime | None

    @property
    def cloturee(self) -> bool:
        return self.date_cloture is not None


def _maintenant(maintenant: datetime | None) -> datetime:
    return (maintenant or datetime.now()).replace(microsecond=0)


def _iso(d: datetime) -> str:
    return d.isoformat(timespec="seconds")


def _date(texte: str | None) -> datetime | None:
    return datetime.fromisoformat(texte) if texte else None


def sha256_fichier(chemin: str | Path) -> str:
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 16), b""):
            h.update(bloc)
    return h.hexdigest()


def domaine_enregistrable(url: str) -> str:
    """Domaine qui identifie le titulaire d'une URL, pour juger l'indépendance.

    `fr.linkedin.com` et `www.linkedin.com` donnent `linkedin.com` ;
    `alice.github.io` et `bob.github.io` restent distincts.
    """
    hote = (urlsplit(url if "//" in url else "//" + url).hostname or "").lower().rstrip(".")
    if ":" in hote:  # adresse IPv6
        return hote
    if not _HOTE_VALIDE.match(hote):
        return ""
    if hote.replace(".", "").isdigit():  # adresse IPv4
        return hote
    labels = hote.split(".")
    if len(labels) >= 3 and ".".join(labels[-2:]) in SUFFIXES_MULTIPLES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


class Stockage:
    def __init__(self, chemin_db: str | Path):
        self.chemin_db = str(chemin_db)
        self.cx = sqlite3.connect(self.chemin_db)
        self.cx.row_factory = sqlite3.Row
        self.cx.create_function("domaine", 1, domaine_enregistrable, deterministic=True)
        self.cx.execute("PRAGMA foreign_keys = ON")
        # WAL + NORMAL : une seule synchronisation disque par point de contrôle
        # au lieu d'une par écriture, sans risque de corruption.
        self.cx.execute("PRAGMA journal_mode = WAL")
        self.cx.execute("PRAGMA synchronous = NORMAL")
        self.cx.executescript(SCHEMA)

    def fermer(self) -> None:
        self.cx.close()

    def __enter__(self) -> Stockage:
        return self

    def __exit__(self, *_exc) -> None:
        self.fermer()

    def _ecrire(self, sql: str, params: tuple = ()) -> int:
        with self.cx:
            return self.cx.execute(sql, params).lastrowid

    # --- enquête ---------------------------------------------------------

    def creer_enquete(self, nom: str, finalite: str, perimetre: str = "",
                      duree_conservation_jours: int = 90,
                      maintenant: datetime | None = None) -> int:
        if not finalite or not finalite.strip():
            raise FinaliteManquante("une enquête exige une finalité déclarée")
        if duree_conservation_jours <= 0:
            raise ErreurStockage("la durée de conservation doit être positive")
        return self._ecrire(
            "INSERT INTO enquete (nom, finalite, perimetre, date_creation,"
            " duree_conservation_jours) VALUES (?, ?, ?, ?, ?)",
            (nom, finalite.strip(), perimetre, _iso(_maintenant(maintenant)),
             duree_conservation_jours))

    def enquete(self, enquete_id: int) -> Enquete:
        ligne = self.cx.execute("SELECT * FROM enquete WHERE id = ?", (enquete_id,)).fetchone()
        if ligne is None:
            raise ErreurStockage(f"enquête {enquete_id} inconnue")
        return Enquete(
            id=ligne["id"], nom=ligne["nom"], finalite=ligne["finalite"],
            perimetre=ligne["perimetre"], date_creation=_date(ligne["date_creation"]),
            duree_conservation_jours=ligne["duree_conservation_jours"],
            date_cloture=_date(ligne["date_cloture"]),
            date_expiration=_date(ligne["date_expiration"]),
            date_purge=_date(ligne["date_purge"]))

    def enquete_par_nom(self, nom: str) -> Enquete:
        ligne = self.cx.execute("SELECT id FROM enquete WHERE nom = ?", (nom,)).fetchone()
        if ligne is None:
            raise ErreurStockage(f"enquête « {nom} » inconnue")
        return self.enquete(ligne["id"])

    def exiger_collecte_possible(self, enquete_id: int) -> Enquete:
        """Garde à appeler avant toute collecte ou modification."""
        e = self.enquete(enquete_id)
        if not e.finalite.strip():
            raise FinaliteManquante(f"l'enquête « {e.nom} » n'a pas de finalité")
        if e.cloturee:
            raise EnqueteCloturee(f"l'enquête « {e.nom} » est close")
        return e

    def _enquete_de(self, table: str, id_: int) -> int:
        ligne = self.cx.execute(f"SELECT enquete_id FROM {table} WHERE id = ?", (id_,)).fetchone()
        if ligne is None:
            raise ErreurStockage(f"{table} {id_} inconnu(e)")
        return ligne["enquete_id"]

    # --- entités ---------------------------------------------------------

    def ajouter_entite(self, enquete_id: int, type_: str, valeur: str,
                       origine: str = "manuel", maintenant: datetime | None = None) -> int:
        """Ajoute une entité, ou renvoie l'id de celle qui existe déjà.

        `origine` : « depart » (posée au cadrage), « manuel » ou nom de l'outil.
        """
        self.exiger_collecte_possible(enquete_id)
        if type_ not in TYPES_ENTITE:
            raise ErreurStockage(f"type d'entité inconnu : {type_}")
        valeur = valeur.strip()
        existante = self.cx.execute(
            "SELECT id FROM entite WHERE enquete_id = ? AND type = ? AND valeur = ?",
            (enquete_id, type_, valeur)).fetchone()
        if existante:
            return existante["id"]
        return self._ecrire(
            "INSERT INTO entite (enquete_id, type, valeur, origine, date) VALUES (?, ?, ?, ?, ?)",
            (enquete_id, type_, valeur, origine, _iso(_maintenant(maintenant))))

    def entite(self, entite_id: int) -> sqlite3.Row:
        ligne = self.cx.execute("SELECT * FROM entite WHERE id = ?", (entite_id,)).fetchone()
        if ligne is None:
            raise ErreurStockage(f"entité {entite_id} inconnue")
        return ligne

    def entites(self, enquete_id: int, statuts: tuple[str, ...] = STATUTS) -> list[sqlite3.Row]:
        marques = ",".join("?" * len(statuts))
        return self.cx.execute(
            f"SELECT * FROM entite WHERE enquete_id = ? AND statut IN ({marques}) ORDER BY id",
            (enquete_id, *statuts)).fetchall()

    def changer_statut_entite(self, entite_id: int, statut: str) -> None:
        """Statut d'une entité (écartée, hors périmètre…), posé par l'utilisateur.
        Une entité n'est jamais confirmée : seuls les faits le sont."""
        if statut == "confirme":
            raise ErreurStockage("une entité ne se confirme pas : confirmer ses faits")
        if statut not in STATUTS_ENTITE:
            raise ErreurStockage(f"statut inconnu : {statut}")
        self.exiger_collecte_possible(self._enquete_de("entite", entite_id))
        self._ecrire("UPDATE entite SET statut = ? WHERE id = ?", (statut, entite_id))

    # --- faits et sources ------------------------------------------------

    def ajouter_fait(self, enquete_id: int, enonce: str, entite_id: int | None = None,
                     maintenant: datetime | None = None) -> int:
        self.exiger_collecte_possible(enquete_id)
        if entite_id is not None and self._enquete_de("entite", entite_id) != enquete_id:
            raise ErreurStockage("l'entité n'appartient pas à cette enquête")
        return self._ecrire(
            "INSERT INTO fait (enquete_id, enonce, entite_id, date) VALUES (?, ?, ?, ?)",
            (enquete_id, enonce.strip(), entite_id, _iso(_maintenant(maintenant))))

    def fait(self, fait_id: int) -> sqlite3.Row:
        ligne = self.cx.execute("SELECT * FROM fait WHERE id = ?", (fait_id,)).fetchone()
        if ligne is None:
            raise ErreurStockage(f"fait {fait_id} inconnu")
        return ligne

    def faits(self, enquete_id: int, statuts: tuple[str, ...] = STATUTS) -> list[sqlite3.Row]:
        marques = ",".join("?" * len(statuts))
        return self.cx.execute(
            f"SELECT * FROM fait WHERE enquete_id = ? AND statut IN ({marques}) ORDER BY id",
            (enquete_id, *statuts)).fetchall()

    def ajouter_source(self, fait_id: int, url: str, outil: str,
                       maintenant: datetime | None = None) -> int:
        self.exiger_collecte_possible(self._enquete_de("fait", fait_id))
        if not domaine_enregistrable(url):
            raise ErreurStockage(f"URL de source invalide : {url!r}")
        return self._ecrire(
            "INSERT INTO source (fait_id, url, outil, date_consultation) VALUES (?, ?, ?, ?)",
            (fait_id, url.strip(), outil, _iso(_maintenant(maintenant))))

    def sources(self, fait_id: int) -> list[sqlite3.Row]:
        return self.cx.execute(
            "SELECT * FROM source WHERE fait_id = ? ORDER BY id", (fait_id,)).fetchall()

    def domaines_independants(self, fait_id: int) -> set[str]:
        return {domaine_enregistrable(s["url"]) for s in self.sources(fait_id)}

    def confirmation_possible(self, fait_id: int) -> bool:
        """Signale seulement : la décision reste à l'utilisateur."""
        return (self.fait(fait_id)["statut"] == "a_verifier"
                and len(self.domaines_independants(fait_id)) >= SOURCES_MIN_CONFIRMATION)

    def confirmer_fait(self, fait_id: int) -> None:
        """À n'appeler que sur décision explicite de l'utilisateur."""
        self.exiger_collecte_possible(self._enquete_de("fait", fait_id))
        domaines = self.domaines_independants(fait_id)
        if len(domaines) < SOURCES_MIN_CONFIRMATION:
            raise ConfirmationImpossible(
                f"fait {fait_id} : {len(domaines)} domaine(s) indépendant(s) "
                f"({', '.join(sorted(domaines)) or 'aucun'}), "
                f"il en faut {SOURCES_MIN_CONFIRMATION}")
        self._ecrire("UPDATE fait SET statut = 'confirme' WHERE id = ?", (fait_id,))

    def changer_statut_fait(self, fait_id: int, statut: str) -> None:
        """Écarter, remettre à vérifier ou exclure un fait. Confirmer passe par
        `confirmer_fait`, qui vérifie les sources."""
        if statut == "confirme":
            raise ErreurStockage("utiliser confirmer_fait pour confirmer un fait")
        if statut not in STATUTS:
            raise ErreurStockage(f"statut inconnu : {statut}")
        self.exiger_collecte_possible(self._enquete_de("fait", fait_id))
        self._ecrire("UPDATE fait SET statut = ? WHERE id = ?", (statut, fait_id))

    # --- preuves ---------------------------------------------------------

    def ajouter_preuve(self, entite_id: int, chemin_fichier: str | Path,
                       maintenant: datetime | None = None) -> int:
        self.exiger_collecte_possible(self._enquete_de("entite", entite_id))
        chemin = Path(chemin_fichier)
        if not chemin.is_file():
            raise ErreurStockage(f"fichier de preuve introuvable : {chemin}")
        return self._ecrire(
            "INSERT INTO preuve (entite_id, chemin_fichier, sha256, date) VALUES (?, ?, ?, ?)",
            (entite_id, str(chemin), sha256_fichier(chemin), _iso(_maintenant(maintenant))))

    def preuves(self, entite_id: int) -> list[sqlite3.Row]:
        return self.cx.execute(
            "SELECT * FROM preuve WHERE entite_id = ? ORDER BY id", (entite_id,)).fetchall()

    def verifier_preuve(self, preuve_id: int) -> bool:
        """Vrai si le fichier existe et n'a pas changé depuis son ajout."""
        ligne = self.cx.execute("SELECT * FROM preuve WHERE id = ?", (preuve_id,)).fetchone()
        if ligne is None:
            raise ErreurStockage(f"preuve {preuve_id} inconnue")
        chemin = Path(ligne["chemin_fichier"])
        return chemin.is_file() and sha256_fichier(chemin) == ligne["sha256"]

    # --- liens -----------------------------------------------------------

    def ajouter_lien(self, enquete_id: int, entite_a: int, entite_b: int, nature: str,
                     source: str = "", maintenant: datetime | None = None) -> int:
        self.exiger_collecte_possible(enquete_id)
        for e in (entite_a, entite_b):
            if self._enquete_de("entite", e) != enquete_id:
                raise ErreurStockage("les entités liées doivent appartenir à l'enquête")
        return self._ecrire(
            "INSERT INTO lien (enquete_id, entite_a, entite_b, nature, source, date)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (enquete_id, entite_a, entite_b, nature, source, _iso(_maintenant(maintenant))))

    def liens(self, enquete_id: int) -> list[sqlite3.Row]:
        return self.cx.execute(
            "SELECT * FROM lien WHERE enquete_id = ? ORDER BY id", (enquete_id,)).fetchall()

    # --- tâches (méthodes faites ou ignorées) ------------------------------

    def marquer_tache(self, enquete_id: int, methode: str, entite_id: int | None = None,
                      etat: str = "faite", maintenant: datetime | None = None) -> None:
        """Note qu'une méthode a été faite (ou ignorée) sur une entité, pour que
        le guide ne la suggère plus. Une seule ligne par (méthode, entité)."""
        self.exiger_collecte_possible(enquete_id)
        if etat not in ETATS_TACHE:
            raise ErreurStockage(f"état de tâche inconnu : {etat}")
        if entite_id is not None and self._enquete_de("entite", entite_id) != enquete_id:
            raise ErreurStockage("l'entité n'appartient pas à cette enquête")
        with self.cx:
            self.cx.execute(
                "DELETE FROM tache WHERE enquete_id = ? AND methode = ? AND entite_id IS ?",
                (enquete_id, methode, entite_id))
            self.cx.execute(
                "INSERT INTO tache (enquete_id, methode, entite_id, etat, date)"
                " VALUES (?, ?, ?, ?, ?)",
                (enquete_id, methode, entite_id, etat, _iso(_maintenant(maintenant))))

    def taches(self, enquete_id: int) -> list[sqlite3.Row]:
        return self.cx.execute(
            "SELECT * FROM tache WHERE enquete_id = ? ORDER BY id", (enquete_id,)).fetchall()

    # --- journal ---------------------------------------------------------

    def journaliser(self, enquete_id: int, outil: str, requete: str, url: str = "",
                    resultat_resume: str = "", maintenant: datetime | None = None) -> int:
        return self._ecrire(
            "INSERT INTO journal (enquete_id, date, outil, requete, url, resultat_resume)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (enquete_id, _iso(_maintenant(maintenant)), outil, requete, url, resultat_resume))

    def journal(self, enquete_id: int) -> list[sqlite3.Row]:
        return self.cx.execute(
            "SELECT * FROM journal WHERE enquete_id = ? ORDER BY id", (enquete_id,)).fetchall()

    # --- clôture et expiration -------------------------------------------

    def cloturer(self, enquete_id: int, maintenant: datetime | None = None) -> datetime:
        """Fige l'enquête et fixe la date d'expiration des données brutes."""
        e = self.exiger_collecte_possible(enquete_id)
        cloture = _maintenant(maintenant)
        expiration = cloture + timedelta(days=e.duree_conservation_jours)
        with self.cx:
            self.cx.execute(
                "UPDATE enquete SET date_cloture = ?, date_expiration = ? WHERE id = ?",
                (_iso(cloture), _iso(expiration), enquete_id))
            self.cx.execute(
                "INSERT INTO journal (enquete_id, date, outil, requete, resultat_resume)"
                " VALUES (?, ?, 'cloture', 'cloturer', ?)",
                (enquete_id, _iso(cloture), f"expiration des données le {_iso(expiration)}"))
        return expiration

    def enquetes_expirees(self, maintenant: datetime | None = None) -> list[Enquete]:
        lignes = self.cx.execute(
            "SELECT id FROM enquete WHERE date_expiration IS NOT NULL"
            " AND date_purge IS NULL AND date_expiration <= ? ORDER BY id",
            (_iso(_maintenant(maintenant)),)).fetchall()
        return [self.enquete(l["id"]) for l in lignes]

    def purger_enquete(self, enquete_id: int, maintenant: datetime | None = None) -> list[Path]:
        """Supprime fichiers de preuve et données collectées d'une enquête close.

        Restent la fiche de l'enquête (nom, finalité, dates) et le journal
        complet, pour la traçabilité, complété d'une ligne attestant la purge.
        Le rapport archivé n'est pas touché. Renvoie les fichiers supprimés.
        """
        e = self.enquete(enquete_id)
        if not e.cloturee:
            raise ErreurStockage(f"l'enquête « {e.nom} » n'est pas close")
        chemins = [Path(l["chemin_fichier"]) for l in self.cx.execute(
            "SELECT p.chemin_fichier FROM preuve p JOIN entite en ON en.id = p.entite_id"
            " WHERE en.enquete_id = ?", (enquete_id,))]
        supprimes = []
        for chemin in chemins:
            if chemin.is_file():
                chemin.unlink()
                supprimes.append(chemin)
        date = _iso(_maintenant(maintenant))
        with self.cx:
            # Les cascades suppriment faits, sources, preuves et liens.
            self.cx.execute("DELETE FROM entite WHERE enquete_id = ?", (enquete_id,))
            self.cx.execute("DELETE FROM fait WHERE enquete_id = ?", (enquete_id,))
            self.cx.execute("UPDATE enquete SET date_purge = ? WHERE id = ?", (date, enquete_id))
            self.cx.execute(
                "INSERT INTO journal (enquete_id, date, outil, requete, resultat_resume)"
                " VALUES (?, ?, 'purge', 'purger_enquete', ?)",
                (enquete_id, date, f"{len(supprimes)} fichier(s) de preuve supprimé(s)"))
        return supprimes

    def purger_expirees(self, maintenant: datetime | None = None) -> list[Enquete]:
        """À appeler à chaque lancement de l'outil."""
        expirees = self.enquetes_expirees(maintenant)
        for e in expirees:
            self.purger_enquete(e.id, maintenant)
        return expirees
