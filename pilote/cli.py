"""Point d'entrée des commandes.

Chaque lancement commence par purger les enquêtes dont la conservation est
échue. L'enquête visée est celle passée par -e, sinon l'enquête courante
(définie par `nouvelle` ou `ouvrir`).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from core.anonymisation import ErreurAnonymisation
from core.config import CleManquante
from core.requetes import ErreurRequete
from core.stockage import ErreurStockage, STATUTS, TYPES_ENTITE
from ia import confiance
from ia import entites as ia_entites
from ia.entites import ErreurIA
from outils import OUTILS
from outils.commun import STATUTS_COLLECTABLES, ErreurOutil
from pilote import enquete as enq
from pilote.enquete import ErreurPilote, EnqueteOuverte
from pilote.etat import calculer
from pilote.guide import METHODES, STATUTS_ACTIFS

LIBELLES_STATUT = {"a_verifier": "à vérifier", "confirme": "confirmé",
                   "ecarte": "écarté", "hors_perimetre": "hors périmètre"}
STATUTS_MANUELS = tuple(s for s in STATUTS if s != "confirme")


def _ouvrir(args) -> EnqueteOuverte:
    return enq.ouvrir(args.enquete or enq.courante())


def _depart(texte: str) -> tuple[str, str]:
    type_, sep, valeur = texte.partition(":")
    if not sep or type_ not in TYPES_ENTITE or not valeur.strip():
        raise argparse.ArgumentTypeError(
            f"attendu TYPE:VALEUR avec TYPE parmi {', '.join(TYPES_ENTITE)}")
    return type_, valeur.strip()


def _demander(question: str) -> str | None:
    return input(question).strip() if sys.stdin.isatty() else None


# --- affichages --------------------------------------------------------------

def afficher_statut(e: EnqueteOuverte) -> None:
    etat = calculer(e.stockage, e.id)
    q = etat.enquete
    print(f"Enquête « {q.nom} », ouverte le {q.date_creation:%d/%m/%Y}"
          + (f", close le {q.date_cloture:%d/%m/%Y}" if q.cloturee else ""))
    print(f"Finalité : {q.finalite}")
    if q.perimetre:
        print(f"Périmètre : {q.perimetre}")

    print("\nDépart :")
    for d in etat.depart or []:
        print(f"  E{d['id']} {d['type']} « {d['valeur']} »")
    if not etat.depart:
        print("  aucun point de départ")

    def compte(c) -> str:
        return ", ".join(f"{n} {LIBELLES_STATUT[s]}" for s, n in sorted(c.items())) or "aucun"
    print("\nFait :")
    print(f"  entités : {compte(etat.entites_par_statut)}")
    print(f"  faits   : {compte(etat.faits_par_statut)}")
    for t in etat.taches:
        cible = f" sur E{t['entite_id']}" if t["entite_id"] else ""
        print(f"  {t['methode']}{cible} ({t['etat']})")

    if q.cloturee:
        print(f"\nEnquête close : données brutes conservées jusqu'au "
              f"{q.date_expiration:%d/%m/%Y}.")
        return
    print("\nÀ faire :")
    for f in etat.confirmables:
        print(f"  F{f['id']} a des sources sur 2 domaines indépendants ou plus :"
              f" à valider → confirmer {f['id']}")
    for s in etat.suggestions:
        print(f"  E{s.entite_id} {s.type_entite} « {s.valeur} » : {s.methode.libelle}"
              f" → {s.commande}")
    if not etat.confirmables and not etat.suggestions:
        print("  rien de suggéré : ajouter une trouvaille (`ajouter`) ou rédiger le rapport")
    if etat.pivot:
        print(f"\nPivot suggéré : {etat.pivot.commande}  ({etat.pivot.raison})")


def afficher_entites(e: EnqueteOuverte) -> None:
    entites = e.stockage.entites(e.id)
    if not entites:
        print("Aucune entité.")
        return
    liens = e.stockage.liens(e.id)
    noms = {x["id"]: x["valeur"] for x in entites}
    for x in entites:
        origine = " (départ)" if x["origine"] == "depart" else ""
        print(f"E{x['id']} [{x['type']}] {x['valeur']} — {LIBELLES_STATUT[x['statut']]}{origine}")
        for k in liens:
            if k["entite_a"] == x["id"]:
                print(f"    → E{k['entite_b']} {noms[k['entite_b']]} : {k['nature']}")
            elif k["entite_b"] == x["id"]:
                print(f"    ← E{k['entite_a']} {noms[k['entite_a']]} : {k['nature']}")


def afficher_recoupement(e: EnqueteOuverte) -> None:
    s = e.stockage
    faits = s.faits(e.id)
    if not faits:
        print("Aucun fait.")
        return
    print(f"{'Fait':<6}{'Statut':<16}{'Domaines':<10}{'Confirmable':<13}Énoncé")
    for f in faits:
        domaines = s.domaines_independants(f["id"])
        confirmable = "oui" if s.confirmation_possible(f["id"]) else ""
        sujet = f" [E{f['entite_id']}]" if f["entite_id"] else ""
        print(f"F{f['id']:<5}{LIBELLES_STATUT[f['statut']]:<16}{len(domaines):<10}"
              f"{confirmable:<13}{f['enonce']}{sujet}")
        for src in s.sources(f["id"]):
            print(f"{'':<6}- {src['url']} ({src['outil']}, {src['date_consultation']})")


# --- commandes ---------------------------------------------------------------

def cmd_nouvelle(args) -> None:
    finalite = args.finalite if args.finalite is not None else _demander(
        "Finalité de l'enquête (obligatoire) : ")
    perimetre = args.perimetre if args.perimetre is not None else (
        _demander("Périmètre (facultatif) : ") or "")
    with enq.creer(args.nom, finalite or "", perimetre, args.conservation, args.depart) as e:
        print(f"Enquête créée : {e.dossier}\n")
        afficher_statut(e)


def cmd_ouvrir(args) -> None:
    with enq.ouvrir(args.nom) as e:
        enq.definir_courante(e.nom)
        afficher_statut(e)


def cmd_liste(_args) -> None:
    try:
        actuelle = enq.courante()
    except ErreurPilote:
        actuelle = None
    noms = enq.lister()
    for nom in noms:
        with enq.ouvrir(nom) as e:
            q = e.enquete
            etat = "purgée" if q.date_purge else "close" if q.cloturee else "en cours"
        print(f"{'*' if nom == actuelle else ' '} {nom} ({etat})")
    if not noms:
        print("Aucune enquête.")


def cmd_statut(args) -> None:
    with _ouvrir(args) as e:
        afficher_statut(e)


def cmd_ajouter(args) -> None:
    with _ouvrir(args) as e:
        s = e.stockage
        if args.objet == "entite":
            id_ = s.ajouter_entite(e.id, args.type, args.valeur)
            print(f"E{id_} {args.type} « {args.valeur} »")
            for fichier in args.preuve:
                p = e.ajouter_preuve(id_, fichier)
                print(f"  preuve {p} : sha256 {s.preuves(id_)[-1]['sha256']}")
        elif args.objet == "fait":
            id_ = s.ajouter_fait(e.id, args.enonce, args.entite)
            for url in args.source:
                s.ajouter_source(id_, url, args.outil)
            print(f"F{id_} ajouté ({len(args.source)} source(s))")
        elif args.objet == "source":
            s.ajouter_source(args.fait, args.url, args.outil)
            print(f"Source ajoutée à F{args.fait} :"
                  f" {len(s.domaines_independants(args.fait))} domaine(s) indépendant(s)")
            if s.confirmation_possible(args.fait):
                print(f"  confirmation possible, à valider → confirmer {args.fait}")
        elif args.objet == "lien":
            id_ = s.ajouter_lien(e.id, args.a, args.b, args.nature, args.source or "")
            print(f"Lien {id_} : E{args.a} → E{args.b} ({args.nature})")
        elif args.objet == "preuve":
            p = e.ajouter_preuve(args.entite, args.fichier)
            print(f"Preuve {p} : sha256 {s.preuves(args.entite)[-1]['sha256']}")
        e.ecrire_fiches()


def cmd_confirmer(args) -> None:
    with _ouvrir(args) as e:
        e.stockage.confirmer_fait(args.fait)
        domaines = sorted(e.stockage.domaines_independants(args.fait))
        print(f"F{args.fait} confirmé par l'enquêteur (sources : {', '.join(domaines)})")
        e.ecrire_fiches()


def cmd_statuer(args) -> None:
    with _ouvrir(args) as e:
        if args.objet == "entite":
            e.stockage.changer_statut_entite(args.id, args.statut)
        else:
            e.stockage.changer_statut_fait(args.id, args.statut)
        print(f"{'E' if args.objet == 'entite' else 'F'}{args.id} : "
              f"{LIBELLES_STATUT[args.statut]}")
        e.ecrire_fiches()


def cmd_marquer(args) -> None:
    with _ouvrir(args) as e:
        etat = "ignoree" if args.ignorer else "faite"
        e.stockage.marquer_tache(e.id, args.methode, args.entite, etat)
        cible = f" sur E{args.entite}" if args.entite else ""
        print(f"{args.methode}{cible} : {'ignorée' if args.ignorer else 'faite'}")


def cmd_lancer(args) -> None:
    outil = OUTILS.get(args.outil)
    if outil is None:
        manuelle = METHODES.get(args.outil)
        if manuelle and manuelle.manuelle:
            raise ErreurPilote(f"« {args.outil} » est une méthode manuelle : la faire, puis"
                               f" `marquer {args.outil} --entite N`")
        raise ErreurPilote(f"outil « {args.outil} » inconnu ou pas encore disponible"
                           f" (disponibles : {', '.join(sorted(OUTILS))})")
    opts = parser_outil(outil).parse_args(args.args)

    with _ouvrir(args) as e:
        s = e.stockage
        s.exiger_collecte_possible(e.id)
        entite = s.entite(opts.entite)
        if entite["enquete_id"] != e.id:
            raise ErreurPilote(f"E{opts.entite} n'appartient pas à cette enquête")
        if entite["statut"] not in STATUTS_ACTIFS:
            raise ErreurPilote(f"E{opts.entite} est {LIBELLES_STATUT[entite['statut']]} :"
                               f" aucune collecte sur cette entité")
        if entite["type"] not in outil.TYPES:
            raise ErreurPilote(f"{outil.NOM} ne s'applique pas à une entité {entite['type']}"
                               f" (types acceptés : {', '.join(outil.TYPES)})")
        resultat = outil.lancer(e, entite, opts)
        s.marquer_tache(e.id, outil.NOM, entite["id"])
        print("\n".join(resultat.lignes))


def parser_outil(outil) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=f"osint_lab.py lancer {outil.NOM}",
        description=f"Types d'entité acceptés : {', '.join(outil.TYPES)}.")
    p.add_argument("--entite", type=int, required=True, help="entité visée (EN → N)")
    outil.options(p)
    return p


EXEMPLES = {
    "nouvelle": ['nouvelle dupont --finalite "Vérification préalable d\'un fournisseur"'
                 ' --perimetre "activité professionnelle" --conservation 30'
                 ' --depart "personne:Jean Dupont" --depart "email:jdupont@exemple.fr"'],
    "ouvrir": ["ouvrir dupont"],
    "liste": ["liste"],
    "statut": ["statut", "-e dupont statut"],
    "ajouter": ['ajouter entite compte jdupont --preuve capture.png',
                'ajouter fait "Gérant de Dupont Conseil" --entite 1'
                ' --source https://annuaire-entreprises.data.gouv.fr/entreprise/123456789'
                ' --outil entreprises',
                "ajouter source 1 https://www.pappers.fr/entreprise/123456789",
                'ajouter lien 1 3 "dirigeant"',
                "ajouter preuve 3 ~/Téléchargements/profil.png"],
    "confirmer": ["confirmer 1"],
    "statuer": ["statuer entite 4 ecarte", "statuer fait 2 hors_perimetre",
                "statuer entite 4 a_verifier"],
    "marquer": ["marquer recherche_inversee --entite 5",
                "marquer dorks --entite 1 --ignorer"],
    "lancer": ["lancer dorks --entite 1 --contexte Lyon", "lancer entreprises --entite 1",
               "lancer domaine --entite 6 --sources crtsh,rdap", "aide lancer pseudo"],
    "analyser": ["analyser enquetes/dupont/bruts/page.html --entite 3"],
    "confiance": ["confiance", "confiance --entite 4", "confiance --evaluer --seuil 3"],
    "entites": ["entites"],
    "recouper": ["recouper"],
    "journal": ["journal", "journal --outil domaine", "journal --dernieres 20"],
    "rapport": ["rapport"],
    "anonymiser": ["anonymiser", "-e dupont anonymiser"],
    "desanonymiser": ["desanonymiser", "desanonymiser relu.md"],
    "cloturer": ["cloturer", "cloturer --oui"],
    "aide": ["aide", "aide ajouter", "aide ajouter fait", "aide lancer", "aide lancer fuite"],
}

PARCOURS = """\
Parcours type (le détail de chaque commande : aide <commande>) :
  1. nouvelle NOM --finalite "..." --depart type:valeur   finalité obligatoire
  2. statut                       ce qui est fait, ce qui reste, pivot suggéré
  3. lancer OUTIL --entite N      collecte (journalisée) ; l'outil propose, rien n'est créé
  4. ajouter entite|fait|source|lien|preuve               l'enquêteur retient ce qui vaut
  5. recouper ; confirmer F       confirmation : au moins 2 domaines indépendants
  6. statuer entite N ecarte|hors_perimetre               faux positifs, tiers
  7. rapport ; cloturer           rapport sourcé, puis purge à l'échéance
  (facultatif) anonymiser ; desanonymiser                 rapport à étiquettes, et retour

Outils de collecte (aide lancer OUTIL) :
{outils}
Méthodes manuelles (à faire soi-même, puis `marquer`) : {manuelles}"""


def cmd_aide(args) -> None:
    racine: argparse.ArgumentParser = args.parser
    sujet = args.sujet
    if not sujet:
        racine.print_help()
        outils = "\n".join(f"  {nom:<12} {', '.join(o.TYPES)}" for nom, o in sorted(OUTILS.items()))
        manuelles = ", ".join(sorted(n for n, m in METHODES.items() if m.manuelle))
        print("\n" + PARCOURS.format(outils=outils, manuelles=manuelles))
        return
    commandes = _sous_parsers(racine)
    if sujet[0] not in commandes:
        raise ErreurPilote(f"commande inconnue : {sujet[0]} (aide sans argument pour la liste)")
    if sujet[0] == "lancer" and len(sujet) > 1:
        outil = OUTILS.get(sujet[1])
        if outil is None:
            raise ErreurPilote(f"outil inconnu : {sujet[1]} (disponibles : {', '.join(sorted(OUTILS))})")
        parser_outil(outil).print_help()
        return
    cible = commandes[sujet[0]]
    if len(sujet) > 1:
        objets = _sous_parsers(cible)
        if sujet[1] not in objets:
            raise ErreurPilote(f"« {sujet[0]} {sujet[1]} » n'existe pas")
        cible = objets[sujet[1]]
    cible.print_help()
    exemples = [x for x in EXEMPLES.get(sujet[0], [])
                if len(sujet) == 1 or x.split()[1] == sujet[1]]
    if exemples:
        print("\nExemples :")
        print("\n".join(f"  python osint_lab.py {x}" for x in exemples))
    if sujet == ["lancer"]:
        print("\nOutils : " + ", ".join(sorted(OUTILS)))


def _sous_parsers(p: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    for action in p._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    return {}


def cmd_analyser(args) -> None:
    with _ouvrir(args) as e:
        s = e.stockage
        s.exiger_collecte_possible(e.id)
        actives = s.entites(e.id, STATUTS_COLLECTABLES)
        if args.entite is not None and args.entite not in {x["id"] for x in actives}:
            raise ErreurPilote(f"E{args.entite} n'est pas une entité active de cette enquête")
        texte = ia_entites.lire_page(args.fichier)
        mentions, tronque = ia_entites.analyser(texte, ia_entites.charger_modele(args.modele))
        ia_entites.rapprocher(mentions, actives)
        lignes = [f"Analyse locale de {args.fichier} (propositions, rien n'est enregistré"
                  " dans l'enquête) :"]
        if tronque:
            lignes.append(f"  texte tronqué à {ia_entites.TAILLE_MAX} caractères")
        lignes += ia_entites.propositions(mentions, args.entite)
        chemin = e.chemin("bruts") / (f"analyse-{Path(args.fichier).stem}-"
                                      f"{datetime.now():%Y%m%d-%H%M%S}.txt")
        chemin.write_text("\n".join(lignes) + "\n", encoding="utf-8")
        compte = ia_entites.compter(mentions)
        s.journaliser(e.id, "analyser", f"analyse locale (spaCy {args.modele}) de {args.fichier}",
                      resultat_resume=", ".join(f"{n} {t}" for t, n in sorted(compte.items()))
                      + f" ; {chemin.name}")
        print("\n".join(lignes))
        print(f"Propositions enregistrées : {chemin}")


def cmd_confiance(args) -> None:
    with _ouvrir(args) as e:
        if args.evaluer:
            m = confiance.evaluer(e.stockage, e.id, args.seuil)
            def pct(x):
                return "n/a" if x is None else f"{x:.0%}"
            print(f"Règles mesurées sur {m.decisions} décision(s) de l'enquêteur"
                  f" (seuil {m.seuil}) :")
            print(f"  vrais positifs {m.vp}, faux positifs {m.fp},"
                  f" faux négatifs {m.fn}, vrais négatifs {m.vn}")
            print(f"  précision {pct(m.precision)}, rappel {pct(m.rappel)}")
            if not m.suffisant:
                print(f"  moins de {confiance.DECISIONS_MIN} décisions : trop peu pour conclure")
            return
        resultats = confiance.indices(e.stockage, e.id)
        if args.entite is not None:
            resultats = [r for r in resultats if r.entite_id == args.entite]
        if not resultats:
            print("Aucune entité candidate (à vérifier, hors points de départ).")
            return
        print("Indices de rattachement (aide à la lecture, aucune décision prise) :")
        noms = {x["id"]: x for x in e.stockage.entites(e.id)}
        for r in resultats:
            x = noms[r.entite_id]
            print(f"E{r.entite_id} [{x['type']}] {x['valeur']} : {r.niveau} ({r.score:+d})")
            for _nom, poids, explication in r.raisons:
                print(f"    {poids:+d} {explication}")


def cmd_entites(args) -> None:
    with _ouvrir(args) as e:
        afficher_entites(e)


def cmd_recouper(args) -> None:
    with _ouvrir(args) as e:
        afficher_recoupement(e)


def cmd_journal(args) -> None:
    with _ouvrir(args) as e:
        lignes = [j for j in e.stockage.journal(e.id)
                  if not args.outil or j["outil"].split("/")[0] == args.outil]
        if args.dernieres:
            lignes = lignes[-args.dernieres:]
        if not lignes:
            print("Journal vide.")
        for j in lignes:
            print(f"{j['date'][:19].replace('T', ' ')}  {j['outil']:<16} {j['requete']}")
            if j["url"]:
                print(f"{'':37}{j['url']}")
            if j["resultat_resume"]:
                print(f"{'':37}→ {j['resultat_resume']}")


def cmd_rapport(args) -> None:
    with _ouvrir(args) as e:
        print(f"Rapport écrit : {e.rapport()}")


def _signaler_conservee(copie: Path) -> None:
    nom = copie.name.split(enq.SUFFIXE_MODIFIE)[0] + copie.suffix
    print(f"Attention : {nom} avait été modifié depuis sa génération."
          f" Votre version est conservée : {copie}")


def cmd_anonymiser(args) -> None:
    with _ouvrir(args) as e:
        source, anonyme, table, a, conservees = e.anonymiser()
        for copie in conservees:
            _signaler_conservee(copie)
        c = a.correspondance
        print(f"Rapport source : {source}")
        print(f"Rapport anonymisé : {anonyme}")
        print(f"Correspondance : {table}  (à ne jamais transmettre avec le rapport)")
        print(f"{c.nb_entites} entité(s) et {len(c.sources)} source(s) remplacées.")
        if a.fragments:
            print("À relire avant diffusion, morceaux de noms encore présents :")
            print("\n".join(f"  « {f} »" for f in a.fragments))
        print("Rapport pseudonymisé, pas anonyme au sens du RGPD : le contexte (dates, "
              "fonctions, lieux) reste à relire.")


def cmd_desanonymiser(args) -> None:
    with _ouvrir(args) as e:
        final, r, conservee = e.desanonymiser(args.fichier)
        if conservee:
            _signaler_conservee(conservee)
        print(f"Rapport restitué : {final}")
        if not r.exact:
            print("Fichier différent du rapport anonymisé d'origine : chaque étiquette reprend"
                  " la valeur de la table (casse et variantes d'origine non rétablies).")
        if r.inconnues:
            print("Étiquettes absentes de la table, laissées telles quelles :")
            print("\n".join(f"  {x}" for x in r.inconnues))


def cmd_cloturer(args) -> None:
    with _ouvrir(args) as e:
        if not args.oui:
            reponse = _demander(f"Clôturer « {e.nom} » ? Plus aucune modification ne sera "
                                f"possible. (oui/non) ")
            if reponse != "oui":
                raise ErreurPilote("clôture annulée (confirmer par « oui », ou --oui)")
        chemin, expiration = e.cloturer()
        print(f"Enquête close. Rapport final archivé : {chemin}")
        print(f"Fiches, preuves et sorties brutes seront supprimées le {expiration:%d/%m/%Y}.")


# --- analyse des arguments ---------------------------------------------------

def construire_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="osint_lab.py", description=__doc__.splitlines()[0])
    p.add_argument("-e", "--enquete", help="enquête visée (défaut : enquête courante)")
    sous = p.add_subparsers(dest="commande", required=True, metavar="COMMANDE")

    c = sous.add_parser("nouvelle", help="créer une enquête (finalité obligatoire)")
    c.add_argument("nom", help="nom court, sert de nom de dossier (ex. dupont)")
    c.add_argument("--finalite", help="pourquoi cette enquête : obligatoire, demandée si absente")
    c.add_argument("--perimetre", help="ce qui est dans le champ (demandé si absent)")
    c.add_argument("--conservation", type=int, default=90, metavar="JOURS",
                   help="durée de conservation des bruts après clôture (défaut : 90)")
    c.add_argument("--depart", type=_depart, action="append", default=[],
                   metavar="TYPE:VALEUR", help="point de départ (répétable)")
    c.set_defaults(func=cmd_nouvelle)

    c = sous.add_parser("ouvrir", help="désigner l'enquête courante")
    c.add_argument("nom", help="nom de l'enquête (voir `liste`)")
    c.set_defaults(func=cmd_ouvrir)

    sous.add_parser("liste", help="lister les enquêtes").set_defaults(func=cmd_liste)
    sous.add_parser("statut", help="départ, fait, à faire, pivot suggéré"
                    ).set_defaults(func=cmd_statut)

    c = sous.add_parser("ajouter", help="saisir une trouvaille manuelle")
    objets = c.add_subparsers(dest="objet", required=True, metavar="OBJET")
    o = objets.add_parser("entite", help="entité (et ses preuves)")
    o.add_argument("type", choices=TYPES_ENTITE, help="type d'entité")
    o.add_argument("valeur", help="nom, adresse, pseudo, domaine… (guillemets si espaces)")
    o.add_argument("--preuve", action="append", default=[], metavar="FICHIER",
                   help="capture ou fichier copié et haché dans preuves/ (répétable)")
    o = objets.add_parser("fait", help="fait (et ses sources)")
    o.add_argument("enonce", help="le fait, en une phrase vérifiable")
    o.add_argument("--entite", type=int, help="entité concernée (EN → N)")
    o.add_argument("--source", action="append", default=[], metavar="URL",
                   help="page qui atteste le fait (répétable)")
    o.add_argument("--outil", default="manuel", help="outil qui a trouvé la source (défaut : manuel)")
    o = objets.add_parser("source", help="source d'un fait")
    o.add_argument("fait", type=int, help="numéro du fait (FN → N)")
    o.add_argument("url", help="page qui atteste le fait")
    o.add_argument("--outil", default="manuel", help="outil qui a trouvé la source (défaut : manuel)")
    o = objets.add_parser("lien", help="lien entre deux entités")
    o.add_argument("a", type=int, help="première entité")
    o.add_argument("b", type=int, help="seconde entité")
    o.add_argument("nature", help="nature du lien (ex. \"dirigeant\", \"même avatar\")")
    o.add_argument("--source", metavar="URL", help="page qui atteste le lien")
    o = objets.add_parser("preuve", help="preuve d'une entité")
    o.add_argument("entite", type=int, help="entité concernée")
    o.add_argument("fichier", help="fichier à copier (horodaté, sha256) dans preuves/")
    c.set_defaults(func=cmd_ajouter)

    c = sous.add_parser("confirmer", help="confirmer un fait (≥ 2 domaines indépendants)")
    c.add_argument("fait", type=int, help="numéro du fait (voir `recouper`)")
    c.set_defaults(func=cmd_confirmer)

    c = sous.add_parser("statuer", help="écarter, exclure ou remettre à vérifier")
    c.add_argument("objet", choices=("entite", "fait"), help="entite ou fait")
    c.add_argument("id", type=int, help="numéro de l'entité ou du fait")
    c.add_argument("statut", choices=STATUTS_MANUELS,
                   help="ecarte (faux positif), hors_perimetre (exclu du rapport), a_verifier."
                        " Jamais confirme : voir `confirmer`")
    c.set_defaults(func=cmd_statuer)

    c = sous.add_parser("marquer", help="noter une méthode faite (ou ignorée)")
    c.add_argument("methode", choices=sorted(METHODES), help="méthode du guide")
    c.add_argument("--entite", type=int, help="entité sur laquelle la méthode a été faite")
    c.add_argument("--ignorer", action="store_true",
                   help="méthode volontairement non faite (elle n'est plus suggérée)")
    c.set_defaults(func=cmd_marquer)

    c = sous.add_parser("lancer", help="exécuter un outil de collecte")
    c.add_argument("outil", help="nom de l'outil (aide lancer OUTIL pour ses options)")
    c.add_argument("args", nargs=argparse.REMAINDER, help="--entite N et options de l'outil")
    c.set_defaults(func=cmd_lancer)

    c = sous.add_parser("analyser", help="IA locale : noms, organisations, lieux, dates d'une page")
    c.add_argument("fichier", help="page enregistrée (.html ou texte), ex. dans bruts/")
    c.add_argument("--entite", type=int, help="entité d'où provient la page (pour proposer des liens)")
    c.add_argument("--modele", default=ia_entites.MODELE_DEFAUT,
                   help=f"modèle spaCy installé localement (défaut : {ia_entites.MODELE_DEFAUT})")
    c.set_defaults(func=cmd_analyser)

    c = sous.add_parser("confiance", help="indices vrai/faux positif (règles)")
    c.add_argument("--entite", type=int)
    c.add_argument("--evaluer", action="store_true",
                   help="mesurer les règles sur les décisions déjà prises")
    c.add_argument("--seuil", type=int, default=2)
    c.set_defaults(func=cmd_confiance)

    sous.add_parser("entites", help="graphe des entités et liens").set_defaults(func=cmd_entites)
    sous.add_parser("recouper", help="tableau faits / sources / statut"
                    ).set_defaults(func=cmd_recouper)
    c = sous.add_parser("journal", help="lire le journal des requêtes (lecture seule)")
    c.add_argument("--outil", help="seulement cet outil (ex. domaine, pseudo, cloture)")
    c.add_argument("--dernieres", type=int, metavar="N", help="seulement les N dernières lignes")
    c.set_defaults(func=cmd_journal)

    sous.add_parser("rapport", help="compiler le rapport sourcé").set_defaults(func=cmd_rapport)

    c = sous.add_parser(
        "anonymiser", help="version à étiquettes du dernier rapport, et table de correspondance",
        description="Remplace dans le dernier rapport généré chaque entité de l'enquête par une"
                    " étiquette (PERSONNE A, ENTREPRISE 1, PSEUDO A, EMAIL A, DOMAINE 1, NUMERO 1,"
                    " DOCUMENT 1, IMAGE 1) et chaque URL par la nature de la source ([fiche"
                    " Pappers], [registre officiel], [compte public]…). Écrit rapport-anonyme.md et"
                    " correspondance.md dans le dossier de l'enquête. Une version modifiée à la main"
                    " n'est pas écrasée : elle est renommée NOM-modifie-AAAAMMJJ-HHMMSS.md."
                    " Signale les morceaux de noms restés dans le texte : ils sont à relire."
                    " Local, journalisé. Tant que la table existe, le résultat reste une donnée"
                    " personnelle (pseudonymisation).")
    c.set_defaults(func=cmd_anonymiser)

    c = sous.add_parser(
        "desanonymiser", help="réinjecter les vrais noms : produit rapport-final.md",
        description="Remplace les étiquettes par les valeurs de correspondance.md et écrit"
                    " rapport-final.md dans le dossier de l'enquête. Une version modifiée à la main"
                    " n'est pas écrasée : elle est renommée"
                    " rapport-final-modifie-AAAAMMJJ-HHMMSS.md."
                    " Sur le rapport anonymisé inchangé, redonne exactement le rapport d'origine ;"
                    " sur un fichier modifié, chaque étiquette reprend la valeur de la table. Les"
                    " étiquettes se reconnaissent à l'identique, casse comprise.")
    c.add_argument("fichier", nargs="?",
                   help="fichier à traiter (défaut : rapport-anonyme.md de l'enquête)")
    c.set_defaults(func=cmd_desanonymiser)

    c = sous.add_parser("cloturer", help="clore, archiver le rapport, planifier la purge")
    c.add_argument("--oui", action="store_true", help="ne pas demander confirmation")
    c.set_defaults(func=cmd_cloturer)

    c = sous.add_parser("aide", help="aide générale, ou d'une commande : aide <commande>")
    c.add_argument("sujet", nargs="*", help="commande (et sous-commande ou outil)")
    c.set_defaults(func=cmd_aide, parser=p)
    return p


def main(argv: list[str] | None = None) -> int:
    args = construire_parser().parse_args(argv)
    try:
        for nom in enq.purger_expirees():
            print(f"Conservation échue : « {nom} » purgée (rapport archivé conservé).",
                  file=sys.stderr)
        args.func(args)
    except (ErreurPilote, ErreurStockage, ErreurOutil, ErreurRequete, CleManquante, ErreurIA,
            ErreurAnonymisation, sqlite3.IntegrityError) as ex:
        print(f"erreur : {ex}", file=sys.stderr)
        return 1
    return 0
