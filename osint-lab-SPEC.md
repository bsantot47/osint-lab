# osint-lab — spécification

Outil en ligne de commande pour mener des enquêtes OSINT de façon organisée,
traçable et conforme au cadre légal français. Rôle central : un « pilote » qui
guide l'enquêteur (quoi faire ensuite), range les trouvailles, et génère un
rapport sourcé. L'outil sera développé sur le PC hôte, mais exécuté plus tard
dans une VM isolée.

## Principes directeurs (non négociables)

- **Script d'abord, IA seulement en dernier recours.** Toute tâche
  déterministe (ranger, journaliser, compter, relier par règles, générer) est
  du script pur. L'IA (spaCy) n'intervient que pour comprendre du texte libre.
- **Légalité par conception.** L'outil impose une finalité avant toute
  collecte, journalise chaque requête, limite le débit, applique une durée de
  conservation, et exclut du rapport tout élément marqué hors périmètre.
- **L'humain décide.** L'outil ne confirme jamais un fait et ne tranche jamais
  un faux positif : il prépare, l'enquêteur valide (règle des deux sources).
- **Pas de contournement.** Données publiques sans authentification
  uniquement ; API officielles préférées au scraping ; respect des CGU et du
  robots.txt.

## Langage et environnement

- Python 3.11+
- CLI via argparse ou click
- Stockage : SQLite (base de données) + fichiers Markdown (fiches, rapport)
- Pas de service cloud, tout en local
- Dépendances externes appelées comme sous-processus : sherlock, maigret,
  holehe, exiftool
- IA locale : spaCy avec le modèle français `fr_core_news_md`

## Arborescence du projet

```
osint-lab/
├── core/
│   ├── config.py        # lecture de la config locale (clés d'API, délais), hors dépôt
│   ├── requetes.py      # couche HTTP : délai entre appels, user agent, journal auto
│   ├── stockage.py      # SQLite : entités, faits, sources, preuves + expiration
│   └── rapport.py       # génération du rapport Markdown sourcé
├── pilote/
│   ├── enquete.py       # créer, charger, sauver une enquête
│   ├── etat.py          # ce qui est fait / à faire
│   ├── guide.py         # règles de pivot : quoi faire ensuite selon l'état
│   └── cli.py           # point d'entrée des commandes
├── outils/
│   ├── dorks.py         # génère des requêtes Google/Bing prêtes à cliquer
│   ├── pseudo.py        # lance sherlock/maigret, agrège, crée les fiches
│   ├── courriel.py      # lance holehe + HIBP (API)
│   ├── archives.py      # API CDX Wayback : toutes les captures d'une URL
│   ├── entreprises.py   # API annuaire-entreprises + Pappers
│   ├── domaine.py       # crt.sh + RDAP + Shodan : sources publiées, jamais la cible
│   └── exif.py          # exiftool par lots (non écrit : méthode suggérée, à faire à la main)
├── ia/
│   ├── entites.py       # spaCy : extraction noms/orgs/lieux d'une page
│   └── confiance.py     # score vrai/faux positif (règles d'abord, modèle ensuite)
├── enquetes/            # un dossier par enquête (créé à l'exécution)
└── osint_lab.py         # entrée principale
```

## Modèle de données (SQLite)

- **enquete** : id, nom, finalité, périmètre, date_création, durée_conservation
- **entite** : id, enquête_id, type (compte/email/numéro/document/entreprise/personne/
  domaine/image), valeur, statut (à_vérifier/confirmé/écarté/hors_périmètre),
  origine (depart/manuel/nom de l'outil), date
- **fait** : id, enquête_id, énoncé, entité_id, statut (mêmes valeurs), date
- **source** : id, fait_id, url, outil, date_consultation
- **preuve** : id, entité_id, chemin_fichier, sha256, date
- **lien** : id, enquête_id, entité_a, entité_b, nature, source (URL), date
- **journal** : id, enquête_id, date, outil, requête, url, résultat_résumé
  (conservé à la purge, pour la traçabilité)
- **tache** : id, enquête_id, méthode, entité_id, état (faite/ignorée) — ce que
  le guide ne doit plus suggérer

La confirmation porte sur le **fait**, pas sur l'entité. Un fait est
« confirmé » uniquement quand il a ≥ 2 sources indépendantes, et cette
confirmation est posée par l'utilisateur, jamais par l'outil. Indépendance =
domaines enregistrables différents : deux sources sur le même domaine comptent
pour une. L'outil signale qu'une confirmation est possible, l'utilisateur valide.

## Commandes (CLI)

- `nouvelle <nom>` — crée l'enquête, l'arborescence, ouvre le cadrage (finalité
  obligatoire)
- `statut` — affiche départ, fait, à faire, pivot suggéré
- `lancer <outil> [args]` — exécute un outil, journalise, range les sorties
- `ajouter` — saisie d'une trouvaille manuelle → fiche entité + preuve horodatée
- `entites` — affiche le graphe des entités et leurs liens
- `recouper` — construit le tableau faits / sources / statut
- `rapport` — compile le rapport Markdown sourcé (exclut le hors-périmètre)
- `cloturer` — applique la durée de conservation (archive rapport, planifie
  suppression des bruts)
- `confirmer <fait>` — confirmation par l'enquêteur (refusée sous 2 domaines)
- `statuer <entite|fait> <id> <statut>` — écarter, exclure, remettre à vérifier
- `marquer <méthode> [--entite id] [--ignorer]` — méthode faite (ex. manuelle)
- `ouvrir <nom>`, `liste` — enquête courante ; `-e NOM` cible une autre enquête
- `analyser <fichier> [--entite id]` — IA locale : noms, organisations, lieux,
  dates d'une page ; propositions seulement, rien n'est enregistré
- `confiance [--entite id] [--evaluer]` — indices vrai/faux positif par règles,
  et mesure des règles sur les décisions de l'enquêteur
- `journal [--outil X] [--dernieres N]` — lecture seule du journal
- `aide [commande [sous-commande|outil]]` — aide intégrée avec exemples

## Règles de pivot (module guide.py)

Machine à états simple. Selon ce qui existe dans la base, suggérer la prochaine
méthode. Exemples de règles :

- Entrée = nom seul → suggérer `dorks`, puis `entreprises`
- Un pseudo trouvé → suggérer `pseudo` (sherlock/maigret)
- Un email trouvé → suggérer `email` puis vérification de fuite
- Une entreprise trouvée → suggérer `entreprises` (mandats, liens)
- Un domaine trouvé → suggérer `domaine` et `archives`
- Une image collectée → suggérer `exif` et recherche inversée (manuelle)
- Toute méthode déjà faite n'est plus suggérée
- Rien n'est suggéré sur une entité écartée ou hors périmètre

Les règles sont déclaratives (une table de règles), pas codées en dur dans des
if imbriqués, pour être faciles à étendre.

## Rôle de l'IA (strictement délimité)

spaCy, et seulement là où il faut comprendre du texte libre :

- `entites.py` : extrait noms de personnes, organisations, lieux, dates d'une
  page collectée → propose des liens vers les fiches existantes
- `classer.py` : abandonné (doublon de `entites.py`), à ajouter seulement si
  le besoin apparaît à l'usage
- `confiance.py` : score vrai/faux positif. **Commencer par des règles**
  (même photo ? lien croisé vers une entité connue ?) et ne passer à un modèle
  (scikit-learn) que si les règles ne suffisent pas. Mesurer les deux.

L'IA ne doit jamais : marquer un fait confirmé, compléter une information
manquante, prioriser seule les pistes.

## Légalité intégrée (à faire respecter par le code)

- Refuser de lancer un outil si l'enquête n'a pas de finalité
- Limite de débit configurable dans `requetes.py`, pause entre appels
- Journal automatique de chaque requête (date, outil, requête, URL), en ajout
  seul (triggers : ni modification ni suppression)
- Chaque outil revérifie lui-même finalité, clôture et statut de l'entité
  (`controler_cible`), même appelé hors de la CLI
- Empreinte sha256 de chaque preuve (intégrité)
- Champ `statut = hors_périmètre` exclu du rapport
- `cloturer` applique la durée de conservation définie au cadrage
- Aucun téléchargement de base de fuite ; HIBP sert seulement à vérifier la
  présence
- `domaine` : aucun scan, aucune sonde, aucune requête vers les serveurs de la
  cible (ni HTTP, ni DNS, ni whois) ; uniquement des sources publiées par des
  tiers, imposé par une liste d'hôtes autorisés dans le client HTTP
- Les outils ne créent pas d'entité d'eux-mêmes à partir de tiers (dirigeants,
  titulaires, sous-domaines) : ils proposent les commandes `ajouter`
- Les bénéficiaires effectifs (accès restreint) ne sont jamais lus ni conservés
- Sous-processus lancés sans shell, valeur validée avant passage (refus de
  tout ce qui commence par « - ») ; jamais de --tor ni de --proxy ; maigret en
  --no-recursion --no-extracting, 20 connexions ; holehe toujours en
  --no-password-recovery ; durée max par programme
- `fuite` (HIBP) : truncateResponse, seuls les noms des fuites sont conservés
- Les données des enquêtes expirées sont purgées à chaque lancement de l'outil
  (pas de démon)
- Clés d'API dans une config locale hors dépôt (`config.local.toml`, ignorée
  par git), jamais en dur ; elles sont masquées dans le journal
- Sous-processus (sherlock, maigret, holehe) : l'appel global est journalisé,
  leurs propres options de délai/concurrence sont passées et documentées

## Ordre de développement recommandé

1. `core/` complet (requetes, stockage, rapport) + tests
2. `pilote/` (enquete, etat, guide, cli) → commandes nouvelle/statut/ajouter/rapport
   fonctionnelles même sans outils de collecte
3. `outils/dorks.py` et `outils/archives.py` (les plus simples, zéro risque)
4. `outils/entreprises.py`, `outils/domaine.py`
5. Branchement de sherlock/maigret/holehe via sous-processus
6. `ia/entites.py` (spaCy) puis `ia/confiance.py`

## Tests attendus

- Création d'enquête et refus si finalité absente
- Ajout d'entité, calcul du sha256, journalisation
- Règles de pivot : vérifier la suggestion pour chaque état d'entrée
- Génération de rapport : exclusion du hors-périmètre, présence des sources
- Expiration à la clôture
