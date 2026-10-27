#!/bin/bash
# Génère docker-compose.yml pour N nœuds (défaut 6) et les démarre.
# Les dossiers srv/noeudN sont créés ici, par l'utilisateur courant : sinon
# Docker les crée avec le propriétaire root et plus personne ne peut y écrire.
N=${1:-6}
echo "services:" > docker-compose.yml
for i in $(seq 1 $N); do
mkdir -p "srv/noeud$i"
cat >> docker-compose.yml << EOT
  noeud$i:
    image: alpine:3.20
    container_name: noeud$i
    user: "$(id -u):$(id -g)"
    command: sleep infinity
    volumes:
      - ./srv/noeud$i:/data
EOT
done
docker compose up -d
docker ps --format 'table {{.Names}}\t{{.Status}}'
