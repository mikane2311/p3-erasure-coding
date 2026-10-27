# P3 : Le stockage qui répare tout seul (erasure coding)
Prérequis : Docker, Python 3.10+, Linux ou WSL2.

python3 -m venv ~/venv && source ~/venv/bin/activate
pip install -r requirements.txt # zfec peut nécessiter : sudo apt install build-essential python3-dev
mkdir -p data logs && head -c 20M /dev/urandom > data/test20M.bin
./setup.sh 8 # 6 nœuds + 2 de secours
python surveillant.py & # boucle de réparation (30 s)
streamlit run dashboard.py # http://localhost:8501
./demo.sh # démo guidée
python bench.py data/bench100M.bin # mesures (arrêter le surveillant avant)

