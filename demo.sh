#!/bin/bash
# demo.sh : démonstration guidée du stockage auto-réparant (P3).
# Prérequis, dans d'autres terminaux :
#   T1 : python surveillant.py          (intervalle par défaut : 30 s)
#   T2 : streamlit run dashboard.py     (http://localhost:8501)
# Usage : ./demo.sh     (WAIT = délai d'attente du surveillant, 35 s par défaut)
cd "$(dirname "$0")" || exit 1
source ~/venv/bin/activate
F=data/test20M.bin
NOM=$(basename "$F")
WAIT=${WAIT:-45}

pause()    { read -r -p $'\n[Entrée pour continuer] '; }
titre()    { echo -e "\n\033[1;36m=== $* ===\033[0m"; }
cmd()      { echo -e "\033[0;33m\$ $*\033[0m"; eval "$@"; }
journal()  { grep -E "WARNING|CRITICAL|réparé" logs/surveillant.log | tail -n 4; }
attendre() { echo "Attente du surveillant (${WAIT} s)..."; sleep "$WAIT"; }

titre "0. Remise à zéro"
docker start $(docker ps -aq --filter name=noeud) > /dev/null
mkdir -p logs/archives
[ -f logs/reparations.csv ] && mv logs/reparations.csv "logs/archives/rep_$(date +%H%M%S).csv"
rm -rf index.json srv/noeud*/*
echo "Stockage vide, tous les nœuds démarrés."
pause

titre "1. Dépôt d'un fichier en 4+2 sur 6 nœuds (jamais 2 morceaux sur le même nœud)"
cmd md5sum "$F"
cmd python repartir.py put "$F" 4 2
cmd python repartir.py etat
pause

titre "2. Panne d'un nœud : docker stop noeud3, le fichier reste lisible"
cmd docker stop noeud3
cmd "python repartir.py get $NOM /tmp/demo.bin && md5sum /tmp/demo.bin"
pause

titre "3. Le surveillant détecte la panne et reconstruit le morceau sur un nœud de secours"
attendre
cmd journal
cmd python repartir.py etat
pause

titre "4. Deux pannes simultanées (tolérance maximale)"
cmd docker start noeud3          # revient comme nœud libre
cmd docker stop noeud1 noeud2
cmd "python repartir.py get $NOM /tmp/demo.bin && md5sum /tmp/demo.bin"
attendre
cmd python repartir.py etat
pause

titre "5. Limite : 3 pannes simultanées (tolérance = 2), message clair, pas de crash"
cmd docker stop noeud4 noeud5 noeud6
cmd python repartir.py get "$NOM" /tmp/demo2.bin
echo "-> Les disques sont intacts : au retour des nœuds, tout est récupérable."
cmd docker start noeud4 noeud5 noeud6
cmd "python repartir.py get $NOM /tmp/demo.bin && md5sum /tmp/demo.bin"
pause

titre "6. Bilan"
cmd docker start noeud1 noeud2
cmd "column -s, -t < logs/reparations.csv"
