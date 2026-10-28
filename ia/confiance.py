"""Indice vrai/faux positif d'une entité candidate : des règles d'abord.

Question posée : cette entité (un compte, une adresse, une image…) se rattache-
t-elle vraiment aux entités de référence de l'enquête, ou est-ce un homonyme ?

Références : les points de départ et les entités ayant un fait confirmé par
l'enquêteur. Candidates : les autres entités actives.

Règles déclaratives (REGLES) : chacune a un poids et renvoie une explication
quand elle s'applique. L'indice est la somme des poids, toujours affiché avec
ses raisons. C'est une aide à la lecture, jamais une décision :
- il ne change aucun statut ;
- l'affichage suit l'ordre des entités, pas l'ordre des indices.

`evaluer` mesure les règles sur les décisions déjà prises par l'enquêteur
(écartée = faux positif ; fait confirmé = vrai positif). Un modèle
(scikit-learn) ne sera envisagé que si cette mesure montre que les règles ne
suffisent pas, et sera mesuré de la même façon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from core.stockage import Stockage, domaine_enregistrable
from outils.commun import normaliser

STATUTS_CANDIDATS = ("a_verifier",)
DECISIONS_MIN = 10


@dataclass
class Contexte:
    """Ce que les règles savent de l'enquête, lu une fois dans la base."""
    entites: dict[int, dict]
    references: set[int]
    ecartees: set[int]
    empreintes: dict[int, set[str]]          # entité → sha256 de ses preuves
    voisins: dict[int, set[int]]             # entité → entités liées
    domaines: dict[int, set[str]]            # entité → domaines des sources de ses faits à vérifier
    confirmees: set[int]                     # entités ayant un fait confirmé

    def sans(self, entite_id: int) -> Contexte:
        """Même contexte, sans que l'entité serve de référence à elle-même."""
        return Contexte(self.entites, self.references - {entite_id}, self.ecartees,
                        self.empreintes, self.voisins, self.domaines, self.confirmees)


@dataclass(frozen=True)
class Regle:
    nom: str
    poids: int
    test: Callable[[Contexte, int], str | None]


@dataclass
class Indice:
    entite_id: int
    score: int
    raisons: list[tuple[str, int, str]] = field(default_factory=list)

    @property
    def niveau(self) -> str:
        if self.score >= 4:
            return "indices forts"
        if self.score >= 2:
            return "indices moyens"
        if self.score == 1:
            return "indice faible"
        return "indices contraires" if self.score < 0 else "aucun indice"


# --- règles --------------------------------------------------------------------

def _nom(ctx: Contexte, i: int) -> str:
    e = ctx.entites[i]
    return f"E{i} {e['valeur']}"


def photo_identique(ctx: Contexte, i: int) -> str | None:
    for r in sorted(ctx.references):
        if ctx.empreintes.get(i, set()) & ctx.empreintes.get(r, set()):
            return f"même fichier de preuve (sha256 identique) que {_nom(ctx, r)}"
    return None


def photo_d_une_ecartee(ctx: Contexte, i: int) -> str | None:
    for r in sorted(ctx.ecartees - {i}):
        if ctx.empreintes.get(i, set()) & ctx.empreintes.get(r, set()):
            return f"même fichier de preuve que {_nom(ctx, r)}, écartée"
    return None


def lien_avec_reference(ctx: Contexte, i: int) -> str | None:
    liees = sorted(ctx.voisins.get(i, set()) & ctx.references)
    if liees:
        return "lié à " + ", ".join(_nom(ctx, r) for r in liees)
    return None


def sources_independantes(ctx: Contexte, i: int) -> str | None:
    domaines = ctx.domaines.get(i, set())
    if len(domaines) >= 2:
        return f"faits sourcés sur {len(domaines)} domaines ({', '.join(sorted(domaines))})"
    return None


def _formes(e: dict) -> set[str]:
    """Formes comparables d'un identifiant : pseudo, partie locale d'une adresse,
    nom de domaine sans extension, nom de personne accolé."""
    v = e["valeur"].strip().lower()
    formes = set()
    if e["type"] == "email" and "@" in v:
        local, _, dom = v.rpartition("@")
        formes.add(local)
        formes.add(dom.split(".")[0])
    elif e["type"] == "domaine":
        formes.add(domaine_enregistrable(v).split(".")[0])
    elif e["type"] == "compte":
        formes.add(v.removeprefix("@"))
    elif e["type"] in ("personne", "entreprise"):
        mots = normaliser(v).split()
        if len(mots) >= 2:
            formes |= {"".join(mots), mots[0][0] + "".join(mots[1:]), "".join(mots[1:]) + mots[0]}
    return {re.sub(r"[._\-]", "", f) for f in formes if len(re.sub(r"[._\-]", "", f)) >= 4}


def identifiant_partage(ctx: Contexte, i: int) -> str | None:
    formes = _formes(ctx.entites[i])
    for r in sorted(ctx.references):
        if r != i and formes & _formes(ctx.entites[r]):
            return f"même identifiant que {_nom(ctx, r)} ({', '.join(sorted(formes & _formes(ctx.entites[r])))})"
    return None


REGLES = (
    Regle("photo_identique", 3, photo_identique),
    Regle("lien_avec_reference", 2, lien_avec_reference),
    Regle("identifiant_partage", 1, identifiant_partage),
    Regle("sources_independantes", 1, sources_independantes),
    Regle("photo_d_une_ecartee", -2, photo_d_une_ecartee),
)


# --- calcul ------------------------------------------------------------------------

def contexte(stockage: Stockage, enquete_id: int) -> Contexte:
    entites = {e["id"]: dict(e) for e in stockage.entites(enquete_id)}
    faits = stockage.faits(enquete_id)
    confirmees = {f["entite_id"] for f in faits if f["statut"] == "confirme" and f["entite_id"]}
    # Faits à vérifier seulement : un fait confirmé a forcément ≥ 2 domaines, le
    # compter ferait coïncider la règle avec la décision qu'`evaluer` mesure.
    domaines: dict[int, set[str]] = {}
    for f in faits:
        if f["entite_id"] and f["statut"] == "a_verifier":
            domaines.setdefault(f["entite_id"], set()).update(
                stockage.domaines_independants(f["id"]))
    voisins: dict[int, set[int]] = {}
    for l in stockage.liens(enquete_id):
        voisins.setdefault(l["entite_a"], set()).add(l["entite_b"])
        voisins.setdefault(l["entite_b"], set()).add(l["entite_a"])
    actives = {i for i, e in entites.items() if e["statut"] in ("a_verifier",)}
    return Contexte(
        entites=entites,
        references={i for i in actives
                    if entites[i]["origine"] == "depart" or i in confirmees},
        ecartees={i for i, e in entites.items() if e["statut"] == "ecarte"},
        empreintes={i: {p["sha256"] for p in stockage.preuves(i)} for i in entites},
        voisins=voisins, domaines=domaines, confirmees=confirmees)


def indice(ctx: Contexte, entite_id: int, regles=REGLES) -> Indice:
    ctx = ctx.sans(entite_id)
    resultat = Indice(entite_id, 0)
    for regle in regles:
        explication = regle.test(ctx, entite_id)
        if explication:
            resultat.score += regle.poids
            resultat.raisons.append((regle.nom, regle.poids, explication))
    return resultat


def candidats(ctx: Contexte) -> list[int]:
    return sorted(i for i, e in ctx.entites.items()
                  if e["statut"] in STATUTS_CANDIDATS and i not in ctx.references)


def indices(stockage: Stockage, enquete_id: int, regles=REGLES) -> list[Indice]:
    """Un indice par candidate, dans l'ordre des entités (pas de tri par score)."""
    ctx = contexte(stockage, enquete_id)
    return [indice(ctx, i, regles) for i in candidats(ctx)]


# --- mesure ------------------------------------------------------------------------

@dataclass
class Mesure:
    seuil: int
    vp: int = 0
    fp: int = 0
    fn: int = 0
    vn: int = 0

    @property
    def decisions(self) -> int:
        return self.vp + self.fp + self.fn + self.vn

    @property
    def precision(self) -> float | None:
        return self.vp / (self.vp + self.fp) if self.vp + self.fp else None

    @property
    def rappel(self) -> float | None:
        return self.vp / (self.vp + self.fn) if self.vp + self.fn else None

    @property
    def suffisant(self) -> bool:
        return self.decisions >= DECISIONS_MIN


def evaluer(stockage: Stockage, enquete_id: int, seuil: int = 2, regles=REGLES) -> Mesure:
    """Confronte les règles aux décisions de l'enquêteur : une entité écartée est un
    faux positif avéré, une entité (hors départ) ayant un fait confirmé un vrai
    positif avéré. L'indice d'une entité est calculé sans elle comme référence."""
    ctx = contexte(stockage, enquete_id)
    mesure = Mesure(seuil)
    for i, e in ctx.entites.items():
        if e["origine"] == "depart":
            continue
        if e["statut"] == "ecarte":
            verite = False
        elif i in ctx.confirmees:
            verite = True
        else:
            continue
        predit = indice(ctx, i, regles).score >= seuil
        if predit and verite:
            mesure.vp += 1
        elif predit:
            mesure.fp += 1
        elif verite:
            mesure.fn += 1
        else:
            mesure.vn += 1
    return mesure
