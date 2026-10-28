# osint-lab — justification légale

> ⚠️ **Avertissement — références non vérifiées.**
> Les références légales de ce document (articles, arrêts, numéros de pourvoi)
> sont citées à titre indicatif, de mémoire, et n'ont pas été vérifiées.
> Elles doivent être confirmées sur Légifrance / EUR‑Lex ou par un juriste
> avant tout usage face à un tiers.

## Objet et portée de ce document

Ce document justifie la **conception** du logiciel. Pour chaque action
qu'osint-lab sait accomplir, il indique :

- ce que fait l'action ;
- pourquoi elle est licite en droit français et européen ;
- la limite intégrée dans le code qui la maintient dans ce cadre.

Il montre que l'outil, utilisé tel qu'il est livré, ne commet par lui-même
aucune des infractions visées et applique les principes du RGPD.

**La légalité d'un usage concret dépend en plus de la finalité déclarée pour
chaque enquête.** Le logiciel impose qu'une finalité soit déclarée, mais il ne
peut pas juger si elle est légitime. Il ne peut pas non plus vérifier que
l'enquêteur dispose d'une base légale, qu'il a le droit d'enquêter sur cette
personne, ni que les éléments qu'il saisit à la main ont été obtenus
loyalement. Ces points relèvent de l'enquêteur (voir la dernière section).

Ce document n'est pas un avis juridique. Les garde-fous cités sont détaillés,
avec leurs tests, dans la section 9 de [DOC_TECHNIQUE.md](DOC_TECHNIQUE.md#9-garde-fous-légaux-et-emplacement-dans-le-code).

## Textes de référence

| Référence | Objet |
|---|---|
| **Art. 323-1 C. pén.** | Accès ou maintien frauduleux dans tout ou partie d'un système de traitement automatisé de données (STAD). Selon la jurisprudence, le maintien est frauduleux même sans protection technique, dès lors que la personne sait que le maître du système ne veut pas de cet accès (Cass. crim., 20 mai 2015, n° 14-81.336, « Bluetouff »). |
| Art. 323-2 et 323-3 C. pén. | Entrave au fonctionnement d'un STAD ; extraction ou reproduction frauduleuse de données. |
| **Art. 321-1 C. pén.** | Recel : détenir ou transmettre une chose provenant d'un crime ou d'un délit, ou en tirer profit en connaissance de cause. Cela vise notamment des données issues d'une intrusion ou d'une fuite. |
| Art. 226-18 et 226-20 C. pén. | Collecte de données personnelles par un moyen frauduleux, déloyal ou illicite ; conservation au-delà de la durée prévue. |
| **Art. L342-1 CPI** | Droit du producteur d'une base de données : il peut interdire l'extraction ou la réutilisation d'une partie substantielle de son contenu (L342-2 : extraction répétée et systématique). |
| **RGPD** | Art. 5 : licéité, limitation des finalités, minimisation, exactitude, limitation de la conservation, intégrité, responsabilité. Art. 6 : base légale (notamment 6.1.f, intérêt légitime). Art. 9 : données sensibles. Art. 14 : information des personnes. Art. 22 : décision fondée exclusivement sur un traitement automatisé. Art. 25 : protection des données dès la conception. Art. 30 : registre. Art. 32 : sécurité. Art. 44 et suivants : transferts hors UE. |
| CJUE, 22 nov. 2022, C-37/20 et C-601/20 ; art. L561-46 CMF | Fin de l'accès public aux registres des bénéficiaires effectifs ; accès réservé aux autorités et aux personnes justifiant d'un intérêt légitime. |
| Art. 1366 C. civ. | Force probante de l'écrit électronique, sous réserve d'identifier son auteur et de garantir son intégrité. |

## Tableau des actions

| Action | Ce qu'elle fait | Pourquoi elle est licite | Limite intégrée dans le code |
|---|---|---|---|
| **Création d'une enquête** (`nouvelle`) | Ouvre un dossier et une base locale, enregistre finalité, périmètre, durée de conservation et points de départ (`cadrage.md`). | Aucune collecte. C'est la condition préalable de tout traitement licite : finalité déterminée, explicite et légitime (**RGPD art. 5.1.b**), protection dès la conception (art. 25). Le cadrage sert de base au registre de l'enquêteur (art. 30). | Refus sans finalité à trois niveaux : `CHECK (trim(finalite) <> '')` en base, `Stockage.creer_enquete` (`FinaliteManquante`), et `exiger_collecte_possible` avant toute écriture, requête ou sous-processus. Durée de conservation obligatoire et positive. |
| **Couche réseau commune** (toute requête HTTP) | Unique point de sortie réseau des modules Python (`core/requetes.py`). | Consultation de pages et d'API publiques, sans authentification ni contournement : aucun accès frauduleux (**323-1**). Le respect de robots.txt matérialise la volonté de l'exploitant : si elle n'est pas respectée, il pourrait y avoir maintien frauduleux au sens de l'arrêt Bluetouff. La limite de débit évite toute entrave (323-2). Chaque requête est tracée (**RGPD art. 5.2**). | GET uniquement. robots.txt appliqué selon la RFC 9309, revérifié si une redirection change de domaine. Délai minimal par hôte, partagé entre les outils (`LIMITEUR_PARTAGE`). Taille et délai des réponses bornés. Aucun proxy ni Tor. Journal de chaque requête, refus compris, avec les clés masquées. Aucun autre module ne peut ouvrir de connexion (test `test_reseau_uniquement_par_core_requetes`). |
| **dorks** | Fabrique des liens Google et Bing que l'enquêteur ouvre lui-même. | Aucune requête automatisée vers les moteurs : pas de violation de leurs CGU, pas d'extraction de leur index (**L342-1**). Les modèles excluent les recherches de fichiers et de sites de collage, qui mènent surtout à des données issues de fuites : pas de **recel** facilité (**321-1**). | Aucun envoi réseau, mais la génération est journalisée (`dorks.lancer`). Recherches de tableurs, CSV, SQL et sites de collage absentes de `MODELES` (test `test_dorks_sans_fuites`). Option `--contexte` pour écarter les homonymes (exactitude, art. 5.1.d). |
| **entreprises** | Recherche des entreprises et de leurs dirigeants via l'API publique de l'État (recherche-entreprises.api.gouv.fr) et, pour un SIREN, via Pappers. | Données légalement publiées (registres légaux, Sirene, diffusées en open data) et consultées par les API prévues à cet effet, dans leurs conditions d'usage : pas d'accès frauduleux, pas d'extraction substantielle (**L342-1**). Les noms des dirigeants sont des données personnelles : le traitement repose sur la finalité déclarée (**RGPD art. 6.1.f**) et reste minimisé (art. 5.1.c). | Clé Pappers hors dépôt. Nombre de résultats plafonné (25). Les **bénéficiaires effectifs** sont retirés de toute réponse avant écriture (`purger_champs_interdits`), conformément à la CJUE et à L561-46 CMF. Aucune fiche n'est créée pour les dirigeants, qui sont des tiers : l'enquêteur choisit ce qu'il retient. |
| **domaine** | Lit les certificats publiés (crt.sh, journaux Certificate Transparency), les données d'enregistrement du registre (RDAP) et le DNS déjà collecté par Shodan. | Lecture de sources **publiées par des tiers**, sans aucun contact avec le système visé : ni scan, ni sonde, ni requête vers ses serveurs. Aucun accès ou maintien dans le STAD de la cible (**323-1**), aucune entrave (323-2). Les journaux CT et RDAP sont publics par construction. Les données masquées par le registre restent masquées. | `ClientPassif` : liste blanche d'hôtes (`crt.sh`, `data.iana.org`, `api.shodan.io`, serveur RDAP officiel). Tout hôte du domaine visé est refusé avant l'envoi, même s'il a été autorisé explicitement (test `test_domaine_ne_contacte_jamais_la_cible`). Aucun outil de scan dans le projet (test `test_aucun_outil_de_scan`). Les sous-domaines et le titulaire sont proposés, jamais enregistrés d'office. |
| **archives** | Liste les captures de la Wayback Machine pour un domaine ou une page, via l'API CDX. | API officielle et publique d'Internet Archive, conçue pour cet usage. Ne sont conservés que des métadonnées et des liens, pas le contenu archivé. Extraction bornée, non substantielle (**L342-1**). | Un seul appel par exécution, journalisé et soumis au délai. `--limite` plafonné à 10 000. `--url` doit appartenir au domaine de l'entité. Les captures identiques sont regroupées par défaut. |
| **pseudo** (sherlock, maigret) | Vérifie sur quelles plateformes une page de profil publique existe pour un pseudo. | Consultation de pages publiques, sans compte ni authentification (**323-1** non concerné). Un même pseudo sur deux plateformes peut appartenir à deux personnes : rien n'est attribué automatiquement (exactitude, **RGPD art. 5.1.d**). Pas de collecte du contenu des profils (minimisation, art. 5.1.c). | Lancement sans shell, valeur validée pour empêcher l'injection d'options. Jamais `--tor` ni `--proxy`. Pour maigret : `--no-recursion --no-extracting` et 20 connexions au lieu de 100. Durée maximale par programme. Ligne de commande journalisée. Aucune entité ni aucun fait créé : des commandes sont proposées. |
| **courriel** (holehe) | Indique sur quels sites une adresse e-mail est associée à un compte. | Utilise des interfaces publiques, sans authentification ni contournement de protection. C'est l'outil **le plus sensible au regard des CGU** des sites testés : un usage abusif ou massif pourrait être assimilé à un maintien non souhaité par l'exploitant (**323-1**, arrêt Bluetouff). D'où un usage réservé aux finalités qui le justifient clairement. | Toujours `--no-password-recovery` : aucune procédure de récupération de mot de passe n'est déclenchée, aucun fragment de coordonnées de secours n'est collecté. `--only-used`. Délai par site et durée maximale. Adresse validée avant passage. Appel journalisé. Résultat présenté comme « un compte existe », pas comme une attribution. |
| **fuite** (HIBP) | Vérifie si une adresse figure dans des fuites connues, et lesquelles. | Ne vérifie que la **présence**. Aucune donnée issue d'une fuite n'est téléchargée, détenue ou réutilisée : pas de **recel** (**321-1**), pas de collecte déloyale (226-18). Service et API officiels, avec clé, dans leurs conditions d'usage. | `truncateResponse=true` : seuls les noms des fuites reviennent et sont conservés. Pas d'API « paste ». Fuites non vérifiées exclues par défaut (test `test_fuite_ne_recupere_que_la_presence`). Clé hors dépôt, jamais journalisée. |
| **Saisie manuelle et preuves** (`ajouter`) | Enregistre les entités, faits, sources, liens et fichiers de preuve retenus par l'enquêteur. | Rien n'est collecté automatiquement : chaque donnée est un choix humain (minimisation, **RGPD art. 5.1.c**). Chaque fait est rattaché à sa source (loyauté). L'empreinte sha256 garantit l'intégrité de la preuve (**art. 1366 C. civ.**, **RGPD art. 5.1.f**). | Source obligatoire pour confirmer, URL validée. Copie horodatée dans `preuves/` avec sha256 calculé sur la copie (`ajouter_preuve`, `verifier_preuve`). Refus si l'enquête est close. Les outils ne créent jamais ces objets d'eux-mêmes. |
| **Confirmation d'un fait** (`confirmer`) | Passe un fait au statut « confirmé ». | Exactitude des données (**RGPD art. 5.1.d**) : un fait n'est confirmé que s'il est recoupé par des sources indépendantes et validé par une personne. Aucune conclusion n'est tirée par la machine (**art. 22**). | Seul chemin vers `confirme` : `Stockage.confirmer_fait`, appelé uniquement par la commande `confirmer`. Refus sous 2 domaines indépendants. Triggers `fait_confirme_a_la_creation` et `fait_confirme_deux_domaines` qui tiennent même en SQL direct. `statuer` ne peut pas confirmer. Une entité n'est jamais confirmée (test `test_confirmation_verrouillee`). |
| **Écarter, exclure** (`statuer`) | Marque une entité ou un fait comme écarté (faux positif) ou hors périmètre (sans lien avec la finalité). | Limitation des finalités et minimisation (**RGPD art. 5.1.b et c**) : ce qui sort de la finalité n'est plus traité ni restitué. C'est la protection des tiers, homonymes ou proches. | Plus aucune collecte ni suggestion sur une entité inactive : contrôle par la CLI puis par chaque outil (`controler_cible`), et par `guide.suggerer`. Le hors-périmètre est absent du rapport, sans trace. Test `test_outil_refuse_entite_inactive` pour chaque outil. |
| **Analyse d'une page** (`analyser`, IA) | Repère dans une page enregistrée les noms de personnes, organisations, lieux et les dates, puis propose des rapprochements avec les fiches. | Traitement **entièrement local** : aucune donnée envoyée à un tiers ni hors UE (**RGPD art. 44 et suivants**, art. 32). Pas de collecte nouvelle, seulement la lecture d'une page déjà retenue. L'IA propose, n'enregistre rien et ne complète aucune information (art. 5.1.d, **art. 22**). | Aucun import réseau dans `ia/` (tests `test_ia_sans_reseau_ni_core_requetes`, `test_analyse_reseau_coupe`). Modèle spaCy chargé localement, sans téléchargement. Aucune écriture en base hors journal. Rapprochement limité aux entités actives. Analyse journalisée. |
| **Indices de confiance** (`confiance`) | Calcule, par des règles explicites, des indices de rattachement d'une entité candidate à l'enquête. | Aide à la lecture, sans aucun effet sur les données : aucune décision automatisée (**RGPD art. 22**). Les règles sont lisibles et chaque indice est expliqué (transparence). La mesure sur les décisions de l'enquêteur permet de contrôler les erreurs (exactitude, art. 5.1.d). | Aucun statut modifié (test `test_aucun_statut_modifie`). Affichage dans l'ordre des entités, jamais trié par score : pas de priorisation des pistes par la machine. Entités écartées et hors périmètre jamais évaluées. |
| **Rapport** (`rapport`) | Compile un rapport Markdown : faits avec leurs sources, entités, preuves, liens, méthodologie. | Restitution loyale et vérifiable (**RGPD art. 5.1.a**) : chaque fait est sourcé, les faits à vérifier sont distingués des faits confirmés. Minimisation (art. 5.1.c) : le hors-périmètre est exclu, le détail des pistes écartées aussi. Le rapport documente la démarche (art. 5.2). | `generer_rapport` ne retient que les statuts actifs et exclut les faits et liens des entités hors périmètre (test `test_hors_perimetre_absent_du_rapport`). Les écartés sont seulement comptés. Avertissement sur le sens de « confirmé ». sha256 du rapport journalisé. Rapport impossible après purge. |
| **Journal** (`journal`) | Trace chaque requête, appel de programme, analyse, rapport, clôture et purge. | Responsabilité (**RGPD art. 5.2**) : l'enquêteur peut démontrer ce qui a été consulté, quand et comment. Le journal sert de pièce justificative en cas de contestation. | Écrit automatiquement par `ClientHTTP`, `Programme` et les commandes. **Ajout seul** : triggers `journal_sans_modification` et `journal_sans_suppression`. Conservé à la purge. Clés masquées. La commande est en lecture seule. |
| **Clôture et purge** (`cloturer`, purge automatique) | Fige l'enquête, archive le rapport final, fixe l'expiration, puis supprime les données à échéance. | Limitation de la conservation (**RGPD art. 5.1.e**). Conserver au-delà de la durée prévue est pénalement réprimé (**226-20 C. pén.**). Seuls subsistent le rapport final, le cadrage et le journal, nécessaires pour justifier la démarche (art. 5.2). | Plus aucune écriture ni collecte après clôture (`EnqueteCloturee`). Expiration = clôture + durée déclarée. Purge à chaque lancement, sans démon (`purger_expirees`) : fiches, preuves, sorties brutes, entités, faits, sources et liens. Purge journalisée (test `test_journal_survit_a_la_purge`). |
| **Clés d'API et configuration** | Stocke les clés des services (HIBP, Pappers, Shodan) et les réglages réseau. | Sécurité du traitement (**RGPD art. 32**) et respect des conditions des API : accès nominatif, non partagé. | Clés uniquement dans `config.local.toml`, ignoré par git, jamais en dur. Masquées dans le journal. En-têtes non transmis à un autre hôte en cas de redirection. |

## Ce qui reste de la responsabilité de l'enquêteur

Le logiciel encadre le **comment**. Le **pourquoi** et le **qui** relèvent de
l'enquêteur, pour chaque enquête :

- **Finalité légitime et base légale** (RGPD art. 5.1.b et 6) : par exemple
  l'intérêt légitime (vérification d'un partenaire, lutte contre la fraude,
  défense d'un droit en justice), après mise en balance avec les droits de la
  personne. Une finalité déclarée mais illégitime rend le traitement illicite,
  quel que soit l'outil.
- **Qualité pour agir** : enquêter pour le compte d'autrui contre
  rémunération relève de la réglementation des agents de recherches privées
  (Code de la sécurité intérieure, livre VI).
- **Information et droits des personnes** (RGPD art. 14 à 21) : informer la
  personne concernée, sauf exception applicable (art. 14.5), et répondre à ses
  demandes d'accès ou d'effacement.
- **Données sensibles** (RGPD art. 9) : ne pas les retenir, sauf exception
  prévue. Utiliser le statut `hors_perimetre` pour tout ce qui touche à la vie
  privée sans rapport avec la finalité.
- **Saisies manuelles** : ne pas enregistrer de données obtenues par un accès
  non autorisé ou provenant d'une fuite (**323-1**, **321-1**). Le logiciel ne
  peut pas vérifier l'origine de ce qu'on lui fournit.
- **Choix de la durée de conservation**, adaptée à la finalité, et de l'outil
  `courriel` (holehe), à réserver aux cas qui le justifient.
- **Respect des CGU** des services interrogés et des quotas des API.
- **Environnement d'exécution** : VM isolée, et volume chiffré pour les
  enquêtes (variable `OSINT_LAB_ENQUETES`), au titre de la sécurité (art. 32).
