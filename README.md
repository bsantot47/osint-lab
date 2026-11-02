# osint-lab

Pilote d'enquêtes OSINT en ligne de commande, **traçable et conforme au cadre
légal français**. L'outil guide l'enquêteur (quoi faire ensuite), range les
trouvailles, journalise chaque requête et produit un rapport sourcé.

Quatre principes, imposés par le code et pas seulement par la documentation :

- **Script d'abord, IA en dernier recours** : tout ce qui est déterministe est
  du script ; l'IA (spaCy, locale) ne sert qu'à lire du texte libre.
- **Légalité par conception** : pas de collecte sans finalité déclarée,
  journal de chaque requête, limite de débit, durée de conservation, exclusion
  du hors-périmètre dans le rapport.
- **L'humain décide** : l'outil propose, l'enquêteur valide. Un fait n'est
  « confirmé » que par l'enquêteur, et seulement s'il repose sur au moins deux
  sources de domaines différents.
- **Pas de contournement** : données publiques sans authentification, API
  officielles, respect de robots.txt et des CGU, aucun scan de la cible.

La documentation complète (architecture, toutes les commandes, garde-fous,
ajout d'un outil, dépannage) est dans [DOC_TECHNIQUE.md](DOC_TECHNIQUE.md).
La justification légale de chaque action est dans [JUSTIFICATION_LEGALE.md](JUSTIFICATION_LEGALE.md).
Dans le terminal : `python osint_lab.py aide` et `python osint_lab.py aide <commande>`.

---

## 1. Installation dans la VM

L'outil est développé sur le PC hôte mais doit **tourner dans une VM isolée**
(Debian 12 ou Ubuntu 24.04 conseillés), jamais sur le poste habituel. Toutes
les commandes ci-dessous se tapent **dans la VM**, sauf l'étape A.

### A. Sur le PC hôte : préparer un dépôt Git

À faire une seule fois (puis `git add -A && git commit` après chaque modification) :

```bash
cd ~/Bureau/osint-lab
git init
git add -A
git status          # vérifier : ni config.local.toml, ni enquetes/, ni *.db (exclus par .gitignore)
git commit -m "osint-lab : version initiale"
```

Puis transférer le dépôt vers la VM, au choix :

- **VM sans réseau vers l'hôte (recommandé pour l'isolation)** : un paquet Git
  dans un seul fichier, à copier par dossier partagé ou clé USB :

  ```bash
  git bundle create /tmp/osint-lab.bundle --all
  # copier /tmp/osint-lab.bundle dans la VM (dossier partagé, clé USB…)
  ```

- **Dépôt distant privé** (GitHub, GitLab… en privé) :

  ```bash
  git remote add origin git@github.com:<compte>/osint-lab.git
  git push -u origin main      # ou master, selon le nom de la branche
  ```

### B. Dans la VM : paquets système

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip pipx exiftool
pipx ensurepath              # puis fermer et rouvrir le terminal
python3 --version            # doit afficher 3.11 ou plus
```

(`exiftool` sert à la méthode `exif`, faite à la main pour l'instant.)

### C. Dans la VM : récupérer le projet

```bash
cd ~
git clone ~/osint-lab.bundle osint-lab            # depuis le paquet copié (adapter le chemin)
# ou : git clone git@github.com:<compte>/osint-lab.git
cd osint-lab
```

Mise à jour ultérieure : refaire un `git bundle` sur l'hôte, le copier, puis
`git pull ~/osint-lab.bundle` dans la VM (ou `git pull` avec un dépôt distant).

### D. Dans la VM : environnement Python, tests, spaCy

```bash
cd ~/osint-lab
python3 -m venv .venv
source .venv/bin/activate          # à refaire dans chaque nouveau terminal
pip install --upgrade pip
pip install pytest spacy
python -m spacy download fr_core_news_md     # une seule fois ; ensuite tout est local
python -m pytest -q                # tout doit passer, plus aucun test sauté
python osint_lab.py aide
```

Le cœur n'utilise que la bibliothèque standard : sans spaCy, tout fonctionne
sauf `analyser` (et un test est alors sauté).

### E. Dans la VM : outils externes

Chacun dans son propre environnement, via `pipx` (hors du `.venv`) :

```bash
pipx install sherlock-project      # lancer pseudo
pipx install maigret               # lancer pseudo
pipx install holehe                # lancer courriel
which sherlock maigret holehe      # les trois doivent être trouvés
```

| Pour…                              | Dépendance                       | Utilisé par        |
|------------------------------------|----------------------------------|--------------------|
| présence d'un pseudo               | sherlock et/ou maigret (pipx)    | `lancer pseudo`    |
| comptes liés à une adresse e-mail  | holehe (pipx)                    | `lancer courriel`  |
| analyse d'une page par l'IA locale | spacy + `fr_core_news_md` (pip)  | `analyser`         |
| métadonnées d'image (à la main)    | exiftool (apt)                   | méthode `exif`     |
| tests                              | pytest (pip)                     | `python -m pytest` |

### F. Dans la VM : clés d'API (optionnel)

```bash
cp config.exemple.toml config.local.toml
nano config.local.toml             # décommenter et remplir les clés voulues
```

| Clé       | Service                         | Sert à                                                  | Sans la clé               |
|-----------|---------------------------------|---------------------------------------------------------|---------------------------|
| `hibp`    | haveibeenpwned.com (payant)     | `lancer fuite` : présence d'une adresse dans des fuites | `fuite` refuse de tourner |
| `pappers` | api.pappers.fr                  | `lancer entreprises --siren …` : représentants          | Pappers est sauté         |
| `shodan`  | api.shodan.io                   | `lancer domaine` : DNS déjà collecté par Shodan         | Shodan est sauté          |

`config.local.toml` est ignoré par git : **ne jamais mettre une clé ailleurs**,
et ne pas le copier depuis l'hôte par Git. Les clés sont masquées dans le journal.

### G. Dans la VM : stockage des enquêtes (conseillé)

Par défaut, les enquêtes vont dans `osint-lab/enquetes/`. Pour les garder sur
un volume chiffré :

```bash
echo 'export OSINT_LAB_ENQUETES=/chemin/vers/volume-chiffre/enquetes' >> ~/.bashrc
```

### H. Premières vérifications contre les vrais services

Les outils réseau n'ont été testés qu'en simulation. Avant une vraie enquête,
faire les vérifications listées dans la section 12 de
[DOC_TECHNIQUE.md](DOC_TECHNIQUE.md#12-tests) (« Vérifications à faire dans la
VM »), sur une enquête d'essai et des cibles sans enjeu.

---

## 2. Exemple d'enquête de bout en bout

Cas : vérification préalable d'un fournisseur avant signature d'un contrat.
Toutes les commandes se lancent depuis le dossier du projet.

### Créer l'enquête (finalité obligatoire)

```bash
python osint_lab.py nouvelle dupont \
  --finalite "Vérification préalable d'un fournisseur avant contrat" \
  --perimetre "activité professionnelle uniquement" \
  --conservation 30 \
  --depart "personne:Jean Dupont" \
  --depart "email:jdupont@dupont-conseil.fr"
```

Sans `--finalite`, l'outil la demande ; vide, l'enquête est refusée.
L'enquête devient l'**enquête courante** : les commandes suivantes s'y
appliquent (sinon `-e dupont` ou `ouvrir dupont`).

```
Départ :
  E1 personne « Jean Dupont »
  E2 email « jdupont@dupont-conseil.fr »
À faire :
  E1 personne « Jean Dupont » : requêtes Google/Bing prêtes à cliquer → lancer dorks --entite 1
  E1 personne « Jean Dupont » : annuaire-entreprises et Pappers : mandats, liens → lancer entreprises --entite 1
  E2 email … : holehe : services associés à l'adresse → lancer courriel --entite 2
  E2 email … : HIBP : présence dans des fuites connues (vérification seule) → lancer fuite --entite 2

Pivot suggéré : lancer dorks --entite 1  (un nom seul : recherche ouverte, puis mandats)
```

`E1`, `E2`… sont les numéros d'entités ; `F1`, `F2`… ceux des faits. Dans les
commandes, on tape le numéro seul (`--entite 1`, `confirmer 1`).

### Collecter

```bash
python osint_lab.py lancer dorks --entite 1 --contexte Lyon
python osint_lab.py lancer entreprises --entite 1
```

`dorks` n'envoie aucune requête : il fabrique des liens Google/Bing à ouvrir
soi-même. `entreprises` interroge l'API publique de l'État. Chaque outil range
ses sorties dans `enquetes/dupont/bruts/` et **propose** des commandes
`ajouter` prêtes à copier : il ne crée jamais rien lui-même.

### Retenir ce qui est vérifié

```bash
python osint_lab.py ajouter entite entreprise "Dupont Conseil"          # → E3
python osint_lab.py ajouter fait "Jean Dupont est gérant de Dupont Conseil" --entite 3 \
  --source https://annuaire-entreprises.data.gouv.fr/entreprise/123456789 --outil entreprises
python osint_lab.py ajouter source 1 https://www.pappers.fr/entreprise/dupont-conseil-123456789
#   Source ajoutée à F1 : 2 domaine(s) indépendant(s)
#     confirmation possible, à valider → confirmer 1
python osint_lab.py ajouter lien 1 3 "gérant"
python osint_lab.py ajouter entite compte jdupont --preuve ~/captures/profil.png   # copie + sha256
```

### Recouper, confirmer, écarter

```bash
python osint_lab.py recouper          # tableau faits / sources / domaines / confirmable
python osint_lab.py confirmer 1       # refusé s'il y a moins de 2 domaines différents
python osint_lab.py ajouter entite compte jean_dupont_69                  # → E5
python osint_lab.py statuer entite 5 ecarte        # homonyme : faux positif
python osint_lab.py confiance         # indices de rattachement, par règles, sans décision
```

`confirmer` est le **seul** moyen de confirmer un fait. `statuer` sert à
écarter (`ecarte`), exclure (`hors_perimetre`, ex. un tiers sans rapport) ou
remettre à vérifier (`a_verifier`).

### Suivre, puis conclure

```bash
python osint_lab.py statut            # où en est-on, que reste-t-il, pivot suggéré
python osint_lab.py entites           # graphe des entités et des liens
python osint_lab.py journal           # toutes les requêtes, horodatées
python osint_lab.py rapport           # rapport Markdown sourcé dans enquetes/dupont/rapport/
python osint_lab.py cloturer          # demande « oui » ; fige l'enquête, archive le rapport final
```

Pour faire relire le rapport sans exposer les identités :

```bash
python osint_lab.py anonymiser            # rapport-anonyme.md + correspondance.md (dernier rapport généré)
python osint_lab.py desanonymiser         # rapport-final.md, identique au rapport d'origine
python osint_lab.py desanonymiser relu.md # ou : un fichier retravaillé à partir du rapport anonymisé
```

`anonymiser` remplace chaque entité par une étiquette (`PERSONNE A`, `ENTREPRISE 1`,
`PSEUDO A`, `EMAIL A`, `DOMAINE 1`, `NUMERO 1`…) et chaque URL par la nature de la
source (`[fiche Pappers]`, `[registre officiel]`, `[compte public]`…). Il signale les
morceaux de noms restés dans le texte (« M. Dupont ») : à relire avant toute
diffusion, comme le contexte (dates, fonctions, lieux). Ne jamais transmettre
`correspondance.md` avec le rapport : tant qu'elle existe, ce rapport reste une
donnée personnelle (pseudonymisation).

Relancer ces commandes n'écrase pas un travail manuel : un fichier produit modifié
depuis sa génération est renommé `<nom>-modifie-AAAAMMJJ-HHMMSS.md`, et la commande
l'indique.

Après la clôture, plus aucune collecte ni modification n'est possible. Au
terme de la durée de conservation (30 jours ici), la commande suivante, quelle
qu'elle soit, purge les fiches, preuves et sorties brutes, ainsi que
`rapport-anonyme.md`, `correspondance.md`, `rapport-final.md` et leurs copies
`-modifie-`. Le rapport final archivé dans `rapport/`, le cadrage et le journal restent.

### Ce que contient le dossier d'une enquête

```
enquetes/dupont/
├── enquete.db    base SQLite (source de vérité, journal compris)
├── cadrage.md    finalité, périmètre, conservation, points de départ
├── fiches/       E1.md, E2.md… régénérées après chaque modification
├── preuves/      copies horodatées des fichiers de preuve
├── bruts/        sorties brutes des outils
├── rapport/      rapports ; rapport-final-AAAAMMJJ.md à la clôture
├── rapport-anonyme.md, correspondance.md   sortie de `anonymiser`
└── rapport-final.md                        sortie de `desanonymiser`
```

---

## 3. Outils disponibles

| Outil         | Entités                     | Ce qu'il fait                                                    | Réseau                         |
|---------------|-----------------------------|------------------------------------------------------------------|--------------------------------|
| `dorks`       | personne, entreprise, email, compte, domaine, numero | liens Google/Bing à ouvrir soi-même       | aucun                          |
| `entreprises` | personne, entreprise        | mandats, dirigeants (API de l'État, Pappers si clé + SIREN)       | API officielles                |
| `domaine`     | domaine                     | certificats (crt.sh), registre (RDAP), DNS Shodan si clé         | tiers uniquement, jamais la cible |
| `archives`    | domaine                     | captures Wayback Machine                                          | API officielle                 |
| `pseudo`      | compte                      | sherlock / maigret                                                | les programmes eux-mêmes       |
| `courriel`    | email                       | holehe (sans récupération de mot de passe)                        | le programme lui-même          |
| `fuite`       | email                       | HIBP : présence dans des fuites connues, rien n'est téléchargé    | API officielle (clé)           |

`holehe` est l'outil le plus sensible au regard des CGU des sites testés : à
réserver aux cas où la finalité le justifie clairement.

Méthode manuelle suggérée par le guide : `recherche_inversee` (images). La
faire soi-même, puis `marquer recherche_inversee --entite N`. La méthode
`exif` est suggérée pour les images mais n'est pas encore outillée : passer
`exiftool` à la main, puis `marquer exif --entite N`.

---

## 4. En cas de problème

Voir la section « Dépannage » de [DOC_TECHNIQUE.md](DOC_TECHNIQUE.md#11-dépannage).
Les messages d'erreur de l'outil commencent par `erreur :` et disent quoi faire.
