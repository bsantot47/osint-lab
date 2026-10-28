"""Contrat commun des outils de collecte.

Chaque module d'outil expose :
- NOM : nom passé à `lancer` (identique à la méthode du guide) ;
- TYPES : types d'entité acceptés ;
- options(parser) : ajoute ses options propres à argparse ;
- lancer(enquete, entite, opts, **injections) -> Resultat.

La CLI vérifie avant l'appel la finalité, la clôture, le statut de l'entité
(jamais de collecte sur une entité écartée ou hors périmètre) et son type ;
chaque outil refait ces contrôles en tête de `lancer` (`controler_cible`),
pour qu'ils tiennent même hors de la CLI. La CLI marque la tâche faite après
un succès. L'outil, lui, journalise ce qu'il
fait et range ses sorties dans bruts/. Il ne crée jamais de fait confirmé.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable


class ErreurOutil(Exception):
    """Exécution d'un outil impossible (message destiné à l'utilisateur)."""


@dataclass
class Resultat:
    lignes: list[str] = field(default_factory=list)
    fichiers: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class Outil:
    """Pour un module qui expose plusieurs outils (ex. courriel et fuite) :
    mêmes attributs que le contrat de module."""
    NOM: str
    TYPES: tuple[str, ...]
    options: Callable
    lancer: Callable


@dataclass
class Programme:
    """Programme externe lancé en sous-processus ; `trouver` et `lanceur` sont
    injectables pour les tests (aucun programme réel n'est alors exécuté)."""
    nom: str
    installation: str
    trouver: Callable[[str], str | None] = shutil.which
    lanceur: Callable = subprocess.run

    def disponible(self) -> bool:
        return self.trouver(self.nom) is not None

    def executer(self, enquete, outil: str, args: list[str], dossier: Path,
                 duree_max: float) -> subprocess.CompletedProcess:
        """Lance le programme (jamais via un shell), journalise l'appel global
        et conserve stdout/stderr dans `dossier`."""
        enquete.stockage.exiger_collecte_possible(enquete.id)
        chemin = self.trouver(self.nom)
        if chemin is None:
            raise ErreurOutil(f"{self.nom} introuvable : {self.installation}")
        ligne = shlex.join([self.nom, *args])
        debut = time.monotonic()
        try:
            r = self.lanceur([chemin, *args], cwd=dossier, capture_output=True, text=True,
                             timeout=duree_max, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            enquete.stockage.journaliser(enquete.id, outil, ligne,
                                         resultat_resume=f"interrompu après {duree_max:.0f} s")
            raise ErreurOutil(f"{self.nom} interrompu après {duree_max:.0f} s (--duree-max)")
        except OSError as ex:
            enquete.stockage.journaliser(enquete.id, outil, ligne,
                                         resultat_resume=f"erreur : {ex}")
            raise ErreurOutil(f"{self.nom} n'a pas pu être lancé : {ex}") from ex
        duree = time.monotonic() - debut
        (dossier / f"{self.nom}.stdout.txt").write_text(r.stdout or "", encoding="utf-8")
        (dossier / f"{self.nom}.stderr.txt").write_text(r.stderr or "", encoding="utf-8")
        enquete.stockage.journaliser(enquete.id, outil, ligne,
                                     resultat_resume=f"code retour {r.returncode}, {duree:.0f} s")
        return r


STATUTS_COLLECTABLES = ("a_verifier", "confirme")


def controler_cible(enquete, entite, types: tuple[str, ...]):
    """Garde-fou appelé en tête de chaque outil, quel que soit l'appelant :
    enquête ouverte avec finalité, entité de cette enquête, ni écartée ni hors
    périmètre, d'un type accepté. Relit l'entité en base (on ne se fie pas à
    ce qui est passé) et la renvoie."""
    s = enquete.stockage
    s.exiger_collecte_possible(enquete.id)
    ligne = s.entite(entite["id"])
    if ligne["enquete_id"] != enquete.id:
        raise ErreurOutil(f"E{ligne['id']} n'appartient pas à cette enquête")
    if ligne["statut"] not in STATUTS_COLLECTABLES:
        libelle = {"ecarte": "écartée", "hors_perimetre": "hors périmètre"}[ligne["statut"]]
        raise ErreurOutil(f"E{ligne['id']} est {libelle} : aucune collecte sur cette entité")
    if ligne["type"] not in types:
        raise ErreurOutil(f"cet outil ne s'applique pas à une entité {ligne['type']}"
                          f" (types acceptés : {', '.join(types)})")
    return ligne


def dossier_brut(dossier_bruts: Path, outil: str, entite_id: int,
                 maintenant: datetime) -> Path:
    chemin = dossier_bruts / f"{outil}-E{entite_id}-{maintenant:%Y%m%d-%H%M%S}"
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def fichier_brut(dossier_bruts: Path, outil: str, entite_id: int, extension: str,
                 maintenant: datetime) -> Path:
    return dossier_bruts / f"{outil}-E{entite_id}-{maintenant:%Y%m%d-%H%M%S}.{extension}"


def ecrire_json(chemin: Path, donnees) -> Path:
    chemin.write_text(json.dumps(donnees, ensure_ascii=False, indent=1), encoding="utf-8")
    return chemin


def normaliser(texte: str) -> str:
    """Minuscules sans accents ni ponctuation, pour comparer des noms."""
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return " ".join("".join(c if c.isalnum() else " " for c in sans_accents.lower()).split())


def commande(*morceaux: str) -> str:
    """Commande osint_lab prête à copier, valeurs entre guillemets si besoin."""
    return " ".join(m if m and all(c.isalnum() or c in "-_.:/@" for c in m)
                    else '"' + m.replace('"', '\\"') + '"' for m in morceaux)
