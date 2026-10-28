"""Règles de pivot : quelle méthode suggérer ensuite, selon les entités connues.

Tout est déclaratif : pour ajouter une piste, on ajoute une méthode dans
METHODES et/ou une règle dans REGLES, sans toucher à `suggerer`.

Une règle s'applique à chaque entité active (à vérifier ou confirmée) de son
type ; une entité écartée ou hors périmètre ne déclenche jamais de collecte.
Une méthode faite ou ignorée sur une entité n'est plus suggérée pour elle.
L'ordre des suggestions est celui des règles et des méthodes, sans autre
priorisation : c'est l'enquêteur qui choisit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

STATUTS_ACTIFS = ("a_verifier", "confirme")


@dataclass(frozen=True)
class Methode:
    nom: str
    libelle: str
    manuelle: bool = False

    def commande(self, entite_id: int) -> str:
        if self.manuelle:
            return f"(manuel) puis : marquer {self.nom} --entite {entite_id}"
        return f"lancer {self.nom} --entite {entite_id}"


@dataclass(frozen=True)
class Regle:
    type_entite: str
    methodes: tuple[str, ...]
    raison: str


METHODES = {m.nom: m for m in (
    Methode("dorks", "requêtes Google/Bing prêtes à cliquer"),
    Methode("entreprises", "annuaire-entreprises et Pappers : mandats, liens"),
    Methode("pseudo", "sherlock/maigret : présence du pseudo sur les plateformes"),
    Methode("courriel", "holehe : services associés à l'adresse"),
    Methode("fuite", "HIBP : présence dans des fuites connues (vérification seule)"),
    Methode("domaine", "crt.sh, RDAP, Shodan : sources publiées, jamais la cible"),
    Methode("archives", "Wayback Machine : captures historiques"),
    Methode("exif", "métadonnées du fichier (exiftool)"),
    Methode("recherche_inversee", "recherche d'image inversée", manuelle=True),
)}

REGLES = (
    Regle("personne", ("dorks", "entreprises"), "un nom seul : recherche ouverte, puis mandats"),
    Regle("compte", ("pseudo",), "un pseudo : présence sur d'autres plateformes"),
    Regle("email", ("courriel", "fuite"), "une adresse : services associés, puis fuites"),
    Regle("entreprise", ("entreprises",), "une entreprise : mandats et dirigeants"),
    Regle("domaine", ("domaine", "archives"), "un domaine : infrastructure, puis historique"),
    Regle("image", ("exif", "recherche_inversee"), "une image : métadonnées, puis origine"),
)


@dataclass(frozen=True)
class Suggestion:
    methode: Methode
    entite_id: int
    type_entite: str
    valeur: str
    raison: str

    @property
    def commande(self) -> str:
        return self.methode.commande(self.entite_id)


def suggerer(entites: Iterable[Mapping], deja_traitees: set[tuple[str, int | None]],
             regles: Iterable[Regle] = REGLES,
             methodes: Mapping[str, Methode] = METHODES) -> list[Suggestion]:
    """Suggestions pour des entités (lignes avec id, type, valeur, statut).

    `deja_traitees` : couples (méthode, entité_id) faits ou ignorés.
    Les entités de départ passent en premier, puis l'ordre d'ajout.
    """
    actives = sorted((e for e in entites if e["statut"] in STATUTS_ACTIFS),
                     key=lambda e: (e["origine"] != "depart", e["id"]))
    suggestions = []
    for e in actives:
        for regle in regles:
            if regle.type_entite != e["type"]:
                continue
            for nom in regle.methodes:
                if (nom, e["id"]) not in deja_traitees:
                    suggestions.append(
                        Suggestion(methodes[nom], e["id"], e["type"], e["valeur"], regle.raison))
    return suggestions
