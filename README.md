# mdt_prox_distribservices

Déploiement automatisé de postes par le réseau (PXE), à partir d'images Clonezilla existantes.

Un poste démarre sur le réseau, charge un petit système Linux en mémoire, détecte son matériel et demande un code. Le code désigne une image. Le serveur vérifie que le poste convient (modèle, disque) et avertit poliment dans le cas contraire. Après confirmation, l'image est restaurée, puis le nom du poste, le domaine et le compte administrateur sont appliqués. Sans code valide, le poste affiche un message et s'éteint au bout de 1 min 30.

Conception et développement : **Baptiste Renneson-Boutard (Elania-Marvers)**, concepteur principal.
Projet libre sous licence MIT : reprise et adaptation bienvenues, en conservant la mention d'auteur et la licence.

## Contenu

| Dossier | Rôle |
|---|---|
| `server/` | API Python (FastAPI) : codes, jugement du matériel, fichiers de configuration, pages d'administration |
| `client/` | Programme exécuté sur le poste à installer (restauration Clonezilla, ou WinPE pour des images `.wim`) |
| `live/` | Recette `live-build` du mini-système Linux |
| `boot/`, `deploy/`, `scripts/` | Démarrage réseau iPXE, services, installation de la VM, DHCP, construction du mini-système |
| `tests/` | Tests de bout en bout (serveur et client réels, en simulation) |

## Essayer en local

```sh
bash tests/run_all.sh
```

## Documentation

Tout est dans [CDC.md](CDC.md) : cahier des charges, choix techniques expliqués simplement, état des preuves, mise en service et plan d'essai.

## État

Preuve de concept. Le code est testé par simulation ; la construction du mini-système Linux, le démarrage réseau et la restauration sur du matériel réel restent à valider (voir le CDC, section « Ce qui est prouvé »).
