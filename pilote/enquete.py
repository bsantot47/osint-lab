"""Créer, ouvrir, clôturer et purger une enquête sur disque.

Un dossier par enquête :

    enquetes/<nom>/
    ├── enquete.db    base SQLite (source de vérité)
    ├── cadrage.md    finalité, périmètre, conservation, points de départ
    ├── fiches/       une fiche Markdown par entité, régénérée depuis la base
    ├── preuves/      copies horodatées des fichiers de preuve
    ├── bruts/        sorties brutes des outils
    └── rapport/      rapports générés (le rapport final y est archivé)

À la purge d'une enquête expirée, fiches/, preuves/ et bruts/ sont supprimés ;
restent cadrage.md, rapport/ et la base (fiche d'enquête et journal).
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from core.rapport import echapper_md, ecrire_rapport, lien_md
from core.stockage import FinaliteManquante, Stockage, TYPES_ENTITE

RACINE_DEFAUT = Path(__file__).resolve().parent.parent / "enquetes"
NOM_VALIDE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
FICHIER_COURANTE = ".courante"
SOUS_DOSSIERS = ("fiches", "preuves", "bruts", "rapport")
A_PURGER = ("fiches", "preuves", "bruts")


class ErreurPilote(Exception):
    """Opération du pilote impossible (message destiné à l'utilisateur)."""


def racine_enquetes(racine: str | Path | None = None) -> Path:
    return Path(racine or os.environ.get("OSINT_LAB_ENQUETES") or RACINE_DEFAUT)


class EnqueteOuverte:
    def __init__(self, dossier: Path):
        self.dossier = dossier
        self.nom = dossier.name
        self.stockage = Stockage(dossier / "enquete.db")
        self.id = self.stockage.enquete_par_nom(self.nom).id

    def fermer(self) -> None:
        self.stockage.fermer()

    def __enter__(self) -> EnqueteOuverte:
        return self

    def __exit__(self, *_exc) -> None:
        self.fermer()

    def chemin(self, sous_dossier: str) -> Path:
        return self.dossier / sous_dossier

    @property
    def enquete(self):
        return self.stockage.enquete(self.id)

    # --- preuves ---------------------------------------------------------

    def ajouter_preuve(self, entite_id: int, fichier: str | Path,
                       maintenant: datetime | None = None) -> int:
        """Copie le fichier dans preuves/ sous un nom horodaté, puis l'enregistre
        (sha256 calculé sur la copie, qui seule fait foi)."""
        source = Path(fichier)
        if not source.is_file():
            raise ErreurPilote(f"fichier introuvable : {source}")
        self.stockage.exiger_collecte_possible(self.id)
        horodatage = (maintenant or datetime.now()).strftime("%Y%m%d-%H%M%S")
        cible = self.chemin("preuves") / f"{horodatage}-E{entite_id}-{source.name}"
        n = 1
        while cible.exists():
            n += 1
            cible = cible.with_name(f"{horodatage}-E{entite_id}-{n}-{source.name}")
        shutil.copy2(source, cible)
        try:
            return self.stockage.ajouter_preuve(entite_id, cible, maintenant)
        except Exception:
            cible.unlink()
            raise

    # --- fiches ----------------------------------------------------------

    def ecrire_fiches(self) -> None:
        """Régénère toutes les fiches depuis la base, supprime les obsolètes."""
        dossier = self.chemin("fiches")
        attendues = set()
        for e in self.stockage.entites(self.id):
            chemin = dossier / f"E{e['id']}.md"
            chemin.write_text(self._fiche(e), encoding="utf-8")
            attendues.add(chemin)
        for ancienne in dossier.glob("E*.md"):
            if ancienne not in attendues:
                ancienne.unlink()

    def _fiche(self, e) -> str:
        s = self.stockage
        l = [f"# E{e['id']} — {echapper_md(e['valeur'])}", "",
             f"- **Type** : {e['type']}", f"- **Statut** : {e['statut']}",
             f"- **Origine** : {e['origine']}", f"- **Ajoutée le** : {e['date']}", ""]
        faits = [f for f in s.faits(self.id) if f["entite_id"] == e["id"]]
        l.extend(["## Faits", ""] if faits else [])
        for f in faits:
            l.append(f"- **F{f['id']}** {echapper_md(f['enonce'])} ({f['statut']})")
            for src in s.sources(f["id"]):
                l.append(f"  - {lien_md(src['url'])} — {echapper_md(src['outil'])}, "
                         f"{src['date_consultation']}")
        preuves = s.preuves(e["id"])
        l.extend(["", "## Preuves", ""] if preuves else [])
        for p in preuves:
            l.append(f"- `{Path(p['chemin_fichier']).name}` — {p['date']} — sha256 `{p['sha256']}`")
        liens = [k for k in s.liens(self.id) if e["id"] in (k["entite_a"], k["entite_b"])]
        l.extend(["", "## Liens", ""] if liens else [])
        for k in liens:
            autre = k["entite_b"] if k["entite_a"] == e["id"] else k["entite_a"]
            sens = "→" if k["entite_a"] == e["id"] else "←"
            l.append(f"- {sens} E{autre} : {echapper_md(k['nature'])}"
                     + (f" (source : {lien_md(k['source'])})" if k["source"] else ""))
        return "\n".join(l).rstrip() + "\n"

    # --- rapport et clôture ----------------------------------------------

    def rapport(self, nom_fichier: str | None = None,
                maintenant: datetime | None = None) -> Path:
        if self.enquete.date_purge:
            raise ErreurPilote("enquête purgée : le rapport archivé est dans rapport/")
        maintenant = maintenant or datetime.now()
        nom_fichier = nom_fichier or f"rapport-{maintenant:%Y%m%d-%H%M%S}.md"
        return ecrire_rapport(self.stockage, self.id, self.chemin("rapport") / nom_fichier,
                              maintenant)

    def cloturer(self, maintenant: datetime | None = None) -> tuple[Path, datetime]:
        """Fige l'enquête, archive le rapport final, fixe l'expiration des bruts."""
        maintenant = maintenant or datetime.now()
        expiration = self.stockage.cloturer(self.id, maintenant)
        chemin = self.rapport(f"rapport-final-{maintenant:%Y%m%d}.md", maintenant)
        return chemin, expiration


def _ecrire_cadrage(dossier: Path, nom: str, finalite: str, perimetre: str,
                    duree: int, departs: list[tuple[str, str]], date: datetime) -> None:
    l = [f"# Cadrage : {echapper_md(nom)}", "",
         f"- **Date** : {date:%d/%m/%Y %H:%M}",
         f"- **Finalité** : {echapper_md(finalite)}",
         f"- **Périmètre** : {echapper_md(perimetre) or 'non précisé'}",
         f"- **Conservation des données brutes après clôture** : {duree} jours", "",
         "## Points de départ", ""]
    l += [f"- {t} : {echapper_md(v)}" for t, v in departs] or ["- aucun"]
    (dossier / "cadrage.md").write_text("\n".join(l) + "\n", encoding="utf-8")


def creer(nom: str, finalite: str, perimetre: str = "", duree_conservation_jours: int = 90,
          departs: list[tuple[str, str]] | None = None, racine: str | Path | None = None,
          maintenant: datetime | None = None) -> EnqueteOuverte:
    """Crée l'enquête et son arborescence, et la désigne comme courante."""
    departs = departs or []
    if not NOM_VALIDE.match(nom):
        raise ErreurPilote("nom invalide : minuscules, chiffres, - et _ (64 caractères max)")
    if not finalite or not finalite.strip():
        raise FinaliteManquante("une enquête exige une finalité déclarée")
    for type_, valeur in departs:
        if type_ not in TYPES_ENTITE or not valeur.strip():
            raise ErreurPilote(f"point de départ invalide : {type_}:{valeur}")
    racine = racine_enquetes(racine)
    dossier = racine / nom
    if dossier.exists():
        raise ErreurPilote(f"l'enquête « {nom} » existe déjà")
    maintenant = (maintenant or datetime.now()).replace(microsecond=0)

    for sous in SOUS_DOSSIERS:
        (dossier / sous).mkdir(parents=True)
    try:
        with Stockage(dossier / "enquete.db") as s:
            enquete_id = s.creer_enquete(nom, finalite, perimetre, duree_conservation_jours,
                                         maintenant)
            for type_, valeur in departs:
                s.ajouter_entite(enquete_id, type_, valeur, origine="depart",
                                 maintenant=maintenant)
        _ecrire_cadrage(dossier, nom, finalite.strip(), perimetre, duree_conservation_jours,
                        departs, maintenant)
    except Exception:
        shutil.rmtree(dossier)
        raise
    definir_courante(nom, racine)
    e = EnqueteOuverte(dossier)
    e.ecrire_fiches()
    return e


def ouvrir(nom: str, racine: str | Path | None = None) -> EnqueteOuverte:
    dossier = racine_enquetes(racine) / nom
    if not NOM_VALIDE.match(nom) or not (dossier / "enquete.db").is_file():
        raise ErreurPilote(f"enquête « {nom} » introuvable")
    return EnqueteOuverte(dossier)


def lister(racine: str | Path | None = None) -> list[str]:
    racine = racine_enquetes(racine)
    if not racine.is_dir():
        return []
    return sorted(d.name for d in racine.iterdir() if (d / "enquete.db").is_file())


def definir_courante(nom: str, racine: str | Path | None = None) -> None:
    (racine_enquetes(racine) / FICHIER_COURANTE).write_text(nom, encoding="utf-8")


def courante(racine: str | Path | None = None) -> str:
    fichier = racine_enquetes(racine) / FICHIER_COURANTE
    nom = fichier.read_text(encoding="utf-8").strip() if fichier.is_file() else ""
    if not nom:
        raise ErreurPilote("aucune enquête courante : utiliser `nouvelle`, `ouvrir` ou -e NOM")
    return nom


def purger_expirees(racine: str | Path | None = None,
                    maintenant: datetime | None = None) -> list[str]:
    """À appeler à chaque lancement : purge toute enquête dont la conservation
    est échue. Renvoie les noms des enquêtes purgées."""
    purgees = []
    for nom in lister(racine):
        dossier = racine_enquetes(racine) / nom
        with Stockage(dossier / "enquete.db") as s:
            if not s.purger_expirees(maintenant):
                continue
        for sous in A_PURGER:
            shutil.rmtree(dossier / sous, ignore_errors=True)
        purgees.append(nom)
    return purgees
