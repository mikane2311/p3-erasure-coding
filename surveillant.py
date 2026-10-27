#!/usr/bin/env python3
"""
surveillant.py : supervision et auto-réparation du stockage.

    python surveillant.py [--intervalle 30] [--une-fois]

À chaque cycle, pour chaque fichier de l'index :
  1. contrôle l'état de ses morceaux (OK / MANQUANT / CORROMPU / NOEUD_ARRETE) ;
  2. reconstruit les morceaux défaillants à partir de k morceaux sains ;
  3. journalise l'opération (logs/surveillant.log) et enregistre la mesure
     (logs/reparations.csv).

Contrainte de placement : un nœud hébergeant déjà un morceau d'un fichier ne
reçoit jamais un second morceau de ce fichier.
"""
import argparse
import csv
import logging
import subprocess
import time
from pathlib import Path

import codec
from repartir import RACINE, SRV, charger_index, inspecter, sauver_index

LOGS = RACINE / "logs"
CSV_REPARATIONS = LOGS / "reparations.csv"
ENTETE_CSV = ["horodatage", "fichier", "morceau", "cause",
              "ancien_noeud", "nouveau_noeud", "duree_s", "octets"]

log = logging.getLogger("surveillant")


def noeuds_actifs() -> list:
    """Liste des conteneurs 'noeudN' en cours d'exécution, triés par numéro."""
    r = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=^noeud"],
        capture_output=True, text=True)
    return sorted(r.stdout.split(), key=lambda nom: int(nom[5:]))


def enregistrer_mesure(ligne: list) -> None:
    """Ajoute une ligne au CSV des réparations (crée l'en-tête au besoin)."""
    nouveau = not CSV_REPARATIONS.exists()
    with open(CSV_REPARATIONS, "a", newline="") as f:
        w = csv.writer(f)
        if nouveau:
            w.writerow(ENTETE_CSV)
        w.writerow(ligne)


def reparer_fichier(fid: str, entree: dict) -> None:
    """Contrôle un fichier et répare tous ses morceaux défaillants."""
    statuts, disponibles = inspecter(fid, entree)
    defauts = {i: s for i, s in statuts.items() if s != "OK"}
    if not defauts:
        return

    debut = time.perf_counter()
    k, m = entree["k"], entree["m"]
    log.warning("%s : %d morceau(x) défaillant(s) %s", fid, len(defauts),
                {i: defauts[i] for i in sorted(defauts)})

    # Seuil de survie : il faut au moins k morceaux sains pour reconstruire.
    if len(disponibles) < k:
        log.critical("%s : %d morceaux sains < k=%d, réparation impossible "
                     "(tolérance dépassée)", fid, len(disponibles), k)
        return

    # Nœuds candidats : vivants et n'hébergeant aucun morceau de ce fichier.
    occupes = {info["noeud"] for info in entree["morceaux"].values()}
    libres = [n for n in noeuds_actifs() if n not in occupes]

    # Plan de réparation : {indice: nœud cible}
    plan = {}
    for i, statut in sorted(defauts.items()):
        ancien = entree["morceaux"][str(i)]["noeud"]
        if statut == "NOEUD_ARRETE":
            if not libres:
                log.warning("%s morceau %d : état dégradé, aucun nœud de "
                            "remplacement disponible (nouvel essai au cycle "
                            "suivant)", fid, i)
                continue
            plan[i] = libres.pop(0)
        else:  # MANQUANT / CORROMPU : le nœud est vivant, on réécrit sur place
            plan[i] = ancien
    if not plan:
        return

    # Reconstruction : lecture de k morceaux sains, recalcul des manquants.
    refaits = codec.reparer(disponibles, k, m, list(plan))

    index = charger_index()          # relecture : évite d'écraser un put concurrent
    for i, contenu in refaits.items():
        info = entree["morceaux"][str(i)]
        if codec.md5(contenu) != info["hash"]:
            log.error("%s morceau %d : empreinte reconstruite différente de "
                      "l'originale, abandon", fid, i)
            continue
        cible, ancien = plan[i], info["noeud"]
        dossier = SRV / cible / fid
        dossier.mkdir(parents=True, exist_ok=True)
        (dossier / f"morceau.{i}").write_bytes(contenu)

        if cible != ancien:          # migration : on retire l'ancienne copie orpheline
            orphelin = SRV / ancien / fid / f"morceau.{i}"
            orphelin.unlink(missing_ok=True)
            index[fid]["morceaux"][str(i)]["noeud"] = cible

        duree = time.perf_counter() - debut
        log.info("%s morceau %d réparé (%s) : %s -> %s en %.3f s",
                 fid, i, defauts[i], ancien, cible, duree)
        enregistrer_mesure([time.strftime("%F %T"), fid, i, defauts[i],
                            ancien, cible, f"{duree:.3f}", len(contenu)])
    sauver_index(index)


def cycle() -> None:
    index = charger_index()
    for fid, entree in index.items():
        reparer_fichier(fid, entree)
    log.info("cycle terminé (%d fichier(s) contrôlé(s))", len(index))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--intervalle", type=int, default=30, help="secondes entre cycles")
    p.add_argument("--une-fois", action="store_true", help="un seul cycle puis arrêt")
    args = p.parse_args()

    LOGS.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOGS / "surveillant.log"),
                  logging.StreamHandler()])
    log.info("démarrage, intervalle %d s", args.intervalle)

    while True:
        try:
            cycle()
        except Exception:            # la supervision ne doit jamais s'arrêter
            log.exception("cycle en échec")
        if args.une_fois:
            break
        time.sleep(args.intervalle)


if __name__ == "__main__":
    main()
