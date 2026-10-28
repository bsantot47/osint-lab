from datetime import datetime

import pytest

from core.stockage import Stockage

T0 = datetime(2026, 10, 1, 9, 0, 0)


@pytest.fixture
def stockage(tmp_path):
    with Stockage(tmp_path / "enquete.db") as s:
        yield s


@pytest.fixture
def enquete_id(stockage):
    return stockage.creer_enquete(
        "test", finalite="Vérification préalable d'un partenaire commercial",
        perimetre="Activité professionnelle publique", duree_conservation_jours=30,
        maintenant=T0)
