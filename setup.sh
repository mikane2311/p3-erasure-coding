#!/bin/bash
N=${1:-6}
echo "services:" > docker-compose.yml
for i in $(seq 1 $N); do
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
