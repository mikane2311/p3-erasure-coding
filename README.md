# P3 : Stockage distribué auto-réparant (erasure coding)


## Présentation

Ce projet met en œuvre un système de stockage distribué qui tolère les pannes et répare automatiquement les morceaux perdus.

Un fichier est découpé en **k morceaux de données**, auxquels s'ajoutent **m morceaux de parité** (codage à effacement de type Reed-Solomon). N'importe quels k morceaux parmi les k+m suffisent à reconstruire le fichier. Chaque morceau est placé sur un nœud différent (conteneur Docker). Exemple : en 4+2, le système tolère 2 pannes pour 1,5x la place du fichier, contre 3x pour 3 copies simples.

Fonctionnalités :
- **Découpage et reconstruction** vérifiés par md5sum, même après effacement de morceaux.
- **Répartition** sur plusieurs nœuds : jamais 2 morceaux du même fichier sur le même nœud.
- **Surveillant** (boucle de 30 s) : détecte un morceau manquant, corrompu ou hébergé par un nœud arrêté, le reconstruit et journalise l'opération (quel morceau, quand, durée).
- **Tableau de bord** Streamlit : état des nœuds et des morceaux, journal des réparations, résultats du banc d'essai.
- **Banc d'essai** : comparaison de 4+2, 6+2, 8+4 et 3 copies.
- **Démonstration guidée** : pannes simulées avec `docker stop`.

## Technologies

- **Python 3.10+** : logique de stockage, de réparation et de mesure.
- **zfec** : encodage et décodage Reed-Solomon (bibliothèque C, rapide).
- **Docker** : un conteneur Alpine par nœud de stockage.
- **Streamlit** : tableau de bord.
- **Linux / WSL2** : environnement d'exécution.

## Prérequis

- Linux, ou WSL2 sous Windows (Ubuntu 24.04 testé).
- Docker (sous Windows : Docker Desktop avec intégration WSL activée).
- Python 3.10 ou plus récent, et Git.

Sous Windows, travailler dans le système de fichiers WSL (`~/p3`), pas dans `/mnt/c`, pour des mesures fiables.

## Installation

### 0. Récupérer le projet

```bash
git clone https://github.com/mikane2311/p3-erasure-coding.git p3 && cd p3
```

### 1. Environnement virtuel

```bash
python3 -m venv ~/venv
source ~/venv/bin/activate
```

### 2. Dépendances

```bash
pip install -r requirements.txt
```

Si `zfec` demande une compilation (Ubuntu / Debian) :

```bash
sudo apt update && sudo apt install -y build-essential python3-dev
```

### 3. Données de test

```bash
mkdir -p data logs
head -c 20M /dev/urandom > data/test20M.bin
```

### 4. Nœuds de stockage

```bash
chmod +x setup.sh demo.sh
./setup.sh 8
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

`setup.sh` génère `docker-compose.yml` et démarre 8 conteneurs : `noeud1` à `noeud6` (stockage) et `noeud7`, `noeud8` (secours). Le fichier généré contient l'identifiant utilisateur local : toujours relancer `setup.sh` sur une nouvelle machine plutôt que de réutiliser un `docker-compose.yml` copié.

## Lancement

Utiliser un terminal par composant (activer le venv et `cd ~/p3` dans chacun).

### 1. Surveillant

```bash
python surveillant.py                 # intervalle par défaut : 30 s
python surveillant.py --intervalle 10 # pour tester plus vite
```

Journaux : `logs/surveillant.log` (texte) et `logs/reparations.csv` (une ligne par morceau réparé : horodatage, fichier, morceau, cause, ancien et nouveau nœud, durée).

### 2. Tableau de bord

```bash
streamlit run dashboard.py
```

Ouvrir http://localhost:8501. Il affiche l'état des nœuds, l'état de chaque morceau (OK, nœud arrêté, manquant, corrompu), le journal des réparations et les graphiques du banc d'essai.

### 3. Démonstration guidée

```bash
./demo.sh
```

Prérequis : surveillant et tableau de bord déjà lancés. Le script enchaîne : dépôt d'un fichier en 4+2, panne d'un nœud (fichier toujours lisible), auto-réparation, deux pannes simultanées, trois pannes (message d'erreur clair, sans crash), puis retour à la normale. Appuyer sur Entrée à chaque étape.

**Attention :** `demo.sh` réinitialise le stockage (`srv/` et `index.json`). L'ancien `logs/reparations.csv` est archivé dans `logs/archives/`.

## Utilisation manuelle

```bash
python repartir.py put data/test20M.bin 4 2     # dépôt : 4 morceaux de données + 2 de parité
python repartir.py etat                          # état de chaque morceau
docker stop noeud3                               # simuler une panne
python repartir.py get test20M.bin /tmp/o.bin    # lecture malgré la panne
md5sum data/test20M.bin /tmp/o.bin               # les deux empreintes sont identiques
```

Pour tester 8+4 (12 nœuds nécessaires) :

```bash
./setup.sh 12
python repartir.py put data/test20M.bin 8 4 --noeuds 12
```

Test du codec seul (sans conteneurs) :

```bash
python codec.py encode data/test20M.bin 4 2 /tmp/t42
rm /tmp/t42/morceau.1 /tmp/t42/morceau.4         # 2 morceaux perdus
python codec.py decode /tmp/t42 /tmp/out.bin     # reconstruction, md5 vérifié
```

## Banc d'essai

```bash
head -c 100M /dev/urandom > data/bench100M.bin
python bench.py data/bench100M.bin --reps 5
```

**Arrêter le surveillant et fermer les applications lourdes avant de mesurer.** Le fichier `data/bench100M.bin` doit exister.

Méthode : lecture à froid (éviction du cache de pages), écriture synchronisée (`fsync`), 5 répétitions après 1 échauffement ignoré, médiane et écart-type. La réparation est mesurée après perte d'un morceau de données (cas le plus coûteux : décodage complet). Les morceaux sont écrits dans `bench_tmp/` (disque local, sans conteneurs ni réseau).

Sorties : `logs/bench.csv` (toutes les mesures), `logs/tableau.md` (synthèse), `logs/bench.png` (graphique par phase, généré par le tableau de bord).

## Résultats

Fichier de 100 Mo, médiane ± écart-type sur 5 répétitions. Machine : Intel Core i5-8350U, 2 vCPU, 3,8 Go de RAM, WSL2 (détails dans `logs/config.txt`).

| Découpage | Place | Pannes tolérées | Temps découpage (s) | Temps réparation (s) | Lecture/réparation |
|---|---|---|---|---|---|
| 4+2 | 1,50x | 2 | 1,190 ± 0,184 | 0,634 ± 0,079 | 4x |
| 6+2 | 1,33x | 2 | 1,318 ± 0,247 | 0,597 ± 0,216 | 6x |
| 8+4 | 1,50x | 4 | 1,715 ± 0,283 | 0,679 ± 0,077 | 8x |
| 3 copies | 3,00x | 2 | 1,092 ± 0,099 | 0,714 ± 0,227 | 1x |

**Recommandation : 4+2** pour une infrastructure de 6 nœuds : même tolérance que 3 copies pour moitié moins de place.

Réparer un morceau exige de lire k morceaux : l'amplification de lecture vaut k (4x pour 4+2, 8x pour 8+4). Sur disque local, les écarts de temps de réparation entre schémas restent inférieurs à l'écart-type, car un morceau plus petit à réécrire compense la lecture et le calcul supplémentaires. Sur un vrai cluster, ce trafic de lecture sollicite k nœuds et le réseau.

## Principe de fonctionnement

1. **Découpage et encodage** : le fichier est complété par des zéros pour obtenir k blocs égaux, puis m blocs de parité sont calculés.
2. **Distribution** : chaque morceau est écrit sur un nœud distinct ; `index.json` (hors des nœuds) enregistre le nœud et l'empreinte md5 de chaque morceau.
3. **Détection** : le surveillant contrôle l'état des conteneurs (`docker inspect`) et l'empreinte de chaque morceau.
4. **Reconstruction** : les morceaux manquants sont recalculés à partir de k morceaux sains.
5. **Réparation** : un morceau manquant ou corrompu est réécrit sur son nœud ; celui d'un nœud arrêté est placé sur un nœud de secours libre (jamais un nœud qui héberge déjà un morceau du même fichier).
6. **Supervision** : journal des réparations et tableau de bord.

Si plus de m morceaux sont indisponibles, la lecture échoue avec un message explicite, sans crash.

## Structure du projet

```text
p3/
├── codec.py            # Encodage, décodage et réparation (zfec, schéma k+m)
├── repartir.py         # put / get / etat : répartition sur les nœuds, index.json
├── surveillant.py      # Boucle de réparation (30 s), journaux
├── dashboard.py        # Tableau de bord Streamlit
├── bench.py            # Mesures : 4+2, 6+2, 8+4, 3 copies
├── setup.sh            # Génère docker-compose.yml et lance N nœuds
├── demo.sh             # Démonstration guidée (réinitialise le stockage)
├── docker-compose.yml  # Généré par setup.sh (contient l'uid local)
├── requirements.txt
├── logs/               # bench.csv, tableau.md, bench.png, config.txt, reparations.csv, preuves
└── data/, srv/, index.json   # générés à l'exécution (non versionnés)
```

## Limites

- Au-delà de m pannes simultanées (3 pour un schéma 4+2), la lecture échoue avec un message explicite ; les données restent sur les disques et redeviennent lisibles au retour des nœuds.
- Une panne de nœud n'est réparée que s'il existe un nœud de secours libre ; sinon le fichier reste « dégradé » et le surveillant réessaie à chaque cycle.
- `index.json` n'est pas répliqué : sa perte rend les fichiers illisibles (piste d'amélioration : le répliquer ou le stocker avec les morceaux).
- Le banc d'essai mesure le codage et les E/S sur disque local, sans réseau : le trafic de réparation d'un vrai cluster n'est pas mesuré.
- WSL2 conserve un cache côté Windows qui ne peut pas être vidé : les temps de lecture à froid restent approximatifs.
- `demo.sh` réinitialise le stockage (`srv/`, `index.json`) : ne pas l'exécuter sur des données à conserver.

## Équipe (Groupe 13):

- Lchhab Iman
- Soukaina Amehri
- Mallouk Nadia
- Mikane Fatima-Ezzhrae
