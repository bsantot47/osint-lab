"""État d'une enquête : d'où l'on part, ce qui est fait, ce qui reste à faire."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass

from core.stockage import Enquete, Stockage
from pilote.guide import Suggestion, suggerer


@dataclass
class Etat:
    enquete: Enquete
    depart: list[sqlite3.Row]
    entites_par_statut: Counter
    faits_par_statut: Counter
    taches: list[sqlite3.Row]
    confirmables: list[sqlite3.Row]
    suggestions: list[Suggestion]

    @property
    def pivot(self) -> Suggestion | None:
        return self.suggestions[0] if self.suggestions else None


def calculer(stockage: Stockage, enquete_id: int) -> Etat:
    entites = stockage.entites(enquete_id)
    faits = stockage.faits(enquete_id)
    taches = stockage.taches(enquete_id)
    return Etat(
        enquete=stockage.enquete(enquete_id),
        depart=[e for e in entites if e["origine"] == "depart"],
        entites_par_statut=Counter(e["statut"] for e in entites),
        faits_par_statut=Counter(f["statut"] for f in faits),
        taches=taches,
        confirmables=[f for f in faits if stockage.confirmation_possible(f["id"])],
        suggestions=suggerer(entites, {(t["methode"], t["entite_id"]) for t in taches}),
    )
