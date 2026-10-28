"""Configuration locale : clés d'API et réglages réseau.

Le fichier `config.local.toml` (à la racine du projet, ou désigné par la
variable OSINT_LAB_CONFIG) est ignoré par git. Les clés n'existent nulle part
ailleurs. Modèle : `config.exemple.toml`.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
FICHIER_DEFAUT = RACINE / "config.local.toml"

USER_AGENT_DEFAUT = "osint-lab/0.1 (enquete OSINT, donnees publiques)"


class CleManquante(Exception):
    """Une clé d'API requise n'est pas dans la config locale."""


@dataclass(frozen=True)
class Config:
    delai_s: float = 2.0
    timeout_s: float = 20.0
    taille_max_octets: int = 10 * 1024 * 1024
    user_agent: str = USER_AGENT_DEFAUT
    cles: dict[str, str] = field(default_factory=dict)

    def cle(self, service: str) -> str:
        valeur = self.cles.get(service, "").strip()
        if not valeur:
            raise CleManquante(
                f"clé d'API « {service} » absente : l'ajouter dans la section"
                f" [cles] de {FICHIER_DEFAUT.name}")
        return valeur


def charger(chemin: str | Path | None = None) -> Config:
    """Charge la config ; sans fichier, les valeurs par défaut s'appliquent."""
    chemin = Path(chemin or os.environ.get("OSINT_LAB_CONFIG") or FICHIER_DEFAUT)
    if not chemin.is_file():
        return Config()
    with open(chemin, "rb") as f:
        donnees = tomllib.load(f)
    reseau = donnees.get("reseau", {})
    return Config(
        delai_s=float(reseau.get("delai_s", Config.delai_s)),
        timeout_s=float(reseau.get("timeout_s", Config.timeout_s)),
        taille_max_octets=int(reseau.get("taille_max_octets", Config.taille_max_octets)),
        user_agent=str(reseau.get("user_agent", USER_AGENT_DEFAUT)),
        cles={k: str(v) for k, v in donnees.get("cles", {}).items()},
    )
