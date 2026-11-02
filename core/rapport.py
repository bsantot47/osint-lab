"""Génération du rapport Markdown sourcé d'une enquête.

Règles d'exclusion :
- hors périmètre (entité ou fait) : absent du rapport, sans aucune trace ;
- écarté (faux positif, homonyme…) : non détaillé, seul le nombre figure,
  pour ne pas diffuser de données sur une personne étrangère à l'enquête ;
- un fait rattaché à une entité exclue est exclu, de même qu'un lien qui touche
  une entité exclue.

Le texte venu des sources est échappé : une page collectée ne peut pas
injecter de mise en forme dans le rapport.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from core.stockage import Stockage, domaine_enregistrable, sha256_fichier

STATUTS_RAPPORTES = ("a_verifier", "confirme")
TITRE_RAPPORT = "# Rapport d'enquête : "
# Traitements du rapport lui-même : ni des requêtes ni des outils de collecte.
HORS_METHODOLOGIE = ("cloture", "purge", "rapport", "anonymiser", "desanonymiser")
LIBELLES_TYPE = {
    "compte": "Comptes", "email": "Adresses e-mail", "numero": "Numéros",
    "document": "Documents", "entreprise": "Entreprises", "personne": "Personnes",
    "domaine": "Domaines", "image": "Images",
}

_A_ECHAPPER = re.compile(r"([\\`*_{}\[\]<>()#+!|~])")


def echapper_md(texte: str) -> str:
    return _A_ECHAPPER.sub(r"\\\1", " ".join(str(texte).split()))


def lien_md(url: str) -> str:
    return "<" + quote(url.strip(), safe=":/?&=#%+@,;~!$'*-._") + ">"


def _date(texte: str) -> str:
    return datetime.fromisoformat(texte).strftime("%d/%m/%Y %H:%M")


def generer_rapport(stockage: Stockage, enquete_id: int,
                    maintenant: datetime | None = None) -> str:
    e = stockage.enquete(enquete_id)
    maintenant = maintenant or datetime.now()

    entites = {en["id"]: en for en in stockage.entites(enquete_id, STATUTS_RAPPORTES)}
    faits = [f for f in stockage.faits(enquete_id, STATUTS_RAPPORTES)
             if f["entite_id"] is None or f["entite_id"] in entites]
    liens = [l for l in stockage.liens(enquete_id)
             if l["entite_a"] in entites and l["entite_b"] in entites]
    nb_ecartes = (len(stockage.entites(enquete_id, ("ecarte",)))
                  + len(stockage.faits(enquete_id, ("ecarte",))))

    l: list[str] = [f"{TITRE_RAPPORT}{echapper_md(e.nom)}", ""]
    l += [f"- **Finalité** : {echapper_md(e.finalite)}",
          f"- **Périmètre** : {echapper_md(e.perimetre) or 'non précisé'}",
          f"- **Ouverte le** : {e.date_creation:%d/%m/%Y}"]
    if e.date_cloture:
        l.append(f"- **Close le** : {e.date_cloture:%d/%m/%Y}")
    l += [f"- **Conservation des données brutes** : {e.duree_conservation_jours} jours"
          + (f" (jusqu'au {e.date_expiration:%d/%m/%Y})" if e.date_expiration else ""),
          f"- **Rapport généré le** : {maintenant:%d/%m/%Y %H:%M}", ""]
    l += ["> Un fait n'est « confirmé » que s'il repose sur au moins deux sources de "
          "domaines indépendants **et** que l'enquêteur l'a validé. Les faits « à "
          "vérifier » sont des pistes, pas des conclusions.", ""]

    def section_faits(titre: str, statut: str) -> None:
        choisis = [f for f in faits if f["statut"] == statut]
        l.extend([f"## {titre}", ""])
        if not choisis:
            l.extend(["_Aucun._", ""])
            return
        for f in choisis:
            sources = stockage.sources(f["id"])
            domaines = {domaine_enregistrable(s["url"]) for s in sources}
            sujet = ""
            if f["entite_id"] is not None:
                en = entites[f["entite_id"]]
                sujet = f" — _{en['type']}_ {echapper_md(en['valeur'])}"
            l.append(f"### F{f['id']}. {echapper_md(f['enonce'])}{sujet}")
            l.append("")
            if not sources:
                l.append("- _Aucune source enregistrée._")
            for s in sources:
                l.append(f"- {lien_md(s['url'])} — {echapper_md(s['outil'])}, "
                         f"consulté le {_date(s['date_consultation'])}")
            l.extend(["", f"_{len(sources)} source(s), {len(domaines)} domaine(s) "
                          f"indépendant(s)._", ""])

    section_faits("Faits confirmés", "confirme")
    section_faits("Faits à vérifier", "a_verifier")

    l.extend(["## Entités", ""])
    if not entites:
        l.extend(["_Aucune._", ""])
    for type_, libelle in LIBELLES_TYPE.items():
        du_type = [en for en in entites.values() if en["type"] == type_]
        if not du_type:
            continue
        l.extend([f"### {libelle}", ""])
        for en in du_type:
            l.append(f"- **E{en['id']}** {echapper_md(en['valeur'])}")
            for p in stockage.preuves(en["id"]):
                l.append(f"  - preuve : `{Path(p['chemin_fichier']).name}` du "
                         f"{_date(p['date'])}, sha256 `{p['sha256']}`")
        l.append("")

    l.extend(["## Liens", ""])
    if not liens:
        l.extend(["_Aucun._", ""])
    for lien in liens:
        a, b = entites[lien["entite_a"]], entites[lien["entite_b"]]
        ligne = f"- E{a['id']} {echapper_md(a['valeur'])} → E{b['id']} {echapper_md(b['valeur'])} : {echapper_md(lien['nature'])}"
        if lien["source"]:
            ligne += f" (source : {lien_md(lien['source'])})"
        l.append(ligne)
    l.append("")

    journal = [j for j in stockage.journal(enquete_id)
               if j["outil"] not in HORS_METHODOLOGIE]
    outils = sorted({j["outil"].split("/")[0] for j in journal})
    l.extend(["## Méthodologie", ""])
    l.append(f"- Requêtes journalisées : {len(journal)}"
             + (f", du {_date(journal[0]['date'])} au {_date(journal[-1]['date'])}"
                if journal else ""))
    l.append(f"- Outils utilisés : {', '.join(echapper_md(o) for o in outils) or 'aucun'}")
    if nb_ecartes:
        l.append(f"- Pistes écartées après vérification (non détaillées) : {nb_ecartes}")
    l.append("")
    return "\n".join(l)


def ecrire_rapport(stockage: Stockage, enquete_id: int, chemin: str | Path,
                   maintenant: datetime | None = None) -> Path:
    """Écrit le rapport et journalise son empreinte."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(generer_rapport(stockage, enquete_id, maintenant), encoding="utf-8")
    stockage.journaliser(enquete_id, "rapport", f"rapport {chemin.name}",
                         resultat_resume=f"sha256 {sha256_fichier(chemin)}",
                         maintenant=maintenant)
    return chemin
