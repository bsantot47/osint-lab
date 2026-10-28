# osint-lab — documentation technique

Ce document se suffit à lui-même : il décrit l'architecture, le modèle de
données, toutes les commandes, les règles de pivot, les garde-fous légaux et
leur emplacement dans le code, la façon d'ajouter un outil et le dépannage.
Pour l'installation et un premier exemple, voir [README.md](README.md). La
spécification d'origine est [osint-lab-SPEC.md](osint-lab-SPEC.md).

Sommaire :

1. [Principes et répartition script / IA](#1-principes-et-répartition-script--ia)
2. [Architecture](#2-architecture)
3. [Modèle de données](#3-modèle-de-données)
4. [Cycle de vie d'une enquête](#4-cycle-de-vie-dune-enquête)
5. [Référence des commandes](#5-référence-des-commandes)
6. [Outils de collecte](#6-outils-de-collecte)
7. [Règles de pivot (guide)](#7-règles-de-pivot-guide)
8. [IA locale](#8-ia-locale)
9. [Garde-fous légaux et emplacement dans le code](#9-garde-fous-légaux-et-emplacement-dans-le-code)
10. [Ajouter un outil](#10-ajouter-un-outil)
11. [Dépannage](#11-dépannage)
12. [Tests](#12-tests)
13. [Configuration et variables d'environnement](#13-configuration-et-variables-denvironnement)

---

## 1. Principes et répartition script / IA

| Principe | Traduction dans le code |
|---|---|
| Script d'abord, IA en dernier recours | Ranger, journaliser, compter, relier, suggérer, générer : script pur. spaCy ne sert qu'à reconnaître des noms dans du texte libre (`ia/entites.py`). Les indices de confiance sont des **règles** (`ia/confiance.py`). |
| Légalité par conception | Finalité obligatoire, journal en ajout seul, limite de débit, robots.txt, durée de conservation, purge automatique, hors-périmètre exclu du rapport. Voir [§9](#9-garde-fous-légaux-et-emplacement-dans-le-code). |
| L'humain décide | Aucun outil ne crée d'entité ni de fait : ils proposent des commandes `ajouter`. Seule la commande `confirmer` confirme un fait, et seulement avec ≥ 2 domaines indépendants. |
| Pas de contournement | GET uniquement, API officielles, robots.txt, aucun proxy/Tor, aucun scan de la cible, aucune fuite téléchargée. |

Vocabulaire :

- **Entité** (`E1`, `E2`…) : une chose observée — personne, entreprise,
  adresse e-mail, compte (pseudo), domaine, numéro, document, image.
- **Fait** (`F1`, `F2`…) : un énoncé vérifiable, rattaché ou non à une entité,
  appuyé par des **sources** (URL).
- **Preuve** : un fichier (capture, image) copié dans l'enquête avec son
  empreinte sha256.
- **Lien** : une relation entre deux entités (« gérant », « même avatar »).
- **Tâche** : une méthode faite ou ignorée sur une entité, pour que le guide
  ne la suggère plus.
- **Domaine indépendant** : domaine enregistrable d'une source
  (`fr.linkedin.com` → `linkedin.com`). Deux sources sur le même domaine
  comptent pour une.

---

## 2. Architecture

```
osint-lab/
├── osint_lab.py          point d'entrée : appelle pilote.cli.main
├── config.exemple.toml   modèle de configuration (à copier en config.local.toml)
├── core/                 socle, sans dépendance externe
│   ├── config.py         lecture de config.local.toml (réseau, clés d'API)
│   ├── stockage.py       SQLite : schéma, règles (finalité, confirmation, journal), purge
│   ├── requetes.py       client HTTP unique : finalité, robots.txt, débit, journal, redirections
│   └── rapport.py        rapport Markdown sourcé (exclut le hors-périmètre)
├── pilote/               orchestration et interface
│   ├── enquete.py        dossiers d'enquête, enquête courante, preuves, fiches, clôture, purge
│   ├── etat.py           calcul de l'état : départ, compteurs, tâches, confirmables, suggestions
│   ├── guide.py          règles de pivot déclaratives (METHODES, REGLES)
│   └── cli.py            toutes les commandes (argparse), aide
├── outils/               outils de collecte, un module par outil
│   ├── __init__.py       registre OUTILS (nom → outil)
│   ├── commun.py         contrat des outils, garde-fou controler_cible, sous-processus
│   ├── dorks.py          liens Google/Bing (aucune requête envoyée)
│   ├── archives.py       Wayback Machine (API CDX)
│   ├── entreprises.py    recherche-entreprises.api.gouv.fr + Pappers
│   ├── domaine.py        crt.sh + RDAP + Shodan, client restreint par liste blanche
│   ├── pseudo.py         sherlock + maigret (sous-processus)
│   └── courriel.py       holehe (sous-processus) et fuite (HIBP)
├── ia/                   IA locale, aucun appel réseau
│   ├── entites.py        noms, organisations, lieux (spaCy), dates (regex), rapprochement
│   └── confiance.py      indices vrai/faux positif par règles, et leur mesure
├── tests/                pytest, réseau et programmes simulés
└── enquetes/             données (créé à l'exécution, ignoré par git)
```

### Flux d'une commande

```
osint_lab.py → pilote/cli.main
   1. purge des enquêtes dont la conservation est échue (pilote/enquete.purger_expirees)
   2. ouverture de l'enquête visée (-e NOM, sinon enquête courante)
   3. la commande appelle :
        - pilote/enquete (preuves, fiches, rapport, clôture)
        - core/stockage  (écritures, toutes contrôlées : finalité, clôture, confirmation)
        - outils/<outil>.lancer  → core/requetes.ClientHTTP (réseau)
                                 → outils/commun.Programme (sous-processus)
        - ia/*            (local)
   4. les erreurs prévues s'affichent « erreur : … », code retour 1
```

### Dépendances entre couches

`core` ne dépend de rien d'autre. `outils` et `ia` dépendent de `core`.
`pilote` dépend de tout. Aucun module n'ouvre de connexion réseau en dehors de
`core/requetes.py`, et aucun ne lance de programme en dehors de
`outils/commun.Programme` (vérifié par `tests/test_garde_fous.py`).

### Stockage sur disque

Une enquête = un dossier `enquetes/<nom>/` avec sa propre base SQLite :

```
enquetes/<nom>/
├── enquete.db    base SQLite (source de vérité, journal compris)
├── cadrage.md    finalité, périmètre, conservation, points de départ (écrit à la création)
├── fiches/       une fiche Markdown par entité, régénérée depuis la base après chaque modification
├── preuves/      copies horodatées des fichiers de preuve (AAAAMMJJ-HHMMSS-E<n>-<nom>)
├── bruts/        sorties brutes des outils (<outil>-E<n>-AAAAMMJJ-HHMMSS.*)
└── rapport/      rapports ; rapport-final-AAAAMMJJ.md à la clôture
enquetes/.courante   nom de l'enquête courante
```

La base est en mode WAL : des fichiers `enquete.db-wal` et `enquete.db-shm`
peuvent apparaître à côté, c'est normal. Les fiches sont une **vue** : les
modifier à la main ne sert à rien, elles sont réécrites.

---

## 3. Modèle de données

Schéma complet : constante `SCHEMA` de `core/stockage.py`. Dates en ISO 8601
(heure locale). Statuts stockés en ASCII.

| Table | Colonnes | Remarques |
|---|---|---|
| `enquete` | id, nom (unique), finalite, perimetre, date_creation, duree_conservation_jours, date_cloture, date_expiration, date_purge | `CHECK (trim(finalite) <> '')`, durée > 0 |
| `entite` | id, enquete_id, type, valeur, statut, origine, date | type ∈ compte, email, numero, document, entreprise, personne, domaine, image ; statut ∈ a_verifier, ecarte, hors_perimetre (**jamais** confirme) ; origine = depart, manuel ou nom d'outil ; unique (enquête, type, valeur) |
| `fait` | id, enquete_id, enonce, entite_id (facultatif), statut, date | statut ∈ a_verifier, confirme, ecarte, hors_perimetre |
| `source` | id, fait_id, url, outil, date_consultation | rattachée à un fait |
| `preuve` | id, entite_id, chemin_fichier, sha256, date | sha256 calculé sur la copie dans `preuves/` |
| `lien` | id, enquete_id, entite_a, entite_b, nature, source (URL), date | a ≠ b |
| `journal` | id, enquete_id, date, outil, requete, url, resultat_resume | **ajout seul**, conservé à la purge |
| `tache` | id, enquete_id, methode, entite_id, etat | etat ∈ faite, ignoree |

Clés étrangères en cascade : supprimer une entité supprime ses faits, sources,
preuves et liens (utilisé uniquement par la purge).

### Triggers (règles garanties par la base, même en SQL direct)

| Trigger | Effet |
|---|---|
| `fait_confirme_a_la_creation` | un fait ne peut pas être inséré déjà confirmé |
| `fait_confirme_deux_domaines` | passage à `confirme` refusé si les sources du fait couvrent moins de 2 domaines enregistrables distincts |
| `journal_sans_modification` | aucune ligne du journal ne peut être modifiée |
| `journal_sans_suppression` | aucune ligne du journal ne peut être supprimée |

`fait_confirme_deux_domaines` utilise la fonction SQL `domaine()`, qui est la
fonction Python `domaine_enregistrable` enregistrée à chaque connexion. Hors de
l'outil (par exemple avec le client `sqlite3`), elle n'existe pas : toute
tentative de confirmation échoue.

### Indépendance des sources

`domaine_enregistrable(url)` (`core/stockage.py`) garde les deux derniers
niveaux de l'hôte (`www.pappers.fr` → `pappers.fr`), sauf sous les suffixes
de `SUFFIXES_MULTIPLES` (`gouv.fr`, `co.uk`, `github.io`…) où il en garde
trois (`alice.github.io` ≠ `bob.github.io`). C'est une approximation
volontairement courte de la Public Suffix List : si un cas réel est mal jugé,
ajouter le suffixe à `SUFFIXES_MULTIPLES`.

Exemple : `annuaire-entreprises.data.gouv.fr` → `data.gouv.fr` ;
`www.pappers.fr` → `pappers.fr` : deux domaines, confirmation possible.

---

## 4. Cycle de vie d'une enquête

```
 nouvelle ──► en cours ──────────────► close ─────────────────► purgée
 (finalité)   collecte, ajout,         cloturer :                à l'échéance, au
              statuer, confirmer,      - plus aucune écriture    lancement suivant :
              rapport                  - rapport final archivé   - fiches/, preuves/, bruts/ supprimés
                                       - expiration = clôture    - entités, faits, sources, liens,
                                         + durée de conservation   preuves effacés de la base
                                                                 - restent : cadrage.md, rapport/,
                                                                   fiche d'enquête, journal complet
```

- La durée de conservation (`--conservation JOURS`, 90 par défaut) court **à
  partir de la clôture**.
- La purge n'a pas de démon : elle est faite au début de chaque commande
  (`main` → `enq.purger_expirees`). Une enquête purgée reste listée (`liste`
  affiche « purgée ») avec son rapport archivé.
- Une enquête close ou purgée refuse toute collecte et toute écriture
  (`exiger_collecte_possible`), mais `statut`, `entites`, `recouper`, `journal`
  restent consultables.

---

## 5. Référence des commandes

Forme générale : `python osint_lab.py [-e NOM] COMMANDE [arguments]`.
`-e NOM` vise une autre enquête que l'enquête courante, sans la changer.
Aide intégrée : `aide`, `aide COMMANDE`, `aide ajouter fait`, `aide lancer OUTIL`.

### `nouvelle NOM` — créer une enquête

| Option | Rôle |
|---|---|
| `NOM` | minuscules, chiffres, `-` et `_`, 64 caractères max ; nom du dossier |
| `--finalite TEXTE` | obligatoire ; demandée si absente (terminal interactif) ; vide → refus |
| `--perimetre TEXTE` | facultatif ; demandé si absent |
| `--conservation JOURS` | durée de conservation après clôture (défaut 90) |
| `--depart TYPE:VALEUR` | point de départ, répétable |

```bash
python osint_lab.py nouvelle dupont --finalite "Vérification préalable d'un fournisseur" \
  --perimetre "activité professionnelle" --conservation 30 \
  --depart "personne:Jean Dupont" --depart "email:jdupont@exemple.fr"
```

Crée le dossier, écrit `cadrage.md`, en fait l'enquête courante, affiche le statut.

### `ouvrir NOM` / `liste`

```bash
python osint_lab.py liste          # * marque l'enquête courante ; état : en cours, close, purgée
python osint_lab.py ouvrir dupont  # devient l'enquête courante, affiche son statut
```

### `statut` — où en est l'enquête

Affiche finalité, périmètre, points de départ, compteurs par statut, tâches
faites, faits confirmables (« à valider → confirmer N »), suggestions du guide
et pivot suggéré.

```bash
python osint_lab.py statut
python osint_lab.py -e autre statut
```

### `ajouter` — saisir une trouvaille

| Sous-commande | Syntaxe | Effet |
|---|---|---|
| `entite` | `ajouter entite TYPE VALEUR [--preuve FICHIER]…` | crée E<n> (origine « manuel ») ; chaque preuve est copiée et hachée |
| `fait` | `ajouter fait "ÉNONCÉ" [--entite N] [--source URL]… [--outil NOM]` | crée F<n> à vérifier, avec ses sources |
| `source` | `ajouter source F URL [--outil NOM]` | ajoute une source ; signale si la confirmation devient possible |
| `lien` | `ajouter lien A B "NATURE" [--source URL]` | relie deux entités |
| `preuve` | `ajouter preuve E FICHIER` | copie horodatée dans `preuves/`, sha256 enregistré |

```bash
python osint_lab.py ajouter entite compte jdupont --preuve capture.png
python osint_lab.py ajouter fait "Gérant de Dupont Conseil" --entite 3 \
  --source https://annuaire-entreprises.data.gouv.fr/entreprise/123456789 --outil entreprises
python osint_lab.py ajouter source 1 https://www.pappers.fr/entreprise/123456789
python osint_lab.py ajouter lien 1 3 "dirigeant"
python osint_lab.py ajouter preuve 3 ~/Téléchargements/profil.png
```

`--outil` indique quel outil a trouvé la source (défaut `manuel`) ; il
apparaît dans le rapport. Les outils de collecte affichent ces commandes déjà
remplies : il suffit de copier celles qu'on retient.

### `confirmer F` — confirmer un fait

```bash
python osint_lab.py confirmer 1
```

Seul chemin vers le statut `confirme`. Refusé si les sources du fait couvrent
moins de 2 domaines indépendants (« confirmation refusée : moins de 2 domaines
indépendants »). L'outil signale quand c'est possible, il ne le fait jamais.

### `statuer entite|fait ID STATUT` — écarter, exclure, rétablir

| Statut | Sens | Conséquence |
|---|---|---|
| `ecarte` | faux positif, piste abandonnée | plus de collecte ni de suggestion ; compté (sans détail) dans le rapport |
| `hors_perimetre` | hors de la finalité (tiers, vie privée sans rapport…) | plus de collecte ni de suggestion ; **absent du rapport**, sans trace |
| `a_verifier` | remettre en piste active | — |

`confirme` n'est pas accepté (ni pour un fait, ni pour une entité).

```bash
python osint_lab.py statuer entite 4 ecarte
python osint_lab.py statuer fait 2 hors_perimetre
python osint_lab.py statuer entite 4 a_verifier
```

### `marquer METHODE [--entite N] [--ignorer]`

Note une méthode faite à la main (ex. recherche inversée) ou volontairement
ignorée : le guide ne la suggère plus pour cette entité. `lancer` marque
automatiquement la méthode faite après un succès.

```bash
python osint_lab.py marquer recherche_inversee --entite 5
python osint_lab.py marquer fuite --entite 2 --ignorer
```

### `lancer OUTIL --entite N [options]` — collecter

```bash
python osint_lab.py lancer dorks --entite 1 --contexte Lyon
python osint_lab.py lancer entreprises --entite 3 --siren 123456789
python osint_lab.py lancer domaine --entite 6 --sources crtsh,rdap
python osint_lab.py aide lancer pseudo      # options d'un outil
```

Avant l'appel : finalité, enquête non close, entité de l'enquête, ni écartée
ni hors périmètre, type accepté par l'outil. Détail des outils au [§6](#6-outils-de-collecte).

### `analyser FICHIER [--entite N] [--modele NOM]` — IA locale

```bash
python osint_lab.py analyser enquetes/dupont/bruts/page.html --entite 3
```

Extrait personnes, organisations, lieux (spaCy) et dates (script) d'une page
enregistrée, les rapproche des fiches actives et propose des commandes
`ajouter entite` / `ajouter lien`. **N'enregistre rien dans l'enquête** ; les
propositions sont écrites dans `bruts/analyse-<page>-<date>.txt` et l'analyse
est journalisée. Voir [§8](#8-ia-locale).

### `confiance [--entite N] [--evaluer] [--seuil S]`

```bash
python osint_lab.py confiance                 # indices pour chaque entité candidate
python osint_lab.py confiance --entite 4
python osint_lab.py confiance --evaluer --seuil 3
```

Indices de rattachement des entités « à vérifier » aux entités de référence,
avec leurs raisons. Ne change aucun statut. `--evaluer` mesure les règles sur
les décisions déjà prises. Voir [§8](#8-ia-locale).

### `entites`, `recouper`, `journal` — consulter

```bash
python osint_lab.py entites                   # entités, statut, liens (→ sortants, ← entrants)
python osint_lab.py recouper                  # faits, statut, nb de domaines, confirmable, sources
python osint_lab.py journal                   # toutes les requêtes et actions tracées
python osint_lab.py journal --outil domaine   # un outil (inclut domaine/robots)
python osint_lab.py journal --dernieres 20
```

### `rapport` — rapport sourcé

```bash
python osint_lab.py rapport     # → enquetes/<nom>/rapport/rapport-AAAAMMJJ-HHMMSS.md
```

Contenu : finalité, périmètre, dates, conservation ; faits confirmés puis à
vérifier, chacun avec ses sources et le nombre de domaines ; entités par type
avec leurs preuves (nom de fichier, date, sha256) ; liens ; méthodologie
(nombre de requêtes journalisées, période, outils utilisés, nombre de pistes
écartées). Exclus : tout ce qui est hors périmètre (entités, leurs faits et
liens), et le détail des éléments écartés. L'empreinte sha256 du rapport est
journalisée.

### `cloturer [--oui]`

```bash
python osint_lab.py cloturer          # demande de taper « oui »
python osint_lab.py cloturer --oui    # sans question (scripts)
```

Fige l'enquête, archive `rapport-final-AAAAMMJJ.md`, fixe la date
d'expiration (clôture + conservation) et l'affiche. Irréversible.

### `aide [COMMANDE [SOUS-COMMANDE|OUTIL]]`

```bash
python osint_lab.py aide                  # liste des commandes, parcours type, outils
python osint_lab.py aide ajouter fait     # options et exemples
python osint_lab.py aide lancer fuite     # options d'un outil
```

---

## 6. Outils de collecte

Tous suivent le même contrat ([§10](#10-ajouter-un-outil)), écrivent dans
`bruts/` et terminent par des commandes `ajouter` à copier. Aucun ne crée
d'entité ni de fait.

### `dorks` — personne, entreprise, email, compte, domaine, numero

Fabrique des requêtes Google et Bing (liens cliquables) à partir de modèles
déclaratifs (`MODELES` dans `outils/dorks.py`). **Aucune requête n'est
envoyée** : interroger un moteur par script enfreint ses CGU.

| Option | Rôle |
|---|---|
| `--contexte "TERMES"` | ajouté à chaque requête pour écarter les homonymes (ville, métier) |

Volontairement absents : recherches de tableurs/CSV/SQL et de sites de
collage (pastebin…), qui mènent surtout à des fuites. Pour une adresse chez un
webmail grand public (`WEBMAILS`), pas de recherche « autres adresses du
domaine ». Sortie : `bruts/dorks-E<n>-<date>.md`.

### `entreprises` — personne, entreprise

- API publique `recherche-entreprises.api.gouv.fr` (sans clé), toujours.
- Pappers (`api.pappers.fr`, clé `pappers`) seulement pour un SIREN connu.

Pour une personne : entreprises dont un dirigeant porte ce nom (tous les mots
du nom présents, sans accents ni casse), avec année de naissance pour
départager les homonymes. Pour une entreprise : candidates par nom, ou fiche
complète par SIREN (valeur de l'entité si c'est un SIREN, ou `--siren`).

| Option | Rôle |
|---|---|
| `--siren 123456789` | fiche d'une entreprise précise (et Pappers si clé) |
| `--limite N` | nombre de résultats, 1 à 25 (défaut 10) |
| `--sans-pappers` | ne pas interroger Pappers |

Les **bénéficiaires effectifs** (accès restreint par la loi) sont retirés de
toute donnée avant écriture (`purger_champs_interdits`).

### `domaine` — domaine

Lecture de sources publiées par des tiers, **jamais la cible** : ni HTTP, ni
DNS, ni whois vers ses serveurs.

| Source | Donnée | Clé |
|---|---|---|
| `crtsh` | noms d'hôtes vus dans les certificats (Certificate Transparency), dates | non (robots.txt vérifié) |
| `rdap` | données d'enregistrement publiées par le registre (successeur de whois) ; serveur trouvé dans l'amorçage IANA | non |
| `shodan` | enregistrements DNS déjà collectés par Shodan | `shodan` |

| Option | Rôle |
|---|---|
| `--sources crtsh,rdap,shodan` | sources à interroger (défaut : toutes ; shodan sauté sans clé) |
| `--actifs-seulement` | crt.sh : ignorer les certificats expirés |

Le client `ClientPassif` n'accepte que `crt.sh`, `data.iana.org`,
`api.shodan.io` et le serveur RDAP lu dans l'amorçage, et refuse tout hôte du
domaine visé, même si on l'y autorise explicitement. Censys n'est pas branché.

### `archives` — domaine

API CDX de la Wayback Machine (`web.archive.org`), un appel par exécution.
Par défaut, les captures identiques sont regroupées : une ligne = une version
différente de la page. Chaque lien de capture peut servir de source.

| Option | Rôle |
|---|---|
| `--url URL` | page précise (doit appartenir au domaine de l'entité) |
| `--domaine-entier` | toutes les URL du domaine et des sous-domaines |
| `--depuis AAAA` / `--jusqua AAAA` | bornes |
| `--toutes` | ne pas regrouper les captures identiques |
| `--limite N` | 1 à 10 000 (défaut 500) |

### `pseudo` — compte

Lance sherlock et/ou maigret en sous-processus. Les sites où le pseudo existe
sont agrégés par URL ; pour chacun, une commande `ajouter fait` avec le profil
comme source est proposée.

| Option | Rôle |
|---|---|
| `--programmes sherlock,maigret` | défaut : les deux ; un programme absent est ignoré |
| `--timeout S` | délai max par site (défaut 15) |
| `--connexions N` | maigret : requêtes simultanées (défaut 20, au lieu de 100) |
| `--sites N` | maigret : nombre de sites les plus fréquentés testés (défaut 500) |
| `--duree-max S` | durée max de chaque programme (défaut 900) |

Toujours passé à maigret : `--no-recursion --no-extracting` (pas de recherche
en cascade, pas d'extraction de données des profils). Jamais de `--tor` ni de
`--proxy`.

### `courriel` — email

holehe en sous-processus : sites où l'adresse est associée à un compte.
Toujours lancé avec `--only-used --no-password-recovery` (aucune procédure de
récupération de mot de passe déclenchée). **Outil le plus sensible au regard
des CGU** : à réserver aux cas où la finalité le justifie.

| Option | Rôle |
|---|---|
| `--timeout S` | délai max par site (défaut 15) |
| `--duree-max S` | durée max de holehe (défaut 600) |

### `fuite` — email

HIBP v3 (clé `hibp`) : vérifie seulement la **présence** de l'adresse dans des
fuites connues (`truncateResponse=true` : seuls les noms des fuites
reviennent). Aucune donnée de fuite n'est téléchargée ni conservée.

| Option | Rôle |
|---|---|
| `--non-verifiees` | inclure les fuites que HIBP n'a pas pu authentifier |

### Méthodes sans outil

- `recherche_inversee` (images) : manuelle par nature. La faire soi-même, puis
  `marquer recherche_inversee --entite N`.
- `exif` (images) : suggérée par le guide, outil `outils/exif.py` prévu par la
  spec mais **non écrit**. `lancer exif` répond « inconnu ou pas encore
  disponible ». En attendant : `exiftool fichier.jpg` à la main, puis
  `marquer exif --entite N`. Pour l'écrire, suivre le [§10](#10-ajouter-un-outil)
  (modèle : `pseudo.py`, sous-processus).

---

## 7. Règles de pivot (guide)

Fichier : `pilote/guide.py`. Tout est déclaratif.

`METHODES` : nom → `Methode(nom, libelle, manuelle)`. Une méthode non manuelle
se lance par `lancer NOM --entite N` ; une manuelle par « (manuel) puis :
marquer NOM --entite N ».

`REGLES` : pour chaque type d'entité, les méthodes à suggérer, dans l'ordre.

| Type d'entité | Méthodes suggérées | Raison affichée |
|---|---|---|
| personne | dorks, entreprises | un nom seul : recherche ouverte, puis mandats |
| compte | pseudo | un pseudo : présence sur d'autres plateformes |
| email | courriel, fuite | une adresse : services associés, puis fuites |
| entreprise | entreprises | une entreprise : mandats et dirigeants |
| domaine | domaine, archives | un domaine : infrastructure, puis historique |
| image | exif, recherche_inversee (manuelle) | une image : métadonnées, puis origine |

Fonctionnement (`suggerer`) :

1. seules les entités actives (`a_verifier`) sont considérées : **rien n'est
   suggéré sur une entité écartée ou hors périmètre** ;
2. les points de départ passent en premier, puis l'ordre d'ajout ;
3. une méthode faite ou ignorée sur une entité (table `tache`) n'est plus
   suggérée pour elle ;
4. aucune autre priorisation : le **pivot suggéré** affiché par `statut` est
   simplement la première suggestion. L'enquêteur choisit.

`pilote/etat.py` (`calculer`) assemble l'état affiché par `statut` : départ,
compteurs, tâches, faits confirmables, suggestions.

Ajouter une règle : une ligne dans `REGLES` (et une `Methode` si nouvelle).
Aucun autre code à toucher. Le test `tests/test_guide.py` vérifie la
suggestion pour chaque type.

---

## 8. IA locale

Aucun module de `ia/` n'importe de bibliothèque réseau ni `core/requetes`
(vérifié par test). `spacy.load` charge un modèle installé, il ne télécharge
jamais rien. L'IA ne marque jamais un fait confirmé, ne complète aucune
information, ne trie pas les pistes.

### `ia/entites.py` (commande `analyser`)

| Étape | Qui | Détail |
|---|---|---|
| lecture | script | `.html`/`.htm` ou contenu commençant par `<html` → texte (scripts, styles, svg retirés) ; sinon texte brut |
| personnes, organisations, lieux | spaCy (`fr_core_news_md`) | étiquettes PER, ORG, LOC ; MISC ignoré ; noms de moins de 3 caractères utiles ignorés |
| dates | script (regex `DATES`) | `12/03/2015`, `2015-03-12`, `1er juin 2024`, `mars 2021` (le modèle français n'a pas d'étiquette date) |
| regroupement | script | par (type, nom normalisé), avec nombre d'occurrences et extrait de contexte |
| rapprochement | script | une mention de personne/organisation correspond à une fiche active du même type (personne / entreprise) si tous les mots de la fiche figurent dans la mention, sans accents ni casse |
| propositions | script | `ajouter lien SOURCE FICHE mentionné` si `--entite` est donnée, sinon `ajouter entite …` pour les mentions sans fiche |

Texte limité à 1 000 000 caractères (`TAILLE_MAX`, signalé s'il est tronqué).

### `ia/confiance.py` (commande `confiance`)

Question : cette entité candidate se rattache-t-elle vraiment à l'enquête, ou
est-ce un homonyme ?

- **Références** : points de départ actifs et entités ayant un fait confirmé.
- **Candidates** : les autres entités `a_verifier`. Les écartées et hors
  périmètre ne sont jamais évaluées.

Règles (`REGLES`, poids additionnés) :

| Règle | Poids | S'applique quand |
|---|---|---|
| `photo_identique` | +3 | un fichier de preuve a le même sha256 qu'une preuve d'une référence |
| `lien_avec_reference` | +2 | un lien relie la candidate à une référence |
| `identifiant_partage` | +1 | même identifiant (≥ 4 caractères, `.`/`_`/`-` ignorés) : pseudo, partie locale d'une adresse, nom du domaine, nom accolé (`jeandupont`, `jdupont`, `dupontjean`) |
| `sources_independantes` | +1 | ses faits **à vérifier** ont des sources sur ≥ 2 domaines |
| `photo_d_une_ecartee` | −2 | même fichier de preuve qu'une entité écartée |

Niveaux : ≥ 4 « indices forts », 2–3 « moyens », 1 « faible », 0 « aucun
indice », < 0 « indices contraires ». Affichage dans l'ordre des entités,
**jamais trié par score**.

`--evaluer` confronte les règles aux décisions de l'enquêteur : entité écartée
= faux positif avéré ; entité (hors départ) avec un fait confirmé = vrai
positif avéré. Pour chacune, l'indice est recalculé sans qu'elle serve de
référence à elle-même ; on compte vrais/faux positifs/négatifs au seuil
choisi (défaut 2), avec précision et rappel. En dessous de 10 décisions, la
mesure est signalée comme insuffisante.

La règle `sources_independantes` ignore volontairement les faits confirmés :
un fait confirmé a toujours ≥ 2 domaines, la compter ferait « prédire » à la
règle la décision mesurée (test `test_mesure_sans_fuite_de_la_decision`).

**Quand passer à un modèle (scikit-learn)** : seulement si `--evaluer`, sur
des enquêtes réelles avec assez de décisions, montre que les règles ne
suffisent pas. Le modèle serait alors mesuré exactement de la même façon
(même fonction `evaluer`, avec ses prédictions à la place de `indice`), pour
comparer. Ajuster d'abord les poids ou ajouter une règle dans `REGLES`.

---

## 9. Garde-fous légaux et emplacement dans le code

Chaque garde-fou est appliqué **au plus bas niveau possible**, pour qu'aucune
couche supérieure ne puisse l'oublier, et vérifié par un test.
`tests/test_garde_fous.py` regroupe les vérifications transverses.

### Refus sans finalité

| Où | Quoi |
|---|---|
| `core/stockage.py` : `CHECK (trim(finalite) <> '')` | la base refuse une finalité vide, même en SQL direct |
| `Stockage.creer_enquete` | lève `FinaliteManquante` |
| `Stockage.exiger_collecte_possible` | appelée avant toute écriture et toute collecte : finalité présente, enquête non close |
| `ClientHTTP.__init__` et `ClientHTTP.get` | l'appellent avant chaque requête |
| `Programme.executer` | l'appelle avant chaque sous-processus |
| `outils/commun.controler_cible` | l'appelle en tête de chaque outil |

Tests : `test_refus_sans_finalite`, `tests/test_stockage.py`.

### Journal de chaque requête

| Où | Quoi |
|---|---|
| `ClientHTTP._appel` | chaque requête HTTP (et chaque saut de redirection, chaque refus) : date, outil, requête en clair, URL, résultat (code, taille, mention « robots.txt non vérifié (API officielle) ») |
| `ClientHTTP._robots_autorise` | chaque lecture de robots.txt (outil `<outil>/robots`) |
| `masquer_url` / `PARAMETRES_SENSIBLES` | les clés d'API passées en paramètre sont remplacées par `***` ; les clés passées en en-tête ne sont jamais journalisées |
| `Programme.executer` | ligne de commande complète, code retour, durée, ou interruption |
| `dorks.lancer` | journalise la génération (aucune requête) |
| `cmd_analyser` | journalise chaque analyse locale |
| `ecrire_rapport`, `Stockage.cloturer`, `purger_enquete` | rapport (sha256), clôture, purge |
| triggers `journal_sans_modification`, `journal_sans_suppression` | journal en ajout seul |
| `purger_enquete` | ne touche pas au journal |

Lecture : commande `journal`. Tests : `test_journal_en_ajout_seul`,
`test_journal_survit_a_la_purge`, `tests/test_requetes.py`.

### Limite de débit et robots.txt

| Où | Quoi |
|---|---|
| `LimiteurDebit`, `LIMITEUR_PARTAGE` (`core/requetes.py`) | délai minimal (`delai_s`, 2 s par défaut) entre deux appels au même hôte, partagé par tous les outils du processus |
| `ClientHTTP._robots_autorise` | robots.txt selon la RFC 9309 : absent (4xx) = permis ; 401/403, 5xx ou réseau = tout interdit |
| `ClientHTTP.get` | redirections suivies à la main (5 max) ; robots.txt revérifié dès qu'un saut change de domaine, même pour une API officielle ; en-têtes (clés) non transmis à un autre hôte |
| `verifier_robots=False` | réservé aux API officielles documentées (CDX, recherche-entreprises, Pappers, RDAP, Shodan, HIBP) ; mentionné au journal |
| `transport_urllib` | GET seulement, taille de réponse bornée (`taille_max_octets`), délai (`timeout_s`) |

### Exclusion du hors-périmètre

| Où | Quoi |
|---|---|
| `core/rapport.generer_rapport` | n'inclut que les statuts `a_verifier`/`confirme` ; les faits et liens d'une entité hors périmètre sont aussi exclus ; aucune trace du hors-périmètre ; les écartés ne sont que comptés |
| `pilote/guide.suggerer` | aucune suggestion sur une entité écartée ou hors périmètre |
| `cmd_lancer` et `outils/commun.controler_cible` | aucune collecte sur une entité écartée ou hors périmètre (double contrôle : la CLI, puis l'outil lui-même, qui relit l'entité en base) |
| `cmd_analyser` | ne rapproche qu'avec les entités actives ; refuse `--entite` inactive |
| `ia/confiance.candidats` | n'évalue jamais une entité inactive |

Tests : `test_hors_perimetre_absent_du_rapport`,
`test_outil_refuse_entite_inactive` (chaque outil × chaque statut inactif).

### Confirmation verrouillée

| Où | Quoi |
|---|---|
| `Stockage.confirmer_fait` | seule méthode qui pose `confirme` ; lève `ConfirmationImpossible` sous 2 domaines |
| `Stockage.changer_statut_fait` | refuse `confirme` |
| `Stockage.changer_statut_entite` + `CHECK` sur `entite.statut` | une entité n'est jamais confirmée |
| triggers `fait_confirme_a_la_creation`, `fait_confirme_deux_domaines` | la règle tient même en SQL direct |
| `cmd_statuer` | choix limités à `a_verifier`, `ecarte`, `hors_perimetre` |
| `cmd_confirmer` | seul appel à `confirmer_fait` dans tout le code (vérifié par test) |
| outils et IA | ne créent ni entité ni fait ; `confiance` ne change aucun statut |

Tests : `test_confirmation_verrouillee`,
`test_seuls_stockage_et_cli_touchent_a_la_confirmation`,
`test_aucun_statut_modifie` (confiance).

### Pas de scan actif

| Où | Quoi |
|---|---|
| `core/requetes.py` | seul module autorisé à importer une bibliothèque réseau ; GET uniquement |
| `outils/domaine.ClientPassif._envoyer` | liste blanche d'hôtes ; tout hôte du domaine visé refusé avant l'envoi (et le refus est journalisé) |
| `outils/commun.Programme` | seul point de lancement de programmes, jamais via un shell |
| `verifier_pseudo`, `verifier_email` | valeur validée avant passage en argument (refus de ce qui commence par `-`, des espaces, etc.) : pas d'injection d'options |
| `args_maigret`, `args_holehe` | arguments fixés : pas de proxy/Tor, pas de récursion, pas de récupération de mot de passe |

Tests : `test_reseau_uniquement_par_core_requetes`,
`test_sous_processus_uniquement_par_programme`,
`test_domaine_ne_contacte_jamais_la_cible`, `test_aucun_outil_de_scan`,
`tests/test_pseudo.py`, `tests/test_courriel.py`.

### IA locale

| Où | Quoi |
|---|---|
| `ia/*.py` | aucun import réseau, pas de `core/requetes` |
| `ia/entites.charger_modele` | `spacy.load` d'un modèle installé ; message d'installation sinon |
| `cmd_analyser` | n'écrit rien dans la base hors journal |

Tests : `test_ia_sans_reseau_ni_core_requetes`,
`test_aucun_module_reseau_importe_dans_ia`, `test_analyse_reseau_coupe`
(connexions interdites pendant l'analyse).

### Pas de téléchargement de fuite

| Où | Quoi |
|---|---|
| `courriel.lancer_fuite` | `truncateResponse=true`, seuls les noms des fuites sont conservés ; pas d'API « paste » |
| `dorks.MODELES` | aucune recherche de tableurs/CSV/SQL ni de sites de collage |

Tests : `test_fuite_ne_recupere_que_la_presence`, `test_dorks_sans_fuites`.

### Autres

| Garde-fou | Où |
|---|---|
| Intégrité des preuves (sha256 sur la copie) | `EnqueteOuverte.ajouter_preuve`, `Stockage.ajouter_preuve`, `verifier_preuve` |
| Bénéficiaires effectifs jamais conservés | `entreprises.purger_champs_interdits` |
| Durée de conservation et purge | `Stockage.cloturer`, `purger_enquete`, `pilote/enquete.purger_expirees`, appelée par `main` |
| Clés hors dépôt | `core/config.py`, `.gitignore` (`config.local.toml`) |
| Aucune entité créée par un outil | contrat de `outils/commun.py` ; les outils renvoient des commandes `ajouter` |

---

## 10. Ajouter un outil

### Contrat

Un module `outils/<nom>.py` expose :

```python
NOM = "monoutil"                       # nom tapé après `lancer`, et nom de la méthode du guide
TYPES = ("domaine",)                   # types d'entité acceptés

def options(parser) -> None:           # options propres (argparse) ; --entite est ajouté par la CLI
    parser.add_argument("--limite", type=int, default=100, help="…")

def lancer(enquete, entite, opts, maintenant=None, transport=None, limiteur=None) -> Resultat:
    ...
```

- `enquete` : `pilote.enquete.EnqueteOuverte` (`.stockage`, `.id`, `.chemin("bruts")`).
- `entite` : ligne de la table `entite` (au moins `["id"]`).
- `opts` : `argparse.Namespace` des options.
- Paramètres après `opts` : injections pour les tests (horloge, transport
  HTTP, limiteur, programme). La CLI ne les passe jamais.
- Retour : `Resultat(lignes=[…], fichiers=[…])` — lignes affichées, fichiers écrits.

Un module qui expose deux outils (comme `courriel` et `fuite`) déclare le
second avec `Outil(NOM, TYPES, options, lancer)` (`outils/commun.py`).

### Règles à respecter

1. **Première ligne utile de `lancer`** :
   `entite = controler_cible(enquete, entite, TYPES)` — finalité, clôture,
   appartenance, statut actif, type. Ne pas s'en passer, même si la CLI vérifie déjà.
2. **Réseau** : uniquement via `core.requetes.ClientHTTP(enquete.stockage,
   enquete.id, NOM, transport=transport, limiteur=limiteur)` puis `client.get(…)`
   ou `client.get_json(…)`, avec `requete="description en clair"`. Ne jamais
   importer `urllib.request`, `socket`, `requests`… (un test échoue sinon).
   `verifier_robots=False` uniquement pour une API officielle documentée.
   Pour restreindre les hôtes, s'inspirer de `domaine.ClientPassif`.
3. **Programme externe** : uniquement via `Programme("nom", "commande
   d'installation").executer(enquete, NOM, args, dossier, duree_max)`.
   Valider toute valeur passée en argument (refuser ce qui commence par `-`).
   Passer les options de délai/concurrence du programme et les documenter dans
   la docstring du module.
4. **Clés** : `client.config.cle("service")` (lève `CleManquante` avec un
   message clair) ; ajouter la clé commentée dans `config.exemple.toml`.
5. **Sorties** : dans `enquete.chemin("bruts")`, nommées avec
   `fichier_brut(...)` ou `dossier_brut(...)` ; JSON avec `ecrire_json`.
6. **Ne rien créer** : ni entité, ni fait, ni lien. Proposer des commandes avec
   `commande("ajouter", "fait", "…", "--entite", str(id), "--source", url, "--outil", NOM)`.
7. **Minimisation** : ne garder que ce qui sert la finalité ; retirer les
   champs à accès restreint avant écriture.
8. Si l'outil ne fait aucune requête via `ClientHTTP` ni `Programme`,
   journaliser son action avec `enquete.stockage.journaliser(...)` (voir `dorks`).

### Brancher

1. `outils/__init__.py` : importer le module et l'ajouter au tuple de `OUTILS`.
2. `pilote/guide.py` : ajouter une `Methode(NOM, "libellé")` dans `METHODES`
   et le nom dans la règle du type concerné (`REGLES`).
3. `pilote/cli.py` : facultatif, un exemple dans `EXEMPLES["lancer"]`.
4. Tests : `tests/test_<nom>.py` sur le modèle de `tests/test_archives.py`
   (HTTP simulé avec `tests/reseau.Routeur`) ou `tests/test_pseudo.py`
   (programme simulé avec un faux lanceur). Les tests transverses de
   `tests/test_garde_fous.py` s'appliquent automatiquement au nouvel outil
   (refus sur entité inactive, enquête close, mauvais type).
5. Mettre à jour ce document (§6) et le README (tableau des outils).

### Squelette

```python
"""Mon outil : ce qu'il lit, d'où, et ce qu'il ne fait jamais."""

from __future__ import annotations

from datetime import datetime

from core.requetes import ClientHTTP
from outils.commun import Resultat, commande, controler_cible, ecrire_json, fichier_brut

NOM = "monoutil"
TYPES = ("domaine",)
API = "https://api.exemple.org/v1/recherche"


def options(parser) -> None:
    parser.add_argument("--limite", type=int, default=100, help="nombre de résultats")


def lancer(enquete, entite, opts, maintenant: datetime | None = None,
           transport=None, limiteur=None) -> Resultat:
    maintenant = maintenant or datetime.now()
    entite = controler_cible(enquete, entite, TYPES)
    client = ClientHTTP(enquete.stockage, enquete.id, NOM, transport=transport,
                        limiteur=limiteur)
    donnees = client.get_json(API, params={"q": entite["valeur"], "n": str(opts.limite)},
                              verifier_robots=False,
                              requete=f"recherche de {entite['valeur']}")
    brut = ecrire_json(fichier_brut(enquete.chemin("bruts"), NOM, entite["id"], "json",
                                    maintenant), donnees)
    lignes = [f"{len(donnees)} résultat(s)"]
    for d in donnees[:10]:
        lignes.append(f"  {d['titre']} — pour retenir : " + commande(
            "ajouter", "fait", d["titre"], "--entite", str(entite["id"]),
            "--source", d["url"], "--outil", NOM))
    lignes.append(f"Résultat enregistré : {brut}")
    return Resultat(lignes, [brut])
```

---

## 11. Dépannage

### Messages d'erreur courants

| Message | Cause | Solution |
|---|---|---|
| `aucune enquête courante : utiliser nouvelle, ouvrir ou -e NOM` | pas d'enquête désignée | `liste` puis `ouvrir NOM` |
| `une enquête exige une finalité déclarée` | finalité vide ou absente (hors terminal interactif, elle n'est pas demandée) | passer `--finalite "…"` |
| `nom invalide : minuscules, chiffres, - et _` | nom d'enquête | ex. `dupont-2026` |
| `l'enquête « X » existe déjà` | nom pris | autre nom, ou `ouvrir X` |
| `l'enquête « X » est close` | collecte ou écriture après clôture | normal ; une enquête close ne se rouvre pas, en créer une nouvelle |
| `enquête purgée : le rapport archivé est dans rapport/` | conservation échue | lire `enquetes/X/rapport/rapport-final-*.md` |
| `EN est écartée : aucune collecte sur cette entité` | entité écartée ou hors périmètre | `statuer entite N a_verifier` si c'était une erreur |
| `cet outil ne s'applique pas à une entité X` / `… ne s'applique pas …` | type non accepté | `aide lancer OUTIL` (types acceptés) |
| `outil « X » inconnu ou pas encore disponible` | outil non écrit (ex. `exif`) | voir §6 « Méthodes sans outil » |
| `« X » est une méthode manuelle` | `lancer recherche_inversee` | la faire soi-même, puis `marquer` |
| `confirmation refusée : moins de 2 domaines indépendants` | sources sur un seul domaine | ajouter une source d'un autre domaine (`ajouter source F URL`) |
| `invalid choice: 'confirme'` (sur `statuer`) | `statuer` ne confirme jamais | une entité ne se confirme pas : ajouter un fait sur l'entité, le sourcer sur 2 domaines, `confirmer F` |
| `clé d'API « hibp » absente : l'ajouter dans la section [cles] de config.local.toml` | clé manquante | §13 |
| `HIBP : clé refusée` / `HIBP : trop de requêtes, réessayer après N s` | clé invalide / quota | vérifier la clé ; attendre |
| `robots.txt interdit …` | le site refuse les robots pour cette URL | ne pas contourner ; consulter la page à la main |
| `hôte non autorisé pour une collecte passive : …` | `domaine` a tenté un hôte hors liste blanche (ou la cible) | comportement voulu ; si un nouveau serveur RDAP légitime est en cause, voir `ClientPassif.autoriser` |
| `aucun serveur RDAP publié pour .xx` | extension sans RDAP chez l'IANA | utiliser `--sources crtsh,shodan` |
| `HTTP 4xx/5xx sur …`, `JSON invalide sur …` | service indisponible ou format changé | réessayer plus tard ; `journal --dernieres 5` ; si persistant, le format de l'API a changé (adapter l'outil) |
| `réponse > N octets` | réponse plus grosse que `taille_max_octets` | réduire `--limite`, ou augmenter `taille_max_octets` |
| `sherlock introuvable : pipx install …` | programme absent du `PATH` | installer, puis `which sherlock` ; `pipx ensurepath` et rouvrir le terminal |
| `sherlock interrompu après N s (--duree-max)` | trop long | augmenter `--duree-max` ou réduire `--sites` |
| `holehe a échoué (code N), voir …` | erreur du programme | lire `holehe.stderr.txt` dans le dossier indiqué ; mettre à jour (`pipx upgrade holehe`) |
| `pseudo inutilisable` / `adresse e-mail invalide` | valeur refusée avant lancement (protection contre l'injection d'options) | corriger la valeur de l'entité (en ajouter une nouvelle, écarter l'ancienne) |
| `spaCy n'est pas installé` / `modèle fr_core_news_md absent` | IA non installée | `pip install spacy && python -m spacy download fr_core_news_md` |
| `fichier introuvable : …` | chemin de preuve ou de page | chemin absolu, ou relatif au dossier courant |
| `UNIQUE constraint failed` | entité déjà existante (même type et valeur) | `entites` pour retrouver son numéro |

### Situations

- **La commande semble bloquée** : la limite de débit attend `delai_s` entre
  deux appels au même hôte, et les programmes externes peuvent tourner
  plusieurs minutes (`--duree-max`). `journal --dernieres 5` (dans un autre
  terminal) montre la progression.
- **Un résultat attendu manque** : vérifier `journal` (requête refusée par
  robots.txt ? erreur ?) et les fichiers dans `bruts/`.
- **Rien n'est suggéré** : toutes les méthodes sont faites ou ignorées, ou les
  entités sont inactives. Ajouter une trouvaille ou rédiger le rapport.
- **Deux sources « différentes » ne comptent que pour une** : elles ont le
  même domaine enregistrable (`recouper` affiche le nombre de domaines). Si
  c'est un hébergement mutualisé (ex. `x.monhebergeur.fr` pour des titulaires
  distincts), ajouter le suffixe à `SUFFIXES_MULTIPLES` (`core/stockage.py`).
- **Vérifier l'intégrité d'une preuve** :
  `python -c "from core.stockage import Stockage; s=Stockage('enquetes/X/enquete.db'); print(s.verifier_preuve(1))"`
  (ou comparer `sha256sum enquetes/X/preuves/<fichier>` avec la fiche).
- **Lire la base directement** : `python -c "import sqlite3; cx=sqlite3.connect('enquetes/X/enquete.db'); [print(tuple(r)) for r in cx.execute('select * from fait')]"`.
  Ne pas modifier la base à la main : les triggers refusent les confirmations
  et les modifications du journal, et les fiches ne seraient pas régénérées.
- **Changer d'emplacement des enquêtes** : variable `OSINT_LAB_ENQUETES` (§13).
- **Tests très lents** : SQLite sur disque lent ; la base est déjà en WAL +
  `synchronous=NORMAL`. Lancer un seul fichier : `python -m pytest tests/test_dorks.py -q`.

---

## 12. Tests

```bash
python -m pytest -q                         # tout (≈ 3 minutes)
python -m pytest tests/test_garde_fous.py   # garde-fous transverses
python -m pytest -k confirmation -q         # par mot-clé
```

Aucun test ne fait de vrai appel réseau ni ne lance de vrai programme :
`tests/reseau.py` fournit un faux transport HTTP (`Routeur`) et un limiteur
sans pause ; les tests de sous-processus utilisent un faux lanceur. Le test
`test_vrai_modele_francais` est sauté si spaCy ou le modèle sont absents.

| Fichier | Couvre |
|---|---|
| `test_stockage.py` | schéma, finalité, confirmation, triggers, preuves, clôture, purge |
| `test_requetes.py` | robots.txt, débit, journal, masquage des clés, redirections |
| `test_rapport.py` | contenu du rapport, exclusions |
| `test_config.py` | lecture de la configuration |
| `test_guide.py` | règles de pivot pour chaque type |
| `test_pilote.py` | commandes de bout en bout, aide, journal |
| `test_dorks.py` … `test_courriel.py` | chaque outil |
| `test_ia_entites.py`, `test_confiance.py` | IA locale |
| `test_garde_fous.py` | engagements légaux, sur tout le projet |

### Vérifications à faire dans la VM (vrais services)

Les formats réels des API et des programmes n'ont été testés qu'en simulation.
À la première utilisation dans la VM, sur des cibles sans enjeu :

1. `lancer archives` sur un domaine connu : format CDX, redirections, taille bornée.
2. `lancer entreprises` (nom puis `--siren`) : format de l'API, Pappers avec clé.
3. `lancer domaine` : crt.sh (et son robots.txt), RDAP, Shodan avec clé.
4. `lancer pseudo` avec sherlock et maigret installés : options acceptées,
   rapports CSV (sherlock) et JSON simple (maigret) bien lus.
5. `lancer courriel` avec holehe : options et CSV.
6. `lancer fuite` avec une vraie clé HIBP.
7. `pip install spacy && python -m spacy download fr_core_news_md`, puis
   `python -m pytest tests/test_ia_entites.py -q` (le test du vrai modèle ne doit plus être sauté).

Si un format a changé, l'erreur se voit dans `journal` et dans `bruts/` ;
adapter la fonction de lecture de l'outil concerné et son test.

---

## 13. Configuration et variables d'environnement

`config.local.toml` (à la racine du projet ; copier `config.exemple.toml`) :

```toml
[reseau]
delai_s = 2.0                 # pause minimale entre deux appels au même hôte (secondes)
timeout_s = 20.0              # délai max d'une requête
taille_max_octets = 10485760  # taille max d'une réponse (10 Mo)
# user_agent = "osint-lab/0.1 (enquete OSINT, donnees publiques)"

[cles]
hibp = "…"
pappers = "…"
shodan = "…"
```

Sans fichier, les valeurs par défaut ci-dessus s'appliquent et les outils à
clé sont sautés ou refusés avec un message. Le fichier est ignoré par git.

| Variable | Rôle | Défaut |
|---|---|---|
| `OSINT_LAB_CONFIG` | chemin d'un autre fichier de configuration | `config.local.toml` du projet |
| `OSINT_LAB_ENQUETES` | dossier des enquêtes | `enquetes/` du projet |

Exemple : `OSINT_LAB_ENQUETES=/media/chiffre/enquetes python osint_lab.py liste`
(utile pour garder les enquêtes sur un volume chiffré).
