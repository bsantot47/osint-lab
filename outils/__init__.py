"""Outils de collecte, indexés par nom (celui des méthodes du guide)."""

from outils import archives, courriel, domaine, dorks, entreprises, pseudo

OUTILS = {o.NOM: o for o in (dorks, archives, entreprises, domaine, pseudo, courriel,
                             courriel.FUITE)}
