# Cahier des charges — Déploiement de postes par le réseau (mdtprox)

Conception et développement : Baptiste Renneson-Boutard (Elania-Marvers), concepteur principal.
Projet libre sous licence MIT : tout établissement ou organisme peut le reprendre et l'adapter en conservant cette mention et la licence.

**État du projet (octobre 2026).**

- Le code est écrit et testé en simulation : plus de 150 tests côté serveur, 96 côté client, 31 tests de bout en bout et de cohérence du mini-système.
- Rien n'a encore tourné sur du vrai matériel : ni démarrage par le réseau, ni restauration d'un disque, ni jonction au domaine. Le mini-système Linux n'a jamais été construit.
- La section 7 sépare ce qui est écrit, ce qui est testé en simulation et ce qui reste à vérifier sur un banc d'essai.

Ce fichier est le seul document du projet : cahier des charges, choix techniques expliqués, plan d'essai et contrat entre les morceaux de code (annexe A).

---

## 0. Comment lire ce document

Le document est écrit pour être compris sans connaissance préalable en informatique. Chaque terme technique est expliqué à sa première apparition, et le glossaire en fin de document rassemble les définitions.

Chaque tâche de la section 5 suit le même plan :

- **En une phrase** : à quoi elle sert.
- **L'image pour comprendre** : une comparaison avec la vie quotidienne.
- **Concrètement** : ce qu'elle fait réellement.
- **Les choix et pourquoi** : chaque décision, avec les solutions écartées.
- **Où en est-on** : ce qui est fait, testé ou non, et les limites connues.

Les statuts (tableaux uniquement) :

| Statut | Sens |
|---|---|
| Vérifié | Écrit et vérifié par des tests automatiques ou un essai en simulation |
| À vérifier sur banc | Écrit, mais vérifiable seulement sur du vrai matériel (le « banc d'essai » : un vrai PC et un vrai serveur) |
| À faire | Pas encore fait |

---

## 1. Objet et contexte

### 1.1 Le besoin

Quand un PC est neuf, en panne ou doit être remis à zéro, il faut lui réinstaller Windows avec les logiciels, les réglages et la langue, puis le rattacher au réseau de l'établissement. À la main, cela prend des heures par poste.

Aujourd'hui, les installations se font avec Clonezilla : un logiciel libre qui copie un disque entier dans un dossier, puis rejoue cette copie sur un autre disque. Les images (les copies) existent déjà, par modèle de PC et par salle. Le serveur MDT n'est plus utilisé pour ces opérations.

Le projet **réutilise ces images** et ajoute un parcours contrôlé, plus sûr et peu utilisable par les élèves :

1. Le PC démarre par le réseau et affiche un écran de texte.
2. Il détecte son matériel et demande un **code**. Un code correspond à une salle, donc à une image.
3. Il compare le PC à l'image et prévient clairement, mais poliment, quand le choix est risqué.
4. Sans code valide, il s'éteint après un message ferme et un compte à rebours.
5. Avec un code valide et deux confirmations, il restaure l'image, donne au PC son nom, le rattache au domaine et crée le compte administrateur habituel.

### 1.2 Pourquoi maintenant

| Fait | Source |
|---|---|
| Microsoft a annoncé le retrait de MDT le 6 janvier 2026 : plus de mises à jour, de correctifs ni de support. Les installations existantes continuent de fonctionner, mais les téléchargements peuvent disparaître. | https://learn.microsoft.com/en-us/troubleshoot/mem/configmgr/mdt/mdt-retirement |
| WDS (Windows Deployment Services) est déprécié à partir de la version de Windows Server qui suivra Windows Server 2025, y compris son démarrage PXE. Sur Windows Server 2019, le démarrage PXE avec une image de démarrage personnalisée reste pris en charge. Depuis la mise à jour d'avril 2026, WDS ne prend plus en charge par défaut le déploiement sans surveillance. | https://learn.microsoft.com/en-us/windows/deployment/wds-boot-support |
| Windows Server 2019 reste supporté (support étendu) jusqu'en janvier 2029. | https://learn.microsoft.com/en-us/lifecycle/products/windows-server-2019 |
| Windows 10 (version 22H2, la dernière) a atteint la fin de son support le 14 octobre 2025. | https://learn.microsoft.com/en-us/lifecycle/products/windows-10-home-and-pro |

Conclusion : Windows Server 2019 n'est pas à remplacer, mais le moteur de déploiement ne doit plus dépendre de MDT ni de WDS. Les solutions recommandées par Microsoft (Configuration Manager, Autopilot) demandent plus d'infrastructure et de licences et ne réutilisent pas directement les images Clonezilla existantes ; elles n'ont pas été étudiées en détail ici.

### 1.3 Hors périmètre

- Windows 7 (trop ancien).
- L'envoi simultané d'une image à plusieurs PC en un seul flux (multicast).
- Le chiffrement HTTPS intégré : à placer devant le service (section 6).

---

## 2. Comment ça marche

**L'image pour comprendre.** Un PC vide ressemble à un élève qui arrive le jour de la rentrée dans un établissement qu'il ne connaît pas. Il appelle dans le couloir : « Je suis nouveau, qui peut m'aider ? ». Le surveillant (le serveur DHCP, qui distribue déjà les adresses) lui répond : « Va au bureau de la vie scolaire, et demande le petit livret d'accueil. » Le livret (le mini-système) pose une question : « Quel est ton code de classe ? ». Selon la réponse, le bureau (le serveur de déploiement) remet le bon emploi du temps complet (l'image de la salle), l'aide à s'installer, puis lui colle son étiquette de nom et son carnet de liaison (nom du poste et rattachement au domaine).

L'histoire d'un PC en huit étapes :

1. **Démarrer par le réseau.** Le PC ne lance pas son Windows : il demande au réseau un programme de démarrage (technique appelée PXE).
2. **Charger le mini-système.** Le DHCP lui indique où aller. Un petit programme (iPXE) se charge, puis télécharge un mini-système Linux qui s'installe dans la mémoire du PC, sans rien écrire sur le disque.
3. **Identifier le PC.** Le programme lit marque, modèle, processeur, mémoire, disques, mode de démarrage.
4. **Saisir le code.** Frappe masquée. Code vide ou refusé trois fois : message, compte à rebours de 1 min 30, extinction.
5. **Lire le verdict.** Le serveur compare le PC à l'image et répond « convient », « faisable mais pas idéal » ou « déconseillé ». Dans les deux derniers cas, une confirmation est demandée (réponse par défaut : non).
6. **Nommer le poste et confirmer.** Le technicien tape le nom du PC, puis `OUI` en majuscules après avoir lu : « Le disque sera entièrement effacé ».
7. **Restaurer.** L'image arrive par le réseau, est décompressée au fil de l'eau et écrite sur le disque.
8. **Premier démarrage.** Windows démarre, applique le nom, rejoint le domaine et crée le compte administrateur local.

```
 PC à installer                  Serveur DHCP (existant)      Serveur de déploiement (10.0.0.3)
   │ 1. « Je démarre par le réseau (PXE) »
   │ ───────────────────────────────▶
   │ 2. « Adresse IP attribuée. Chargez ipxe.efi chez 10.0.0.3 »
   │ ◀───────────────────────────────
   │ 3. TFTP : télécharge un tout petit programme de démarrage (iPXE)
   │ ◀═══════════════════════════════════════════════════════════════
   │ 4. iPXE redemande au DHCP, puis lit  http://10.0.0.3:8080/boot.ipxe
   │ 5. HTTP : télécharge le noyau Linux et son système de départ ; ce dernier
   │    télécharge le système complet (~300 Mo) et le charge en mémoire
   │ ◀═══════════════════════════════════════════════════════════════
   │ 6. Le mini-système démarre ; le programme client prend l'écran
   │ 7. Le client détecte le PC et demande le CODE ────────────────▶ API (Python)
   │ 8. L'API vérifie le code et juge le PC ◀──────────────────────
   │ 9. Nom du poste, confirmation OUI, effacement du disque
   │10. Les fichiers de l'image arrivent un par un, décompressés au fil
   │    de l'eau, écrits sur les partitions ◀═════════════════════════
   │11. Dépôt du fichier de réglages (nom, domaine, administrateur), redémarrage
   │12. Windows termine sa configuration et rejoint le domaine
```

Ce que voit le technicien, copié d'une simulation :

```
----------------------------------------------------------------
  Ce poste est un peu juste pour Windows 11.
----------------------------------------------------------------
  - Le processeur (Intel Core i3-8100, 8ᵉ génération) est un Core i3, de la gamme
    d'entrée : Windows 11 fonctionnera mais les performances seront limitées.

  Caractéristiques du poste :
    * Modèle : HP Exemple 800 G4 SFF
    * Processeur : Intel Core i3-8100 (8ᵉ génération)
    * Mémoire : 8 Go
    * Disque : 256 Go (SSD)
    * Démarrage : UEFI
    * TPM : 2.0

Êtes-vous sûr(e) de votre choix ? (oui/NON) :
```

```
Il semblerait que vous tentiez de forcer le passage.
Merci de renoncer : ce poste ne sera pas installé.

        .-~~~~~-.
      .'   .-.   '.
     /    (   )    \
     \     '-'     /
      '.         .'
        '-.___.-'

   00:03   Allez vous faire cuire un œuf
```

Les trois ensembles de code du dépôt :

| Dossier | Rôle | Image |
|---|---|---|
| `server/` | L'API : vérifie les codes, juge les PC, fournit les images et les réglages, affiche les pages d'administration | Le bureau de la vie scolaire |
| `client/` | Le programme du mini-système : pose les questions, restaure | Le livret d'accueil |
| `boot/`, `live/`, `scripts/`, `deploy/` | Démarrage réseau, fabrication du mini-système, installation du serveur | Les panneaux, le plan et la clé du bureau |

---

## 3. Où héberger le service

### 3.1 Les deux options

| | Windows Server 2019 (cible) | VM Debian sur Proxmox (banc d'essai, puis option) |
|---|---|---|
| Rôle dans le projet | Hébergement visé en exploitation, sur un serveur Windows existant | Essai temporaire sur une VM et un PC dédié ; option à conserver seulement avec l'accord du gestionnaire du réseau |
| API Python | Installée comme tâche au démarrage de Windows (section 3.3) | Service systemd, installé par `scripts/setup-vm.sh` |
| Démarrage PXE / TFTP | Service TFTP de WDS en mode « serveur de transport », ou service TFTP tiers approuvé : à valider | Serveur TFTP dnsmasq, installé par le script |
| Fichier d'environnement | Lu grâce à la variable `MDTPROX_ENV_FILE` | `/etc/mdtprox.env` (systemd) |
| Fabrication du mini-système | Sur une machine Debian, puis copie de trois fichiers | Dans la VM elle-même |
| HTTPS | À placer devant le service (frontal approuvé) | Idem |
| Isolation, sauvegarde | Celles du serveur existant | Une VM se copie, se sauvegarde, se rejoue |
| Statut | Étapes manuelles écrites, jamais exécutées | Scripts écrits, jamais exécutés |

### 3.2 Ce qui ne change pas

Le programme client, l'API, le format des images Clonezilla et le mini-système Linux sont identiques dans les deux cas. Seuls changent le service qui lance l'API et le service qui distribue les premiers fichiers de démarrage.

### 3.3 Installation sur Windows Server 2019 (à valider sur Windows Server 2019)

Aucun script d'installation Windows n'existe : voici les étapes manuelles prévues. Elles n'ont pas été exécutées.

1. Installer Python 3.12 (64 bits) pour tous les utilisateurs.
2. Copier le dépôt dans `C:\mdtprox` et créer un dossier de données (par exemple `D:\mdtprox-data`) avec les sous-dossiers `images` et `live`.
3. Créer l'environnement Python et installer les dépendances :
   `py -3.12 -m venv C:\mdtprox\.venv`
   `C:\mdtprox\.venv\Scripts\pip install -r C:\mdtprox\server\requirements.txt`
4. Créer `C:\mdtprox\mdtprox.env` d'après `server\.env.example` (adresse du serveur, `DATA_DIR`, comptes). Restreindre ses droits :
   `icacls C:\mdtprox\mdtprox.env /inheritance:r /grant:r "SYSTEM:(R)" "Administrators:(F)"`
5. Indiquer le fichier au service : `setx /M MDTPROX_ENV_FILE C:\mdtprox\mdtprox.env`
6. Copier `boot\boot-linux.ipxe` sous le nom `boot.ipxe` dans le dossier de données, avec `profiles.yaml` (modèle : `server\profiles.example.yaml`).
7. Créer une tâche planifiée au démarrage, sous le compte SYSTEM :
   `schtasks /Create /TN "mdtprox-api" /SC ONSTART /RU SYSTEM /RL HIGHEST /TR "C:\mdtprox\.venv\Scripts\python.exe -m uvicorn --factory app.main:create_app --app-dir C:\mdtprox\server --host 0.0.0.0 --port 8080"`
   puis activer dans ses propriétés le redémarrage en cas d'échec.
8. Ouvrir le port dans le pare-feu, profil « domaine » seulement :
   `netsh advfirewall firewall add rule name="mdtprox-api" dir=in action=allow protocol=TCP localport=8080 profile=domain`
9. Choisir et valider le service TFTP qui distribue `ipxe.efi` et `undionly.kpxe` (WDS en transport seul, ou service tiers), ainsi que sa coexistence avec d'éventuels services WDS existants.
10. Fabriquer le mini-système sur une machine Debian (`scripts/build-live.sh`), puis copier `vmlinuz`, `initrd.img` et `filesystem.squashfs` dans le dossier `live` des données.
11. Configurer le DHCP avec `scripts/dhcp-pxe.ps1` : lecture seule d'abord (section 5, tâche T5).

### 3.4 Pourquoi essayer d'abord sur Proxmox

- Le serveur existant reste intact tant que le principe n'est pas validé.
- Une VM est jetable : en cas d'erreur, on la recrée en quelques minutes.
- Le banc se limite à une VM, un PC dédié et un réseau séparé (petit commutateur ou VLAN approuvé). Aucun changement général du DHCP n'est demandé.

**Une réussite sur Proxmox ne prouve pas que l'hébergement Windows fonctionne.** Une seconde recette de bout en bout sur Windows Server 2019 est obligatoire avant toute mise en service.

---

## 4. Décisions techniques

| # | Question | Décision | Pourquoi | Solutions écartées |
|---|---|---|---|---|
| D1 | Où héberger le service ? | Cible : Windows Server 2019. Essai : VM Debian sur Proxmox | Le serveur Windows existe déjà. Proxmox permet un essai isolé et jetable | Configuration Manager et Autopilot (recommandés par Microsoft, plus lourds, images Clonezilla non réutilisées directement) |
| D2 | Comment le PC trouve-t-il le serveur ? | Le DHCP de l'établissement annonce le serveur (options 66 et 67) dans des « politiques » réservées aux PC qui démarrent en PXE | Les PC des salles sont dans d'autres réseaux que le serveur : leur appel ne traverse pas les routeurs. Seul le DHCP central les atteint | DHCP d'appoint (proxyDHCP) : limité à un seul réseau, réservé au banc d'essai |
| D3 | Quel mini-système ? (décision révisée) | Un mini-système Linux (Debian « live ») qui restaure les images Clonezilla | Une image Clonezilla copie le disque entier, partition de démarrage comprise : il n'y a rien à reconstruire, donc plus besoin de WinPE. Linux détecte aussi mieux le matériel | WinPE (gardé en option pour des images .wim), Clonezilla Live tel quel (menus, pas de code, partage réseau ouvert à tout PC), FOG (complet, mais sans code de salle, pénalité ni jugement du matériel) |
| D4 | Quel langage ? | Python pour l'API et le client | Lisible, une seule compétence à maintenir. Le client n'utilise que la bibliothèque standard | PowerShell, C# |
| D5 | Comment l'image arrive-t-elle ? | Fichier par fichier en HTTP, avec reprise, décompressé et écrit au fil de l'eau (rien n'est stocké sur le PC) | Pas besoin de place sur le PC, pas de partage réseau ouvert, jeton et journal conservés | Partage NFS ou SMB (ouvert sans code) ; copie préalable de 47 Go sur le PC |
| D6 | Comment protéger les codes ? | Empreinte scrypt pour vérifier ; code en clair facultatif, seulement pour l'afficher aux administrateurs | L'empreinte ne se retrouve pas ; le code en clair, quand il est choisi, n'est visible qu'après identification | Codes en clair partout ; codes masqués par une opération réversible |
| D7 | Où mettre les secrets ? | Dans un fichier d'environnement du serveur, aux droits restreints | Jamais dans le code, jamais dans Git | Dans l'image (recopié sur tous les PC, impossible à changer) |
| D8 | Nom, domaine, administrateur : dans l'image ? | À l'installation, par un fichier de réponses `unattend.xml` fabriqué pour chaque PC | Le nom est propre à chaque PC ; la jonction crée un compte machine dans l'annuaire | Tout figer dans l'image (identités en double) |
| D9 | Que faire d'un PC mal adapté ? | Jugement côté serveur en trois niveaux (convient, faisable mais pas idéal, déconseillé), avec confirmation | Les règles évoluent : on les corrige à un seul endroit | Règles dans le client ; refus pur et simple |
| D10 | Qui protège les pages d'administration ? | Le serveur, par une fenêtre d'identifiants vérifiée à chaque requête | Une alerte dans la page ne protège rien | Un mot de passe caché dans la page ou dans un fichier accessible |

---

## 5. Les tâches

| # | Tâche | Statut |
|---|---|---|
| T1 | Dépôt et structure | Vérifié |
| T2 | L'API | Vérifié |
| T3 | Le jugement du matériel | Vérifié |
| T4 | Le client Linux de restauration | Vérifié ; matériel à vérifier |
| T5 | Le démarrage par le réseau et le DHCP | À vérifier sur banc |
| T6 | Le mini-système Linux | À vérifier sur banc |
| T7 | La VM Proxmox et l'installation du service | À vérifier sur banc |
| T8 | Nom du poste, domaine, administrateur | Vérifié ; matériel à vérifier |
| T9 | Les pages d'administration | Vérifié |
| T10 | Le chemin WinPE et les images .wim (option) | À vérifier sur banc |
| T11 | Les tests | Vérifié |
| T12 | Livraison et recette | À faire |

### T1 — Dépôt et structure

**En une phrase.** Ranger le projet pour que chacun s'y retrouve.

**L'image pour comprendre.** Poser des étagères étiquetées avant de ranger quoi que ce soit.

**Concrètement.**

| Dossier | Contenu |
|---|---|
| `server/` | L'API, ses outils (`hash_code.py`, `scan_images.py`), ses tests |
| `client/` | Le programme du PC à installer, ses tests |
| `boot/` | Scripts iPXE, configuration TFTP, démarrage WinPE |
| `live/` | Configuration de fabrication du mini-système Linux |
| `scripts/` | Installation du serveur, fabrication des mini-systèmes, DHCP, création de la VM |
| `deploy/` | Fichiers de service (systemd, dnsmasq) |
| `tests/` | Test de bout en bout et cohérence du mini-système |

**Les choix et pourquoi.**

- Un seul document de référence (ce fichier) : la documentation ne peut pas se contredire.
- Rien de secret dans Git : un mot de passe publié, même supprimé ensuite, reste dans l'historique. Le fichier `.gitignore` bloque les fichiers d'environnement, `profiles.yaml`, les journaux, les images et les caches.
- Le code est générique. Les valeurs propres à un établissement (adresses, domaine, noms de salles) restent dans sa configuration locale, jamais dans le dépôt public.

**Où en est-on.** Fait.

### T2 — L'API

**En une phrase.** Le programme qui tourne en permanence sur le serveur, répond aux questions du PC (« ce code est-il bon ? », « quelle image ? », « quels réglages ? ») et distribue les fichiers.

**L'image pour comprendre.** Le guichet de la vie scolaire. L'élève donne un code ; la personne de l'accueil regarde sa liste (les codes ne sont connus que sous forme d'empreintes illisibles), puis remet un ticket numéroté valable une heure (le jeton). Le reste (l'emploi du temps, la fiche de renseignements) n'est donné que contre ce ticket. Tout est noté dans un cahier (le journal).

**Concrètement.** Les portes de l'API (tableau complet en annexe A.6) :

| Porte | Rôle |
|---|---|
| `/healthz` | Répond « je fonctionne » |
| `/boot.ipxe` | Donne au PC le script de démarrage réseau |
| `/live/…`, `/winpe/…` | Donnent les fichiers du mini-système (Linux ou WinPE) |
| `/api/v1/session` | Reçoit le code et la description du PC ; répond avec le jeton, la salle, l'image et le verdict |
| `/api/v1/deploy/start` | Reçoit le nom du PC ; répond avec la liste des fichiers de l'image et le fichier de réglages |
| `/api/v1/images/…` | Distribue les fichiers de l'image, avec reprise |
| `/api/v1/deploy/report` | Reçoit « installé » ou « échec » |
| `/admin/…` | Pages d'administration (tâche T9) |

**Les choix et pourquoi.**

- **FastAPI** : court à écrire, gère nativement la reprise de téléchargement.
- **Empreintes scrypt** : un code se vérifie mais ne se retrouve pas. Le calcul est volontairement lent (environ 50 ms) pour décourager les essais en masse.
- **Blocage par adresse IP** : après 3 codes faux, l'adresse est bloquée 5 minutes (réglable). Sans cela, on pourrait redémarrer le PC en boucle.
- **Jeton aléatoire, valable une heure, lié à une seule image.**
- **Journal** `audit.jsonl` : une ligne par événement (qui, quelle salle, quel verdict, quand). Jamais de code, de mot de passe ni de jeton.
- **Pas de page de documentation automatique**, et l'en-tête `X-Forwarded-For` est ignoré : un PC pourrait le truquer pour échapper au blocage.
- **Secret manquant : le serveur démarre quand même** mais répond « non configuré » (HTTP 503), sans jamais écrire la valeur.
- **`profiles.yaml` est relu quand il change** : ajouter une salle ne demande pas de redémarrage.
- **Images Clonezilla** : le serveur lit lui-même les petits fichiers d'information du dossier (modèle d'origine, taille du disque d'origine, partitions, liste des fichiers) et ne lit jamais les gros fichiers. Il ne calcule pas d'empreinte pour ces images (47 Go à hacher) : `zstd`, `gzip` et `partclone` vérifient eux-mêmes l'intégrité à la décompression.

**Où en est-on.** Plus de 150 tests automatiques, plus un test facultatif qui relit de vraies images quand on lui indique leur dossier (`MDTPROX_REAL_IMAGES`). Limites : un seul processus (les compteurs de blocage sont en mémoire et repartent à zéro au redémarrage) ; l'API distribue elle-même les images, ce qui limite le débit quand beaucoup de PC téléchargent ensemble.

### T3 — Le jugement du matériel

**En une phrase.** Comparer le PC à l'image choisie et répondre : « convient », « faisable mais pas idéal » ou « déconseillé », toujours avec les raisons, sans jamais empêcher de continuer quand c'est possible.

**L'image pour comprendre.** Le conseiller d'orientation : il ne vous interdit pas une filière, mais il vous dit calmement ce qui risque de poser problème et demande : « Êtes-vous sûr de votre choix ? ».

**Concrètement.** Trois niveaux, calculés par `server/app/hardware.py` :

| Niveau | Sens | Ce que voit la personne |
|---|---|---|
| `ok` | Aucun souci | Rien, on passe à la suite |
| `warn` | Faisable, pas idéal | Titre, raisons, question « Êtes-vous sûr(e) de votre choix ? » |
| `discouraged` | Très mauvaise idée | Titre, toutes les raisons, question appuyée |

Règles liées au système de l'image :

| Cible | Déconseillé si… | À éviter si… |
|---|---|---|
| Windows 11 | processeur Intel antérieur à la 8ᵉ génération, ou AMD antérieur à Ryzen 2000 ; pas de TPM 2.0 ; BIOS classique ; moins de 4 Go de mémoire ; disque de moins de 64 Go ; BIOS de 2016 ou avant | Core i3 (même récent) ; moins de 8 Go ; disque mécanique ; BIOS de 2017-2018 |
| Windows 10 | moins de 2 Go ; disque de moins de 32 Go ; processeur antérieur à la 4ᵉ génération ; BIOS de 2012 ou avant | moins de 4 Go ; disque mécanique ; Celeron, Pentium ou Atom |

Règles liées à l'image Clonezilla elle-même :

| Cas | Verdict | Message |
|---|---|---|
| Même modèle que le PC d'origine | rien | — |
| Même modèle, autre format (tour, petit format…) | `warn` | « Cette image est prévue pour un format de poste un peu différent. » |
| Autre modèle (par exemple un 600 G3 pour une image préparée pour un 400 G7) | `discouraged` | « Cette image n'a pas été préparée pour ce poste. » + les deux modèles |
| Disque plus petit que le disque d'origine | `discouraged` et bloquant | « Le disque du poste (128 Go) est plus petit que le disque d'origine de l'image (256 Go) : la restauration ne pourra pas tenir dessus. » |

**Les choix et pourquoi.**

- **Le jugement vit dans l'API** : les règles évoluent (nouveaux modèles, nouvelles versions de Windows) et se corrigent en un seul fichier, sans refabriquer le mini-système.
- **Le pire motif l'emporte.**
- **On ne condamne jamais sur une incertitude** : une information absente (par exemple le TPM) donne « Ce poste n'a pas pu être vérifié entièrement » (`warn`), pas « déconseillé ».
- **Tolérance de 15 % sur la mémoire et le disque** : un PC de 4 Go en affiche 3,5 une fois le système chargé.
- **Comparaison des modèles par morceaux** (famille, numéro, génération, format) : un même modèle s'écrit de plusieurs façons (« Small Form Factor PC », « SFF »). Un nom trop différent pour être comparé donne un simple avertissement.
- **Taille du disque stricte** : une restauration Clonezilla échoue si le disque cible est plus petit que l'original, même de quelques Mo (la partition de récupération est à la fin du disque).
- **Le modèle d'origine est lu dans l'image** (`Info-dmi.txt`), pas dans le nom du dossier : les noms se révèlent peu fiables (annexe B.3).
- **Ton diplomatique** : jamais de moquerie, raisons concrètes, caractéristiques réelles du PC affichées.

**Où en est-on.** Testé avec des matériels types (PC récent, i3, vieux modèle, champs manquants) en Windows 10 et 11, et avec des images de modèles différents. Limites : les PC dont le processeur date d'avant la 8ᵉ génération sont jugés « déconseillés » pour Windows 11, conformément aux exigences de Microsoft ; sous Linux, la détection du disque mécanique et du TPM est possible, pas sous WinPE ; Windows 10 a atteint la fin de son support en octobre 2025, ce qui relève d'une décision de politique et pas du code.

### T4 — Le client Linux de restauration

**En une phrase.** Le programme que l'on voit à l'écran sur le PC à installer : il détecte le matériel, demande le code, affiche le verdict, demande le nom du PC, puis restaure l'image.

**L'image pour comprendre.** Un déménageur qui remonte un appartement à l'identique d'après des photos. Il trace d'abord le plan des pièces (la table de partitions), remet les meubles pièce par pièce (les partitions) sans décharger tout le camion dans le jardin (rien n'est stocké), rend l'appartement habitable (le démarrage) et glisse la fiche d'état civil sous la porte (le fichier de réglages).

**Concrètement.** Le fichier `client/linux_restore.py` enchaîne cinq étapes numérotées à l'écran :

1. **Préparation du disque.** Le client télécharge la sauvegarde de la table de partitions (`<disque>-gpt.gdisk`, produite par `sgdisk -b`), efface le disque, recrée la même table (`sgdisk --load-backup`) et replace la copie de secours en fin de disque (`sgdisk -e`).
2. **Restauration des partitions.** Chaque fichier arrive par HTTP avec le jeton, traverse `zstd` ou `gzip`, puis `partclone` qui écrit dans la partition. Une coupure réseau provoque une reprise là où le flux s'était arrêté. Les images découpées en plusieurs morceaux (`.aa`, `.ab`) sont mises bout à bout.
3. **Démarrage UEFI.** Le chemin de secours `\EFI\BOOT\BOOTX64.EFI` est copié depuis `bootmgfw.efi` s'il manque, et une entrée « Windows Boot Manager » est créée (`efibootmgr`).
4. **Fichiers de réglages.** La partition Windows est montée en écriture (`ntfs-3g`) et reçoit `Unattend.xml` et `SetupComplete.cmd` fabriqués par l'API (tâche T8).
5. **Nettoyage.** `sync`, démontage, rapport au serveur, redémarrage.

Avant ces étapes, le déroulé commun à l'écran est :

1. Attente du réseau et de l'API (jusqu'à environ une minute).
2. Détection du matériel (sous Linux : fichiers `/sys` et `/proc`).
3. Saisie du code, masquée par des `*`.
4. **Pénalité** si le code est vide, refusé trois fois ou bloqué par le serveur : message, puis compte à rebours de 90 secondes avec « Allez vous faire cuire un œuf », puis extinction.
5. Affichage du verdict (tâche T3). Si ce n'est pas « convient », confirmation avec « non » par défaut ; un refus ramène à la saisie du code, sans pénalité. Un verdict bloquant ne propose même pas de continuer.
6. Choix du disque, saisie du nom du poste (préfixe de la salle suggéré), vérification du nom (1 à 15 caractères, lettres, chiffres et tirets, ni au début ni à la fin, pas uniquement des chiffres).
7. Récapitulatif, puis `OUI` en majuscules exigé après le message « Le disque … sera ENTIÈREMENT EFFACÉ ». Tout autre texte annule sans rien toucher.

**Les choix et pourquoi.**

- **On refait la restauration plutôt que d'appeler `ocs-sr`** (l'outil de Clonezilla) : il lit l'image dans un dossier local ou monté, ce qui obligerait soit à copier toute l'image sur le PC, soit à ouvrir un partage réseau sans code où n'importe quel PC en PXE pourrait tout lire. Notre flux passe par l'API : jeton, journal, reprise. Contrepartie : dépendance au format des fichiers Clonezilla (annexe B).
- **`sgdisk --load-backup`** plutôt que de refaire les partitions à la main : le fichier contient la table exacte (types, identifiants, tailles).
- **Les partitions « dd » sont écrites telles quelles** : l'image contient parfois une partition de 16 Mo copiée brute, sans en-tête `partclone`. Le client regarde le début du flux : signature `partclone-image` présente, il utilise `partclone.dd` ; sinon il écrit les octets avec `dd`. Une erreur ici corromprait la partition en silence.
- **Deux chemins de démarrage UEFI** (fichier de secours et entrée dans le firmware) : un PC neuf ne connaît pas encore « Windows Boot Manager », et les firmwares diffèrent.
- **Noms de fichiers insensibles à la casse** pour retrouver `Windows`, `windows` ou `WINDOWS`.
- **Veille prolongée** : si `ntfs-3g` refuse d'écrire, le client réessaie en supprimant le fichier de veille, sans risque puisque le disque vient d'être restauré à l'identique.
- **Aucun mode « oui à tout »**. Le matériel simulé (`--simulate`) n'est accepté qu'avec `--dry-run` : on ne peut pas effacer un vrai disque en croyant simuler.
- **Rien n'affiche ni n'enregistre les mots de passe** du fichier de réglages.
- **Les clés USB ne sont jamais proposées comme disque cible.**

**Garde-fous** (le client s'arrête avant de toucher au disque) : PC en BIOS classique (les images sont en UEFI) ; image qui n'est pas en GPT ; disque plus petit que l'original (octets exacts) ; outil manquant dans le mini-système (la liste est donnée) ; cible qui n'est pas un disque ou déjà montée ; nom de disque suspect ; verdict bloquant du serveur.

**Où en est-on.** 96 tests côté client, avec de faux outils (`sgdisk`, `partclone`, `mount`… remplacés par de petits scripts qui notent leurs appels) et un vrai serveur HTTP : ordre des commandes, partitions écrites octet pour octet, coupure réseau au milieu d'un fichier (la reprise redonne exactement les mêmes octets), partition « dd » brute ou `partclone`, échec de `partclone`, veille prolongée, garde-fous, mode simulation. Un test facultatif (`MDTPROX_REAL_IMAGE`) fait passer de vrais fichiers d'image dans les vrais décompresseurs.

À vérifier sur banc : options de la version de `partclone` installée ; `sgdisk -e` sur un disque plus grand que l'original ; création de l'entrée UEFI ; montage en écriture d'une partition NTFS tout juste restaurée ; apparition des partitions (`partprobe`) ; débit réel sur une image de plusieurs dizaines de Go ; démarrage de Windows sans demande de réparation. Deux limites : le fichier de réglages n'est lu que si l'image a été généralisée avec `sysprep` (tâche T8, question Q1) ; sur un disque plus grand que l'original, l'espace supplémentaire reste inutilisé (question Q10).

### T5 — Le démarrage par le réseau et le DHCP

**En une phrase.** Aiguiller automatiquement un PC vide qui démarre par le réseau vers le serveur de déploiement, sans toucher au PC.

**L'image pour comprendre.** L'élève perdu ne sait lire qu'un papier très court. Le surveillant (le DHCP) lui tend ce papier : « va au bureau 10.0.0.3 et demande le programme `ipxe.efi` ». Ce programme (iPXE) le rend plus malin : il sait lire de grands documents (HTTP). Il retourne voir le surveillant, qui lui donne l'adresse de la page `boot.ipxe` ; cette page dit quoi télécharger pour démarrer.

**Concrètement.**

| Fichier | Rôle |
|---|---|
| `scripts/dhcp-pxe.ps1` | À lancer sur le serveur DHCP Windows : apprend au DHCP à aiguiller, uniquement les PC qui démarrent en PXE |
| `boot/dnsmasq-tftp.conf` | Serveur TFTP de la VM (donne `ipxe.efi`, `undionly.kpxe`) |
| `boot/boot-linux.ipxe` | Script lu par iPXE pour le mini-système Linux (`boot/boot.ipxe` pour WinPE) |
| `boot/dnsmasq-lab.conf` | DHCP et TFTP du banc d'essai, limités à la carte réseau du réseau isolé |

Le script DHCP a trois modes, du plus prudent au plus actif :

1. **Sans option : lecture seule.** Il n'écrit rien et affiche les étendues (une par réseau), les options 66 et 67 en place et les politiques existantes.
2. **`-Apply -ScopeId … -ServerIp …`** : crée quatre « politiques » (règles du type « si le PC est de telle sorte, lui donner telle option ») pour l'étendue choisie. Il demande `OUI` en majuscules, accepte `-WhatIf` pour simuler, et ne touche ni aux autres options ni aux autres PC.
3. **`-Remove`** : retire exactement ce qu'il a créé (repéré par la marque `[MDTPROX]` dans la description).

Les quatre politiques, dans cet ordre de priorité :

| Ordre | Pour qui | Réponse |
|---|---|---|
| 1 | Un client déjà iPXE | « Lisez `http://10.0.0.3:8080/boot.ipxe` » |
| 2 et 3 | Un PC UEFI (deux codes d'architecture possibles) | « Serveur 10.0.0.3, fichier `ipxe.efi` » |
| 4 | Un PC en BIOS classique | « Serveur 10.0.0.3, fichier `undionly.kpxe` » |

**Pourquoi l'ordre compte : la boucle infinie.** Un iPXE qui se réveille refait une demande DHCP et se présente aussi comme un PC UEFI. S'il recevait de nouveau « cherchez `ipxe.efi` », il se rechargerait sans fin. La règle 1 doit donc passer avant les autres. C'est le premier point à contrôler sur banc.

**Les choix et pourquoi.**

- **DHCP central en exploitation, DHCP d'essai sur le banc seulement** : l'appel d'un PC ne traverse pas les routeurs ; seul le DHCP qui sert déjà tous les réseaux l'atteint. Le DHCP d'essai de `boot/dnsmasq-lab.conf` ne couvre que le réseau isolé du banc : il est lié à une seule carte réseau et n'annonce aucune passerelle.
- **Politiques par étendue, pas options globales** : seuls les PC en PXE sont concernés, et on active réseau par réseau.
- **Le TFTP du serveur ne contient volontairement aucune ligne de DHCP** : il ne peut pas se mettre à distribuer des adresses en concurrence avec le DHCP de l'établissement.
- **Un même script iPXE pour BIOS et UEFI.**
- **En cas d'échec, `boot-linux.ipxe` s'arrête et l'écrit** (message, 15 secondes, invite iPXE) au lieu de redémarrer en boucle.

**Où en est-on.** Écrit et relu, jamais exécuté sur du matériel (le script PowerShell n'a pas été lancé sur un Windows Server). À vérifier sur banc : l'ordre de priorité des politiques, la syntaxe exacte de `Add-DhcpServerv4Policy`, le démarrage UEFI et BIOS. Limite : l'`ipxe.efi` fourni par Debian n'est pas signé pour Secure Boot ; il faut désactiver Secure Boot sur les PC démarrés en PXE. Un `snponly.efi` est fourni en repli pour les cartes réseau que `ipxe.efi` ne reconnaît pas.

### T6 — Le mini-système Linux

**En une phrase.** Fabriquer le petit système Linux qui démarre par le réseau, affiche l'écran du client et fait tourner la restauration.

**L'image pour comprendre.** Un livret d'accueil rédigé sur mesure, avec uniquement ce dont le client a besoin : ni bureau, ni navigateur, ni accès à distance, ni invite de connexion.

**Concrètement.**

| Fichier | Rôle |
|---|---|
| `live/auto/config` | Réglages de fabrication (Debian 12, miroir, pas d'ISO) |
| `live/config/package-lists/mdtprox.list.chroot` | Liste de paquets : exactement ce que le client exige (`sgdisk`, `partclone`, `ntfs-3g`, `zstd`, `efibootmgr`, `python3`…) |
| `live/config/includes.chroot/…` | Fichiers ajoutés : lanceur `start.sh`, service `mdtprox-client`, réseau DHCP, clavier AZERTY, police avec le « œ » |
| `live/config/hooks/normal/9000-mdtprox.hook.chroot` | Dernier réglage : active le réseau et le client, supprime les invites de connexion |
| `boot/boot-linux.ipxe` | Script lu par iPXE (noyau, système de départ, paramètres) |
| `scripts/build-live.sh` | Fabrique `vmlinuz`, `initrd.img` et `filesystem.squashfs` |
| route `/live/…` | Sert ces trois fichiers aux PC, avec reprise |

Pour fabriquer le mini-système (sur une machine Debian, après `setup-vm.sh`) : `scripts/build-live.sh`. Le téléchargement est de l'ordre de 400 Mo et la fabrication de 10 à 30 minutes (estimations à confirmer au premier essai, comme les 300 Mo du système final). Aucune ISO Windows n'est nécessaire.

Au démarrage, iPXE charge le noyau et le système de départ (`initrd`), qui télécharge le système complet (`filesystem.squashfs`) et le charge en mémoire (paramètre `fetch=`). Le service `mdtprox-client` prend alors la console 1 et lance `start.sh`, qui lit l'adresse du serveur dans la ligne de commande du noyau (`mdtprox_server=`).

**Les choix et pourquoi.**

- **Debian « live » fabriqué avec `live-build`** : outil standard de Debian, paquets officiels signés, fabrication reproductible. Écartés : Clonezilla Live tel quel ; Alpine (`partclone`, `ntfs-3g` et micrologiciels y sont moins courants).
- **Système chargé en mémoire** plutôt que monté depuis un partage : même mécanique HTTP que le reste, rien à ouvrir de plus. Contrepartie : au moins 2 Go de mémoire sur le PC.
- **L'adresse du serveur n'est pas écrite dans l'image** : `boot-linux.ipxe` la passe au noyau. Le même mini-système sert donc au banc d'essai et à la production.
- **Le client est recopié dans l'image** : après toute modification de `client/`, relancer `build-live.sh`. Choix de simplicité et de sécurité (pas de code téléchargé puis exécuté en administrateur sans contrôle), au prix de 10 à 30 minutes par changement.
- **Aucune invite de connexion** (les six consoles sont désactivées) : une personne devant le PC ne peut pas ouvrir de console avec Alt+F2. Le dépannage passe par le paramètre `mdtprox_debug`, à ajouter à la main dans l'invite iPXE ; il ouvre un shell au lieu du client. Quelqu'un de très motivé peut interrompre iPXE et ajouter ce paramètre, mais il pourrait aussi démarrer une clé USB : la vraie protection est un mot de passe du BIOS et l'ordre de démarrage « disque d'abord » (section 6).
- **Rien d'inutile** : ni bureau, ni SSH, ni `sudo`, ni gestionnaire de réseau (`systemd-networkd` suffit). Un test vérifie cette absence.
- **Clavier AZERTY et police Latin-9** pour taper le code et afficher « œuf ».
- **Micrologiciels réseau Realtek et Broadcom inclus** : les cartes Intel n'en ont pas besoin, certaines Realtek oui.
- **`build-live.sh` est prudent** : il refuse de supprimer son dossier de travail s'il reste des montages (sinon il pourrait effacer des fichiers du système), ne remplace `live/` qu'une fois la fabrication réussie et en un seul geste, vérifie la taille des trois fichiers et garde le journal dans `/var/log/mdtprox-live-build.log`.
- **Debian 12 par défaut** (`LB_DIST=trixie` pour une version plus récente). Risque connu (question Q8) : les images ont pu être faites avec `partclone` 0.3.37, alors que Debian 12 en fournit une version plus ancienne (0.3.23, selon https://packages.debian.org/bookworm/partclone ; à vérifier par `apt policy partclone`). Le format d'image est le même (« version 0002 » dans l'en-tête), mais seul le banc le confirmera.

**Ce que le mini-système doit contenir** (le client refuse de démarrer sans) : `python3` (3.9 ou plus), `sgdisk` (paquet `gdisk`), `wipefs`, `blockdev`, `mount` et `umount` (`util-linux`), `partprobe` (`parted`), `mount.ntfs-3g` (`ntfs-3g`), `partclone` (au moins `partclone.ntfs` et `partclone.vfat`), `zstd`, `gzip`, `dd`, `sync`, avec `efibootmgr` recommandé.

**Où en est-on.** 22 tests de cohérence : chaque programme exigé par le client figure dans la liste de paquets ; le lanceur passe l'adresse du serveur au client, ouvre un shell en mode dépannage ou après une erreur, et ignore un paramètre au nom voisin ; le script iPXE est complet ; `shellcheck` ne donne aucun avertissement sur les scripts ; la fabrication ne supprime jamais un dossier qui a encore des montages. Jamais construit ni démarré. À vérifier sur banc : que `lb build` réussit sur Debian 12 avec `--binary-images netboot` (le script cherche les fichiers là où live-build les place, ou dans l'archive qu'il produit) ; que le noyau démarre par iPXE en UEFI et en BIOS ; que `fetch=` et `ip=dhcp` fonctionnent ; la reconnaissance des cartes réseau ; la taille réelle du système et la mémoire consommée.

### T7 — La VM Proxmox et l'installation du service

**En une phrase.** Créer la VM Debian dans Proxmox, puis y installer tout le service avec des scripts relançables sans rien casser.

**L'image pour comprendre.** Monter un bureau dans un local déjà câblé : `proxmox-create-vm.sh` livre le bureau vide, `setup-vm.sh` installe les meubles et branche la lumière.

**Concrètement.**

- **`scripts/proxmox-create-vm.sh`**, à lancer sur l'hôte Proxmox : télécharge l'image officielle Debian 12 « cloud », vérifie son empreinte SHA-512, crée la VM (2 processeurs, 4 Go de mémoire, disque de 200 Go réglable, pont `vmbr0`, adresse IP fixe donnée par *cloud-init*) et ne la démarre pas. Il refuse de s'exécuter si le numéro de VM existe déjà ou si l'adresse IP répond déjà au ping.
- **`scripts/setup-vm.sh`**, à lancer dans la VM en administrateur : installe les paquets, crée l'utilisateur système `mdt`, place le dépôt dans `/opt/mdtprox`, crée `/srv/deploy` et `/srv/tftp`, télécharge `wimboot` (empreinte vérifiée), installe le service `mdtprox-api` et le TFTP, ouvre les ports seulement si un pare-feu `ufw` est déjà actif. La variable `MINI_OS` choisit le script iPXE : `linux` (défaut, images Clonezilla) ou `winpe` (images .wim). Il ne remplace jamais un `/etc/mdtprox.env` ou un `profiles.yaml` existant, et ne démarre pas l'API tant qu'il reste « CHANGEZ-MOI » dans le fichier d'environnement.
- **`deploy/mdtprox-api.service`** : lance l'API au démarrage, avec durcissement (utilisateur sans droits, disque en lecture seule sauf `/srv/deploy`, aucun privilège).

**Les choix et pourquoi.**

- **Debian 12 et image « cloud »** : stable, gratuite, et *cloud-init* règle l'IP et la clé SSH au premier démarrage.
- **Un seul processus** pour l'API : le blocage des mauvais codes est en mémoire.
- **Scripts idempotents** : on peut les relancer, le résultat est le même.
- **Rien n'est démarré ni écrasé sans l'annoncer** ; chaque script s'arrête au premier problème.
- **L'installation de Proxmox lui-même n'est pas scriptée** : elle se fait une fois depuis une clé USB ou la console du serveur.

**Où en est-on.** `bash -n` et `shellcheck` ne donnent aucun avertissement. Jamais exécutés (ils modifient un serveur). À vérifier sur banc : la syntaxe des commandes `qm` de la version de Proxmox en place, l'étiquette de VLAN du port du commutateur, la configuration du pare-feu de la VM.

### T8 — Nom du poste, domaine, administrateur

**En une phrase.** Faire en sorte que le PC, une fois l'image posée, se retrouve avec son propre nom, inscrit dans l'annuaire, avec le compte administrateur habituel, sans que personne ne clique.

**L'image pour comprendre.** L'image est un clone parfait de l'élève modèle : mêmes cours, mêmes réglages. Deux clones ne peuvent pas porter le même nom ni le même numéro d'élève. On leur remet donc une enveloppe (`unattend.xml`) : « ton nom est ROOMA-01 ; inscris-toi au registre avec ce compte ; ton administrateur est celui-ci ». Windows l'ouvre au premier démarrage, applique tout, puis détruit l'enveloppe (elle contient des mots de passe).

**Concrètement.**

1. Après un code valide et une confirmation, l'API fabrique l'enveloppe pour ce PC (`POST /api/v1/deploy/start`) : nom du poste saisi, fuseau horaire et langue, jonction au domaine avec le compte de jonction (et l'unité d'organisation de la salle si elle est indiquée), compte administrateur local, instruction de sauter les écrans de première configuration.
2. Le client dépose `Unattend.xml` dans `Windows\Panther\Unattend\` et `SetupComplete.cmd` dans `Windows\Setup\Scripts\`. Au premier démarrage, Windows lit l'enveloppe ; à la fin, `SetupComplete.cmd` supprime les fichiers de réponses puis se supprime.
3. Les identifiants viennent du fichier d'environnement du serveur et sont les mêmes pour tous les PC : `DOMAIN_JOIN_USER`, `DOMAIN_JOIN_PASSWORD`, `LOCAL_ADMIN_USER`, `LOCAL_ADMIN_PASSWORD` (annexe A.3).

**Peut-on tout pré-paramétrer dans l'image ?**

| Élément | Dans l'image ? | Pourquoi |
|---|---|---|
| Logiciels, réglages, fond d'écran, langue | Oui | C'est le but d'une image |
| Nom du poste | Non | Chaque PC doit avoir un nom différent |
| Jonction au domaine | Non | Elle crée un compte machine propre à chaque PC ; une image « déjà jointe » donnerait des identités en double |
| Administrateur local | Possible, non retenu | Son mot de passe serait figé dans l'image, recopié partout et impossible à changer sans la refaire |

**Les choix et pourquoi.**

- **Les images doivent être généralisées avec `sysprep`** (`sysprep /generalize /oobe /shutdown`) avant capture. Sans cela, Windows ne relit pas l'enveloppe au premier démarrage : le PC garde son ancien nom et ne rejoint pas le domaine. C'est à vérifier pour chaque image (question Q1). Pour une image non généralisée, un script de premier démarrage serait nécessaire (section 11).
- **Tout ce qui entre dans le XML est protégé** : un mot de passe comme `A&B<c>"d'` casserait le fichier sinon. Un test vérifie que le XML reste valide ; les lettres accentuées sont écrites en entités.
- **Un compte de jonction dédié et limité** (droit de créer des ordinateurs dans les unités d'organisation des salles, jamais administrateur du domaine) : s'il fuite, on ne peut qu'ajouter des PC.
- **Les mots de passe sont en clair dans l'enveloppe** (seul format accepté par Windows). L'enveloppe n'est donnée qu'après un code valide et une confirmation, et détruite au premier démarrage. Pendant quelques minutes, ils sont lisibles sur le disque du PC en cours d'installation.
- **Un nom de poste ne peut pas finir par un tiret** : Windows et le DNS le refusent.

**Où en est-on.** Génération et validité du XML testées. À vérifier sur banc : exécution de `SetupComplete.cmd` après le premier démarrage ; jonction au domaine depuis un PC fraîchement installé (réseau disponible pendant la phase de personnalisation) ; droits du compte de jonction sur l'unité d'organisation.

### T9 — Les pages d'administration

**En une phrase.** Permettre à un administrateur identifié de consulter l'état des images et les codes de salle.

**L'image pour comprendre.** Le coffre de la vie scolaire : tout le monde voit qu'il existe, mais il ne s'ouvre qu'avec la bonne clé, vérifiée par le gardien, pas par un panneau « entrée autorisée ».

**Concrètement.**

- **`/admin/images`** : ouvre la fenêtre d'identifiants du navigateur. Après identification, la page liste chaque salle, son image (type, taille, modèle d'origine, présente ou absente du serveur) et son code, replié derrière « Afficher le code ». Une salle déclarée avec son seul empreinte (`code_hash`) affiche « non affichable ».
- **Comptes** : pour les premiers essais, `ADMIN_USER` et `ADMIN_PASSWORD` dans le fichier d'environnement. À terme, les identifiants seront vérifiés auprès de l'annuaire (Active Directory) avec un groupe autorisé, sans jamais récupérer ni conserver les mots de passe de l'annuaire.
- **Compte de démonstration** : `admin` / `pikachu`, accepté seulement quand `LAB_MODE=1` (banc d'essai isolé). Pourquoi Pikachu ? L'auteur avait une Nintendo DS avec Pikachu devant lui au moment de la rédaction. Hors banc, les pages refusent de s'ouvrir tant que le mot de passe est de démonstration (`pikachu`, `admin`, `azerty`…) ou fait moins de 10 caractères.

**Les choix et pourquoi.**

- **Le serveur décide.** Les identifiants sont vérifiés à chaque requête ; aucun code n'est présent dans la page avant l'identification.
- **Aucune mise en cache, aucun référencement** : les réponses portent `Cache-Control: no-store` et `X-Robots-Tag: noindex` ; la page refuse d'être affichée dans un cadre et n'exécute aucun script.
- **Blocage après trop d'essais** (même règle que pour les codes de salle).
- **Journal** : chaque ouverture de page est notée (qui, quelle page), jamais un code ni un mot de passe.
- **Tout ce qui s'affiche est protégé** contre l'injection de code (un nom de salle contenant des balises s'affiche tel quel).
- **Code en clair facultatif** : une salle peut être déclarée avec `code` (affichable) ou `code_hash` (non affichable). Le serveur calcule lui-même l'empreinte quand seul `code` est donné.
- **Avertissement visible en HTTP** : une bannière rappelle que les identifiants circulent en clair hors HTTPS.

**Où en est-on.** Accès, codes, blocage, refus des mots de passe de démonstration hors banc, échappement et journal sont testés (fichier `server/tests/test_admin.py`). Limites : le navigateur garde les identifiants jusqu'à sa fermeture ; en HTTP ils circulent en clair ; un code conservé en clair dans `profiles.yaml` impose de restreindre les droits du fichier (lecture par le seul compte du service).

### T10 — Le chemin WinPE et les images .wim (option)

**En une phrase.** Conserver la possibilité d'installer des images au format Windows (.wim) avec un mini-Windows, pour les établissements qui n'ont pas d'images Clonezilla.

**L'image pour comprendre.** L'intérimaire : un mini-Windows temporaire qui sait partitionner, copier et installer, auquel on remet une fiche de poste (le client) et une boîte à outils (Python).

**Concrètement.** `scripts/build-winpe.sh` (dans la VM) prend une ISO Windows 10 ou 11 officielle fournie par l'établissement et l'adresse du serveur, et fabrique `boot.wim`, `BCD`, `boot.sdi`, `bootmgr` et `wimboot` dans `winpe/`. Il extrait l'image « Setup » de l'ISO, la réduit à une seule image amorçable, y ajoute le client, un Python « embarqué » (version figée, empreinte vérifiée) et `startnet.cmd`, et neutralise `winpeshl.ini` (sinon l'installeur Windows démarrerait à la place du client). Le client applique ensuite l'image avec `diskpart`, `dism` et `bcdboot`. Le chemin est activé par `MINI_OS=winpe`.

**Les choix et pourquoi.**

- **Fabriqué à partir de l'ISO de l'établissement et jamais publié** : `boot.wim` est un produit Microsoft.
- **`wimlib-imagex`** (Linux) plutôt que l'outillage Windows : une machine Debian suffit.
- **Empreintes vérifiées** pour `wimboot` et Python : un programme exécuté au démarrage de tous les PC doit être contrôlé.
- **L'adresse du serveur est écrite dans le WinPE** : il faut le refabriquer pour passer du banc à la production (contrairement au mini-système Linux).

**Où en est-on.** Écrit, testé sur des données factices, jamais exécuté avec une vraie ISO. Sous WinPE, le client ne sait pas détecter le disque mécanique ni l'absence de TPM, donc ces deux règles du jugement du matériel ne se déclenchent pas.

### T11 — Les tests

**En une phrase.** Vérifier que le serveur et le client, écrits séparément, se comprennent, en les faisant tourner ensemble sur un ordinateur de développement.

**L'image pour comprendre.** La répétition générale : on ne joue pas dans le vrai bâtiment (vrai matériel, vrai réseau), mais on s'assure que les deux équipes jouent la même pièce.

**Concrètement.** Le fichier `tests/test_e2e_simulation.py` démarre le vrai serveur sur un port local avec des données jetables, puis lance le vrai client en simulation (`--simulate … --dry-run`) en lui tapant les réponses. Neuf scénarios :

| Scénario | Résultat attendu |
|---|---|
| PC récent, code de salle | Passe sans avertissement jusqu'au démarrage de l'installation |
| Vieux PC avec une image Windows 11, confirmation donnée | Verdict « fortement déconseillé », puis passe |
| Même PC, confirmation refusée | Rien n'est démarré |
| Code vide | « Allez vous faire cuire un œuf », extinction |
| Image Clonezilla, PC du même modèle | Plan de restauration affiché, aucun avertissement |
| Image Clonezilla, PC d'un autre modèle | « Cette image n'a pas été préparée pour ce poste », puis passe si confirmé |
| Image Clonezilla, PC en BIOS | Refusé avant toute action |
| Image Clonezilla, disque trop petit | Bloqué par le serveur |
| Trois mauvais codes | Pénalité, puis blocage de l'adresse : même le bon code est refusé |

Dans tous les cas, aucun mot de passe ni code n'apparaît à l'écran.

Pour tout relancer, depuis la racine du dépôt :

```bash
bash tests/run_all.sh
```

Les tests du client et le test de bout en bout ouvrent un port réseau local. Deux variables facultatives relisent de vraies images en lecture seule : `MDTPROX_REAL_IMAGES` (dossier contenant des images) et `MDTPROX_REAL_IMAGE` (un dossier d'image).

**Où en est-on.** Plus de 150 tests côté serveur, 96 côté client (chacun avec un test facultatif sur de vraies images), 31 de bout en bout et de cohérence du mini-système.

### T12 — Livraison et recette

**En une phrase.** Permettre à un établissement et au gestionnaire de son réseau de reprendre le code et de l'essayer sans risque.

**Concrètement.**

- **Le dépôt public est générique.** Il ne contient aucune donnée propre à un établissement : ni adresse, ni domaine, ni nom de machine ou de salle, ni image, ni secret. Il est préparé à partir du dépôt de travail : les données locales en sont retirées, puis un contrôle automatique vérifie qu'il n'en reste aucune.
- **La configuration de l'établissement est livrée à part** (modèles de fichier d'environnement et de `profiles.yaml`, schémas réseau).
- **La recette** suit le plan et la check-list de la section 9 : banc d'essai isolé, puis une seule étendue du réseau réel, puis bilan.

**Où en est-on.** Le plan d'essai est écrit ; la recette n'a pas eu lieu.

---

## 6. Sécurité et limites

Aucun système n'est « sûr » ou « pas sûr » : il est plus ou moins difficile à abuser. Voici ce qui a été fait et ce qui reste.

| Risque | Gravité | Ce qui est fait | Ce qui reste |
|---|---|---|---|
| Une personne qui connaît le code d'une salle réinstalle un PC | Élevée | Code jamais stocké en clair par défaut ; blocage après trois essais ; double confirmation et rappel « disque entièrement effacé » ; journal | Un code connu permet d'effacer un PC qui démarre en PXE. À faire : ordre de démarrage « disque d'abord » et mot de passe du BIOS sur les PC (le PXE exige alors une touche volontaire) ; changer les codes régulièrement ; ne les confier qu'aux techniciens. Une fenêtre d'intervention côté serveur (poste autorisé pour une durée donnée) est une évolution possible, non livrée |
| Mots de passe lisibles pendant l'installation | Moyenne | Remis seulement après un code valide et une confirmation ; supprimés par `SetupComplete.cmd` ; compte de jonction limité | Quelques minutes de présence sur le disque du PC |
| API en HTTP simple | Moyenne | Jeton aléatoire, durée d'une heure, lié à une image ; réglages remis après le code | Le code et les réglages circulent en clair sur le réseau. Le HTTPS n'est pas intégré : iPXE et le mini-système ne connaissent pas les certificats de l'établissement. Réduire le risque en n'ouvrant le port que depuis les réseaux de PC |
| Mots de passe en clair dans le fichier d'environnement | Moyenne | Droits restreints (`0600` sous Linux, ACL sous Windows) ; jamais dans Git | Quiconque lit ce fichier lit les secrets |
| Code en clair dans `profiles.yaml` (si l'affichage est souhaité) | Moyenne | Facultatif ; fichier en lecture seule pour le compte du service (`0640`) ; l'empreinte seule reste possible | Fichier à protéger comme un secret |
| Pages d'administration | Moyenne | Identifiants vérifiés par le serveur ; blocage ; refus des mots de passe de démonstration ou de moins de 10 caractères hors `LAB_MODE` | En HTTP, les identifiants circulent en clair ; l'annuaire (AD) n'est pas encore branché |
| Compte `admin` / `pikachu` | Faible hors banc | Accepté seulement avec `LAB_MODE=1` | Ne jamais l'activer en exploitation |
| Secrets dans Git | Élevée si cela arrive | `.gitignore` ; seuls des fichiers d'exemple aux valeurs factices sont suivis | Relire `git status` avant chaque envoi. Les codes de `profiles.example.yaml` sont publics |
| Perte du serveur | Moyenne | Tout est reconstructible : le code est dans Git, la configuration est un fichier | Sauvegarder les images, `profiles.yaml` et le fichier d'environnement |
| Mini-système démarré par quelqu'un de motivé | Moyenne | Aucune invite de connexion ; mode dépannage réservé à l'invite iPXE | Mot de passe du BIOS |
| PC trop faible installé quand même | Faible | Verdict et confirmation | Décision humaine |
| Images Windows 10 (fin de support le 14 octobre 2025) | Moyenne | Aucune | Décision de politique à prendre avec le gestionnaire du réseau |
| Licences Windows | À clarifier | Aucune | Mode d'activation des PC (KMS, MAK, licence numérique) à vérifier |

**Plafonds de performance.**

- Un serveur avec une carte réseau de 1 Gbit/s fournit environ 110 Mo/s au total. Exemple : 30 PC recevant ensemble une image de 15 Go représentent 450 Go, soit environ 68 minutes, chaque PC recevant environ 4 Mo/s. Lancer les restaurations par groupes de dix permet de finir les premiers plus tôt, sans changer le débit total. Pistes : multicast, un serveur par salle, carte à 10 Gbit/s.
- L'API sert elle-même les images : suffisant pour un essai. Un serveur web dédié devant l'API serait la suite.
- Le suivi des essais ratés est en mémoire : un redémarrage de l'API le remet à zéro.

---

## 7. Ce qui est prouvé et ce qui ne l'est pas

| Élément | Statut | Comment |
|---|---|---|
| API : routes, codes, blocage, XML, jugement du matériel, reprise, images Clonezilla, pages d'administration | Vérifié | Plus de 150 tests automatiques (`server/tests`) |
| Client : détection, nom du poste, pénalité, téléchargement avec reprise, restauration Clonezilla avec de faux outils, garde-fous | Vérifié | 96 tests automatiques (`client/tests`) |
| Serveur et client réels ensemble, en simulation | Vérifié | 9 scénarios (`tests/test_e2e_simulation.py`) |
| Mini-système Linux : liste de paquets, lanceur, script iPXE, scripts | Vérifié | 22 tests de cohérence (`tests/test_live_config.py`) et `shellcheck` |
| Flux de vrais fichiers d'image dans les vrais décompresseurs | Vérifié | Test facultatif, avec `MDTPROX_REAL_IMAGE` |
| Empreintes de `wimboot` et de Python | Vérifié | Téléchargés, calculés, comparés aux publications officielles |
| Fabrication du mini-système Linux (`build-live.sh`) | À vérifier sur banc | Jamais exécutée |
| Démarrage du noyau Linux par iPXE (UEFI et BIOS), `fetch=`, DHCP, console, clavier | À vérifier sur banc | Jamais démarré |
| Vrais `sgdisk`, `partclone`, `ntfs-3g`, `efibootmgr` sur un vrai PC ; Windows qui démarre après restauration | À vérifier sur banc | Jamais essayé |
| Débit réel de restauration | À vérifier sur banc | Jamais mesuré |
| Jonction au domaine, exécution de `SetupComplete.cmd` | À vérifier sur banc | Jamais essayé |
| Politiques DHCP (`scripts/dhcp-pxe.ps1`) | À vérifier sur banc | Jamais exécutées sur un serveur DHCP Windows |
| Création de la VM et installation sur Proxmox | À vérifier sur banc | Scripts jamais exécutés |
| Installation et fonctionnement sur Windows Server 2019 | À faire | Étapes manuelles écrites (section 3.3), jamais suivies |
| Chemin WinPE : fabrication et exécution | À vérifier sur banc | Jamais essayé avec une vraie ISO |
| Authentification par l'annuaire (AD) des pages d'administration | À faire | Prévu |

La logique est donc vérifiée de bout en bout en simulation ; l'intégration avec le vrai matériel et les vrais outils ne peut l'être que sur le banc d'essai de la section 9. La liste des éléments « à vérifier sur banc » est la liste de contrôle de cet essai.

---

## 8. Mise en service

### 8.1 Sur Windows Server 2019 (cible)

Suivre la section 3.3, puis le plan d'essai de la section 9. Cette installation ne doit être faite qu'après la réussite du banc d'essai et l'accord du gestionnaire du réseau.

### 8.2 Sur Proxmox (banc d'essai)

**À avoir sous la main :**

- l'accord écrit du gestionnaire du réseau pour l'essai ;
- un ou plusieurs dossiers d'images Clonezilla ;
- un compte de jonction au domaine limité et le mot de passe de l'administrateur local à imposer ;
- un code de test par salle de test ;
- le réseau et l'étendue DHCP d'une salle de test.

**Étapes :**

1. Installer Proxmox VE sur une machine dédiée à l'essai (jamais sur le serveur de production), avec sa propre adresse IP (différente de celle de la VM) ; vérifier que la virtualisation est activée dans le BIOS de cette machine.
2. Si la VM doit reprendre l'adresse d'un ancien serveur de déploiement, éteindre celui-ci.
3. Sur l'hôte Proxmox : `scripts/proxmox-create-vm.sh /root/cle.pub` (variables en tête de fichier : numéro de VM, IP, étiquette de VLAN).
4. Démarrer la VM (`qm start <numéro>`), s'y connecter en SSH, passer administrateur, installer `git` et cloner le dépôt dans `/opt/mdtprox`.
5. Lancer `/opt/mdtprox/scripts/setup-vm.sh`. Pour le chemin WinPE : `MINI_OS=winpe /opt/mdtprox/scripts/setup-vm.sh`.
6. Éditer `/etc/mdtprox.env` (adresse du serveur, comptes, mots de passe) puis redémarrer : `systemctl restart mdtprox-api`.
7. Copier les dossiers d'images Clonezilla dans `/srv/deploy/images/`, puis `python3 /opt/mdtprox/server/tools/scan_images.py /srv/deploy/images` pour obtenir les lignes `images:` de `profiles.yaml`. Fabriquer l'empreinte de chaque code avec `python3 /opt/mdtprox/server/tools/hash_code.py` (ou déclarer `code:` si l'affichage dans les pages d'administration est souhaité).
8. Fabriquer le mini-système : `scripts/build-live.sh` (chemin WinPE : `scripts/build-winpe.sh Windows.iso http://<IP-du-serveur>:8080`).
9. Sur le serveur DHCP : `scripts/dhcp-pxe.ps1` sans option (lecture seule) pour relever les étendues et les options 66 et 67 en place.
10. Même script avec `-Apply -ScopeId <étendue de test> -ServerIp <IP-du-serveur> -WhatIf` (simulation), puis sans `-WhatIf`.
11. Démarrer un PC de test en PXE et suivre la check-list de la section 9.

---

## 9. Plan d'essai et check-list du banc d'essai

### 9.1 Plan

Objectif : démontrer en une demi-journée que le principe fonctionne, sans toucher au réseau de production.

1. **Banc isolé** (un commutateur ou un VLAN dédié, la machine virtuelle, un PC de test) avec le DHCP d'essai `boot/dnsmasq-lab.conf`. Critère : le PC affiche l'écran du client. Ce DHCP distribue les adresses du réseau d'essai et n'est jamais relié à la production.
2. **Pénalité** : code vide, texte « Allez vous faire cuire un œuf », extinction après 1 min 30.
3. **Vieux PC avec une image récente** : le verdict détaille les raisons et demande confirmation.
4. **Installation réelle** d'un PC compatible, avec un nom de test. Critères : Windows démarre ; le PC porte le bon nom ; il est dans la bonne unité d'organisation ; l'administrateur local se connecte ; les fichiers de réponses ont disparu.
5. **Réseau réel, une seule étendue DHCP** (phase E, uniquement avec l'accord écrit P1, une fois l'essai isolé terminé) avec `scripts/dhcp-pxe.ps1 -Apply`. Critères : un PC de cette étendue démarre sur le serveur ; un PC d'une autre étendue n'est pas affecté. Retour en arrière : `-Remove`.
6. **Bilan** : compléter la section 7.

### 9.2 Check-list (à imprimer, à cocher au stylo)

Chaque case se coche quand le résultat attendu est observé. En cas d'échec, ne pas insister : noter ce qui s'est passé dans la feuille de relevé, puis consulter le tableau « Si ça rate ». Les identifiants (`P1`, `R3`…) servent à reporter les résultats dans la section 7 (tableau de correspondance plus bas).

Durée prévue : une demi-journée pour les phases V à C, une journée de plus pour la phase I si plusieurs modèles sont testés. N'utiliser comme PC de test que des PC dont le contenu peut être effacé : à la phase I, le disque est réellement effacé.

#### Préparation (environ une semaine avant)

- [ ] **P1** Accord écrit du gestionnaire du réseau pour l'essai (surtout avant la phase E, qui touche au DHCP de production).
- [ ] **P2** Étendues DHCP relevées (lecture seule de `scripts/dhcp-pxe.ps1`) ; une salle de test choisie, avec son réseau et son étendue.
- [ ] **P3** Adresse du serveur de déploiement décidée (adresse d'un ancien serveur après son arrêt, ou adresse libre).
- [ ] **P4** Compte de jonction limité créé (droit de créer des ordinateurs dans l'unité d'organisation de test seulement) ; mot de passe de l'administrateur local choisi.
- [ ] **P5** Code de test choisi, différent des codes définitifs.
- [ ] **P6** Au moins un dossier d'image Clonezilla copié sur le serveur. Pour les phases V à C, une petite image de test suffit ; ne jamais taper `OUI` avec une image factice.
- [ ] **P7** Serveur de test prêt : VM Proxmox (éventuellement un clone) ou, pour la seconde recette, Windows Server 2019.
- [ ] **P8** Dépôt présent sur ce serveur et installation exécutée (`setup-vm.sh` sur Proxmox, étapes de la section 3.3 sur Windows Server 2019).
- [ ] **P9** Un petit commutateur et deux câbles réseau.
- [ ] **P10** Un serveur DHCP pour la paillasse (box ou routeur de test).
- [ ] **P11** Au moins deux PC de test : un récent en UEFI et un ancien en BIOS classique (idéalement un troisième avec un Core i3). Marque, modèle, processeur, mémoire et disque notés.
- [ ] **P12** Un chronomètre et un appareil photo (téléphone) pour garder les écrans importants.
- [ ] **P13** Un écran et un clavier supplémentaires pour le serveur, ou sa console web.
- [ ] **P14** Dans le BIOS de chaque PC de test : démarrage réseau (PXE) activé ; Secure Boot désactivé ; ordre de démarrage « disque d'abord ».

#### Phase V — Le serveur, sans aucun PC (environ 20 minutes)

- [ ] **V1** Le service de l'API est actif (`systemctl status mdtprox-api`). Cause fréquente d'échec : « CHANGEZ-MOI » resté dans `/etc/mdtprox.env`.
- [ ] **V2** Le service TFTP est actif (`systemctl status dnsmasq`).
- [ ] **V3** `curl -s http://<IP>:8080/healthz` répond `{"status":"ok"}`.
- [ ] **V4** `curl -s http://<IP>:8080/boot.ipxe` renvoie un script qui commence par `#!ipxe` et où l'adresse du serveur apparaît (plus aucun `@@BASE_URL@@`).
- [ ] **V5** `curl -sI http://<IP>:8080/live/filesystem.squashfs` répond `200 OK` avec un `Content-Length` ; idem pour `vmlinuz` et `initrd.img`.
- [ ] **V6** `curl -s -o /dev/null -w '%{http_code}\n' tftp://127.0.0.1/undionly.kpxe` ne renvoie pas d'erreur ; idem pour `ipxe.efi`.
- [ ] **V7** Essai à blanc du client : `python3 /opt/mdtprox/client/deploy_client.py --server http://127.0.0.1:8080 --simulate old-lenovo --dry-run`, puis saisie du code de test : le verdict s'affiche.
- [ ] **V8** Une ligne `session_ok` figure dans `audit.jsonl`, sans le code saisi.
- [ ] **V9** Lecture seule du DHCP : `scripts/dhcp-pxe.ps1` sans option s'exécute sans erreur et affiche les étendues.
- [ ] **V10** `http://<IP>:8080/admin/images` ouvre une fenêtre d'identifiants ; après identification, la page liste les salles ; le code d'une salle n'apparaît qu'après identification.

#### Phase R — Réseau et mini-système (paillasse isolée, environ 1 h)

**Montage.** Commutateur, box DHCP de test, serveur, PC de test, sans aucun lien avec le réseau de production. L'adresse du serveur sur la paillasse peut différer de l'adresse définitive : le mini-système Linux n'est pas à refaire, mais `PUBLIC_BASE_URL` du fichier d'environnement doit correspondre à l'adresse vue par le PC (puis redémarrer l'API).

**DHCP d'essai.** Dans la machine virtuelle, après avoir relié sa seconde carte au réseau isolé : arrêter le TFTP de production (`systemctl stop dnsmasq`), remplacer les deux valeurs `A_RELEVER_…` de `boot/dnsmasq-lab.conf` (nom de la carte du réseau d'essai, adresse de la machine virtuelle), vérifier avec `dnsmasq --test --conf-file=…`, puis lancer au premier plan avec `dnsmasq --keep-in-foreground --conf-file=…`. Les deux configurations ne peuvent pas tourner ensemble (même port TFTP 69). Vérifier que le réseau d'essai n'est relié à aucun autre DHCP.

- [ ] **R1** PC en UEFI : touche de démarrage réseau, adresse IP obtenue, lignes `DHCPDISCOVER` et `PXE` côté dnsmasq.
- [ ] **R2** Le PC charge `ipxe.efi`, puis `boot.ipxe` : le bandeau affiche l'IP, la MAC et le mode.
- [ ] **R3** Pas de boucle : le bandeau iPXE n'apparaît qu'une fois.
- [ ] **R4** Les étapes `[1/3]`, `[2/3]` et `[3/3]` s'affichent sans message d'échec.
- [ ] **R5** Le système complet se télécharge : durée notée entre `[3/3]` et l'écran du client.
- [ ] **R6** L'écran « Installation automatique des postes » apparaît, sans invite de connexion ; Alt+F2 ne donne rien.
- [ ] **R7** Clavier AZERTY pour la saisie du code ; accents et « œ » correctement affichés.
- [ ] **R8** « Poste détecté » : modèle et numéro de série justes ; disques internes seulement (la clé USB éventuelle n'est pas proposée).
- [ ] **R9** Le client trouve l'API sans configuration (adresse transmise par le noyau).
- [ ] **R10** Mode dépannage : dans l'invite iPXE (Ctrl+B au démarrage), ajouter `mdtprox_debug` à la ligne du noyau ouvre un shell ; `ip -br a` et `lsblk` répondent.
- [ ] **R11** Mémoire du plus petit PC sur lequel le mini-système démarre notée (minimum prévu : 2 Go).
- [ ] **R12** PC en BIOS classique (`undionly.kpxe`) : le noyau démarre aussi ; une image UEFI est refusée avec le message sur le BIOS.
- [ ] **R13** Carte réseau non reconnue sur un modèle : modèle noté (`lspci`) pour ajouter son micrologiciel à `mdtprox.list.chroot`.

#### Phase B — La pénalité (environ 15 minutes)

- [ ] **B1** Entrée directe à la demande de code : le texte « Il semblerait que vous tentiez de forcer le passage… » s'affiche avec l'œuf dessiné.
- [ ] **B2** Le compte à rebours part de 01:30 et affiche « Allez vous faire cuire un œuf ».
- [ ] **B3** Au bout de 1 min 30 (à 5 secondes près), le PC s'éteint.
- [ ] **B4** Après rallumage, trois codes faux donnent la même pénalité.
- [ ] **B5** Aussitôt après, le bon code est refusé (adresse bloquée) : le blocage du serveur fonctionne. Attendre 5 minutes ou redémarrer l'API pour débloquer.
- [ ] **B6** `audit.jsonl` contient des lignes `bad_code` puis `locked` (pas de ligne `empty_code` : le client traite le code vide sans appeler le serveur).

#### Phase C — Le verdict sur le matériel (environ 30 minutes)

Avec le bon code, sur chaque PC de test :

- [ ] **C1** PC du même modèle que l'image : aucun avertissement.
- [ ] **C2** Vieux PC avec une image Windows 11 : titre, liste des raisons et caractéristiques réelles s'affichent, puis la question « Êtes-vous vraiment sûr(e) ? ».
- [ ] **C3** Réponse par défaut (Entrée) : retour à la saisie du code, sans pénalité.
- [ ] **C4** PC avec un Core i3 récent : message adouci (« un peu juste »), même question.
- [ ] **C5** Caractéristiques affichées justes : processeur, génération, mémoire, disque, UEFI ou BIOS (comparer avec le BIOS du PC).
- [ ] **C6** Disque mécanique et TPM correctement détectés (possible sous Linux).
- [ ] **C7** PC d'un autre modèle que l'image : message « Cette image n'a pas été préparée pour ce poste » avec les deux modèles.
- [ ] **C8** Disque plus petit que celui de l'image : verdict bloquant, aucune proposition de continuer.
- [ ] **C9** Le ton des messages est jugé acceptable (diplomatique, sans moquerie). Phrases à reformuler notées (dans `server/app/hardware.py`).

#### Phase I — Installation réelle d'un PC (1 à 2 h par PC)

**Prérequis.** Une vraie image dans `/srv/deploy/images/`, `profiles.yaml` pointant dessus, un PC de test sans donnée utile, une unité d'organisation de test prête dans l'annuaire, le compte de jonction et l'administrateur local réglés dans le fichier d'environnement, API redémarrée. Pendant qu'un PC s'installe, ne pas redémarrer l'API ni modifier le fichier d'environnement : le PC perdrait son jeton.

- [ ] **I1** Avec le bon code, le nom proposé reprend le préfixe de la salle ; un nom de test clairement identifiable est saisi.
- [ ] **I2** Un nom invalide (`-ABC`, `ABC-`, 16 caractères, `12345`) est refusé avec un message clair.
- [ ] **I3** Le récapitulatif s'affiche avec « Le disque … sera ENTIÈREMENT EFFACÉ » ; `oui` en minuscules annule ; `OUI` continue.
- [ ] **I4** Étape 1/5 sans erreur de `sgdisk` ; `lsblk` montre les mêmes partitions que l'image.
- [ ] **I5** Étape 2/5 : la barre de progression avance pour chaque partition ; débit moyen et durée totale notés (donnée clé pour dimensionner le serveur).
- [ ] **I6** (Facultatif) Câble réseau débranché environ 1 min 30 pendant la grosse partition : la restauration reprend toute seule.
- [ ] **I7** Aucun message d'erreur de `partclone` (sinon photographier la fin du journal affichée).
- [ ] **I8** Étape 3/5 : l'entrée « Windows Boot Manager » est créée (`efibootmgr -v`) ou le message de secours s'affiche.
- [ ] **I9** Étape 4/5 : pas d'erreur de montage `ntfs-3g` ; le message de veille prolongée, s'il apparaît, est noté avec le modèle.
- [ ] **I10** Le PC redémarre sur Windows sans demander de réparation, puis termine sa configuration sans intervention.
- [ ] **I11** Le PC porte le bon nom (`hostname`).
- [ ] **I12** Le PC est dans le domaine (`systeminfo | findstr /i domai`).
- [ ] **I13** L'objet ordinateur est dans la bonne unité d'organisation de l'annuaire.
- [ ] **I14** Le compte administrateur local se connecte avec le mot de passe défini dans l'environnement.
- [ ] **I15** Un compte du domaine (élève ou enseignant de test) se connecte.
- [ ] **I16** Les fichiers de réponses ont disparu : plus de `C:\Windows\Panther\Unattend\Unattend.xml`, de `C:\Windows\Panther\unattend.xml`, de `C:\Windows\System32\Sysprep\unattend.xml` ni de `C:\Windows\Setup\Scripts\SetupComplete.cmd`.
- [ ] **I17** En cas d'échec de I12, `C:\Windows\debug\NetSetup.log` et `C:\Windows\Panther\setupact.log` sont copiés sur une clé.
- [ ] **I18** Si I11 ou I12 échouent alors que I10 réussit, l'image n'est probablement pas généralisée (Q1) : notée.
- [ ] **I19** Temps total relevé, de la touche de démarrage réseau à la session ouverte.
- [ ] **I20** Image d'un autre modèle : noter si Windows démarre quand même (pilotes) ; cela dira si l'avertissement du serveur est trop sévère ou juste.
- [ ] **I21** `audit.jsonl` contient `session_ok`, `deploy_start` (avec le bon nom) et `deploy_report` avec `status: success`.
- [ ] **I22** Les cases I1 à I16 sont refaites avec le second PC.

**Variante WinPE** (à la place de R4 à R8 et I4 à I9, si `MINI_OS=winpe`) : les étapes iPXE se terminent par le démarrage de Windows PE ; l'installeur Windows ne doit pas démarrer à la place du client ; modèle et numéro de série lus dans le registre correspondent à l'étiquette ; `diskpart`, téléchargement avec empreinte vérifiée, `dism /Apply-Image` et `bcdboot` se déroulent sans erreur (lettres `S:` et `W:` libres) ; accents et « œ » s'affichent dans la console (sinon `"ascii_only": true` dans `client.json`, qui demande de refabriquer le WinPE).

#### Phase E — Sur le réseau réel, une seule salle (environ 1 h), uniquement avec l'accord écrit P1

**Prérequis.** Le serveur a son adresse définitive ; `PUBLIC_BASE_URL` est cohérent ; le TFTP de production est relancé et le DHCP d'essai du banc est arrêté ; avec WinPE, le mini-système est refabriqué avec l'adresse définitive.

- [ ] **E1** `scripts/dhcp-pxe.ps1 -Apply -ScopeId <étendue de test> -ServerIp <IP> -WhatIf` affiche ce qui serait créé, sans rien modifier.
- [ ] **E2** La liste ne contient que l'étendue de test et les quatre politiques `MDTPROX-PXE-*`.
- [ ] **E3** Même commande sans `-WhatIf` : demande `OUI`, crée les classes et politiques sans erreur (premier vrai essai du script).
- [ ] **E4** Mode lecture seule relancé : les quatre politiques apparaissent dans l'ordre 1 (iPXE), 2, 3, 4.
- [ ] **E5** Un PC de la salle de test en PXE UEFI arrive jusqu'à l'écran du client.
- [ ] **E6** Un PC de la salle de test en PXE BIOS aussi.
- [ ] **E7** Pas de boucle sur le bandeau iPXE (l'ordre des politiques est éprouvé ici).
- [ ] **E8** Un PC d'une autre étendue qui tente le PXE n'est pas aiguillé vers le serveur.
- [ ] **E9** Un PC de la salle de test qui démarre normalement sur son disque n'est pas affecté.
- [ ] **E10** Retour en arrière : `scripts/dhcp-pxe.ps1 -Remove -ScopeId <étendue de test>` retire les quatre politiques ; la lecture seule confirme qu'il n'en reste aucune et que les options 66 et 67 d'origine sont intactes.
- [ ] **E11** Un export ou une photo des étendues, avant et après, est conservé.

#### Phase F — Bilan (environ 30 minutes)

- [ ] **F1** Feuille de relevé remplie pour chaque PC.
- [ ] **F2** Chaque ligne « à vérifier sur banc » de la section 7 validée est passée en « vérifié », ou l'écart observé est noté.
- [ ] **F3** Ajustements reportés dans les tâches concernées.
- [ ] **F4** Nettoyage : objets ordinateurs de test supprimés de l'annuaire ; BIOS des PC de test remis dans leur état d'origine ; compte de jonction de test retiré s'il était temporaire ; code de test changé ; politiques DHCP supprimées si elles ne doivent pas rester ; ordre de démarrage « disque d'abord » remis.
- [ ] **F5** Décision notée par écrit (tableau ci-dessous).

**Critères de décision**

| Résultat | Décision |
|---|---|
| Phases V, R, B, C, I cochées ; phase E sans boucle ni effet sur les autres salles | Go : pilote sur une salle réelle, avec les vrais codes |
| Phases V à I cochées ; phase E non faite ou en échec | Go conditionnel : poursuivre sur la paillasse, corriger le DHCP avant le réseau réel |
| Une case I10 à I16 en échec répété | Pas encore : corriger le fichier de réponses ou l'image (T8), puis refaire la phase I |
| Aucun PC n'atteint l'écran du client (R6) | Pas encore : corriger le démarrage réseau (T5, T6), puis refaire la phase R |

#### Si ça rate

| Symptôme | Cause probable | À regarder |
|---|---|---|
| Le PC n'obtient pas d'adresse IP | Pas de DHCP sur la paillasse, câble, réseau | Voyant du commutateur ; la box ; `ip -br a` sur le serveur |
| « No bootable device », « PXE-E53 », « PXE-E51 » | Aucun fichier de démarrage proposé | `dnsmasq` tourne-t-il ? lignes `DHCPDISCOVER` ? options 66 et 67 côté DHCP (phase E) |
| iPXE se recharge en boucle | Politique iPXE (ordre 1) pas prioritaire, ou option 67 mal renseignée | Relancer `scripts/dhcp-pxe.ps1` sans option et lire l'ordre des politiques |
| iPXE affiche « ECHEC du demarrage par le reseau » | API injoignable, ou fichier de `live/` absent | `curl` de V3 et V5 depuis un autre poste ; `PUBLIC_BASE_URL` identique à ce que voit le PC |
| Écran de sécurité, « Access Denied » | `ipxe.efi` non signé | Désactiver Secure Boot (P14) |
| Le noyau démarre, puis aucun écran du client | Système complet non téléchargé (mémoire insuffisante, `fetch=` en échec) | Retirer `quiet loglevel=3` de `boot-linux.ipxe` pour lire les messages ; mémoire du PC |
| Le client attend environ une minute, puis s'arrête | L'API ne répond pas depuis le PC | `ping <IP>` depuis le mode dépannage ; pare-feu ; port 8080 |
| « Adresse du serveur inconnue » | Paramètre `mdtprox_server=` absent de la ligne du noyau | `boot-linux.ipxe` ; `PUBLIC_BASE_URL` |
| `503 server_not_configured` | Variable manquante ou `profiles.yaml` invalide | `journalctl -u mdtprox-api -n 50` |
| `503 image_missing` | Dossier ou fichier d'image absent de `/srv/deploy/images/` | Nom exact dans `profiles.yaml` |
| Le bon code est refusé | Adresse bloquée, ou empreinte mal copiée | `audit.jsonl` (`locked` ?) ; refaire `hash_code.py` |
| Erreur de `sgdisk`, `partclone` ou `mount` | Disque, image corrompue, outil absent | Photographier le message ; relire la tâche T4 |
| Windows démarre mais le PC garde son ancien nom ou n'est pas dans le domaine | Image non généralisée (Q1), ou fichier de réponses non lu | `C:\Windows\Panther\setupact.log` ; revoir `sysprep /generalize` |
| Jonction au domaine refusée | Droits du compte de jonction, unité d'organisation invalide, DNS | `C:\Windows\debug\NetSetup.log` ; tester le compte |
| Des fichiers de réponses restent sur le PC | `SetupComplete.cmd` non exécuté | Présence de `C:\Windows\Setup\Scripts\SetupComplete.cmd` ; `setupact.log` |
| Page `/admin/images` refusée (503) | Mot de passe de démonstration ou trop court hors `LAB_MODE` | Message affiché ; fichier d'environnement |

#### Correspondance avec la section 7

| Ligne de la section 7 | Cases à avoir cochées |
|---|---|
| Fabrication du mini-système Linux | V5, R4, R5 |
| Démarrage du noyau Linux par iPXE (UEFI et BIOS), `fetch=`, DHCP, console, clavier | R1 à R13 |
| Vrais outils de restauration sur un vrai PC ; Windows qui démarre | I4 à I10 |
| Débit réel de restauration | I5 |
| Jonction au domaine, `SetupComplete.cmd` | I11 à I16 |
| Politiques DHCP | V9, E1 à E4, E10 |
| Création de la VM et installation sur Proxmox | V1 à V3 |
| Pénalité, verdict, nom du poste à l'écran sur un vrai PC | B1 à B6, C1 à C9, I1 à I3 |
| Pages d'administration en situation réelle | V10 |

#### Feuille de relevé (une ligne par PC et par essai)

| PC (marque, modèle) | BIOS ou UEFI | Secure Boot | Image | Phases réussies | Durée | Écarts constatés | Photos |
|---|---|---|---|---|---|---|---|
| | | | | | | | |
| | | | | | | | |
| | | | | | | | |

---

## 10. Questions ouvertes

| # | Question | Qui répond | Pourquoi elle compte |
|---|---|---|---|
| Q1 | Les images ont-elles été prises après `sysprep /generalize` ? Lesquelles sont déjà jointes au domaine ? | L'établissement | Sinon le nom et le domaine ne s'appliquent pas tout seuls (T8) |
| Q2 | Quel compte de jonction et quelle unité d'organisation pour chaque salle ? | L'établissement, le gestionnaire du réseau | Droits minimaux (T8) |
| Q3 | Quelles sont les étendues DHCP, les réseaux des salles et les options 66 et 67 en place ? | Lecture seule du script DHCP | Préalable à toute modification (T5) |
| Q4 | Quelle adresse pour le serveur de déploiement (reprise de l'ancienne adresse ou nouvelle) ? | L'établissement, le gestionnaire du réseau | Évite un conflit d'adresse |
| Q5 | Quelle politique pour Windows 10, dont le support a pris fin en octobre 2025 ? | Direction, gestionnaire du réseau | Durée de vie des images actuelles |
| Q6 | Comment les PC s'activent-ils (KMS, MAK, licence numérique) ? | Gestionnaire du réseau | Section 6 |
| Q7 | Secure Boot peut-il être désactivé sur les PC démarrés en PXE ? | L'établissement | Limite d'`ipxe.efi` (T5) |
| Q8 | La version de `partclone` de Debian 12 lit-elle les images faites avec `partclone` 0.3.37 ? | Banc | Sinon `LB_DIST=trixie` ou `partclone` plus récent (T6) |
| Q9 | Une salle contient-elle un seul modèle de PC ? Que faire d'un PC d'un autre modèle ? | L'établissement | Une image est liée à un modèle (annexe B) |
| Q10 | Que faire d'un disque plus grand que celui de l'image (espace inutilisé) ? | L'établissement | T4 |
| Q11 | Qui connaît les codes ? À quel rythme les changer ? Faut-il les afficher ou ne garder que l'empreinte ? | L'établissement | Section 6 |
| Q12 | Quels modèles ont une carte réseau que le mini-système ne reconnaît pas ? | Banc | Micrologiciels à ajouter (T6) |
| Q13 | Quel service TFTP sous Windows Server 2019 : WDS en transport seul ou service tiers ? | Gestionnaire du réseau | Section 3.3 |
| Q14 | Quel nom DNS, quel certificat et quel frontal HTTPS ? | Gestionnaire du réseau | Section 6 |
| Q15 | Quel groupe de l'annuaire autorisera l'accès aux pages d'administration ? | Gestionnaire du réseau | T9 |

---

## 11. Évolutions prévues

Ces fonctions ne sont pas livrées. Aucune ne doit retarder la restauration sécurisée ni la recette.

- **Authentification par l'annuaire (AD)** des pages d'administration, avec un groupe autorisé, sans récupérer ni conserver les mots de passe de l'annuaire. L'abstraction locale ou annuaire évitera de réécrire le portail.
- **HTTPS** devant le service, avec un nom DNS et un certificat validés avec le gestionnaire du réseau.
- **Fenêtre d'intervention côté serveur** : un poste n'est effaçable que pendant une période ouverte par un administrateur, de sorte que connaître un code ne suffise plus.
- **Script de premier démarrage** pour les images non généralisées (renommer, quitter puis rejoindre le domaine).
- **Extension de la partition Windows** quand le disque est plus grand que celui de l'image ; **partition de récupération** (WinRE) pour le chemin WinPE.
- **Multicast** pour envoyer une image à une salle en un seul flux.
- **Client téléchargé au démarrage** plutôt que recopié dans l'image, pour éviter de refabriquer le mini-système à chaque modification.

---

## Glossaire

| Mot | Explication simple |
|---|---|
| **Ordinateur, PC** | Une machine qui exécute des programmes. Son processeur calcule, sa mémoire vive (RAM) sert de bureau de travail et se vide à l'extinction, son disque garde les fichiers. |
| **Système d'exploitation** | Le programme principal qui fait fonctionner le PC : Windows, Linux, macOS. |
| **Image (« dump »)** | Un ensemble de fichiers qui contient un système déjà installé et réglé, comme une photo complète d'un PC modèle. Pour installer un nouveau PC, on « rejoue » la photo dessus. |
| **Clonezilla** | Logiciel libre qui copie un disque entier dans un dossier de fichiers et peut rejouer cette copie sur un autre disque. Son programme en ligne de commande s'appelle `ocs-sr`. |
| **partclone** | Outil de Clonezilla qui copie seulement les parties utilisées d'une partition. C'est pourquoi une image d'un disque de 223 Go peut ne faire que 47 Go. |
| **zstd, gzip** | Deux façons de compresser un fichier, comme un fichier ZIP. `zstd` est plus récent et plus rapide à décompresser. |
| **Partition** | Une « tranche » du disque. Un PC Windows en a au moins deux : une petite pour le démarrage, une grande pour Windows. |
| **WIM** | Format d'image de Microsoft qui range les fichiers, indépendamment du modèle de PC. |
| **Réseau, adresse IP** | Les câbles et boîtiers qui relient les machines ; l'adresse IP est le « numéro de téléphone » d'une machine, par exemple `10.0.0.3`. |
| **Serveur** | Ordinateur qui rend des services aux autres : distribuer des adresses, des fichiers, vérifier des mots de passe. |
| **VM (machine virtuelle)** | Un ordinateur simulé à l'intérieur d'un vrai. On peut la créer, la copier, la sauvegarder en quelques clics. |
| **Hyperviseur, Proxmox** | Logiciel qui fabrique et fait fonctionner des machines virtuelles dans un vrai serveur. Proxmox VE est libre et se pilote par une page web. |
| **DHCP** | Service qui donne automatiquement une adresse IP à chaque PC qui s'allume. |
| **PXE** | Capacité d'un PC à démarrer depuis le réseau au lieu de son disque (souvent choisie par une touche au démarrage). |
| **TFTP** | Protocole très simple et très ancien pour envoyer de petits fichiers : le PC vide ne sait faire que cela au tout début. |
| **HTTP, HTTPS** | Protocole des sites web. HTTPS ajoute le chiffrement, pour que personne ne puisse lire les échanges. |
| **iPXE** | Petit programme de démarrage plus capable que le PXE d'origine : il sait parler HTTP. |
| **BIOS, UEFI** | Premier programme d'un PC, avant le système. BIOS : ancien. UEFI : moderne. Il faut deux versions d'iPXE (`undionly.kpxe` pour le BIOS, `ipxe.efi` pour l'UEFI). |
| **Secure Boot** | Option d'UEFI qui n'accepte que des programmes de démarrage signés. Le `ipxe.efi` de Debian ne l'est pas : il faut la désactiver pour le PXE. |
| **Noyau Linux** | Le cœur d'un système Linux : le premier programme qui prend la main et sait parler au disque, au réseau et à l'écran (fichier `vmlinuz`). |
| **initrd** | Très petit système de départ, chargé avec le noyau, qui prépare le réseau et va chercher le vrai système (fichier `initrd.img`). |
| **squashfs** | Format qui range tout un système Linux compressé dans un seul fichier. Ici, il est chargé en mémoire : rien n'est écrit sur le disque du PC. |
| **Debian « live », live-build** | Debian est un système Linux libre et stable ; un système « live » démarre sans s'installer ; live-build fabrique un tel système sur mesure à partir d'une liste de paquets. |
| **ntfs-3g** | Logiciel qui permet à Linux de lire et d'écrire dans une partition Windows (NTFS). |
| **WinPE** | Mini-Windows qui tient en mémoire, utilisé pour installer Windows. |
| **diskpart, DISM, bcdboot** | Outils Windows : le premier découpe le disque, le deuxième applique une image, le troisième rend la partition démarrable. |
| **wimboot** | Programme d'iPXE qui démarre un WinPE à partir de fichiers téléchargés en HTTP. |
| **API** | Programme qui répond à des questions posées par d'autres programmes. Ici, le client lui demande « ce code est-il valide ? » et elle répond en texte structuré. |
| **JSON** | Façon d'écrire des données en texte, lisible par une machine et par un humain : `{"nom": "ROOMA-01"}`. |
| **Python, FastAPI, uvicorn** | Python est un langage de programmation lisible. FastAPI est une boîte à outils pour écrire des API ; uvicorn est le programme qui la fait fonctionner et écoute le réseau. |
| **Domaine, Active Directory (AD)** | L'annuaire de l'établissement : la liste officielle des utilisateurs et des PC. Un PC « du domaine » reconnaît leurs comptes. |
| **Joindre le domaine** | Inscrire le PC dans l'annuaire, avec un compte autorisé à y créer des ordinateurs. |
| **Unité d'organisation (OU)** | Un « classeur » dans l'annuaire, par exemple un par salle. |
| **unattend.xml** | Fichier de réponses : au lieu de répondre à chaque question de l'installation, Windows le lit (nom du poste, domaine…). |
| **sysprep** | Outil Microsoft qui prépare un PC modèle avant de le photographier (il retire le nom, l'identité) pour que la copie puisse en recevoir de nouveaux. |
| **Variable d'environnement, fichier d'environnement** | Réglages donnés au programme depuis l'extérieur, sans les écrire dans le code. C'est là que se mettent les mots de passe. |
| **Empreinte (hash)** | Résumé d'un texte ou d'un fichier, de taille fixe, impossible à inverser. Sert à vérifier qu'un code est bon sans le conserver, ou qu'un fichier n'est pas abîmé (SHA-256 et scrypt sont deux recettes). |
| **Jeton** | Ticket aléatoire remis après un code valide ; il donne accès au reste pendant une durée limitée. |
| **Git, dépôt** | Git garde l'historique de tous les changements d'un projet ; le dépôt est le dossier suivi. |
| **Banc d'essai** | Essai sur table avec un vrai PC et un vrai serveur, par opposition à une simulation sur un ordinateur de développement. |

---

## Annexe A — Contrat d'échange entre les morceaux

Cette annexe fait référence pour le code : serveur, client et scripts de démarrage doivent parler le même langage.

### A.1 Les morceaux et leurs dossiers

| Dossier | Morceau | Langage |
|---|---|---|
| `server/` | L'API hébergée sur le serveur de déploiement | Python 3, FastAPI |
| `client/` | Le programme du PC à installer (mini-système Linux, ou WinPE) | Python 3, bibliothèque standard uniquement |
| `boot/`, `live/`, `scripts/`, `deploy/` | Démarrage réseau, fabrication du mini-système, installation du serveur, DHCP | Shell, PowerShell, iPXE, configuration dnsmasq et systemd |

### A.2 Arborescence sur un serveur Linux (Debian 12)

```
/opt/mdtprox/               le dépôt Git cloné et l'environnement Python (.venv)
/etc/mdtprox.env            variables d'environnement (secrets), droits 0600
/srv/deploy/                DATA_DIR : tout ce qui est volumineux ou secret, jamais dans Git
    profiles.yaml           codes, salles, images
    boot.ipxe               script de démarrage réseau (copie de boot/boot-linux.ipxe, ou de boot/boot.ipxe si MINI_OS=winpe)
    images/<dossier>/       une image Clonezilla = un dossier (disk, parts, Info-dmi.txt, *.sgdisk, *-ptcl-img.*)
    images/*.wim            images au format WIM (chemin WinPE), avec leur empreinte *.wim.sha256 (calculée si absente)
    live/                   vmlinuz, initrd.img, filesystem.squashfs (mini-système Linux)
    winpe/                  wimboot, bootmgr, BCD, boot.sdi, boot.wim (mini-système WinPE, option)
    audit.jsonl             journal : une ligne JSON par événement
/srv/tftp/                  ipxe.efi, undionly.kpxe, snponly.efi (servis en TFTP ; snponly.efi = repli UEFI)
```

`winpe/` et `live/` appartiennent à root et restent en lecture seule pour le service ; le reste de `/srv/deploy` appartient à l'utilisateur système `mdt`. Sous Windows, les mêmes sous-dossiers existent dans le dossier désigné par `DATA_DIR`.

### A.3 Variables d'environnement du serveur

Fichier `/etc/mdtprox.env` (systemd) ou fichier désigné par `MDTPROX_ENV_FILE`.

| Variable | Exemple | Rôle |
|---|---|---|
| `PUBLIC_BASE_URL` | `http://10.0.0.3:8080` | Adresse que les PC utilisent pour joindre l'API (sans `/` final) |
| `BIND_HOST`, `BIND_PORT` | `0.0.0.0`, `8080` | Écoute de l'API. Lues par la ligne de commande d'uvicorn dans l'unité systemd, pas par le code Python |
| `DATA_DIR` | `/srv/deploy` | Voir A.2 |
| `DOMAIN_FQDN` | `example.local` | Domaine Active Directory à rejoindre |
| `DOMAIN_JOIN_USER` | `svc-joindomaine` | Compte autorisé à créer des ordinateurs dans l'annuaire |
| `DOMAIN_JOIN_PASSWORD` | (secret) | Son mot de passe |
| `LOCAL_ADMIN_USER` | `admin-local` | Compte administrateur local créé sur tous les PC |
| `LOCAL_ADMIN_PASSWORD` | (secret) | Son mot de passe |
| `WIN_TIMEZONE` | `Romance Standard Time` | Fuseau horaire Windows (valeur par défaut) |
| `WIN_LOCALE` | `fr-FR` | Langue et clavier (valeur par défaut) |
| `MAX_BAD_CODES` | `3` | Essais ratés avant pénalité et blocage (défaut 3) |
| `LOCKOUT_SECONDS` | `300` | Durée du blocage d'une adresse (défaut 300) |
| `ADMIN_USER`, `ADMIN_PASSWORD` | (secret) | Compte des pages `/admin/…` ; vides = pages désactivées |
| `LAB_MODE` | `1` | Banc d'essai isolé : autorise les identifiants de démonstration. Vide en exploitation |
| `MDTPROX_ENV_FILE` | `C:\mdtprox\mdtprox.env` | Fichier KEY="valeur" lu en complément de l'environnement réel (qui l'emporte) ; utile sous Windows |

Variables obligatoires, sans valeur par défaut : `PUBLIC_BASE_URL`, `DOMAIN_FQDN`, `DOMAIN_JOIN_USER`, `DOMAIN_JOIN_PASSWORD`, `LOCAL_ADMIN_USER`, `LOCAL_ADMIN_PASSWORD`. S'il en manque une, le serveur démarre mais `/session` et `/deploy/start` répondent `503 server_not_configured`. Le service systemd refuse en plus de démarrer tant que « CHANGEZ-MOI » reste dans le fichier. `DATA_DIR` vaut `/srv/deploy` par défaut. Un identifiant ou un mot de passe contenant un caractère de contrôle (retour à la ligne, tabulation) est refusé au démarrage. Les mots de passe ne sont jamais écrits dans le journal ni dans les réponses de l'API, sauf le contenu du fichier de réglages remis par `deploy/start`.

Pages d'administration : hors `LAB_MODE`, un mot de passe de démonstration (`pikachu`, `admin`, `password`, `azerty`…) ou de moins de 10 caractères est refusé (HTTP 503).

### A.4 `profiles.yaml` (dans `DATA_DIR`)

```yaml
images:
  win10-rooma:
    label: "Windows 10 22H2 – salle A (HP Exemple 600 G3 MT)"
    kind: clonezilla                    # wim (par défaut) ou clonezilla
    dir: W10-22H2-ROOMA   # nom du dossier d'image dans DATA_DIR/images/
    os_family: windows10                # windows10 ou windows11
    # source_model: "HP Exemple 600 G3 MT"          # facultatif : sinon lu dans Info-dmi.txt
  win11-rooma:                          # image au format WIM
    label: "Windows 11 – salle A"
    file: win11-rooma.wim               # dans DATA_DIR/images/
    wim_index: 1
    os_family: windows11
rooms:
  - id: ROOMA
    label: "Salle A"
    code_hash: "scrypt$16384$8$1$<sel_base64>$<empreinte_base64>"   # voir server/tools/hash_code.py
    # ou, pour pouvoir afficher le code dans /admin/images :  code: "mon-code"
    image: win10-rooma                  # clé de la section images
    name_prefix: "ROOMA-"               # préfixe suggéré pour le nom du poste
    ou: "OU=ROOMA,OU=Ordinateurs,DC=example,DC=local"   # facultatif : unité d'organisation de destination
```

Une salle déclare soit `code_hash` (recommandé : seule l'empreinte est conservée), soit `code` (le serveur calcule l'empreinte et peut afficher le code aux administrateurs), soit les deux (ils doivent correspondre). Pour une image Clonezilla, le serveur lit lui-même dans le dossier le modèle du PC d'origine (`Info-dmi.txt`, bloc « System Information »), la taille du disque d'origine (`<disque>-gpt.sgdisk`), le nom du disque (`disk`), les partitions (`parts`) et la liste des fichiers. `python3 server/tools/scan_images.py /srv/deploy/images` produit les lignes `images:` d'un dossier entier. Pour une image WIM, `size_bytes` et `sha256` sont calculés par le serveur (fichier `.sha256` à côté).

### A.5 Objet « matériel » (envoyé par le client, jugé par le serveur)

```json
{
  "environment": "linux",
  "manufacturer": "HP",
  "model": "HP Exemple 600 G3 MT",
  "serial": "CZC0000000",
  "bios_date": "2017-05-01",
  "firmware": "uefi",
  "cpu_name": "Intel(R) Core(TM) i5-6500 CPU @ 3.20GHz",
  "cpu_cores": 4,
  "ram_mb": 8192,
  "disks": [{"name": "sda", "size_gb": 256, "size_bytes": 256060514304, "kind": "ssd"}],
  "tpm": "2.0",
  "secure_boot": true,
  "mac": "AA:BB:CC:DD:EE:FF"
}
```

Valeurs permises : `environment` : `winpe` ou `linux` ; `firmware` : `uefi`, `bios` ou `unknown` ; `disks[].kind` : `ssd`, `hdd` ou `unknown` ; `tpm` : `2.0`, `1.2`, `none` ou `unknown` ; `secure_boot` : `true`, `false` ou `null`. Tout champ inconnu vaut `null` ou `"unknown"` : le serveur ne plante jamais sur une valeur manquante. `disks[].size_bytes` est facultatif : s'il est présent, le serveur l'utilise pour comparer strictement avec le disque d'origine d'une image Clonezilla ; sinon il se contente de `size_gb` (arrondi, avec une marge de 0,5 Go). Un client Linux l'envoie (`blockdev --getsize64`).

### A.6 Les routes de l'API

| Méthode | Route | Authentification | Rôle |
|---|---|---|---|
| GET | `/healthz` | aucune | `{"status":"ok"}` |
| GET | `/boot.ipxe` | aucune | Contenu de `DATA_DIR/boot.ipxe` où `@@BASE_URL@@` est remplacé par `PUBLIC_BASE_URL` (texte brut) |
| GET | `/live/{nom}` | aucune | Fichiers de `DATA_DIR/live/` ; `Range` géré ; pas de `..` possible |
| GET | `/winpe/{nom}` | aucune | Fichiers de `DATA_DIR/winpe/` ; pas de `..` possible |
| POST | `/api/v1/session` | aucune | Le client envoie le code et le matériel |
| POST | `/api/v1/deploy/start` | jeton | Le client envoie le nom du poste et reçoit tout pour installer |
| GET | `/api/v1/images/{image_id}/files/{nom}` | jeton | Image Clonezilla : un fichier du dossier d'image (`Range` géré) ; `nom` sans `/`, jamais un fichier caché |
| GET | `/api/v1/images/{image_id}/download` | jeton | Image WIM (`Range` géré, `Content-Length` présent) |
| POST | `/api/v1/deploy/report` | jeton | Le client dit « réussi » ou « échoué » |
| GET | `/admin/images` | identifiants | Page des images et des codes |

Les identifiants des routes `/admin/…` sont ceux de `ADMIN_USER` et `ADMIN_PASSWORD` (HTTP Basic). Le jeton des autres routes est transmis par l'en-tête `Authorization: Bearer <jeton>`.

**`POST /api/v1/session`** — corps : `{"code": "…", "hardware": { objet A.5 }}`

- `200` :

```json
{"token": "…", "expires_in": 3600,
 "room": {"id": "ROOMA", "label": "Salle A", "name_prefix": "ROOMA-", "name_example": "ROOMA-01"},
 "image": {"id": "win10-rooma", "label": "…", "os_family": "windows10", "kind": "clonezilla",
           "size_bytes": 47530000000, "sha256": null, "wim_index": null,
           "source_model": "HP Exemple 600 G3 MT", "source_disk_bytes": 256060514304},
 "assessment": {"level": "ok", "title": "…", "reasons": ["…"], "specs": ["Processeur : …"],
                "question": null, "blocked": false}}
```

Pour une image WIM, `sha256` et `wim_index` sont renseignés et `source_*` sont absents. `level` vaut `ok`, `warn` ou `discouraged` ; `question` vaut `null` si `level` est `ok`, sinon une phrase du type « Êtes-vous sûr(e) de votre choix ? » ; `blocked` vaut `true` quand le disque du PC est trop petit pour l'image.

- `400` `{"detail":"empty_code","penalty":true}` : code vide, pénalité immédiate.
- `401` `{"detail":"bad_code","attempts_left":2,"penalty":false}` ; quand `attempts_left` tombe à 0, `penalty` vaut `true`.
- `429` `{"detail":"locked","retry_after":287,"penalty":true}` : adresse bloquée après trop d'échecs.

**`POST /api/v1/deploy/start`** — corps : `{"hostname":"ROOMA-01","disk":"sda","confirmed_warning":true}`

- `200` pour une image Clonezilla :

```json
{"kind": "clonezilla", "image_name": "W10-22H2-ROOMA",
 "files_url": "http://10.0.0.3:8080/api/v1/images/win10-rooma/files",
 "files": [{"name": "sdb3.ntfs-ptcl-img.zst", "size": 47056396385}, {"name": "parts", "size": 20}],
 "source_disk": "sdb", "parts": ["sdb1", "sdb2", "sdb3", "sdb4"], "source_disk_bytes": 256060514304,
 "size_bytes": 47530000000, "unattend_xml": "…", "setup_complete_cmd": "…"}
```

Le client télécharge chaque `files_url/<nom>` avec son jeton ; l'intégrité est garantie par la décompression.

- `200` pour une image WIM :

```json
{"kind": "wim", "image_url": "http://10.0.0.3:8080/api/v1/images/win11-rooma/download",
 "sha256": "…", "size_bytes": 123, "wim_index": 1,
 "unattend_xml": "<?xml version=\"1.0\" …>", "setup_complete_cmd": "@echo off\r\n…"}
```

- `400` `{"detail":"bad_hostname","message":"…"}` : 1 à 15 caractères, lettres, chiffres et tirets, ni au début ni à la fin, pas uniquement des chiffres.
- `409` `{"detail":"warning_not_confirmed"}` si `assessment.level` n'est pas `ok` et que `confirmed_warning` est faux.
- `409` `{"detail":"disk_too_small","message":"…"}` : image Clonezilla, disque choisi (sinon le plus grand) plus petit que le disque d'origine.
- `401` `{"detail":"unauthorized"}` : jeton absent, invalide ou expiré. `403` `{"detail":"forbidden"}` : jeton qui ne correspond pas à l'image demandée.
- `503` `{"detail":"server_not_configured","message":"…"}` : variable ou `profiles.yaml` manquant ou invalide ; ou `{"detail":"image_missing"}` : l'image de la salle n'est pas sur le disque.

**`POST /api/v1/deploy/report`** — corps : `{"status":"success","step":"restore","message":"…"}` (`status` : `success` ou `failed`) → `204`.

### A.7 Jugement du matériel (`assessment`)

Le serveur juge le PC par rapport à la famille de système de l'image (`windows10` ou `windows11`). Trois niveaux : `ok`, `warn` (faisable mais pas une bonne idée), `discouraged` (très mauvaise idée ; on laisse passer si la personne confirme, sauf si `blocked`). Une information absente ou illisible donne au plus `warn`. Tolérance de 15 % sur la mémoire et le disque. Les messages sont en français, diplomatiques, avec les caractéristiques réelles du PC. Les seuils sont dans `server/app/hardware.py`.

| Cible | `discouraged` si… | `warn` si… |
|---|---|---|
| Windows 11 | processeur Intel antérieur à la 8ᵉ génération, AMD antérieur à Ryzen 2000, ou Celeron/Pentium/Atom ancien ; pas de TPM 2.0 ; BIOS classique ; RAM de moins de 4 Go ; disque de moins de 64 Go ; BIOS de 2016 ou avant | Core i3 (même récent) ; RAM de moins de 8 Go ; disque mécanique ; BIOS de 2017-2018 |
| Windows 10 | RAM de moins de 2 Go ; disque de moins de 32 Go ; processeur antérieur à la 4ᵉ génération Intel ; BIOS de 2012 ou avant | RAM de moins de 4 Go ; disque mécanique ; Celeron, Pentium ou Atom |

Pour une image Clonezilla, en plus :

| Constat | Niveau |
|---|---|
| Modèle du PC différent de celui de l'image (autre constructeur, autre numéro ou autre génération) | `discouraged` |
| Même modèle, autre format (tour, petit format…) | `warn` |
| Modèle du PC illisible, ou trop différent dans l'écriture pour être comparé | `warn` (« n'a pas pu être comparé ») |
| Disque du PC plus petit que le disque d'origine (strict si `size_bytes` est connu ; marge de 0,5 Go sinon) | `discouraged` et `blocked: true` |
| Disque plus grand | rien (question Q10) |

### A.8 Le programme client (`client/`)

- **Point d'entrée** : `client/deploy_client.py`, bibliothèque standard uniquement, syntaxe compatible Python 3.9 ou plus. Si `image.kind` (réponse de `session`) vaut `clonezilla`, le client appelle `linux_restore.restore()`, réservé au mini-système Linux ; sinon il appelle `windows_install.install()`, réservé à WinPE.
- **Configuration** : option `--server URL` (prioritaire), sinon `client/client.json` : `{"server_url": "http://10.0.0.3:8080", "penalty_seconds": 90, "ascii_only": false}`. Dans le mini-système Linux, l'adresse vient du paramètre du noyau `mdtprox_server=`.
- **Options de test** : `--simulate NOM` (`modern`, `hp-i3`, `old-lenovo`, `exemple-600-g3`, `exemple-600-g3-linux`), `--hardware-json FICHIER` et `--dry-run`. `--simulate` et `--hardware-json` n'existent qu'avec `--dry-run` : un matériel factice ne doit jamais servir à effacer un vrai disque. Il n'existe pas de mode « sans question ».
- **Déroulé** : réseau prêt, détection du matériel, saisie du code (masquée), pénalité en cas de refus, verdict et confirmation si nécessaire, nom du poste, confirmation `OUI` (le disque sera entièrement effacé), `deploy/start`, restauration (Clonezilla) ou partitionnement, téléchargement vérifié, `dism` et `bcdboot` (WIM), dépôt de `Unattend.xml` et `SetupComplete.cmd`, nettoyage, `deploy/report`, redémarrage.
- **Détails de comportement** : un code vide déclenche la pénalité sans appel à l'API ; une réponse `429` est toujours une pénalité ; en `--dry-run`, `deploy/start` est réellement appelé, mais rien n'est téléchargé et aucun rapport n'est envoyé. Sous Linux, le client envoie `disks[].size_bytes` et ne propose jamais les disques amovibles. Sous WinPE, il n'envoie jamais `tpm:"none"` ni `kind:"hdd"`, faute de source fiable sans PowerShell.
- **Pénalité** : code vide ou refus avec `penalty:true` : message poli mais ferme, compte à rebours de 90 secondes avec « Allez vous faire cuire un œuf », puis extinction (`poweroff` sous Linux, `wpeutil shutdown` sous WinPE).
- **Fichiers du poste cible** : `Windows\Panther\Unattend\Unattend.xml` et `Windows\Setup\Scripts\SetupComplete.cmd` ; le serveur fournit le contenu des deux, et `SetupComplete.cmd` supprime les fichiers de réponses après le premier démarrage.

### A.9 Paramètres du noyau du mini-système Linux

Écrits par `boot/boot-linux.ipxe`, lus par `/opt/mdtprox/start.sh` :

| Paramètre | Rôle |
|---|---|
| `boot=live`, `components` | Démarrage « live » |
| `fetch=<URL>/live/filesystem.squashfs`, `ip=dhcp` | Télécharge le système complet en mémoire ; demande une adresse dès le système de départ |
| `noautologin`, `keyboard-layouts=fr`, `timezone=Europe/Paris` | Aucune ouverture de session automatique ; clavier AZERTY ; fuseau horaire |
| `mdtprox_server=<URL>` | Adresse de l'API |
| `mdtprox_debug` | À ajouter à la main dans l'invite iPXE : ouvre un shell à la place du client |

---

## Annexe B — Les images Clonezilla

### B.1 Ce que contient un dossier d'image

Une image produite par `ocs-sr savedisk` est un dossier de fichiers :

| Fichier | Contenu |
|---|---|
| `disk`, `parts` | Nom du disque d'origine (par exemple `sdb`) et liste de ses partitions |
| `<disque>-gpt.gdisk` | Sauvegarde de la table de partitions GPT, faite par `sgdisk -b` : marque MBR de protection, en-têtes `EFI PART` et table de partitions |
| `<disque>-gpt.sgdisk` | Description lisible de la table (taille du disque en secteurs, partitions) |
| `Info-dmi.txt` | Matériel du PC d'origine (bloc « System Information » : constructeur, modèle) |
| `Info-saved-by-cmd.txt` | Commande Clonezilla d'origine |
| `<partition>.<système de fichiers>-ptcl-img.<compression>` | Une partition, par exemple `sdb3.ntfs-ptcl-img.zst` ; découpée en `.aa`, `.ab`… si l'image a été découpée |

### B.2 Comment le projet les lit

- **Le serveur** lit uniquement les petits fichiers d'information (`disk`, `parts`, `Info-dmi.txt`, `*.sgdisk`) et la liste des fichiers. Il ne lit jamais les fichiers de partition.
- **Le client** télécharge `<disque>-gpt.gdisk`, recrée la table avec `sgdisk --load-backup`, puis envoie chaque fichier de partition dans le décompresseur (`zstd`, `gzip`, `bzip2`, `xz`, `lz4`) et dans `partclone.<système de fichiers>` (tâche T4).
- Les noms de partition de l'image (`sdb3`) sont reliés aux partitions du disque cible par leur numéro (`sda3`, `nvme0n1p3`).

### B.3 Particularités constatées

- **Les images actuelles sont des copies de disque entier en UEFI/GPT** (partition de démarrage, partition réservée, partition Windows, le plus souvent une partition de récupération). Une image est donc liée à un modèle de PC et à une taille de disque : la poser sur un disque plus petit est impossible.
- **Partition « dd »** : une petite partition (16 Mo, type « Microsoft reserved ») peut être stockée brute, sans en-tête `partclone`, et contenir de vraies données. Elle est copiée telle quelle (tâche T4).
- **Version de `partclone`** : les images récentes portent l'en-tête `partclone-image`, version `0.3.37`, format `0002`. Debian 12 fournit une version plus ancienne : la lecture est à confirmer sur banc (question Q8).
- **Les noms de dossier ne sont pas fiables.** Une image peut avoir été prise sur un autre modèle que celui que son nom annonce ; le projet lit le modèle réel dans `Info-dmi.txt`.
- **Images généralisées ou non** : seul `sysprep /generalize` garantit que le fichier de réponses sera lu au premier démarrage (tâche T8, question Q1).

---


---

## Historique des versions

| Date | Version |
|---|---|
| 2026-10-24 | Version initiale : hébergement sur Windows Server 2019, banc d'essai sur Proxmox, images Clonezilla existantes, pages d'administration. |
